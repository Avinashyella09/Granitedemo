from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Block, Quarry, Measurement
from .serializers import BlockSerializer
import datetime
import os
import hashlib
import threading
import time
import cv2

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

class BlockListCreateAPIView(APIView):
    """
    API View to list all blocks or create a new block.
    """
    def get(self, request):
        def _fetch_serialized_blocks():
            blocks = list(Block.objects.all().select_related(max_depth=1))
            serialized_data = []
            for block in blocks:
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
                serialized_data.append(serializer.data)
            return serialized_data

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
    """
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
        classification = validated_data['gangsaw_classification']
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

        # 4. Run calculation service using existing Phase 2 layer
        try:
            report = generate_assessment_report(
                block_id=block_id,
                length_m=block_doc.measurement.length_m,
                breadth_m=block_doc.measurement.breadth_m,
                height_m=block_doc.measurement.height_m,
                category=category,
                classification=classification,
                density=density
            )
        except ValueError as e:
            return Response(
                {"error": f"Calculation error: {e}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 5. Persist the Assessment document (storing the exact snapshot values)
        ass = Assessment(
            block=block_doc,
            granite_category=category,
            gangsaw_classification=classification,
            volume_m3=report['volume_m3'],
            weight_mt=report['estimated_weight_mt'],
            rate_per_mt=report['applicable_rate_per_mt'],
            indicative_seigniorage=report['indicative_seigniorage'],
            density_mt_per_m3=report['density_mt_per_m3'],
            status=validated_data.get('status', 'draft')
        )
        ass.save()
        _invalidate_cache()

        # Build clean response output
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
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class AssessmentDetailAPIView(APIView):
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
    """
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        block_id   = request.data.get('block_id', '').strip()
        quarry_id  = request.data.get('quarry_id', '').strip()
        officer_id = request.data.get('officer_id', '').strip()
        uploaded_file = request.FILES.get('image')

        # --- Validate required fields ---
        if not block_id:
            return Response({'error': 'block_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            length_m  = float(request.data.get('length_m', 0))
            breadth_m = float(request.data.get('breadth_m', 0))
            height_m  = float(request.data.get('height_m', 0))
            volume_m3 = float(request.data.get('volume_m3', 0))
        except (TypeError, ValueError):
            return Response({'error': 'length_m, breadth_m, height_m, volume_m3 must be numbers.'}, status=status.HTTP_400_BAD_REQUEST)

        if length_m <= 0 or breadth_m <= 0 or height_m <= 0:
            return Response({'error': 'AR dimensions must be greater than zero.'}, status=status.HTTP_400_BAD_REQUEST)

        if not uploaded_file:
            return Response({'error': 'image file is required.'}, status=status.HTTP_400_BAD_REQUEST)

        # --- Resolve or create block ---
        block = Block.objects(block_id=block_id).first()
        if block:
            # Already exists — update with new AR measurement
            pass
        else:
            # Resolve quarry reference
            quarry_doc = None
            if quarry_id:
                quarry_doc = Quarry.objects(id=quarry_id).first()

            block = Block(
                block_id=block_id,
                quarry=quarry_doc,
                status='pending',
            )

        # --- GPS ---
        try:
            lat = float(request.data.get('gps_latitude')) if request.data.get('gps_latitude') else None
        except (TypeError, ValueError):
            lat = None
        try:
            lon = float(request.data.get('gps_longitude')) if request.data.get('gps_longitude') else None
        except (TypeError, ValueError):
            lon = None

        if lat:
            block.gps_latitude = lat
        if lon:
            block.gps_longitude = lon
        if officer_id:
            block.inspecting_officer_id = officer_id

        # --- Save raw image ---
        from django.conf import settings
        raw_dir = os.path.join(settings.MEDIA_ROOT, 'raw')
        os.makedirs(raw_dir, exist_ok=True)

        timestamp_str  = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        raw_filename   = f"{block_id}_{timestamp_str}_ar_raw.jpg"
        raw_filepath   = os.path.join(raw_dir, raw_filename)

        with open(raw_filepath, 'wb+') as dst:
            for chunk in uploaded_file.chunks():
                dst.write(chunk)

        block.raw_image_path = os.path.relpath(raw_filepath, settings.MEDIA_ROOT).replace('\\', '/')
        block.captured_at    = datetime.datetime.utcnow()

        if block.raw_image_path not in (block.image_paths or []):
            if not block.image_paths:
                block.image_paths = []
            block.image_paths.append(block.raw_image_path)

        # --- Save AR measurement ---
        measurement_doc = Measurement(
            length_m=length_m,
            breadth_m=breadth_m,
            height_m=height_m,
            volume_m3=volume_m3,
            confidence=1.0,
            measurement_method='ar',
            measured_at=datetime.datetime.utcnow()
        )
        block.measurement = measurement_doc
        block.status      = 'measured'
        block.cv_status   = 'success'
        block.save()
        _invalidate_cache()

        # --- Audit log ---
        audit = AuditLog(
            block=block,
            action='ar_measurement_submitted',
            actor=officer_id or 'FieldOfficer',
            details=f'AR inspection: L={length_m}m, B={breadth_m}m, H={height_m}m, V={volume_m3}m³',
            timestamp=datetime.datetime.utcnow()
        )
        audit.save()

        # --- Return full block representation ---
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
        return Response(serializer.data, status=status.HTTP_201_CREATED)

class BlockOverrideAPIView(APIView):
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
        actor = request.data.get('actor', 'Supervisor')
        
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
        log_details = f"Manual override to L: {length_m}, B: {breadth_m}, H: {height_m}. Reason: {reason}"
        audit = AuditLog(
            block=block,
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
        actor = request.data.get('actor', 'Supervisor')
        
        if approval_status_val not in ['approved', 'rejected']:
            return Response(
                {"error": "approval_status must be 'approved' or 'rejected'."},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        block.approval_status = approval_status_val
        block.approved_by = actor
        block.approved_at = datetime.datetime.utcnow()
        block.save()
        _invalidate_cache()
        
        # Log to AuditLog
        audit = AuditLog(
            block=block,
            action=f"block_approval_{approval_status_val}",
            actor=actor,
            details=f"Block inspection status updated to {approval_status_val}",
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
        
        try:
            pdf_bytes = generate_block_pdf(block, assessment)
        except Exception as e:
            return Response(
                {"error": f"Failed to generate PDF: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
            
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="inspection_report_{block_id}.pdf"'
        return response


class BlockAuditLogsAPIView(APIView):
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
        gangsaw_revenue = sum(ass.indicative_seigniorage for ass in assessments if ass.gangsaw_classification == "Gangsaw Size")
        below_gangsaw_revenue = sum(ass.indicative_seigniorage for ass in assessments if ass.gangsaw_classification != "Gangsaw Size")

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
    """
    GET: Estimations for revenue leakage and recovery rates.
    """
    def get(self, request):
        # Fetch all assessments ONCE and compute everything in-memory
        all_assessments = _get_all_assessments()
        
        leakage_list = [a for a in all_assessments if a.variance_pct and a.variance_pct > 8.0]
        total_count = len(all_assessments)
        total_seigniorage = sum(a.indicative_seigniorage for a in all_assessments)
        
        # Calculate recovered value (the difference scaled by variance)
        estimated_recovery = sum(
            a.indicative_seigniorage * (a.variance_pct / 100.0)
            for a in leakage_list
        )
        
        variance_sum = sum(a.variance_pct for a in all_assessments if a.variance_pct)
        avg_leakage = round(variance_sum / total_count, 2) if total_count > 0 else 0.0

        return Response({
            "leakage_blocks_count": len(leakage_list),
            "total_blocks_count": total_count,
            "estimated_revenue_recovered": round(estimated_recovery, 2),
            "total_revenue": round(total_seigniorage, 2),
            "average_leakage_pct": avg_leakage,
            "label": "POC ESTIMATE - NOT ACTUAL GOVERNMENT REVENUE",
            "disclaimer": "POC ONLY: Values generated from synthetic database fields."
        }, status=status.HTTP_200_OK)


class AuditReadinessAPIView(APIView):
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
    """
    GET: Renders notification feeds for supervisor compliance.
    """
    def get(self, request):
        # Use cached data — filter in-memory instead of 4 separate DB queries
        all_blocks = _get_all_blocks()
        all_assessments = _get_all_assessments()
        alerts = []
        
        # 1. High variance assessments
        high_var = [a for a in all_assessments if a.variance_pct and a.variance_pct > 15.0][:5]
        for ass in high_var:
            block_id = ass.block.block_id if ass.block else 'UNKNOWN'
            alerts.append({
                "alert_id": f"ALT-VAR-{block_id}",
                "severity": "CRITICAL",
                "type": "HIGH_VARIANCE",
                "title": f"High Dimensional Variance: {block_id}",
                "description": f"Verified CV dimension differs from weighbridge returns by {ass.variance_pct}%.",
                "related_entity": f"Block: {block_id}",
                "timestamp": ass.created_at.strftime('%Y-%m-%d %H:%M:%S') if ass.created_at else ""
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
    """
    GET: Returns coordinates for Quarries and inspection sites.
    """
    def get(self, request):
        quarries = _get_all_quarries()
        all_blocks = _get_all_blocks()
        all_assessments = _get_all_assessments()
        
        # Build block lookups
        blocks_by_quarry = {}
        for b in all_blocks:
            if b.quarry:
                blocks_by_quarry.setdefault(b.quarry.id, []).append(b)
                
        # Map block ID to assessment
        assessment_by_block_id = {}
        for ass in all_assessments:
            if ass.block:
                assessment_by_block_id[ass.block.id] = ass
                
        map_points = []
        for q in quarries:
            blocks = blocks_by_quarry.get(q.id, [])
            revenue = sum(
                assessment_by_block_id[b.id].indicative_seigniorage
                for b in blocks
                if b.id in assessment_by_block_id
            )
            
            # Extract actual lat/lon or generate mock coordinates if missing
            q_lat = None
            q_lon = None
            if q.location and "," in q.location:
                parts = q.location.split(",")
                try:
                    q_lat = float(parts[-2].strip())
                    q_lon = float(parts[-1].strip())
                except ValueError:
                    pass
            
            if q_lat is None:
                # Deterministic coordinates based on quarry ID hash
                h = int(hashlib.md5(str(q.id).encode()).hexdigest()[:8], 16)
                q_lat = round(15.2 + (h % 9000) / 10000.0, 4)
            if q_lon is None:
                h2 = int(hashlib.md5((str(q.id) + 'lon').encode()).hexdigest()[:8], 16)
                q_lon = round(79.2 + (h2 % 9000) / 10000.0, 4)
                
            map_points.append({
                "quarry_id": str(q.id),
                "name": q.name,
                "district": q.district or "Prakasam",
                "lessee": q.lessee_name or "AP Granite Co.",
                "latitude": q_lat,
                "longitude": q_lon,
                "block_count": len(blocks),
                "revenue": round(revenue, 2),
                "status": "active"
            })
            
        return Response({
            "quarries": map_points,
            "label": "DEMO / SYNTHETIC LOCATIONS",
            "disclaimer": "Synthetic coordinates inside Andhra Pradesh bounding boxes for POC visualization."
        }, status=status.HTTP_200_OK)


class ExecutiveOverviewAPIView(APIView):
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

            # Estimated revenue recovery from high-variance assessments
            estimated_recovery = sum(
                ass.indicative_seigniorage * (ass.variance_pct / 100.0)
                for ass in all_assessments
                if ass.variance_pct and ass.variance_pct > 8.0
            )

            # Critical Alerts count — inline computation (no sub-view call)
            high_var_alerts = [a for a in all_assessments if a.variance_pct and a.variance_pct > 15.0]
            critical_alerts_count = len(high_var_alerts[:5])

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
                "estimated_revenue_recovered": round(estimated_recovery, 2),
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



