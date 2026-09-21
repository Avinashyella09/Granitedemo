"""
Phase 6E: field officer -> Django -> MongoDB -> Supervisor Dashboard.

Proves the two clients share ONE backend and ONE database, and that what the
field app submits is exactly what the dashboard reads back - with anything the
frozen app does not send reported as absent rather than invented.

ISOLATION: mongomock in-memory + temp MEDIA_ROOT. Django auth users live in the
test SQL database. The real Atlas cluster is never touched. Do not run the
legacy blocks/tests.py.

    ./venv/bin/python manage.py test blocks.tests_shared_flow
"""

import io
import shutil
import tempfile
from decimal import Decimal, ROUND_HALF_UP

import mongoengine
import mongomock
from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from blocks.permissions import OFFICER_GROUP, SUPERVISOR_GROUP

_TEMP_MEDIA = tempfile.mkdtemp(prefix="graniteblocks-shared-flow-media-")

SEED_PASSWORD = "seed-test-pw-6e-987"

# What the frozen iOS field app actually sends, and the real values it sent.
FIELD_SUBMISSION = {
    "block_id": "GR-FLOW-1",
    "quarry_id": "Q-9982",
    "officer_id": "Officer",
    "length_m": "0.3719",
    "breadth_m": "0.3556",
    "height_m": "0.2181",
    "volume_m3": "0.0288",
    "gps_latitude": "16.414461",
    "gps_longitude": "80.564009",
}


def make_image():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (48, 48), (90, 90, 90)).save(buf, format="JPEG")
    return SimpleUploadedFile("field.jpg", buf.getvalue(), content_type="image/jpeg")


@override_settings(MEDIA_ROOT=_TEMP_MEDIA)
class SharedFlowTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mongoengine.disconnect(alias="default")
        mongoengine.connect(
            db="granite_blocks_test", alias="default",
            mongo_client_class=mongomock.MongoClient, uuidRepresentation="standard",
        )
        from blocks.models import Assessment, AuditLog, Block, Officer, Quarry

        cls.Block, cls.Assessment = Block, Assessment
        cls.AuditLog, cls.Quarry, cls.Officer = AuditLog, Quarry, Officer

    @classmethod
    def tearDownClass(cls):
        mongoengine.disconnect(alias="default")
        shutil.rmtree(_TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        for model in (self.Block, self.Assessment, self.AuditLog, self.Quarry, self.Officer):
            model.objects.delete()
        User.objects.all().delete()

    # --- helpers --------------------------------------------------------------

    def seed(self, **kwargs):
        call_command("seed_supervisor", password=SEED_PASSWORD, verbosity=0, **kwargs)

    def supervisor_session(self, username="rtgs_supervisor", password=SEED_PASSWORD):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/auth/csrf/")
        response = client.post(
            "/api/auth/login/", {"username": username, "password": password},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 200, response.content[:200])
        return client

    def csrf(self, client):
        return {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}

    def field_submit(self, **overrides):
        """The frozen iOS app: anonymous multipart POST, no credentials."""
        payload = dict(FIELD_SUBMISSION)
        payload.update(overrides)
        payload["image"] = make_image()
        return Client().post("/api/blocks/ar-measure/", payload)

    # --- safety net -----------------------------------------------------------

    def test_isolated_from_real_cluster(self):
        self.assertIn("mongomock", type(mongoengine.get_connection()).__module__.lower())
        self.assertEqual(mongoengine.get_db().name, "granite_blocks_test")

    # ---- 1, 2: the seed command ----------------------------------------------

    def test_seed_creates_exactly_one_supervisor(self):
        self.seed()
        users = User.objects.filter(username="rtgs_supervisor")
        self.assertEqual(users.count(), 1)
        user = users.first()
        self.assertTrue(user.is_active)
        self.assertTrue(user.groups.filter(name=SUPERVISOR_GROUP).exists())

    def test_repeated_seed_does_not_duplicate_the_user(self):
        for _ in range(4):
            self.seed()
        self.assertEqual(User.objects.filter(username="rtgs_supervisor").count(), 1)
        self.assertEqual(User.objects.count(), 1)

    def test_seed_removes_a_conflicting_officer_role(self):
        self.seed()
        user = User.objects.get(username="rtgs_supervisor")
        officer_group, _ = Group.objects.get_or_create(name=OFFICER_GROUP)
        user.groups.add(officer_group)
        self.seed()
        user.refresh_from_db()
        self.assertTrue(user.groups.filter(name=SUPERVISOR_GROUP).exists())
        self.assertFalse(user.groups.filter(name=OFFICER_GROUP).exists())

    def test_seed_reactivates_a_disabled_account(self):
        self.seed()
        User.objects.filter(username="rtgs_supervisor").update(is_active=False)
        self.seed()
        self.assertTrue(User.objects.get(username="rtgs_supervisor").is_active)

    def test_seed_creates_no_field_data(self):
        self.seed()
        self.assertEqual(self.Block.objects.count(), 0)
        self.assertEqual(self.Quarry.objects.count(), 0)
        self.assertEqual(self.Officer.objects.count(), 0)
        self.assertEqual(self.Assessment.objects.count(), 0)
        self.assertEqual(self.AuditLog.objects.count(), 0)

    def test_seed_stores_only_a_hash_never_the_plaintext(self):
        self.seed()
        user = User.objects.get(username="rtgs_supervisor")
        self.assertNotEqual(user.password, SEED_PASSWORD)
        self.assertNotIn(SEED_PASSWORD, user.password)
        self.assertTrue(user.password.startswith("pbkdf2_"))
        self.assertTrue(user.check_password(SEED_PASSWORD))

    # ---- 3, 4, 5, 6: authentication ------------------------------------------

    def test_password_authentication_succeeds(self):
        self.seed()
        client = self.supervisor_session()
        self.assertIn("sessionid", client.cookies)

    def test_wrong_password_returns_401(self):
        self.seed()
        client = Client(enforce_csrf_checks=True)
        client.get("/api/auth/csrf/")
        response = client.post(
            "/api/auth/login/", {"username": "rtgs_supervisor", "password": "not-it"},
            content_type="application/json", **self.csrf(client))
        self.assertEqual(response.status_code, 401)

    def test_auth_me_reports_supervisor(self):
        self.seed()
        response = self.supervisor_session().get("/api/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], "rtgs_supervisor")
        self.assertEqual(response.data["role"], SUPERVISOR_GROUP)
        self.assertTrue(response.data["is_supervisor"])

    # ---- 7, 8, 9, 10, 11, 12: field submission -> dashboard ------------------

    def test_field_submission_creates_a_block_and_dashboard_reads_it_back(self):
        self.seed()
        self.assertEqual(self.field_submit().status_code, 201)
        self.assertEqual(self.Block.objects.count(), 1)

        rows = self.supervisor_session().get("/api/blocks/").data
        row = next(r for r in rows if r["block_id"] == "GR-FLOW-1")

        # 10: the field officer's dimensions, unchanged
        self.assertEqual(row["measurement"]["length_m"], 0.3719)
        self.assertEqual(row["measurement"]["breadth_m"], 0.3556)
        self.assertEqual(row["measurement"]["height_m"], 0.2181)
        self.assertEqual(row["measurement"]["measurement_method"], "ar")
        self.assertEqual(row["cv_status"], "not_applicable")

        # Volume is the SERVER's product, not whatever the client claimed.
        self.assertAlmostEqual(row["measurement"]["volume_m3"],
                               0.3719 * 0.3556 * 0.2181, places=12)

        # 11: real GPS survives the round trip exactly
        self.assertEqual(row["gps_latitude"], 16.414461)
        self.assertEqual(row["gps_longitude"], 80.564009)

        # 12: warnings are real, and gps_incomplete is absent because GPS came
        self.assertIn("quarry_unresolved", row["reference_warnings"])
        self.assertIn("officer_unresolved", row["reference_warnings"])
        self.assertNotIn("gps_incomplete", row["reference_warnings"])

        # The submitted references are preserved verbatim, unresolved.
        self.assertEqual(row["submitted_quarry_id"], "Q-9982")
        self.assertEqual(row["inspecting_officer_id"], "Officer")
        self.assertIsNone(row["quarry_id"])

    def test_missing_gps_is_reported_as_missing_not_invented(self):
        self.seed()
        response = self.field_submit(block_id="GR-FLOW-NOGPS",
                                     gps_latitude="", gps_longitude="")
        self.assertEqual(response.status_code, 201)
        rows = self.supervisor_session().get("/api/blocks/").data
        row = next(r for r in rows if r["block_id"] == "GR-FLOW-NOGPS")
        self.assertIsNone(row["gps_latitude"])
        self.assertIsNone(row["gps_longitude"])
        self.assertIn("gps_incomplete", row["reference_warnings"])

    def test_fields_the_frozen_app_never_sends_are_null_not_fabricated(self):
        self.seed()
        self.field_submit()
        rows = self.supervisor_session().get("/api/blocks/").data
        row = next(r for r in rows if r["block_id"] == "GR-FLOW-1")
        for field in ("device_id", "lighting_condition", "annotated_image_path",
                      "cv_error_message", "override_reason", "approved_by"):
            with self.subTest(field=field):
                self.assertIsNone(row[field], f"{field} was invented")

    def test_dashboard_refresh_creates_no_duplicate_records(self):
        self.seed()
        self.field_submit()
        client = self.supervisor_session()
        before = (self.Block.objects.count(), self.Assessment.objects.count(),
                  self.AuditLog.objects.count())
        for _ in range(5):
            client.get("/api/blocks/")
            client.get("/api/analytics/overview/")
            client.get("/api/analytics/map-data/")
        self.assertEqual(before, (self.Block.objects.count(), self.Assessment.objects.count(),
                                  self.AuditLog.objects.count()))

    # ---- 13, 14: assessment through the dashboard session --------------------

    def test_assessment_and_official_seigniorage_appear_in_the_dashboard(self):
        self.seed()
        self.field_submit()
        client = self.supervisor_session()
        created = client.post(
            "/api/assessments/", {"block_id": "GR-FLOW-1", "granite_category": "Others"},
            content_type="application/json", **self.csrf(client))
        self.assertEqual(created.status_code, 201, created.content[:300])

        # The engine is the only pricing source; verify independently.
        volume = Decimal("0.3719") * Decimal("0.3556") * Decimal("0.2181")
        expected = (volume * Decimal("2.7") * Decimal("720")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.assertEqual(Decimal(str(created.data["indicative_seigniorage"])), expected)
        self.assertEqual(created.data["rate_per_mt"], 720.0)
        self.assertEqual(created.data["gangsaw_classification"], "Within Gangsaw")

        summary = client.get("/api/analytics/revenue/summary/").data
        self.assertEqual(round(summary["total_seigniorage"], 2), float(expected))

    # ---- 15, 16: approval and audit ------------------------------------------

    def test_supervisor_approval_and_audit_actor(self):
        self.seed()
        self.field_submit()
        client = self.supervisor_session()
        client.post("/api/assessments/",
                    {"block_id": "GR-FLOW-1", "granite_category": "Others"},
                    content_type="application/json", **self.csrf(client))

        approved = client.post(
            "/api/blocks/GR-FLOW-1/approve/",
            {"approval_status": "approved", "reason": "shared flow test",
             "actor": "someone-else"},
            content_type="application/json", **self.csrf(client))
        self.assertEqual(approved.status_code, 200, approved.content[:300])

        row = next(r for r in client.get("/api/blocks/").data if r["block_id"] == "GR-FLOW-1")
        self.assertEqual(row["approval_status"], "approved")
        self.assertEqual(row["approved_by"], "rtgs_supervisor")
        self.assertIsNotNone(row["approved_at"])

        actions = {e["action"]: e for e in client.get("/api/blocks/GR-FLOW-1/audit-logs/").data}
        self.assertEqual(set(actions), {"ar_measurement_submitted", "seigniorage_assessed",
                                        "block_approval_approved"})
        self.assertEqual(actions["block_approval_approved"]["actor"], "rtgs_supervisor")
        self.assertNotEqual(actions["block_approval_approved"]["actor"], "someone-else")

    # ---- map: real GPS only --------------------------------------------------

    def test_map_plots_real_gps_and_counts_the_rest(self):
        self.seed()
        self.field_submit()                                   # has GPS
        self.field_submit(block_id="GR-FLOW-NOGPS",
                          gps_latitude="", gps_longitude="")  # has none
        data = self.supervisor_session().get("/api/analytics/map-data/").data

        plotted = {b["block_id"]: b for b in data["blocks"]}
        self.assertEqual(set(plotted), {"GR-FLOW-1"})
        self.assertEqual(plotted["GR-FLOW-1"]["latitude"], 16.414461)
        self.assertEqual(plotted["GR-FLOW-1"]["longitude"], 80.564009)
        self.assertEqual(data["blocks_without_gps"], 1)
        self.assertNotIn("SYNTHETIC", data["label"].upper())

    def test_map_never_invents_coordinates_for_a_quarry(self):
        """The old endpoint hashed the quarry id into a lat/lon when its
        free-text location would not parse."""
        self.seed()
        self.Quarry(id="Q-NOLOC", name="No Location Quarry", location="not coordinates").save()
        data = self.supervisor_session().get("/api/analytics/map-data/").data
        self.assertEqual(data["quarries"], [])

    # ---- 17, 18: report and export -------------------------------------------

    def test_pdf_and_omeps_export_carry_the_same_field_data(self):
        self.seed()
        self.field_submit()
        client = self.supervisor_session()
        client.post("/api/assessments/",
                    {"block_id": "GR-FLOW-1", "granite_category": "Others"},
                    content_type="application/json", **self.csrf(client))

        pdf = client.get("/api/blocks/GR-FLOW-1/pdf/")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

        export = client.get("/api/export/omeps/GR-FLOW-1/").data
        self.assertEqual(export["block"]["block_id"], "GR-FLOW-1")
        self.assertEqual(export["measurement"]["length_m"], 0.3719)
        self.assertEqual(export["measurement"]["gps_latitude"], 16.414461)
        self.assertEqual(export["measurement"]["gps_status"], "captured")
        self.assertEqual(export["block"]["submitted_quarry_id"], "Q-9982")
        self.assertEqual(export["assessment"]["rate_per_mt"], 720.0)
        self.assertEqual(export["export_metadata"]["integration_status"],
                         "OMEPS_READY_PENDING_OFFICIAL_SCHEMA")

    # ---- 20: nothing secret leaks --------------------------------------------

    def test_no_password_or_token_leaks_through_any_dashboard_surface(self):
        self.seed()
        self.field_submit()
        client = self.supervisor_session()
        client.post("/api/assessments/",
                    {"block_id": "GR-FLOW-1", "granite_category": "Others"},
                    content_type="application/json", **self.csrf(client))

        for path in ("/api/auth/me/", "/api/blocks/", "/api/assessments/",
                     "/api/blocks/GR-FLOW-1/audit-logs/", "/api/export/omeps/GR-FLOW-1/",
                     "/api/analytics/overview/", "/api/analytics/map-data/"):
            with self.subTest(path=path):
                body = client.get(path).content.decode(errors="ignore")
                self.assertNotIn(SEED_PASSWORD, body)
                lowered = body.lower()
                for marker in ("pbkdf2", "sha256$", "password", "mongodb+srv", "sessionid"):
                    self.assertNotIn(marker, lowered, f"{marker} leaked from {path}")
