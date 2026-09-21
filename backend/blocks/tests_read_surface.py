"""
Phase 6B: read/export surface authentication.

Every sensitive GET must reject an anonymous caller and serve an authenticated
one, while the FROZEN iOS field app's write paths must keep working with no
credentials at all.

ISOLATION: mongoengine is rebound to in-memory mongomock and MEDIA_ROOT is a
temp directory. Django auth users live in the test SQLite database that TestCase
creates and rolls back. The real Atlas cluster and the real backend/media/ tree
are never touched. Do not run the legacy blocks/tests.py.

    ./venv/bin/python manage.py test blocks.tests_read_surface
"""

import io
import shutil
import tempfile

import mongoengine
import mongomock
from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token

from blocks import seigniorage
from blocks.permissions import OFFICER_GROUP, SUPERVISOR_GROUP

_TEMP_MEDIA = tempfile.mkdtemp(prefix="graniteblocks-readsurface-media-")

# Every endpoint that must require authentication. Kept as one list so a new
# sensitive read cannot be added without a decision about this test.
SENSITIVE_READS = [
    "/api/blocks/",
    "/api/blocks/{bid}/",
    "/api/blocks/{bid}/audit-logs/",
    "/api/blocks/{bid}/pdf/",
    "/api/export/omeps/{bid}/",
    "/api/assessments/",
    "/api/assessments/{bid}/",
    "/api/analytics/overview/",
    "/api/analytics/officers/",
    "/api/analytics/quarries/comparison/",
    "/api/analytics/revenue/summary/",
    "/api/analytics/revenue/leakage/",
    "/api/analytics/audit-readiness/",
    "/api/analytics/alerts/",
    "/api/analytics/map-data/",
]

# Paths the FROZEN iOS app calls. These must stay open in this step.
FROZEN_APP_WRITE_PATHS = [
    "/api/blocks/",
    "/api/blocks/ar-measure/",
    "/api/blocks/{bid}/measure-cv/",
]


def make_image():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (48, 48), (120, 120, 120)).save(buf, format="JPEG")
    return SimpleUploadedFile("photo.jpg", buf.getvalue(), content_type="image/jpeg")


@override_settings(MEDIA_ROOT=_TEMP_MEDIA)
class ReadSurfaceAuthTests(TestCase):
    """TestCase (not SimpleTestCase): Django auth users need the test SQL DB."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mongoengine.disconnect(alias="default")
        mongoengine.connect(
            db="granite_blocks_test", alias="default",
            mongo_client_class=mongomock.MongoClient, uuidRepresentation="standard",
        )
        from blocks.models import Assessment, AuditLog, Block, Measurement, Officer, Quarry

        cls.Block, cls.Measurement, cls.Assessment = Block, Measurement, Assessment
        cls.AuditLog, cls.Quarry, cls.Officer = AuditLog, Quarry, Officer

    @classmethod
    def tearDownClass(cls):
        mongoengine.disconnect(alias="default")
        shutil.rmtree(_TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        for model in (self.Block, self.Assessment, self.AuditLog, self.Quarry, self.Officer):
            model.objects.delete()
        self.officer_user, self.officer_token = self.make_user("reader1", OFFICER_GROUP)
        self.sup_user, self.sup_token = self.make_user("sup6b", SUPERVISOR_GROUP)
        self.plain_user, self.plain_token = self.make_user("plain6b", None)
        self.bid = "block2-6b"
        self.make_assessed_block(self.bid)

    def make_user(self, username, role):
        user = User.objects.create_user(username=username, password="x-test-pass-123")
        if role:
            group, _ = Group.objects.get_or_create(name=role)
            user.groups.add(group)
        return user, Token.objects.get_or_create(user=user)[0].key

    def auth(self, token):
        return {"HTTP_AUTHORIZATION": f"Token {token}"}

    def make_assessed_block(self, block_id):
        """Mirrors the real block2 record: same dimensions, same money."""
        import datetime

        block = self.Block(
            block_id=block_id, status="measured", cv_status="not_applicable",
            approval_status="approved", approved_by="rtgs_supervisor",
            submitted_quarry_id="Q-9982", inspecting_officer_id="Off",
            reference_warnings=["quarry_unresolved", "officer_unresolved", "gps_incomplete"],
            captured_at=datetime.datetime(2026, 9, 19, 6, 35, 46),
            approved_at=datetime.datetime(2026, 9, 19, 7, 41, 23),
        )
        block.measurement = self.Measurement(
            length_m=0.3455, breadth_m=0.3345, height_m=0.2041,
            volume_m3=0.3455 * 0.3345 * 0.2041, measurement_method="ar",
        )
        block.save()
        r = seigniorage.calculate(0.3455, 0.3345, 0.2041, "Others", 2.7)
        self.Assessment(
            block=block, granite_category=r["granite_category"],
            gangsaw_classification=r["gangsaw_classification"],
            volume_m3=r["volume_m3"], weight_mt=r["tonnage_mt"],
            rate_per_mt=r["rate_per_mt"], indicative_seigniorage=r["seigniorage_amount"],
            density_mt_per_m3=r["density_mt_per_m3"],
        ).save()
        self.AuditLog(block=block, block_id_snapshot=block_id,
                      action="ar_measurement_submitted", actor="Off",
                      details="AR inspection: L=0.3455m").save()
        self.AuditLog(block=block, block_id_snapshot=block_id,
                      action="seigniorage_assessed", actor="System",
                      details=f"Official seigniorage: schedule={seigniorage.RATE_SCHEDULE_VERSION},").save()
        self.AuditLog(block=block, block_id_snapshot=block_id,
                      action="block_approval_approved", actor="rtgs_supervisor",
                      details="Approval decision: reason=PoC supervisor approval").save()
        return block

    def url(self, template):
        return template.format(bid=self.bid)

    # --- safety net -----------------------------------------------------------

    def test_isolated_from_real_cluster_and_media(self):
        from django.conf import settings

        self.assertIn("mongomock", type(mongoengine.get_connection()).__module__.lower())
        self.assertEqual(mongoengine.get_db().name, "granite_blocks_test")
        self.assertTrue(str(settings.MEDIA_ROOT).startswith(tempfile.gettempdir()))

    # --- 1, 3, 5, 7, 9: anonymous rejected -----------------------------------

    def test_every_sensitive_read_rejects_anonymous_callers(self):
        for template in SENSITIVE_READS:
            with self.subTest(endpoint=template):
                response = self.client.get(self.url(template))
                self.assertEqual(response.status_code, 401,
                                 f"{template} served an anonymous caller")

    def test_anonymous_block_detail_rejected(self):
        self.assertEqual(self.client.get(f"/api/blocks/{self.bid}/").status_code, 401)

    def test_anonymous_audit_log_read_rejected(self):
        self.assertEqual(
            self.client.get(f"/api/blocks/{self.bid}/audit-logs/").status_code, 401)

    def test_anonymous_pdf_rejected(self):
        self.assertEqual(self.client.get(f"/api/blocks/{self.bid}/pdf/").status_code, 401)

    def test_anonymous_omeps_export_rejected(self):
        self.assertEqual(
            self.client.get(f"/api/export/omeps/{self.bid}/").status_code, 401)

    def test_anonymous_sensitive_analytics_rejected(self):
        for path in ("/api/analytics/overview/", "/api/analytics/revenue/summary/",
                     "/api/analytics/revenue/leakage/", "/api/analytics/audit-readiness/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    def test_anonymous_rejection_leaks_no_business_data(self):
        """A 401 must not carry the payload it was protecting."""
        for template in SENSITIVE_READS:
            with self.subTest(endpoint=template):
                body = self.client.get(self.url(template)).content.decode(errors="ignore")
                for secret in ("45.85", "rtgs_supervisor", "0.023587785975", "Within Gangsaw"):
                    self.assertNotIn(secret, body)

    # --- 2, 4, 6, 8, 10: authenticated accepted -------------------------------

    def test_every_sensitive_read_accepts_an_authenticated_caller(self):
        for template in SENSITIVE_READS:
            with self.subTest(endpoint=template):
                response = self.client.get(self.url(template), **self.auth(self.officer_token))
                self.assertEqual(response.status_code, 200,
                                 f"{template} rejected an authenticated caller")

    def test_authenticated_block_detail_returns_real_values(self):
        data = self.client.get(f"/api/blocks/{self.bid}/", **self.auth(self.officer_token)).json()
        self.assertEqual(data["block_id"], self.bid)
        self.assertEqual(data["measurement"]["length_m"], 0.3455)
        self.assertEqual(data["measurement"]["breadth_m"], 0.3345)
        self.assertEqual(data["measurement"]["height_m"], 0.2041)
        self.assertEqual(data["status"], "measured")
        self.assertEqual(data["cv_status"], "not_applicable")

    def test_authenticated_block_list_returns_real_approval_values(self):
        """Approval state is serialised by the LIST endpoint (block_to_dict), not
        by block detail - which is where the dashboard reads it from."""
        rows = self.client.get("/api/blocks/", **self.auth(self.officer_token)).json()
        row = next(r for r in rows if r["block_id"] == self.bid)
        self.assertEqual(row["approval_status"], "approved")
        self.assertEqual(row["approved_by"], "rtgs_supervisor")
        self.assertEqual(row["measurement"]["length_m"], 0.3455)

    def test_authenticated_audit_log_read_returns_the_same_records(self):
        response = self.client.get(f"/api/blocks/{self.bid}/audit-logs/",
                                   **self.auth(self.officer_token))
        self.assertEqual(response.status_code, 200)
        actions = {e["action"] for e in response.json()}
        self.assertEqual(actions, {"ar_measurement_submitted", "seigniorage_assessed",
                                   "block_approval_approved"})

    def test_authenticated_pdf_succeeds(self):
        response = self.client.get(f"/api/blocks/{self.bid}/pdf/", **self.auth(self.officer_token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_authenticated_omeps_export_succeeds_with_unchanged_schema(self):
        response = self.client.get(f"/api/export/omeps/{self.bid}/",
                                   **self.auth(self.officer_token))
        self.assertEqual(response.status_code, 200)
        meta = response.data["export_metadata"]
        self.assertEqual(meta["export_schema_version"], "RTGS-OMEPS-READY-2026-09")
        self.assertEqual(meta["integration_status"], "OMEPS_READY_PENDING_OFFICIAL_SCHEMA")
        self.assertEqual(response.data["assessment"]["seigniorage_amount"], 45.85)

    def test_authenticated_analytics_succeeds(self):
        for path in ("/api/analytics/overview/", "/api/analytics/revenue/summary/"):
            with self.subTest(path=path):
                self.assertEqual(
                    self.client.get(path, **self.auth(self.officer_token)).status_code, 200)

    def test_plain_authenticated_user_without_a_role_can_still_read(self):
        """IsAuthenticated, not IsSupervisor: reads must not require a role."""
        for template in SENSITIVE_READS:
            with self.subTest(endpoint=template):
                self.assertEqual(
                    self.client.get(self.url(template), **self.auth(self.plain_token)).status_code,
                    200)

    def test_invalid_and_malformed_tokens_are_rejected(self):
        for header in ({"HTTP_AUTHORIZATION": "Token not-a-real-token"},
                       {"HTTP_AUTHORIZATION": "Token "},
                       {"HTTP_AUTHORIZATION": "Bearer " + self.officer_token},
                       {"HTTP_AUTHORIZATION": self.officer_token}):
                with self.subTest(header=str(header)):
                    self.assertIn(
                        self.client.get(f"/api/blocks/{self.bid}/pdf/", **header).status_code,
                        (401, 403))

    # --- 11: no token in the URL ---------------------------------------------

    def test_pdf_does_not_accept_a_token_in_the_query_string(self):
        """The download must authenticate by header only. A URL-bearing token
        would end up in server logs, browser history and the Referer header."""
        response = self.client.get(f"/api/blocks/{self.bid}/pdf/?token={self.officer_token}")
        self.assertEqual(response.status_code, 401)
        response = self.client.get(
            f"/api/blocks/{self.bid}/pdf/?api_key={self.officer_token}")
        self.assertEqual(response.status_code, 401)

    def test_header_authenticated_pdf_url_contains_no_credentials(self):
        url = f"/api/blocks/{self.bid}/pdf/"
        self.assertNotIn(self.officer_token, url)
        response = self.client.get(url, **self.auth(self.officer_token))
        self.assertEqual(response.status_code, 200)

    # --- 12: no credential leakage -------------------------------------------

    def test_no_password_or_token_leaks_in_any_protected_response(self):
        from django.conf import settings

        for template in SENSITIVE_READS:
            with self.subTest(endpoint=template):
                body = self.client.get(
                    self.url(template), **self.auth(self.officer_token)
                ).content.decode(errors="ignore").lower()
                for marker in ("x-test-pass-123", "password", "mongodb+srv",
                               "secret_key", "api_token", "passwd"):
                    self.assertNotIn(marker, body)
                self.assertNotIn(self.officer_token.lower(), body)
                self.assertNotIn(str(settings.SECRET_KEY).lower(), body)

    def test_audit_logs_expose_no_django_auth_internals(self):
        body = self.client.get(f"/api/blocks/{self.bid}/audit-logs/",
                               **self.auth(self.officer_token)).content.decode().lower()
        for marker in ("pbkdf2", "sha256$", "is_superuser", "last_login", "auth_user"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, body)

    # --- 13, 14: protected reads still mutate nothing -------------------------

    def test_authenticated_pdf_download_creates_no_database_records(self):
        before = self.counts()
        for _ in range(3):
            self.client.get(f"/api/blocks/{self.bid}/pdf/", **self.auth(self.officer_token))
        self.assertEqual(before, self.counts())

    def test_authenticated_omeps_export_creates_no_database_records(self):
        before = self.counts()
        for _ in range(3):
            self.client.get(f"/api/export/omeps/{self.bid}/", **self.auth(self.officer_token))
        self.assertEqual(before, self.counts())

    def counts(self):
        return (self.Block.objects.count(), self.Assessment.objects.count(),
                self.AuditLog.objects.count(), self.Quarry.objects.count(),
                self.Officer.objects.count())

    # --- 15: governance writes still work ------------------------------------

    def test_supervisor_approve_still_works(self):
        block = self.make_assessed_block("GR-APPROVE-6B")
        block.approval_status = "pending"
        block.approved_by = None
        block.save()
        response = self.client.post(
            "/api/blocks/GR-APPROVE-6B/approve/",
            {"approval_status": "approved", "reason": "6B regression check"},
            content_type="application/json", **self.auth(self.sup_token))
        self.assertEqual(response.status_code, 200, response.content[:300])
        block.reload()
        self.assertEqual(block.approval_status, "approved")
        self.assertEqual(block.approved_by, "sup6b")

    def test_supervisor_override_still_works(self):
        self.make_assessed_block("GR-OVERRIDE-6B")
        response = self.client.post(
            "/api/blocks/GR-OVERRIDE-6B/override/",
            {"length_m": 0.40, "breadth_m": 0.34, "height_m": 0.21,
             "reason": "6B regression check"},
            content_type="application/json", **self.auth(self.sup_token))
        self.assertEqual(response.status_code, 200, response.content[:300])

    def test_non_supervisor_still_cannot_approve(self):
        self.make_assessed_block("GR-DENY-6B")
        response = self.client.post(
            "/api/blocks/GR-DENY-6B/approve/",
            {"approval_status": "approved", "reason": "should fail"},
            content_type="application/json", **self.auth(self.officer_token))
        self.assertEqual(response.status_code, 403)

    def test_anonymous_still_cannot_approve(self):
        self.make_assessed_block("GR-ANON-6B")
        response = self.client.post(
            "/api/blocks/GR-ANON-6B/approve/",
            {"approval_status": "approved", "reason": "should fail"},
            content_type="application/json")
        self.assertEqual(response.status_code, 401)

    # --- 16: the FROZEN iOS app must keep working ----------------------------

    def test_frozen_app_ar_submission_still_works_without_credentials(self):
        response = self.client.post("/api/blocks/ar-measure/", {
            "block_id": "GR-FROZEN-AR", "length_m": "0.3455", "breadth_m": "0.3345",
            "height_m": "0.2041", "volume_m3": "0.0236", "quarry_id": "Q-9982",
            "officer_id": "Off", "image": make_image(),
        })
        self.assertEqual(response.status_code, 201, response.content[:400])
        self.assertIsNotNone(self.Block.objects(block_id="GR-FROZEN-AR").first())

    def test_frozen_app_block_creation_still_works_without_credentials(self):
        response = self.client.post("/api/blocks/", {
            "block_id": "GR-FROZEN-CREATE", "quarry_id": None, "status": "pending",
        }, content_type="application/json")
        self.assertIn(response.status_code, (201, 200), response.content[:300])

    def test_every_frozen_app_write_path_is_still_anonymous(self):
        """Guards against a later change closing these and silently bricking the
        field app, which has no authentication contract to update."""
        from django.urls import resolve
        from rest_framework.permissions import AllowAny

        for template in FROZEN_APP_WRITE_PATHS:
            with self.subTest(path=template):
                match = resolve(self.url(template))
                view_cls = match.func.cls
                if "get_permissions" in view_cls.__dict__:
                    continue  # per-method; POST branch asserted by the calls above
                self.assertEqual(list(view_cls.permission_classes), [AllowAny],
                                 f"{template} is no longer anonymous")

    def test_ar_ingestion_values_are_unchanged_by_the_auth_work(self):
        self.client.post("/api/blocks/ar-measure/", {
            "block_id": "GR-FROZEN-VALUES", "length_m": "0.3455", "breadth_m": "0.3345",
            "height_m": "0.2041", "volume_m3": "0.0236", "image": make_image(),
        })
        block = self.Block.objects(block_id="GR-FROZEN-VALUES").first()
        self.assertEqual(block.measurement.volume_m3, 0.3455 * 0.3345 * 0.2041)
        self.assertEqual(block.measurement.measurement_method, "ar")
        self.assertEqual(block.cv_status, "not_applicable")

    # --- 404 / 409 still distinguishable once authenticated ------------------

    def test_authenticated_caller_still_gets_404_for_unknown_block(self):
        for path in (f"/api/blocks/NOPE/", "/api/blocks/NOPE/pdf/",
                     "/api/export/omeps/NOPE/"):
            with self.subTest(path=path):
                self.assertEqual(
                    self.client.get(path, **self.auth(self.officer_token)).status_code, 404)

    def test_unknown_block_is_404_only_after_authentication(self):
        """An anonymous caller must not be able to probe which blocks exist."""
        self.assertEqual(self.client.get("/api/blocks/NOPE/").status_code, 401)
        self.assertEqual(self.client.get(f"/api/blocks/{self.bid}/").status_code, 401)
