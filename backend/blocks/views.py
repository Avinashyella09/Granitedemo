from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Block, Quarry, Measurement
from .permissions import IsSupervisor
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.contrib.auth import authenticate, login as django_login, logout as django_logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from .serializers import BlockSerializer
import datetime
import os
import hashlib
import threading
import time
import cv2
import logging

logger = logging.getLogger(__name__)

# ============================================================
# SHARED ANALYTICS DATA CACHE
# Prevents repeated MongoDB Atlas round-trips across views.
# Each collection is fetched at most once per 30-second window.
# ============================================================
_analytics_cache = {}
_analytics_cache_lock = threading.Lock()
_CACHE_TTL_SECONDS = 300

def _get_cached(key, fetcher):
    """Thread-safe cache: returns cached data or fetches fresh."""
    now = time.time()
    with _analytics_cache_lock:
        entry = _analytics_cache.get(key)
        if entry and (now - entry['ts']) < _CACHE_TTL_SECONDS:
            print(f"CACHE HIT for {key}")
            return entry['data']
    print(f"CACHE MISS for {key}")
    # Fetch outside lock to avoid blocking
    data = fetcher()
    with _analytics_cache_lock:
        _analytics_cache[key] = {'data': data, 'ts': time.time()}
    return data

def _invalidate_cache():
    """Clear the analytics cache (call after mutations)."""
    with _analytics_cache_lock:
        _analytics_cache.clear()

def _get_all_blocks():
    return _get_cached('all_blocks', lambda: list(Block.objects.all().select_related(max_depth=1)))

def _get_all_quarries():
    return _get_cached('all_quarries', lambda: list(Quarry.objects.all()))

def _get_all_assessments():
    from .models import Assessment
    return _get_cached('all_assessments', lambda: list(Assessment.objects.all().select_related(max_depth=1)))

def _get_all_officers():
    from .models import Officer
    return _get_cached('all_officers', lambda: list(Officer.objects.all().select_related(max_depth=1)))

def _get_all_audit_logs():
    from .models import AuditLog
    return _get_cached('all_audit_logs', lambda: list(AuditLog.objects.all().select_related(max_depth=1)))

def block_to_dict(block):
    """Single source of truth for the Block API shape.

    This exists because the same dict was previously hand-built in six places
    and had already drifted apart - and, critically, every copy omitted the
    approval/override fields. BlockSerializer declares those read_only, and DRF
    raises SkipField for a read_only field missing from the source object, so
    they were silently absent from every response. The dashboard therefore
    showed every block as "pending", its approval filter matched nothing, and
    the "Original CV Estimates (Preserved)" override panel could never render -
    an audit-trail gap, not a cosmetic one.
    """
    def measurement_dict(m):
        if not m:
            return None
        return {
            "length_m": m.length_m,
            "breadth_m": m.breadth_m,
            "height_m": m.height_m,
            "volume_m3": m.volume_m3,
            "confidence": m.confidence,
            "measurement_method": m.measurement_method,
            "measured_at": m.measured_at,
        }

    return {
        "id": str(block.id),
        "block_id": block.block_id,
        "quarry_id": str(block.quarry.id) if block.quarry else None,
        "image_paths": block.image_paths,
        "captured_at": block.captured_at,
        "gps_latitude": block.gps_latitude,
        "gps_longitude": block.gps_longitude,
        "status": block.status,
        "measurement": measurement_dict(block.measurement),
        "cv_status": block.cv_status,
        "cv_error_message": block.cv_error_message,
        "raw_image_path": block.raw_image_path,
        "annotated_image_path": block.annotated_image_path,
        "created_at": block.created_at,
        "updated_at": block.updated_at,
        # --- previously dropped by SkipField ---
        "is_overridden": block.is_overridden,
        "original_measurement": measurement_dict(block.original_measurement),
        "override_reason": block.override_reason,
        "approval_status": block.approval_status,
        "approved_by": block.approved_by,
        "approved_at": block.approved_at,
        # --- real capture metadata, so the UI stops using hardcoded fallbacks ---
        "inspecting_officer_id": block.inspecting_officer_id,
        "submitted_quarry_id": block.submitted_quarry_id,
        "reference_warnings": list(block.reference_warnings or []),
        "device_id": block.device_id,
        "lighting_condition": block.lighting_condition,
        "capture_attempt_count": block.capture_attempt_count,
    }

class BlockListCreateAPIView(APIView):
    """
    API View to list all blocks or create a new block.

    Phase 6B: permissions are per METHOD, not per class, and that is deliberate.

    GET returns the whole block register - dimensions, approval state, image
    paths - so it requires authentication. POST is called by the FROZEN iOS
    field app (App.js -> apiService.createBlock) on its CV measurement path.
    The frozen app has no authentication contract and must keep working
    unchanged, so POST stays anonymous in this step. A class-level
    permission_classes would have closed the read hole and broken the field app
    at the same time.
    """

    def get_permissions(self):
        if self.request.method == 'GET':
            return [IsAuthenticated()]
        # POST: FIELD-APP AUTHENTICATION PENDING (deliberate PoC exception).
        # The frozen iOS app calls this on its CV measurement path and sends
        # no credential. See BlockARMeasureAPIView for the full rationale.
        return [AllowAny()]

    def get(self, request):
        def _fetch_serialized_blocks():
            blocks = list(Block.objects.all().select_related(max_depth=1))
            return [BlockSerializer(block_to_dict(b)).data for b in blocks]

        data = _get_cached('blocks_json', _fetch_serialized_blocks)
        return Response(data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = BlockSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        validated_data = serializer.validated_data
        block_id = validated_data['block_id']
        
        # Check duplicate block ID
        if Block.objects(block_id=block_id).first():
            return Response(
                {"block_id": ["Block with this ID already exists."]},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # Resolve Quarry if quarry_id is provided
        quarry_id = validated_data.get('quarry_id')
        quarry_doc = None
        if quarry_id:
            try:
                quarry_doc = Quarry.objects(id=quarry_id).first()
                if not quarry_doc:
                    return Response(
                        {"quarry_id": [f"Quarry with ID '{quarry_id}' does not exist."]},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Exception:
                return Response(
                    {"quarry_id": ["Invalid Quarry ID format."]},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
        # Resolve Measurement if provided
        measurement_data = validated_data.get('measurement')
        measurement_doc = None
        if measurement_data:
            measurement_doc = Measurement(
                length_m=measurement_data['length_m'],
                breadth_m=measurement_data['breadth_m'],
                height_m=measurement_data['height_m'],
                volume_m3=measurement_data['volume_m3'],
                confidence=measurement_data.get('confidence', 1.0),
                measurement_method=measurement_data.get('measurement_method', 'manual'),
                measured_at=measurement_data.get('measured_at') or datetime.datetime.utcnow()
            )

        # Create Block document
        block = Block(
            block_id=block_id,
            quarry=quarry_doc,
            image_paths=validated_data.get('image_paths', []),
            captured_at=validated_data.get('captured_at'),
            gps_latitude=validated_data.get('gps_latitude'),
            gps_longitude=validated_data.get('gps_longitude'),
            status=validated_data.get('status', 'pending'),
            measurement=measurement_doc
        )
        block.save()
        _invalidate_cache()

        # Build clean output representation
        output_data = {
            "id": str(block.id),
            "block_id": block.block_id,
            "quarry_id": str(block.quarry.id) if block.quarry else None,
            "image_paths": block.image_paths,
            "captured_at": block.captured_at,
            "gps_latitude": block.gps_latitude,
            "gps_longitude": block.gps_longitude,
            "status": block.status,
            "measurement": {
                "length_m": block.measurement.length_m,
                "breadth_m": block.measurement.breadth_m,
                "height_m": block.measurement.height_m,
                "volume_m3": block.measurement.volume_m3,
                "confidence": block.measurement.confidence,
                "measurement_method": block.measurement.measurement_method,
                "measured_at": block.measurement.measured_at,
            } if block.measurement else None,
            "cv_status": block.cv_status,
            "cv_error_message": block.cv_error_message,
            "raw_image_path": block.raw_image_path,
            "annotated_image_path": block.annotated_image_path,
            "created_at": block.created_at,
            "updated_at": block.updated_at,
        }
        
        response_serializer = BlockSerializer(output_data)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class BlockDetailAPIView(APIView):
    # Phase 6B read-surface authentication. Block detail exposes measurements and approval state.
    # IsAuthenticated, not IsSupervisor: any authenticated RTGS user may
    # read: only decisions are supervisor-gated.
    permission_classes = [IsAuthenticated]
    """
    API View to retrieve a single block by block_id.
    """
    def get(self, request, block_id):
        block = Block.objects(block_id=block_id).first()
        if not block:
            return Response(
                {"error": f"Block with ID '{block_id}' not found."},
                status=status.HTTP_404_NOT_FOUND
            )
            
        data = {
            "id": str(block.id),
            "block_id": block.block_id,
            "quarry_id": str(block.quarry.id) if block.quarry else None,
            "image_paths": block.image_paths,
            "captured_at": block.captured_at,
            "gps_latitude": block.gps_latitude,
            "gps_longitude": block.gps_longitude,
            "status": block.status,
            "measurement": {
                "length_m": block.measurement.length_m,
                "breadth_m": block.measurement.breadth_m,
                "height_m": block.measurement.height_m,
                "volume_m3": block.measurement.volume_m3,
                "confidence": block.measurement.confidence,
                "measurement_method": block.measurement.measurement_method,
                "measured_at": block.measurement.measured_at,
            } if block.measurement else None,
            "cv_status": block.cv_status,
            "cv_error_message": block.cv_error_message,
            "raw_image_path": block.raw_image_path,
            "annotated_image_path": block.annotated_image_path,
            "created_at": block.created_at,
            "updated_at": block.updated_at,
        }
        serializer = BlockSerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)


from .models import Assessment
from .serializers import AssessmentSerializer
from .services import generate_assessment_report

class AssessmentListCreateAPIView(APIView):
    """
    API View to list all assessments or create a new assessment.

    Phase 6B: GET requires authentication - the list carries rate, tonnage and
    the payable amount for every block.

    Phase 6C: POST now requires authentication too. Creating an Assessment is
    the money-determining action in this system - it fixes the tonnage, the
    official rate and the payable seigniorage for a block - so it must be
    attributable to a real identity. It was the last anonymous financial write.

    IsAuthenticated rather than IsSupervisor, deliberately: the business model
    gates DECISIONS (approve / reject / override) on the supervisor role, while
    running the official calculation is a routine step an inspecting officer
    performs. The figures are server-derived either way, and the supervisor's
    approval remains the control that makes an assessment count. Requiring
    SUPERVISOR here would gate a calculation, not a decision, and would break
    the officer workflow for no security gain.

    No client may influence the money: rate, amount, volume and tonnage are
    read_only on the serializer, and the classification a client sends is
    recorded then discarded in favour of the official rule.
    """

    def get_permissions(self):
        # Both methods now require authentication; kept as get_permissions so the
        # GET/POST distinction stays visible if the two ever diverge again.
        return [IsAuthenticated()]

    def get(self, request):
        assessments = Assessment.objects.all()
        serialized_data = []
        for ass in assessments:
            data = {
                "id": str(ass.id),
                "block_id": ass.block.block_id if ass.block else None,
                "granite_category": ass.granite_category,
                "gangsaw_classification": ass.gangsaw_classification,
                "volume_m3": ass.volume_m3,
                "weight_mt": ass.weight_mt,
                "rate_per_mt": ass.rate_per_mt,
                "indicative_seigniorage": ass.indicative_seigniorage,
                "density_mt_per_m3": ass.density_mt_per_m3,
                "status": ass.status,
                "created_at": ass.created_at
            }
            serializer = AssessmentSerializer(data)
            serialized_data.append(serializer.data)
        return Response(serialized_data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = AssessmentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        validated_data = serializer.validated_data
        block_id = validated_data['block_id']
        category = validated_data['granite_category']
        classification = validated_data.get('gangsaw_classification')
        density = validated_data.get('density')
        
        # 1. Resolve Block document
        block_doc = Block.objects(block_id=block_id).first()
        if not block_doc:
            return Response(
                {"block_id": [f"Block with ID '{block_id}' does not exist."]},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # 2. Check if Block has measurements
        if not block_doc.measurement:
            return Response(
                {"block_id": ["Block does not have measurement data. Cannot run assessment."]},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # 3. Check duplicate assessment
        if Assessment.objects(block=block_doc).first():
            return Response(
                {"block_id": ["Assessment for this block already exists."]},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 4. OFFICIAL calculation - server authoritative.
        #
        # The client's gangsaw_classification is accepted for request-shape
        # compatibility and then DISCARDED: classification is derived from the
        # stored dimensions by the official > 270cm x 150cm rule. Rate and
        # amount were already read_only on the serializer and remain so.
        # Density keeps the existing contract (client may supply one, else the
        # project default) - see blocks/config.DEFAULT_DENSITY_MT_PER_M3.
        from . import seigniorage
        from .config import DEFAULT_DENSITY_MT_PER_M3

        effective_density = density if density is not None else DEFAULT_DENSITY_MT_PER_M3
        try:
            result = seigniorage.calculate(
                length_m=block_doc.measurement.length_m,
                breadth_m=block_doc.measurement.breadth_m,
                height_m=block_doc.measurement.height_m,
                granite_category=category,
                density_mt_per_m3=effective_density,
            )
        except seigniorage.SeigniorageError as e:
            return Response(
                {"granite_category": [str(e)]},
                status=status.HTTP_400_BAD_REQUEST
            )

        client_classification = (classification or "").strip()
        server_classification = result["gangsaw_classification"]

        # 5. Persist the Assessment from the SERVER-derived values only.
        ass = Assessment(
            block=block_doc,
            granite_category=result["granite_category"],
            gangsaw_classification=server_classification,
            volume_m3=result["volume_m3"],
            weight_mt=result["tonnage_mt"],
            rate_per_mt=result["rate_per_mt"],
            indicative_seigniorage=result["seigniorage_amount"],
            density_mt_per_m3=result["density_mt_per_m3"],
            status=validated_data.get('status', 'draft')
        )
        ass.save()
        _invalidate_cache()

        # 6. Audit - the assessment is the money-determining action and was
        # previously unaudited entirely.
        AuditLog(
            block=block_doc,
            block_id_snapshot=block_doc.block_id,
            action='seigniorage_assessed',
            # Phase 6C: POST requires IsAuthenticated, so request.user is always
            # a real authenticated identity here and the previous anonymous
            # 'System' fallback is unreachable - removed rather than left as dead
            # code that implies an anonymous write is still possible.
            #
            # The actor comes ONLY from the authenticated session. An 'actor'
            # field in the request body cannot influence it: AssessmentSerializer
            # declares no such field, and this view never reads one.
            actor=request.user.get_username(),
            details=(
                f"Official seigniorage: block_id={block_id}, "
                f"volume={result['volume_m3']}m3, "
                f"density={result['density_mt_per_m3']}MT/m3, "
                f"tonnage={result['tonnage_mt']}MT, "
                f"category={result['granite_category']}, "
                f"classification={server_classification}, "
                f"rate={result['rate_per_mt']}INR/MT, "
                f"amount={result['seigniorage_amount']}INR, "
                f"schedule={result['rate_schedule_version']}, "
                f"client_classification={client_classification or 'none'}"
                + ("" if client_classification in ("", server_classification)
                   else " (client value ignored; server-derived used)")
            ),
            timestamp=datetime.datetime.utcnow(),
        ).save()

        output_data = {
            "id": str(ass.id),
            "block_id": block_id,
            "granite_category": ass.granite_category,
            "gangsaw_classification": ass.gangsaw_classification,
            "volume_m3": ass.volume_m3,
            "weight_mt": ass.weight_mt,
            "rate_per_mt": ass.rate_per_mt,
            "indicative_seigniorage": ass.indicative_seigniorage,
            "density_mt_per_m3": ass.density_mt_per_m3,
            "status": ass.status,
            "created_at": ass.created_at
        }

        response_serializer = AssessmentSerializer(output_data)
        payload = dict(response_serializer.data)
        payload.update({
            "tonnage_mt": result["tonnage_mt"],
            "seigniorage_amount": result["seigniorage_amount"],
            "rate_schedule_version": result["rate_schedule_version"],
            "is_official": True,
            "classification_source": "server",
        })
        return Response(payload, status=status.HTTP_201_CREATED)


class AssessmentDetailAPIView(APIView):
    # Phase 6B read-surface authentication. Assessment detail exposes rate, tonnage and payable amount.
    # IsAuthenticated, not IsSupervisor: any authenticated RTGS user may
    # read: only decisions are supervisor-gated.
    permission_classes = [IsAuthenticated]
    """
    API View to retrieve assessment details for a specific block_id.
    """
    def get(self, request, block_id):
        # Resolve block first
        block_doc = Block.objects(block_id=block_id).first()
        if not block_doc:
            return Response(
                {"error": f"Block with ID '{block_id}' not found."},
                status=status.HTTP_404_NOT_FOUND
            )
            
        ass = Assessment.objects(block=block_doc).first()
        if not ass:
            return Response(
                {"error": f"Assessment for block '{block_id}' not found."},
                status=status.HTTP_404_NOT_FOUND
            )
            
        data = {
            "id": str(ass.id),
            "block_id": block_id,
            "granite_category": ass.granite_category,
            "gangsaw_classification": ass.gangsaw_classification,
            "volume_m3": ass.volume_m3,
            "weight_mt": ass.weight_mt,
            "rate_per_mt": ass.rate_per_mt,
            "indicative_seigniorage": ass.indicative_seigniorage,
            "density_mt_per_m3": ass.density_mt_per_m3,
            "status": ass.status,
            "created_at": ass.created_at
        }
        serializer = AssessmentSerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)


from rest_framework.parsers import MultiPartParser, FormParser
from cv_pipeline.pipeline import GraniteCVPipeline
from .serializers import ImageUploadSerializer

class BlockMeasureCVAPIView(APIView):
    """
    POST: Uploads block image, runs CV pipeline, saves measurement results to Block.

    SECURITY - FIELD-APP AUTHENTICATION PENDING (deliberate PoC exception).
    This endpoint is intentionally left ANONYMOUS. It is called by the FROZEN
    iOS Stage 4 field app, which has no credential contract: it sends no token
    on any request. Gating it would brick the app in the field, so closing this
    requires a deliberate field-app networking change (a per-device or
    per-officer credential), which is tracked as the leading remaining
    limitation rather than applied here.

    The exposure is bounded: an anonymous submission can create a measurement,
    but it cannot be priced (POST /api/assessments/ requires authentication),
    read back (every sensitive GET requires authentication), or approved
    (approve/override require SUPERVISOR).
    """
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, block_id):
        # 1. Resolve block
        block = Block.objects(block_id=block_id).first()
        if not block:
            return Response(
                {"error": f"Block with ID '{block_id}' not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        # 2. Validate image in request
        serializer = ImageUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        uploaded_file = serializer.validated_data['image']

        # 3. Create paths in MEDIA_ROOT
        from django.conf import settings
        raw_dir = os.path.join(settings.MEDIA_ROOT, 'raw')
        annotated_dir = os.path.join(settings.MEDIA_ROOT, 'annotated')
        os.makedirs(raw_dir, exist_ok=True)
        os.makedirs(annotated_dir, exist_ok=True)

        # Generate standard filename
        timestamp_str = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        raw_filename = f"{block_id}_{timestamp_str}_raw.png"
        raw_filepath = os.path.join(raw_dir, raw_filename)

        # Save raw uploaded image
        with open(raw_filepath, 'wb+') as destination:
            for chunk in uploaded_file.chunks():
                destination.write(chunk)

        # Save raw image path relative to media root
        block.raw_image_path = os.path.relpath(raw_filepath, settings.MEDIA_ROOT).replace('\\', '/')

        # 4. Invoke the existing CV pipeline
        model_path = os.path.join(settings.BASE_DIR.parent, "yolov8n-seg.pt")
        try:
            pipeline = GraniteCVPipeline(model_path=model_path)
            # Run image processing
            result = pipeline.process_image(raw_filepath)
        except Exception as e:
            # Preserve failure state
            block.cv_status = 'failed'
            block.cv_error_message = f"Pipeline execution error: {e}"
            block.save()
            return Response(
                {"error": f"Failed to run CV pipeline: {e}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 5. Handle CV pipeline output
        if result.get('status') == 'success':
            # Save annotated image output
            annotated_filename = f"{block_id}_{timestamp_str}_annotated.png"
            annotated_filepath = os.path.join(annotated_dir, annotated_filename)
            cv2.imwrite(annotated_filepath, result['annotated_image'])
            block.annotated_image_path = os.path.relpath(annotated_filepath, settings.MEDIA_ROOT).replace('\\', '/')

            # Convert result into Measurement embedded document
            measurement_doc = Measurement(
                length_m=result['length_m'],
                breadth_m=result['breadth_m'],
                height_m=result['height_m'],
                volume_m3=result['volume_m3'],
                confidence=result.get('confidence', 0.95),
                measurement_method='cv',
                measured_at=datetime.datetime.utcnow()
            )
            block.measurement = measurement_doc
            block.status = 'measured'
            block.cv_status = 'success'
            block.cv_error_message = ""
        else:
            block.cv_status = 'failed'
            block.cv_error_message = result.get('error_message', 'Unknown CV pipeline error')
            
            # Still save the annotated image because it has error text overlay
            if result.get('annotated_image') is not None:
                annotated_filename = f"{block_id}_{timestamp_str}_annotated.png"
                annotated_filepath = os.path.join(annotated_dir, annotated_filename)
                cv2.imwrite(annotated_filepath, result['annotated_image'])
                block.annotated_image_path = os.path.relpath(annotated_filepath, settings.MEDIA_ROOT).replace('\\', '/')
            
        # Append to general image_paths
        if block.raw_image_path and block.raw_image_path not in block.image_paths:
            block.image_paths.append(block.raw_image_path)
        if block.annotated_image_path and block.annotated_image_path not in block.image_paths:
            block.image_paths.append(block.annotated_image_path)
            
        block.save()

        if block.cv_status == 'failed':
            return Response(
                {"error": block.cv_error_message},
                status=status.HTTP_400_BAD_REQUEST
            )
            
            # Build output representation
            data = {
                "id": str(block.id),
                "block_id": block.block_id,
                "quarry_id": str(block.quarry.id) if block.quarry else None,
                "image_paths": block.image_paths,
                "captured_at": block.captured_at,
                "gps_latitude": block.gps_latitude,
                "gps_longitude": block.gps_longitude,
                "status": block.status,
                "measurement": {
                    "length_m": block.measurement.length_m,
                    "breadth_m": block.measurement.breadth_m,
                    "height_m": block.measurement.height_m,
                    "volume_m3": block.measurement.volume_m3,
                    "confidence": block.measurement.confidence,
                    "measurement_method": block.measurement.measurement_method,
                    "measured_at": block.measurement.measured_at,
                },
                "cv_status": block.cv_status,
                "cv_error_message": block.cv_error_message,
                "raw_image_path": block.raw_image_path,
                "annotated_image_path": block.annotated_image_path,
                "created_at": block.created_at,
                "updated_at": block.updated_at,
            }
            response_serializer = BlockSerializer(data)
            return Response(response_serializer.data, status=status.HTTP_200_OK)
        else:
            # Preserve failed state
            block.cv_status = 'failed'
            block.cv_error_message = result.get('error_message', 'Unknown CV pipeline error.')
            block.save()
            return Response(
                {"error": block.cv_error_message},
                status=status.HTTP_400_BAD_REQUEST
            )


from django.http import HttpResponse
from .pdf_generator import generate_block_pdf
from .models import AuditLog


class BlockARMeasureAPIView(APIView):
    """
    POST: Creates (or retrieves) a block, uploads the field officer raw image,
    saves AR-derived measurements, and returns the completed block record.
    This is the single endpoint for the New Block Inspection AR workflow.

    Required form-data fields:
      - image           : the captured photo (multipart)
      - block_id        : unique block identifier
      - quarry_id       : quarry reference (e.g. 'Q-9982')
      - length_m        : AR-measured length in metres
      - breadth_m       : AR-measured breadth in metres
      - height_m        : AR-measured height in metres
      - volume_m3       : AR-calculated volume in m³
    Optional:
      - gps_latitude, gps_longitude, officer_id

    SECURITY - FIELD-APP AUTHENTICATION PENDING (deliberate PoC exception).
    This endpoint is intentionally left ANONYMOUS. It is called by the FROZEN
    iOS Stage 4 field app, which has no credential contract: it sends no token
    on any request. Gating it would brick the app in the field, so closing this
    requires a deliberate field-app networking change (a per-device or
    per-officer credential), which is tracked as the leading remaining
    limitation rather than applied here.

    The exposure is bounded: an anonymous submission can create a measurement,
    but it cannot be priced (POST /api/assessments/ requires authentication),
    read back (every sensitive GET requires authentication), or approved
    (approve/override require SUPERVISOR).
    """
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        import time
        from django.conf import settings

        from . import ar_ingest

        t_start = time.time()
        logger.info(f"[PERF] Server received request at {datetime.datetime.utcnow().isoformat()}")

        # --- Validate everything BEFORE touching the database or the filesystem ---
        try:
            block_id = ar_ingest.clean_block_id(request.data.get('block_id'))
            length_m = ar_ingest.clean_dimension(request.data.get('length_m'), 'length_m')
            breadth_m = ar_ingest.clean_dimension(request.data.get('breadth_m'), 'breadth_m')
            height_m = ar_ingest.clean_dimension(request.data.get('height_m'), 'height_m')
            client_volume = ar_ingest.clean_optional_client_volume(request.data.get('volume_m3'))
            latitude = ar_ingest.clean_coordinate(request.data.get('gps_latitude'), 'gps_latitude', 90)
            longitude = ar_ingest.clean_coordinate(request.data.get('gps_longitude'), 'gps_longitude', 180)
            uploaded_file = request.FILES.get('image')
            image_format = ar_ingest.validate_image(uploaded_file)
        except ar_ingest.ARIngestError as exc:
            return Response(
                {'error': exc.message, 'code': exc.code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        quarry_id = (request.data.get('quarry_id') or '').strip()
        officer_id = (request.data.get('officer_id') or '').strip()

        # Server-side volume is authoritative. The client's figure is compared
        # and reported, never stored - it arrives from the phone and is what
        # the seigniorage amount is ultimately derived from.
        server_volume = length_m * breadth_m * height_m
        volume_check = ar_ingest.compare_volume(server_volume, client_volume)

        # --- Duplicate protection -------------------------------------------
        # A block that already carries a measurement is never re-measured
        # through this endpoint: that silently destroyed field data (and left
        # approved_by/approved_at pointing at an approval of different numbers).
        # A block registered but not yet measured is the legitimate
        # register-then-measure flow and is allowed to proceed.
        block = Block.objects(block_id=block_id).first()
        if block is not None and block.measurement is not None:
            return Response(
                {
                    'error': (
                        f"Block '{block_id}' already has a measurement recorded "
                        f"and was not modified. Use a different block_id, or have a "
                        f"supervisor override the existing measurement."
                    ),
                    'code': 'block_already_measured',
                    'block_id': block_id,
                    'existing_measured_at': block.measurement.measured_at,
                    'existing_status': block.status,
                    'existing_approval_status': block.approval_status,
                },
                status=status.HTTP_409_CONFLICT,
            )

        is_new_block = block is None
        if is_new_block:
            block = Block(block_id=block_id, status='pending')

        # --- Reference resolution -------------------------------------------
        # An unresolved reference does not reject the submission: the measurement
        # itself is real field data and must not be lost because master data has
        # not been registered yet. The submitted identifier is preserved verbatim
        # and flagged, so it can be reconciled later. Nothing is invented.
        warnings = []
        if quarry_id:
            quarry_doc = Quarry.objects(id=quarry_id).first()
            if quarry_doc is not None:
                block.quarry = quarry_doc
            else:
                warnings.append('quarry_unresolved')
            block.submitted_quarry_id = quarry_id
        else:
            warnings.append('quarry_missing')

        if officer_id:
            block.inspecting_officer_id = officer_id
            if Officer.objects(officer_id=officer_id).first() is None:
                warnings.append('officer_unresolved')
        else:
            warnings.append('officer_missing')

        if not volume_check['agrees'] and volume_check['agrees'] is not None:
            warnings.append('client_volume_mismatch')

        block.reference_warnings = warnings

        # --- GPS: 0.0 is a real coordinate, so test against None, not truthiness ---
        if latitude is not None:
            block.gps_latitude = latitude
        if longitude is not None:
            block.gps_longitude = longitude
        if latitude is None or longitude is None:
            warnings.append('gps_incomplete')
            block.reference_warnings = warnings

        # --- Save raw image ---------------------------------------------------
        raw_dir = os.path.join(settings.MEDIA_ROOT, 'raw')
        os.makedirs(raw_dir, exist_ok=True)

        timestamp_str = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')
        extension = 'png' if image_format == 'png' else 'jpg'
        raw_filename = f"{block_id}_{timestamp_str}_ar_raw.{extension}"
        raw_filepath = os.path.join(raw_dir, raw_filename)

        # block_id passed clean_block_id, so it cannot contain a path separator;
        # this assertion makes that guarantee explicit at the point it matters.
        if os.path.dirname(os.path.relpath(raw_filepath, raw_dir)):
            return Response(
                {'error': 'Resolved image path escaped the media directory.', 'code': 'unsafe_path'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with open(raw_filepath, 'wb+') as dst:
            for chunk in uploaded_file.chunks():
                dst.write(chunk)

        block.raw_image_path = os.path.relpath(raw_filepath, settings.MEDIA_ROOT).replace('\\', '/')

        # NOTE: the iOS client does not send a device capture timestamp, so this
        # remains server receipt time. Documented rather than fabricated.
        block.captured_at = datetime.datetime.utcnow()

        if not block.image_paths:
            block.image_paths = []
        if block.raw_image_path not in block.image_paths:
            block.image_paths.append(block.raw_image_path)

        # --- Measurement ------------------------------------------------------
        # confidence is left at the model default: the AR module reports no
        # confidence value, and inventing one would be a fabricated AI metric.
        measurement_doc = Measurement(
            length_m=length_m,
            breadth_m=breadth_m,
            height_m=height_m,
            volume_m3=server_volume,
            measurement_method='ar',
            measured_at=datetime.datetime.utcnow(),
        )
        block.measurement = measurement_doc
        block.status = 'measured'
        # No CV inference ran on this path. Claiming 'success' asserted a YOLO
        # result that never happened; 'not_applicable' is the honest value and
        # no backend logic branches on it (only display and an optional filter).
        block.cv_status = 'not_applicable'
        block.save()
        _invalidate_cache()

        # --- Audit ------------------------------------------------------------
        # Written only after the block is persisted, so a failed validation can
        # never leave an audit entry claiming a measurement was recorded.
        audit_details = (
            f"AR inspection: L={length_m}m, B={breadth_m}m, H={height_m}m, "
            f"V(server)={server_volume:.6f}m3, V(client)={client_volume}, "
            f"volume_agrees={volume_check['agrees']}, "
            f"submitted_quarry_id={quarry_id or 'none'}, "
            f"submitted_officer_id={officer_id or 'none'}, "
            f"gps={'yes' if (latitude is not None and longitude is not None) else 'incomplete'}, "
            f"reference_warnings={','.join(warnings) if warnings else 'none'}"
        )
        audit_entry = AuditLog(
            block=block,
            block_id_snapshot=block.block_id,
            action='ar_measurement_submitted',
            actor=officer_id or 'FieldOfficer',
            details=audit_details,
            timestamp=datetime.datetime.utcnow(),
        )
        audit_entry.save()

        logger.info(f"[PERF] Response sent: Total server time {int((time.time() - t_start)*1000)} ms")

        output = {
            'id': str(block.id),
            'block_id': block.block_id,
            'quarry_id': str(block.quarry.id) if block.quarry else quarry_id,
            'image_paths': block.image_paths,
            'captured_at': block.captured_at,
            'gps_latitude': block.gps_latitude,
            'gps_longitude': block.gps_longitude,
            'status': block.status,
            'measurement': {
                'length_m': block.measurement.length_m,
                'breadth_m': block.measurement.breadth_m,
                'height_m': block.measurement.height_m,
                'volume_m3': block.measurement.volume_m3,
                'confidence': block.measurement.confidence,
                'measurement_method': block.measurement.measurement_method,
                'measured_at': block.measurement.measured_at,
            },
            'cv_status': block.cv_status,
            'raw_image_path': block.raw_image_path,
            'annotated_image_path': block.annotated_image_path,
            'created_at': block.created_at,
            'updated_at': block.updated_at,
        }
        serializer = BlockSerializer(output)
        # Existing contract first, then the PoC additions. Merging afterwards
        # avoids BlockSerializer's read-only SkipField behaviour dropping them.
        payload = dict(serializer.data)
        payload.update({
            'submitted_quarry_id': block.submitted_quarry_id,
            'inspecting_officer_id': block.inspecting_officer_id,
            'reference_warnings': list(block.reference_warnings or []),
            'volume_check': volume_check,
            'approval_status': block.approval_status,
            'created_new_block': is_new_block,
            # Real, persisted reference the officer can quote. This is the
            # AuditLog document id - not a display-only random string - so it
            # resolves to an actual governance record.
            'receipt_id': str(audit_entry.id),
            'audit_action': audit_entry.action,
            'received_at': audit_entry.timestamp,
        })
        return Response(payload, status=status.HTTP_201_CREATED)

class BlockOverrideAPIView(APIView):
    # Governance action: changes stored measurement data. Supervisor only.
    permission_classes = [IsSupervisor]
    """
    POST: Supervise override for block measurements.
    """
    def post(self, request, block_id):
        block = Block.objects(block_id=block_id).first()
        if not block:
            return Response(
                {"error": f"Block with ID '{block_id}' not found."},
                status=status.HTTP_404_NOT_FOUND
            )
            
        length_m = request.data.get('length_m')
        breadth_m = request.data.get('breadth_m')
        height_m = request.data.get('height_m')
        reason = request.data.get('reason', '')
        # Authenticated identity ONLY. A client-supplied 'actor' is ignored:
        # previously any anonymous caller could override a measurement and sign
        # it with any name, and the dashboard hardcoded 'Supervisor-1'.
        actor = request.user.get_username()
        
        if length_m is None or breadth_m is None or height_m is None:
            return Response(
                {"error": "length_m, breadth_m, and height_m are required fields."},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        try:
            length_m = float(length_m)
            breadth_m = float(breadth_m)
            height_m = float(height_m)
        except ValueError:
            return Response(
                {"error": "Dimensions must be valid floats."},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # Prior dimensions, read before the measurement is replaced, so the
        # audit entry can state what actually changed.
        prior = block.measurement
        prior_dims = (
            f"{prior.length_m}/{prior.breadth_m}/{prior.height_m}" if prior else "none"
        )

        # Snapshot the original measurement if not already saved
        if not block.original_measurement and block.measurement:
            block.original_measurement = block.measurement
            
        # Update current measurement
        block.measurement = Measurement(
            length_m=length_m,
            breadth_m=breadth_m,
            height_m=height_m,
            volume_m3=length_m * breadth_m * height_m,
            confidence=1.0,
            measurement_method='manual',
            measured_at=datetime.datetime.utcnow()
        )
        
        block.is_overridden = True
        block.override_reason = reason
        block.status = 'measured'
        block.save()
        _invalidate_cache()
        
        # Log to AuditLog
        log_details = (
            f"Manual override: block_id={block.block_id}, "
            f"old L/B/H={prior_dims}, "
            f"new L/B/H={length_m}/{breadth_m}/{height_m}, "
            f"actor={actor} (authenticated), reason={reason}"
        )
        audit = AuditLog(
            block=block,
            block_id_snapshot=block.block_id,
            action="manual_override",
            actor=actor,
            details=log_details,
            timestamp=datetime.datetime.utcnow()
        )
        audit.save()
        
        # Build clean output representation
        data = {
            "id": str(block.id),
            "block_id": block.block_id,
            "quarry_id": str(block.quarry.id) if block.quarry else None,
            "image_paths": block.image_paths,
            "captured_at": block.captured_at,
            "gps_latitude": block.gps_latitude,
            "gps_longitude": block.gps_longitude,
            "status": block.status,
            "measurement": {
                "length_m": block.measurement.length_m,
                "breadth_m": block.measurement.breadth_m,
                "height_m": block.measurement.height_m,
                "volume_m3": block.measurement.volume_m3,
                "confidence": block.measurement.confidence,
                "measurement_method": block.measurement.measurement_method,
                "measured_at": block.measurement.measured_at,
            },
            "cv_status": block.cv_status,
            "cv_error_message": block.cv_error_message,
            "raw_image_path": block.raw_image_path,
            "annotated_image_path": block.annotated_image_path,
            "is_overridden": block.is_overridden,
            "original_measurement": {
                "length_m": block.original_measurement.length_m,
                "breadth_m": block.original_measurement.breadth_m,
                "height_m": block.original_measurement.height_m,
                "volume_m3": block.original_measurement.volume_m3,
                "confidence": block.original_measurement.confidence,
                "measurement_method": block.original_measurement.measurement_method,
                "measured_at": block.original_measurement.measured_at,
            } if block.original_measurement else None,
            "override_reason": block.override_reason,
            "approval_status": block.approval_status,
            "approved_by": block.approved_by,
            "approved_at": block.approved_at,
            "created_at": block.created_at,
            "updated_at": block.updated_at,
        }
        
        serializer = BlockSerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)


class BlockApproveAPIView(APIView):
    # Governance action: approves/rejects a block. Supervisor only.
    permission_classes = [IsSupervisor]
    """
    POST: Approve or reject block inspection values.
    """
    def post(self, request, block_id):
        block = Block.objects(block_id=block_id).first()
        if not block:
            return Response(
                {"error": f"Block with ID '{block_id}' not found."},
                status=status.HTTP_404_NOT_FOUND
            )
            
        approval_status_val = request.data.get('approval_status')
        # Authenticated identity ONLY - see BlockOverrideAPIView.
        actor = request.user.get_username()
        
        if approval_status_val not in ['approved', 'rejected']:
            return Response(
                {"error": "approval_status must be 'approved' or 'rejected'."},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        previous_status = block.approval_status

        # --- Idempotency (Phase 6G) -------------------------------------------
        # Re-sending a decision the block already carries is a no-op, not a new
        # governance event. block5 in the real database shows why: it was
        # approved three times and the audit trail now claims three separate
        # approval decisions for one act.
        #
        # Checked BEFORE the assessment prerequisite below, deliberately: a
        # record approved under the older rules must not start returning an
        # error when someone merely re-opens it.
        if previous_status == approval_status_val:
            return Response(
                {
                    "code": f"already_{approval_status_val}",
                    "detail": (
                        f"Block '{block.block_id}' is already {approval_status_val}. "
                        f"No new decision was recorded."
                    ),
                    "block_id": block.block_id,
                    "approval_status": block.approval_status,
                    "approved_by": block.approved_by,
                    "approved_at": block.approved_at,
                },
                status=status.HTTP_200_OK,
            )

        # --- Assessment prerequisite (Phase 6G) --------------------------------
        # A block may not become APPROVED until its seigniorage has been
        # determined: approving first would endorse a record whose payable
        # amount nobody has calculated. The correct order is
        # measurement -> assessment -> supervisor review -> approval.
        #
        # REJECTION is deliberately NOT gated. Refusing a measurement you can
        # see is wrong should not first require pricing it, and forcing an
        # assessment before a rejection would create a financial record for a
        # block that is being thrown out.
        #
        # Nothing is auto-created here: no assessment, no rate, no amount. The
        # request is refused and the supervisor runs the assessment themselves.
        if approval_status_val == 'approved':
            from .models import Assessment as _Assessment
            if _Assessment.objects(block=block).first() is None:
                return Response(
                    {
                        "error": (
                            f"Block '{block.block_id}' must have an assessment before it "
                            f"can be approved. Create one via POST /api/assessments/ first."
                        ),
                        "code": "assessment_required_before_approval",
                        "block_id": block.block_id,
                        "approval_status": block.approval_status,
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        block.approval_status = approval_status_val
        block.approved_by = actor
        block.approved_at = datetime.datetime.utcnow()
        block.save()
        _invalidate_cache()
        
        # Log to AuditLog
        audit = AuditLog(
            block=block,
            block_id_snapshot=block.block_id,
            action=f"block_approval_{approval_status_val}",
            actor=actor,
            details=(
                f"Approval decision: block_id={block.block_id}, "
                f"action={approval_status_val}, "
                f"old_status={previous_status}, new_status={approval_status_val}, "
                f"actor={actor} (authenticated), "
                f"reason={request.data.get('reason', 'none')}"
            ),
            timestamp=datetime.datetime.utcnow()
        )
        audit.save()
        
        # Build clean output representation
        data = {
            "id": str(block.id),
            "block_id": block.block_id,
            "quarry_id": str(block.quarry.id) if block.quarry else None,
            "image_paths": block.image_paths,
            "captured_at": block.captured_at,
            "gps_latitude": block.gps_latitude,
            "gps_longitude": block.gps_longitude,
            "status": block.status,
            "measurement": {
                "length_m": block.measurement.length_m,
                "breadth_m": block.measurement.breadth_m,
                "height_m": block.measurement.height_m,
                "volume_m3": block.measurement.volume_m3,
                "confidence": block.measurement.confidence,
                "measurement_method": block.measurement.measurement_method,
                "measured_at": block.measurement.measured_at,
            } if block.measurement else None,
            "cv_status": block.cv_status,
            "cv_error_message": block.cv_error_message,
            "raw_image_path": block.raw_image_path,
            "annotated_image_path": block.annotated_image_path,
            "is_overridden": block.is_overridden,
            "original_measurement": {
                "length_m": block.original_measurement.length_m,
                "breadth_m": block.original_measurement.breadth_m,
                "height_m": block.original_measurement.height_m,
                "volume_m3": block.original_measurement.volume_m3,
                "confidence": block.original_measurement.confidence,
                "measurement_method": block.original_measurement.measurement_method,
                "measured_at": block.original_measurement.measured_at,
            } if block.original_measurement else None,
            "override_reason": block.override_reason,
            "approval_status": block.approval_status,
            "approved_by": block.approved_by,
            "approved_at": block.approved_at,
            "created_at": block.created_at,
            "updated_at": block.updated_at,
        }
        
        serializer = BlockSerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)


class BlockPDFAPIView(APIView):
    # Phase 6B read-surface authentication. The report carries the complete assessment and audit record.
    # IsAuthenticated, not IsSupervisor: any authenticated RTGS user may
    # read: only decisions are supervisor-gated.
    permission_classes = [IsAuthenticated]
    """
    GET: Retrieve and download PDF report.
    """
    def get(self, request, block_id):
        block = Block.objects(block_id=block_id).first()
        if not block:
            return Response(
                {"error": f"Block with ID '{block_id}' not found."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get assessment if exists
        from .models import Assessment
        assessment = Assessment.objects(block=block).first()
        
        # POLICY CHANGE: every registered block is exportable.
        #
        # This used to return 409 assessment_required when the block had not been
        # priced, so a measured-but-unassessed block could not be exported at
        # all. The generator now produces a clearly-marked MEASUREMENT RECORD
        # instead - every financial field reads "Not assessed", the title and
        # disclaimer say so, and the report number is suffixed -M. Nothing is
        # fabricated; the document simply reports what has and has not been
        # determined.
        try:
            pdf_bytes = generate_block_pdf(block, assessment)
        except ValueError as e:
            return Response(
                {"error": str(e), "code": "report_preconditions_unmet"},
                status=status.HTTP_409_CONFLICT
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to generate PDF: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
            
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        # The filename says which document this is, so a folder of exports can be
        # told apart at a glance: an unassessed block yields a measurement
        # record, not an assessment report. Carries no session or secret data.
        filename = (f"inspection_report_{block_id}.pdf" if assessment is not None
                    else f"measurement_record_{block_id}.pdf")
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response


class OMEPSExportAPIView(APIView):
    # Phase 6B read-surface authentication. The export carries block, money, approval and audit in one document.
    # IsAuthenticated, not IsSupervisor: any authenticated RTGS user may
    # read: only decisions are supervisor-gated.
    permission_classes = [IsAuthenticated]
    """
    GET /api/export/omeps/<block_id>/

    OMEPS-ready export / integration contract - PENDING OFFICIAL OMEPS SCHEMA.

    Packages the complete RTGS workflow for one block (block -> AR measurement ->
    assessment -> approval -> audit) as a versioned JSON document an OMEPS
    adapter can map onto the real specification once that specification exists.
    This is an integration-readiness layer: it does not claim OMEPS integration,
    does not define official OMEPS fields, and transmits nothing externally.

    Read-only. No document is written, no audit entry is created, and the
    response cache is not touched - repeated calls leave the database identical.

    Contract details, source-of-truth rules and the adapter guidance live in
    blocks/omeps_export.py and blocks/OMEPS_EXPORT_CONTRACT.md.
    """

    def get(self, request, block_id):
        from . import omeps_export
        from .models import Assessment

        block = Block.objects(block_id=block_id).first()
        if not block:
            return Response(
                {
                    "error": f"Block with ID '{block_id}' not found.",
                    "code": "block_not_found",
                    "integration_status": omeps_export.INTEGRATION_STATUS,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        assessment = Assessment.objects(block=block).first()

        try:
            document = omeps_export.build_export(block, assessment)
        except omeps_export.ExportPreconditionError as e:
            # A missing section is a clear integration error, never an export
            # with fabricated or empty-but-present values.
            return Response(
                {
                    "error": e.message,
                    "code": e.code,
                    "integration_status": omeps_export.INTEGRATION_STATUS,
                    "export_schema_version": omeps_export.EXPORT_SCHEMA_VERSION,
                },
                status=e.status_code,
            )

        return Response(document, status=status.HTTP_200_OK)


class BlockAuditLogsAPIView(APIView):
    # Phase 6B read-surface authentication. The audit trail is governance data.
    # IsAuthenticated, not IsSupervisor: any authenticated RTGS user may
    # read: only decisions are supervisor-gated.
    permission_classes = [IsAuthenticated]
    """
    GET: List all audit logs for a block.
    """
    def get(self, request, block_id):
        block = Block.objects(block_id=block_id).first()
        if not block:
            return Response(
                {"error": f"Block with ID '{block_id}' not found."},
                status=status.HTTP_404_NOT_FOUND
            )
            
        logs = AuditLog.objects(block=block).order_by('-timestamp')
        log_data = []
        for log in logs:
            log_data.append({
                "action": log.action,
                "actor": log.actor,
                "details": log.details,
                "timestamp": log.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            })
        return Response(log_data, status=status.HTTP_200_OK)


from .models import Officer, WeeklyOfficerSummary, Assessment, AuditLog
from mongoengine.queryset.visitor import Q

class OfficerAnalyticsAPIView(APIView):
    # Phase 6B read-surface authentication. Officer performance is governance data.
    # IsAuthenticated, not IsSupervisor: any authenticated RTGS user may
    # read: only decisions are supervisor-gated.
    permission_classes = [IsAuthenticated]
    """
    GET: Returns summaries for all officers.
    """
    def get(self, request):
        officers = _get_all_officers()
        all_blocks = _get_all_blocks()
        
        # Build lookup dictionary in memory: officer_id -> list of blocks
        blocks_by_officer = {}
        for b in all_blocks:
            oid = b.inspecting_officer_id
            if oid:
                blocks_by_officer.setdefault(oid, []).append(b)
                
        now = datetime.datetime.utcnow()
        week_ago = now - datetime.timedelta(days=7)
        
        data = []
        for o in officers:
            blocks = blocks_by_officer.get(o.officer_id, [])
            total_blocks = len(blocks)
            
            blocks_this_week = sum(1 for b in blocks if b.captured_at and b.captured_at >= week_ago)
            overrides = sum(1 for b in blocks if b.is_overridden)
            approved = sum(1 for b in blocks if b.approval_status == 'approved')
            
            avg_confidence = 0.0
            avg_duration = 0.0
            if total_blocks > 0:
                avg_confidence = float(sum(b.measurement.confidence for b in blocks if b.measurement) / total_blocks)
                avg_duration = float(sum(b.inspection_duration_seconds for b in blocks if b.inspection_duration_seconds) / total_blocks)
            
            override_rate = float(overrides / total_blocks) if total_blocks > 0 else 0.0
            approval_rate = float(approved / total_blocks) if total_blocks > 0 else 0.0

            data.append({
                "officer_id": o.officer_id,
                "name": o.name,
                "designation": o.designation or "Mining Inspector",
                "assigned_quarries": [q.name for q in o.assigned_quarries if q],
                "blocks_this_week": blocks_this_week,
                "total_blocks": total_blocks,
                "avg_confidence": round(avg_confidence, 2),
                "override_count": overrides,
                "override_rate": round(override_rate, 2),
                "approval_rate": round(approval_rate, 2),
                "avg_inspection_duration": round(avg_duration, 1),
                "phone": o.phone,
                "email": o.email,
                "active_status": o.active_status
            })
        return Response(data, status=status.HTTP_200_OK)


class OfficerWeeklyAnalyticsAPIView(APIView):
    # Phase 6B read-surface authentication. Officer performance is governance data.
    # IsAuthenticated, not IsSupervisor: any authenticated RTGS user may
    # read: only decisions are supervisor-gated.
    permission_classes = [IsAuthenticated]
    """
    GET: Returns 6 weeks historical summary trends for a specific officer.
    """
    def get(self, request, officer_id):
        # Resolve summaries from WeeklyOfficerSummary collection
        summaries = WeeklyOfficerSummary.objects(officer_id=officer_id).order_by('week_start_date')
        
        # Fallback to empty series if none exist
        data = []
        for s in summaries:
            data.append({
                "week": s.week_start_date.strftime('%b %d'),
                "blocks_inspected": s.blocks_inspected,
                "avg_confidence": s.avg_confidence,
                "override_count": s.override_count,
                "override_rate": s.override_count / s.blocks_inspected if s.blocks_inspected > 0 else 0.0,
                "approval_rate": s.approval_rate,
                "avg_inspection_duration": s.avg_inspection_duration_seconds
            })
            
        # Ensure at least some data structure even if empty
        if not data:
            now = datetime.datetime.utcnow()
            for w in range(5, -1, -1):
                week_start = now - datetime.timedelta(weeks=w)
                data.append({
                    "week": week_start.strftime('%b %d'),
                    "blocks_inspected": 0,
                    "avg_confidence": 0.0,
                    "override_count": 0,
                    "override_rate": 0.0,
                    "approval_rate": 0.0,
                    "avg_inspection_duration": 0.0
                })
        return Response(data, status=status.HTTP_200_OK)


class QuarryComparisonAPIView(APIView):
    # Phase 6B read-surface authentication. Discloses per-quarry revenue.
    # IsAuthenticated, not IsSupervisor: any authenticated RTGS user may
    # read: only decisions are supervisor-gated.
    permission_classes = [IsAuthenticated]
    """
    GET: Returns a summary for all quarries to compare performance metrics.
    """
    def get(self, request):
        quarries = _get_all_quarries()
        all_blocks = _get_all_blocks()
        all_assessments = _get_all_assessments()

        # Map blocks by quarry ID
        blocks_by_quarry = {}
        for b in all_blocks:
            if b.quarry:
                qid = b.quarry.id
                blocks_by_quarry.setdefault(qid, []).append(b)

        # Map block ID to assessment
        assessment_by_block_id = {}
        for ass in all_assessments:
            if ass.block:
                assessment_by_block_id[ass.block.id] = ass

        data = []
        for q in quarries:
            blocks = blocks_by_quarry.get(q.id, [])
            total_blocks = len(blocks)
            
            overrides = sum(1 for b in blocks if b.is_overridden)
            approved = sum(1 for b in blocks if b.approval_status == 'approved')
            
            avg_confidence = 0.0
            total_volume = 0.0
            avg_volume = 0.0
            
            if total_blocks > 0:
                avg_confidence = float(sum(b.measurement.confidence for b in blocks if b.measurement) / total_blocks)
                total_volume = float(sum(b.measurement.volume_m3 for b in blocks if b.measurement))
                avg_volume = total_volume / total_blocks

            # Aggregate seigniorage from Assessments referencing these blocks
            total_revenue = sum(
                assessment_by_block_id[b.id].indicative_seigniorage
                for b in blocks
                if b.id in assessment_by_block_id
            )

            override_rate = float(overrides / total_blocks) if total_blocks > 0 else 0.0
            approval_rate = float(approved / total_blocks) if total_blocks > 0 else 0.0

            data.append({
                "quarry_id": str(q.id),
                "name": q.name,
                "district": q.district or "Unknown",
                "lessee": q.lessee_name or "Standard Lessee",
                "total_blocks": total_blocks,
                "total_volume": round(total_volume, 4),
                "avg_block_volume": round(avg_volume, 4),
                "total_revenue": round(total_revenue, 2),
                "override_rate": round(override_rate, 2),
                "approval_rate": round(approval_rate, 2),
                "avg_confidence": round(avg_confidence, 2)
            })
        return Response(data, status=status.HTTP_200_OK)


class QuarryTrendAPIView(APIView):
    # Phase 6B read-surface authentication. Discloses per-quarry revenue over time.
    # IsAuthenticated, not IsSupervisor: any authenticated RTGS user may
    # read: only decisions are supervisor-gated.
    permission_classes = [IsAuthenticated]
    """
    GET: Returns a 6-week trend of block counts, volume, and revenue for a specific quarry.
    """
    def get(self, request, quarry_id):
        all_quarries = _get_all_quarries()
        q = next((qu for qu in all_quarries if str(qu.id) == quarry_id or qu.name == quarry_id), None)
        if not q:
            return Response({"error": "Quarry not found"}, status=status.HTTP_404_NOT_FOUND)

        all_blocks = _get_all_blocks()
        blocks = [b for b in all_blocks if b.quarry and b.quarry.id == q.id]
        
        all_assessments = _get_all_assessments()
        block_id_set = {b.id for b in blocks}
        assessments = [ass for ass in all_assessments if ass.block and ass.block.id in block_id_set]
        
        # Build assessment lookup map
        assessment_by_block_id = {ass.block.id: ass for ass in assessments if ass.block}

        now = datetime.datetime.utcnow()
        data = []

        for w in range(5, -1, -1):
            week_start = now - datetime.timedelta(weeks=w)
            week_start = week_start - datetime.timedelta(days=week_start.weekday())
            week_start = datetime.datetime(week_start.year, week_start.month, week_start.day, 0, 0, 0)
            week_end = week_start + datetime.timedelta(days=7)

            week_blocks = [b for b in blocks if b.captured_at and week_start <= b.captured_at < week_end]
            block_count = len(week_blocks)
            
            total_volume = sum(b.measurement.volume_m3 for b in week_blocks if b.measurement)
            avg_confidence = sum(b.measurement.confidence for b in week_blocks if b.measurement) / block_count if block_count > 0 else 0.0
            overrides = sum(1 for b in week_blocks if b.is_overridden)
            
            indicative_revenue = sum(
                assessment_by_block_id[b.id].indicative_seigniorage
                for b in week_blocks
                if b.id in assessment_by_block_id
            )

            data.append({
                "week": week_start.strftime('%b %d'),
                "block_count": block_count,
                "total_volume": round(total_volume, 4),
                "indicative_revenue": round(indicative_revenue, 2),
                "avg_confidence": round(avg_confidence, 2),
                "override_rate": overrides / block_count if block_count > 0 else 0.0
            })
        return Response(data, status=status.HTTP_200_OK)


class RevenueSummaryAPIView(APIView):
    # Phase 6B read-surface authentication. Discloses aggregate revenue.
    # IsAuthenticated, not IsSupervisor: any authenticated RTGS user may
    # read: only decisions are supervisor-gated.
    permission_classes = [IsAuthenticated]
    """
    GET: Returns total seigniorage, trends, and category summaries across the state.
    """
    def get(self, request):
        assessments = _get_all_assessments()
        total_seigniorage = sum(ass.indicative_seigniorage for ass in assessments)
        total_volume = sum(ass.volume_m3 for ass in assessments)
        block_count = len(assessments)

        # Category-wise breakdown
        categories = {}
        for ass in assessments:
            cat = ass.granite_category or "Standard"
            categories[cat] = categories.get(cat, 0.0) + ass.indicative_seigniorage

        category_revenue = [{"category": k, "revenue": round(v, 2)} for k, v in categories.items()]

        # Gangsaw vs below-gangsaw revenue
        # Official RTGS classification (blocks/seigniorage.py). The previous
        # comparison matched the retired POC literal "Gangsaw Size", so every
        # officially-assessed block fell into the below-gangsaw bucket.
        from .seigniorage import ABOVE_GANGSAW, WITHIN_GANGSAW
        gangsaw_revenue = sum(
            ass.indicative_seigniorage for ass in assessments
            if ass.gangsaw_classification == ABOVE_GANGSAW
        )
        below_gangsaw_revenue = sum(
            ass.indicative_seigniorage for ass in assessments
            if ass.gangsaw_classification == WITHIN_GANGSAW
        )

        # 6-week weekly revenue trend
        weekly_revenue = []
        now = datetime.datetime.utcnow()
        for w in range(5, -1, -1):
            week_start = now - datetime.timedelta(weeks=w)
            week_start = week_start - datetime.timedelta(days=week_start.weekday())
            week_start = datetime.datetime(week_start.year, week_start.month, week_start.day, 0, 0, 0)
            week_end = week_start + datetime.timedelta(days=7)

            week_ass = [ass for ass in assessments if ass.created_at and week_start <= ass.created_at < week_end]
            rev = sum(ass.indicative_seigniorage for ass in week_ass)
            vol = sum(ass.volume_m3 for ass in week_ass)

            weekly_revenue.append({
                "week": week_start.strftime('%b %d'),
                "revenue": round(rev, 2),
                "volume": round(vol, 4)
            })

        return Response({
            "total_seigniorage": round(total_seigniorage, 2),
            "total_volume": round(total_volume, 4),
            "block_count": block_count,
            "weekly_revenue": weekly_revenue,
            "category_revenue": category_revenue,
            "gangsaw_revenue": round(gangsaw_revenue, 2),
            "below_gangsaw_revenue": round(below_gangsaw_revenue, 2),
            "disclaimer": "POC ONLY: Calculation values are mock simulations for demonstration purposes."
        }, status=status.HTTP_200_OK)


class RevenueLeakageAPIView(APIView):
    # Phase 6B read-surface authentication. Discloses revenue-variance findings.
    # IsAuthenticated, not IsSupervisor: any authenticated RTGS user may
    # read: only decisions are supervisor-gated.
    permission_classes = [IsAuthenticated]
    """
    GET: Estimations for revenue leakage and recovery rates.
    """
    def get(self, request):
        """Leakage requires a real weighbridge figure to compare against.

        Assessment.weighbridge_weight_mt is never written by any code path, and
        variance_pct was only ever populated by random.uniform() in
        generate_mock_data. This endpoint previously multiplied that random
        number by the seigniorage amount and returned it as "revenue recovered"
        - a fabricated money figure presented as a finding, with alert text
        attributing it to "weighbridge returns" that do not exist.

        It now reports honestly that the data is unavailable. It will produce
        real numbers once weighbridge reconciliation is implemented.
        """
        all_assessments = _get_cached('all_assessments', lambda: list(Assessment.objects.all()))
        with_weighbridge = [
            a for a in all_assessments if a.weighbridge_weight_mt is not None
        ]

        if not with_weighbridge:
            return Response({
                "available": False,
                "reason": "no_weighbridge_data",
                "message": (
                    "Not available - no weighbridge data. Leakage requires a recorded "
                    "weighbridge weight to compare against the assessed tonnage."
                ),
                "assessments_total": len(all_assessments),
                "assessments_with_weighbridge": 0,
                "leakage_blocks_count": None,
                "estimated_revenue_recovered": None,
                "average_leakage_pct": None,
            }, status=status.HTTP_200_OK)

        leakage_list = [a for a in with_weighbridge if a.variance_pct and a.variance_pct > 8.0]
        recovered = sum(a.indicative_seigniorage * (a.variance_pct / 100.0) for a in leakage_list)
        variance_sum = sum(a.variance_pct for a in with_weighbridge if a.variance_pct)
        return Response({
            "available": True,
            "assessments_total": len(all_assessments),
            "assessments_with_weighbridge": len(with_weighbridge),
            "leakage_blocks_count": len(leakage_list),
            "estimated_revenue_recovered": round(recovered, 2),
            "average_leakage_pct": round(variance_sum / len(with_weighbridge), 2) if with_weighbridge else 0,
        }, status=status.HTTP_200_OK)


class AuditReadinessAPIView(APIView):
    # Phase 6B read-surface authentication. Compliance posture is governance data.
    # IsAuthenticated, not IsSupervisor: any authenticated RTGS user may
    # read: only decisions are supervisor-gated.
    permission_classes = [IsAuthenticated]
    """
    GET: Compliance statistics grouping blocks into Green, Amber, Red completeness.
    """
    def get(self, request):
        blocks = _get_all_blocks()
        total_blocks = len(blocks)
        
        all_assessments = _get_all_assessments()
        all_audit_logs = _get_all_audit_logs()
        
        assessments_by_block = {ass.block.id for ass in all_assessments if ass.block}
        audit_logs_by_block = {log.block.id for log in all_audit_logs if log.block}
        
        green_cnt = 0
        amber_cnt = 0
        red_cnt = 0
        
        needs_attention_list = []
        
        for b in blocks:
            # Completeness checklist
            has_photo = bool(b.raw_image_path)
            has_gps = bool(b.gps_latitude and b.gps_longitude)
            has_timestamp = bool(b.captured_at)
            has_measurement = bool(b.measurement)
            has_approval = b.approval_status in ['approved', 'rejected']
            has_assessment = b.id in assessments_by_block
            has_audit = b.id in audit_logs_by_block
            
            checklist = [has_photo, has_gps, has_timestamp, has_measurement, has_approval, has_assessment, has_audit]
            missing_count = len(checklist) - sum(checklist)
            
            if missing_count == 0:
                color = "green"
                green_cnt += 1
            elif missing_count == 1:
                color = "amber"
                amber_cnt += 1
                needs_attention_list.append({
                    "block_id": b.block_id,
                    "quarry": b.quarry.name if b.quarry else "Unspecified",
                    "reason": "Missing single data element (e.g. approval, assessment)",
                    "severity": "WARNING",
                    "missing": ["photo" if not has_photo else "gps" if not has_gps else "approval" if not has_approval else "assessment"]
                })
            else:
                color = "red"
                red_cnt += 1
                needs_attention_list.append({
                    "block_id": b.block_id,
                    "quarry": b.quarry.name if b.quarry else "Unspecified",
                    "reason": f"Multiple missing elements ({missing_count} fields missing)",
                    "severity": "CRITICAL",
                    "missing": [k for k, v in [("photo", has_photo), ("gps", has_gps), ("approval", has_approval), ("assessment", has_assessment)] if not v]
                })
                
        completeness_pct = (green_cnt / total_blocks * 100.0) if total_blocks > 0 else 100.0
        
        return Response({
            "total_blocks": total_blocks,
            "green_count": green_cnt,
            "amber_count": amber_cnt,
            "red_count": red_cnt,
            "completeness_percentage": round(completeness_pct, 1),
            "needs_attention": needs_attention_list[:20] # Limit to top 20
        }, status=status.HTTP_200_OK)


class AnalyticsAlertsAPIView(APIView):
    # Phase 6B read-surface authentication. Alerts disclose governance exceptions.
    # IsAuthenticated, not IsSupervisor: any authenticated RTGS user may
    # read: only decisions are supervisor-gated.
    permission_classes = [IsAuthenticated]
    """
    GET: Renders notification feeds for supervisor compliance.
    """
    def get(self, request):
        # Use cached data — filter in-memory instead of 4 separate DB queries
        all_blocks = _get_all_blocks()
        all_assessments = _get_all_assessments()
        alerts = []
        
        # 1. Reference-integrity alerts, derived from real ingestion state.
        #
        # This replaces the former HIGH_VARIANCE rule, which was driven entirely
        # by Assessment.variance_pct - a field no production path writes, only
        # random.uniform() in generate_mock_data - and whose description told
        # the supervisor the figure came from "weighbridge returns" when no
        # weighbridge data exists anywhere in the system.
        WARNING_TEXT = {
            "quarry_unresolved": ("WARNING", "Unresolved quarry reference",
                                  "The submitted quarry ID does not match any registered Quarry."),
            "officer_unresolved": ("WARNING", "Unresolved officer reference",
                                   "The submitted officer ID does not match any registered Officer."),
            "quarry_missing": ("WARNING", "No quarry submitted",
                               "The submission carried no quarry identifier."),
            "officer_missing": ("WARNING", "No officer submitted",
                                "The submission carried no officer identifier."),
            "gps_incomplete": ("INFO", "GPS not captured",
                               "The submission carried no usable GPS coordinates."),
            "client_volume_mismatch": ("CRITICAL", "Client/server volume disagreement",
                                       "The device-reported volume disagreed with L x B x H; the server value was stored."),
        }
        for b in all_blocks:
            for warning in (b.reference_warnings or []):
                severity, title, description = WARNING_TEXT.get(
                    warning, ("INFO", warning, "See block record.")
                )
                alerts.append({
                    "alert_id": f"ALT-{warning.upper()}-{b.block_id}",
                    "severity": severity,
                    "type": warning.upper(),
                    "title": f"{title}: {b.block_id}",
                    "description": description,
                    "related_entity": f"Block: {b.block_id}",
                    "timestamp": b.created_at.strftime('%Y-%m-%d %H:%M:%S') if b.created_at else ""
                })

        # 2. Repeated capture attempts (> 2)
        for b in [b for b in all_blocks if b.capture_attempt_count and b.capture_attempt_count > 2][:5]:
            alerts.append({
                "alert_id": f"ALT-ATT-{b.block_id}",
                "severity": "INFO",
                "type": "REPEATED_ATTEMPTS",
                "title": f"Multiple Image Captures: {b.block_id}",
                "description": f"Field Officer attempted {b.capture_attempt_count} captures before finalizing upload.",
                "related_entity": f"Inspector: {b.inspecting_officer_id}",
                "timestamp": b.captured_at.strftime('%Y-%m-%d %H:%M:%S') if b.captured_at else ""
            })

        # 3. Long inspection duration (> 250s)
        for b in [b for b in all_blocks if b.inspection_duration_seconds and b.inspection_duration_seconds > 250][:5]:
            alerts.append({
                "alert_id": f"ALT-DUR-{b.block_id}",
                "severity": "WARNING",
                "type": "LONG_DURATION",
                "title": f"Slow Sizing Execution: {b.block_id}",
                "description": f"Inspector spent {b.inspection_duration_seconds} seconds completing sizing forms.",
                "related_entity": f"Block: {b.block_id}",
                "timestamp": b.captured_at.strftime('%Y-%m-%d %H:%M:%S') if b.captured_at else ""
            })

        # 4. Low confidence (< 0.75)
        for b in [b for b in all_blocks if b.measurement and b.measurement.confidence and b.measurement.confidence < 0.75][:5]:
            alerts.append({
                "alert_id": f"ALT-CONF-{b.block_id}",
                "severity": "WARNING",
                "type": "LOW_CONFIDENCE",
                "title": f"Low CV Confidence Alert: {b.block_id}",
                "description": f"Computer Vision measurement completed with lower confidence margin ({int(b.measurement.confidence * 100)}%).",
                "related_entity": f"Block: {b.block_id}",
                "timestamp": b.captured_at.strftime('%Y-%m-%d %H:%M:%S') if b.captured_at else ""
            })

        alerts.sort(key=lambda x: x['timestamp'], reverse=True)
        return Response(alerts[:20], status=status.HTTP_200_OK)


class MapDataAPIView(APIView):
    # Phase 6B read-surface authentication. Plots block locations against
    # seigniorage values. IsAuthenticated, not IsSupervisor: any authenticated
    # RTGS user may read; only decisions are supervisor-gated.
    permission_classes = [IsAuthenticated]
    """
    GET: Real captured coordinates only.

    REWRITTEN (Phase 6E). The previous implementation iterated QUARRIES and,
    when a quarry's free-text location failed to parse - which it always did -
    synthesised a latitude and longitude from an MD5 hash of the quarry id. It
    also defaulted the district to "Prakasam" and the lessee to "AP Granite Co."
    for any quarry missing them, and labelled the whole payload
    "DEMO / SYNTHETIC LOCATIONS".

    Two consequences, both wrong once the field app started capturing GPS:
      * blocks carrying a REAL fix were never plotted, because the endpoint only
        knew about quarries, and there are no registered Quarry documents
      * every coordinate it did return was invented

    This version returns only coordinates that were actually recorded. A block
    without a fix is COUNTED, never placed. Nothing is inferred or substituted.
    """

    def get(self, request):
        all_blocks = _get_all_blocks()
        all_assessments = _get_all_assessments()

        assessment_by_block_id = {}
        for ass in all_assessments:
            if ass.block:
                assessment_by_block_id[ass.block.id] = ass

        block_points = []
        without_gps = 0
        for b in all_blocks:
            if b.gps_latitude is None or b.gps_longitude is None:
                without_gps += 1
                continue
            ass = assessment_by_block_id.get(b.id)
            block_points.append({
                "block_id": b.block_id,
                "latitude": b.gps_latitude,
                "longitude": b.gps_longitude,
                "submitted_quarry_id": b.submitted_quarry_id,
                "officer_id": b.inspecting_officer_id,
                "status": b.status,
                "approval_status": b.approval_status,
                "seigniorage": (round(ass.indicative_seigniorage, 2) if ass else None),
                "captured_at": b.captured_at.isoformat() + "Z" if b.captured_at else None,
            })

        # Quarries appear ONLY when they carry a real, parseable coordinate pair.
        # No hash fallback, and no invented district or lessee.
        quarry_points = []
        for q in _get_all_quarries():
            if not q.location or "," not in q.location:
                continue
            parts = q.location.split(",")
            try:
                lat = float(parts[-2].strip())
                lon = float(parts[-1].strip())
            except (ValueError, IndexError):
                continue
            blocks = [b for b in all_blocks if b.quarry and b.quarry.id == q.id]
            revenue = sum(
                assessment_by_block_id[b.id].indicative_seigniorage
                for b in blocks if b.id in assessment_by_block_id
            )
            quarry_points.append({
                "quarry_id": str(q.id),
                "name": q.name,
                "district": q.district,          # null when not recorded
                "lessee": q.lessee_name,         # null when not recorded
                "latitude": lat,
                "longitude": lon,
                "block_count": len(blocks),
                "revenue": round(revenue, 2),
            })

        return Response({
            "blocks": block_points,
            "quarries": quarry_points,
            "blocks_plotted": len(block_points),
            "blocks_without_gps": without_gps,
            "label": "REAL CAPTURED COORDINATES",
            "disclaimer": (
                "Only coordinates actually recorded at capture are plotted. "
                f"{without_gps} block(s) have no GPS fix and are counted here "
                "rather than placed at an inferred location."
            ),
        }, status=status.HTTP_200_OK)


class ExecutiveOverviewAPIView(APIView):
    # Phase 6B read-surface authentication. Discloses aggregate financial position.
    # IsAuthenticated, not IsSupervisor: any authenticated RTGS user may
    # read: only decisions are supervisor-gated.
    permission_classes = [IsAuthenticated]
    """
    GET: Core numbers dashboard.
    """
    def get(self, request):
        def _fetch_executive_overview():
            all_blocks = _get_all_blocks()
            all_assessments = _get_all_assessments()
            all_audit_logs = _get_all_audit_logs()
            total_blocks = len(all_blocks)
            quarries = _get_all_quarries()
            officers = _get_all_officers()

            total_seigniorage = sum(ass.indicative_seigniorage for ass in all_assessments)

            # Override rate calculation
            overrides = sum(1 for b in all_blocks if b.is_overridden)
            override_rate = (overrides / total_blocks) if total_blocks > 0 else 0.0

            # Approval rate
            approved = sum(1 for b in all_blocks if b.approval_status == 'approved')
            approval_rate = (approved / total_blocks) if total_blocks > 0 else 0.0

            # Confidence average
            conf_vals = [b.measurement.confidence for b in all_blocks if b.measurement]
            avg_confidence = (sum(conf_vals) / len(conf_vals)) if conf_vals else 0.0

            # Revenue recovery requires weighbridge reconciliation, which does
            # not exist yet. Reported as None ("Not available") rather than
            # derived from the random variance_pct that only generate_mock_data
            # ever wrote. See RevenueLeakageAPIView for the full rationale.
            estimated_recovery = None

            # Critical alerts now come from real ingestion warnings, not from a
            # random variance figure. Counted uncapped - the old version was
            # structurally capped at 5, so a genuine 40-alert situation reported 5.
            critical_alerts_count = sum(
                1 for b in all_blocks
                if 'client_volume_mismatch' in (b.reference_warnings or [])
            )

            # Compliance Readiness — inline computation (no sub-view call)
            assessments_by_block = {ass.block.id for ass in all_assessments if ass.block}
            audit_logs_by_block = {log.block.id for log in all_audit_logs if log.block}
            green_cnt = 0
            amber_cnt = 0
            red_cnt = 0
            needs_attention_list = []
            for b in all_blocks:
                has_photo = bool(b.raw_image_path)
                has_gps = bool(b.gps_latitude and b.gps_longitude)
                has_timestamp = bool(b.captured_at)
                has_measurement = bool(b.measurement)
                has_approval = b.approval_status in ['approved', 'rejected']
                has_assessment = b.id in assessments_by_block
                has_audit = b.id in audit_logs_by_block
                checklist = [has_photo, has_gps, has_timestamp, has_measurement, has_approval, has_assessment, has_audit]
                missing_count = len(checklist) - sum(checklist)
                if missing_count == 0:
                    green_cnt += 1
                elif missing_count == 1:
                    amber_cnt += 1
                    needs_attention_list.append({
                        "block_id": b.block_id,
                        "quarry": b.quarry.name if b.quarry else "Unspecified",
                        "reason": "Missing single data element",
                        "severity": "WARNING",
                        "missing": ["photo" if not has_photo else "gps" if not has_gps else "approval" if not has_approval else "assessment"]
                    })
                else:
                    red_cnt += 1
                    needs_attention_list.append({
                        "block_id": b.block_id,
                        "quarry": b.quarry.name if b.quarry else "Unspecified",
                        "reason": f"Multiple missing elements ({missing_count} fields missing)",
                        "severity": "CRITICAL",
                        "missing": [k for k, v in [("photo", has_photo), ("gps", has_gps), ("approval", has_approval), ("assessment", has_assessment)] if not v]
                    })
            completeness_pct = (green_cnt / total_blocks * 100.0) if total_blocks > 0 else 100.0

            # Weekly trend for Overview chart
            weekly_revenue = []
            now = datetime.datetime.utcnow()
            for w in range(5, -1, -1):
                week_start = now - datetime.timedelta(weeks=w)
                week_start = week_start - datetime.timedelta(days=week_start.weekday())
                week_start = datetime.datetime(week_start.year, week_start.month, week_start.day, 0, 0, 0)
                week_end = week_start + datetime.timedelta(days=7)

                week_ass = [ass for ass in all_assessments if ass.created_at and week_start <= ass.created_at < week_end]
                rev = sum(ass.indicative_seigniorage for ass in week_ass)
                vol = sum(ass.volume_m3 for ass in week_ass)

                weekly_revenue.append({
                    "week": week_start.strftime('%b %d'),
                    "revenue": round(rev, 2),
                    "volume": round(vol, 4)
                })

            return {
                "total_blocks_inspected": total_blocks,
                "total_quarries": len(quarries),
                "active_officers": len(officers),
                "total_seigniorage": round(total_seigniorage, 2),
                "avg_confidence": round(avg_confidence, 2),
                "override_rate": round(override_rate, 4),
                "approval_rate": round(approval_rate, 4),
                "estimated_revenue_recovered": estimated_recovery,  # None until weighbridge reconciliation exists
                "critical_alerts_count": critical_alerts_count,
                "compliance_completeness_pct": round(completeness_pct, 1),
                "completeness_percentage": round(completeness_pct, 1),
                "green_count": green_cnt,
                "amber_count": amber_cnt,
                "red_count": red_cnt,
                "needs_attention": needs_attention_list[:5],
                "weekly_revenue": weekly_revenue
            }

        data = _get_cached('overview_api', _fetch_executive_overview)
        return Response(data, status=status.HTTP_200_OK)





class AuthCsrfAPIView(APIView):
    """GET /api/auth/csrf/ - hand the browser a CSRF cookie before logging in.

    The dashboard authenticates with a Django SESSION, so every unsafe request
    must carry a CSRF token. A browser that has never talked to this backend has
    no csrftoken cookie yet, and the login POST is itself unsafe - so there has
    to be one safe call that sets the cookie first. That is this endpoint.

    @ensure_csrf_cookie sets the cookie on the response. The cookie is readable
    by JavaScript by design (that is how the client echoes it back in the
    X-CSRFToken header); the SESSION cookie is HttpOnly and never readable.

    This is the documented Django flow, not a CSRF bypass: no endpoint is
    exempted, and the login view below is explicitly csrf_protect'ed.
    """

    permission_classes = [AllowAny]

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return Response({"detail": "CSRF cookie set."}, status=status.HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class AuthLoginAPIView(APIView):
    """POST /api/auth/login/ - username + password, Django session out.

    Phase 6E. The dashboard previously asked the operator to paste an API token,
    which meant a human handling a long-lived bearer credential by hand. This
    authenticates against the SAME Django auth stack and the SAME User records -
    no second backend, no parallel identity store - and returns a session.

    NOTHING secret is returned: no token, no password, no hash. The session
    lives in an HttpOnly cookie the browser manages, so the dashboard never
    holds a credential it could leak.

    csrf_protect is applied explicitly. DRF's SessionAuthentication only
    enforces CSRF once a session user exists, so an anonymous login POST would
    otherwise be unchecked - the decorator closes that gap rather than exempting
    the endpoint.
    """

    permission_classes = [AllowAny]
    authentication_classes = []  # nothing to authenticate with yet

    # One message for every failure mode. A different message for "no such user"
    # versus "wrong password" would let an attacker enumerate valid usernames.
    INVALID = "Invalid username or password."

    def post(self, request):
        username = (request.data.get("username") or "").strip()
        password = request.data.get("password") or ""

        if not username or not password:
            return Response(
                {"error": "Username and password are required.", "code": "missing_credentials"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # authenticate() returns None for a bad password, an unknown user AND an
        # inactive account (ModelBackend.user_can_authenticate), so all three
        # collapse into the same 401 with the same wording.
        user = authenticate(request, username=username, password=password)
        if user is None:
            return Response(
                {"error": self.INVALID, "code": "invalid_credentials"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        django_login(request, user)

        from .permissions import OFFICER_GROUP, SUPERVISOR_GROUP, is_supervisor

        groups = list(user.groups.values_list("name", flat=True))
        if is_supervisor(user):
            role = SUPERVISOR_GROUP
        elif OFFICER_GROUP in groups:
            role = OFFICER_GROUP
        else:
            role = None

        # Identical shape to GET /api/auth/me/ so the dashboard has one contract.
        return Response({
            "username": user.get_username(),
            "role": role,
            "groups": groups,
            "is_supervisor": is_supervisor(user),
        }, status=status.HTTP_200_OK)


class AuthLogoutAPIView(APIView):
    """POST /api/auth/logout/ - end the Django session.

    Requires an authenticated session, so DRF's SessionAuthentication enforces
    CSRF on this POST automatically. Flushes the session server-side; the
    browser's session cookie is cleared by django_logout.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        django_logout(request)
        return Response({"detail": "Signed out."}, status=status.HTTP_200_OK)


class AuthMeAPIView(APIView):
    """GET /api/auth/me/ - who am I, according to the backend?

    The dashboard calls this on load with a stored token so it can (a) confirm
    the token is still valid and (b) learn the role, rather than trusting
    anything it kept in localStorage. Read-only, and deliberately narrow: it
    returns the username and role only - never the password hash, email, or any
    other user field.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .permissions import OFFICER_GROUP, SUPERVISOR_GROUP, is_supervisor

        groups = list(request.user.groups.values_list('name', flat=True))
        if is_supervisor(request.user):
            role = SUPERVISOR_GROUP
        elif OFFICER_GROUP in groups:
            role = OFFICER_GROUP
        else:
            role = None

        return Response({
            "username": request.user.get_username(),
            "role": role,
            "groups": groups,
            "is_supervisor": is_supervisor(request.user),
        }, status=status.HTTP_200_OK)
