"""
Phase 6E: Supervisor Dashboard username/password session authentication.

The dashboard no longer asks a human to paste an API token. It signs in against
the SAME Django auth stack and the SAME User records, and gets a session cookie
the browser manages. Token authentication remains for API clients.

ISOLATION: mongomock in-memory + temp MEDIA_ROOT. Django auth users live in the
test SQL database. The real Atlas cluster is never touched. Do not run the
legacy blocks/tests.py.

    ./venv/bin/python manage.py test blocks.tests_session_auth
"""

import io
import shutil
import tempfile

import mongoengine
import mongomock
from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from rest_framework.authtoken.models import Token

from blocks import seigniorage
from blocks.permissions import OFFICER_GROUP, SUPERVISOR_GROUP

_TEMP_MEDIA = tempfile.mkdtemp(prefix="graniteblocks-session-auth-media-")

PASSWORD = "x-test-pass-6e-123"


def make_image():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (48, 48), (100, 100, 100)).save(buf, format="JPEG")
    return SimpleUploadedFile("photo.jpg", buf.getvalue(), content_type="image/jpeg")


@override_settings(MEDIA_ROOT=_TEMP_MEDIA)
class SessionAuthTests(TestCase):
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
        self.supervisor = self.make_user("sup6e", SUPERVISOR_GROUP)
        self.officer = self.make_user("off6e", OFFICER_GROUP)
        self.inactive = self.make_user("dormant6e", SUPERVISOR_GROUP, active=False)
        self.make_block("GR-6E")

    def make_user(self, username, role, active=True):
        user = User.objects.create_user(username=username, password=PASSWORD)
        if role:
            group, _ = Group.objects.get_or_create(name=role)
            user.groups.add(group)
        if not active:
            user.is_active = False
            user.save()
        return user

    def make_block(self, block_id, assess=True):
        block = self.Block(block_id=block_id, status="measured", cv_status="not_applicable")
        block.measurement = self.Measurement(
            length_m=0.3719, breadth_m=0.3556, height_m=0.2181,
            volume_m3=0.3719 * 0.3556 * 0.2181, measurement_method="ar")
        block.save()
        if assess:
            r = seigniorage.calculate(0.3719, 0.3556, 0.2181, "Others", 2.7)
            self.Assessment(
                block=block, granite_category=r["granite_category"],
                gangsaw_classification=r["gangsaw_classification"],
                volume_m3=r["volume_m3"], weight_mt=r["tonnage_mt"],
                rate_per_mt=r["rate_per_mt"],
                indicative_seigniorage=r["seigniorage_amount"],
                density_mt_per_m3=r["density_mt_per_m3"]).save()
            self.AuditLog(block=block, block_id_snapshot=block_id,
                          action="ar_measurement_submitted", actor="Officer",
                          details="AR inspection").save()
        return block

    # --- helpers --------------------------------------------------------------

    def session_client(self, username=None, password=PASSWORD):
        """A client that has completed the real CSRF + login flow."""
        client = Client(enforce_csrf_checks=True)
        client.get("/api/auth/csrf/")
        if username:
            response = self.login(client, username, password)
            self.assertEqual(response.status_code, 200, response.content[:200])
        return client

    def login(self, client, username, password=PASSWORD):
        return client.post(
            "/api/auth/login/",
            {"username": username, "password": password},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )

    # --- safety net -----------------------------------------------------------

    def test_isolated_from_real_cluster(self):
        self.assertIn("mongomock", type(mongoengine.get_connection()).__module__.lower())
        self.assertEqual(mongoengine.get_db().name, "granite_blocks_test")

    # ---- 1-4: login ----------------------------------------------------------

    def test_valid_username_password_login_succeeds(self):
        client = self.session_client()
        response = self.login(client, "sup6e")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], "sup6e")
        self.assertEqual(response.data["role"], SUPERVISOR_GROUP)
        self.assertTrue(response.data["is_supervisor"])
        self.assertEqual(response.data["groups"], [SUPERVISOR_GROUP])

    def test_invalid_password_returns_401(self):
        client = self.session_client()
        response = self.login(client, "sup6e", "wrong-password")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], "invalid_credentials")

    def test_unknown_user_and_wrong_password_are_indistinguishable(self):
        """A different message would let an attacker enumerate usernames."""
        client = self.session_client()
        wrong_pw = self.login(client, "sup6e", "wrong-password")
        no_user = self.login(client, "no-such-person", "wrong-password")
        self.assertEqual(wrong_pw.status_code, no_user.status_code)
        self.assertEqual(str(wrong_pw.data["error"]), str(no_user.data["error"]))

    def test_inactive_user_is_rejected(self):
        client = self.session_client()
        response = self.login(client, "dormant6e")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], "invalid_credentials")

    def test_missing_credentials_rejected_without_touching_auth(self):
        client = self.session_client()
        for body in ({"username": "sup6e"}, {"password": PASSWORD}, {}):
            with self.subTest(body=str(body)):
                response = client.post(
                    "/api/auth/login/", body, content_type="application/json",
                    HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["code"], "missing_credentials")

    def test_login_creates_a_session(self):
        client = self.session_client("sup6e")
        self.assertIn("sessionid", client.cookies)
        self.assertTrue(client.cookies["sessionid"].value)

    def test_session_cookie_is_http_only(self):
        """The dashboard must never be able to read the session from JS."""
        client = self.session_client("sup6e")
        self.assertTrue(client.cookies["sessionid"]["httponly"])

    # ---- 5, 6: identity and logout -------------------------------------------

    def test_auth_me_works_with_the_session(self):
        client = self.session_client("sup6e")
        response = client.get("/api/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], "sup6e")
        self.assertTrue(response.data["is_supervisor"])

    def test_logout_destroys_the_session(self):
        client = self.session_client("sup6e")
        self.assertEqual(client.get("/api/auth/me/").status_code, 200)
        response = client.post(
            "/api/auth/logout/", {}, content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get("/api/auth/me/").status_code, 401)

    def test_logout_requires_authentication(self):
        client = self.session_client()
        response = client.post(
            "/api/auth/logout/", {}, content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 401)

    # ---- CSRF is enforced, not bypassed --------------------------------------

    def test_login_without_a_csrf_token_is_refused(self):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/auth/csrf/")
        response = client.post(
            "/api/auth/login/", {"username": "sup6e", "password": PASSWORD},
            content_type="application/json")  # no X-CSRFToken
        self.assertEqual(response.status_code, 403, "login endpoint is not CSRF protected")

    def test_session_write_without_csrf_is_refused(self):
        client = self.session_client("sup6e")
        response = client.post(
            "/api/blocks/GR-6E/approve/",
            {"approval_status": "approved", "reason": "no csrf"},
            content_type="application/json")  # no X-CSRFToken
        self.assertEqual(response.status_code, 403)
        self.Block.objects(block_id="GR-6E").first().reload()
        self.assertEqual(self.Block.objects(block_id="GR-6E").first().approval_status, "pending")

    def test_csrf_endpoint_sets_the_cookie(self):
        client = Client(enforce_csrf_checks=True)
        response = client.get("/api/auth/csrf/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", client.cookies)
        # Readable by JS on purpose - that is how the client echoes it back.
        self.assertFalse(client.cookies["csrftoken"]["httponly"])

    # ---- 7, 8: protected reads ------------------------------------------------

    def test_anonymous_protected_read_returns_401(self):
        anon = Client(enforce_csrf_checks=True)
        for path in ("/api/blocks/", "/api/blocks/GR-6E/audit-logs/",
                     "/api/analytics/overview/", "/api/export/omeps/GR-6E/"):
            with self.subTest(path=path):
                self.assertEqual(anon.get(path).status_code, 401)

    def test_session_authenticated_reads_succeed(self):
        client = self.session_client("off6e")
        for path in ("/api/blocks/", "/api/blocks/GR-6E/", "/api/blocks/GR-6E/audit-logs/",
                     "/api/assessments/", "/api/analytics/overview/",
                     "/api/analytics/revenue/summary/"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)

    # ---- 14, 15, 16: report, export, audit over a session --------------------

    def test_pdf_works_with_the_session(self):
        client = self.session_client("sup6e")
        response = client.get("/api/blocks/GR-6E/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_omeps_export_works_with_the_session(self):
        client = self.session_client("sup6e")
        response = client.get("/api/export/omeps/GR-6E/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["export_metadata"]["integration_status"],
                         "OMEPS_READY_PENDING_OFFICIAL_SCHEMA")

    def test_audit_reads_work_with_the_session(self):
        client = self.session_client("off6e")
        response = client.get("/api/blocks/GR-6E/audit-logs/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(len(response.data) >= 1)

    # ---- 9, 10, 11, 12: governance over a session -----------------------------

    def test_supervisor_session_can_approve(self):
        client = self.session_client("sup6e")
        response = client.post(
            "/api/blocks/GR-6E/approve/",
            {"approval_status": "approved", "reason": "6E session approval"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 200, response.content[:300])
        block = self.Block.objects(block_id="GR-6E").first()
        self.assertEqual(block.approval_status, "approved")
        self.assertEqual(block.approved_by, "sup6e")
        self.assertIsNotNone(block.approved_at)

    def test_officer_session_cannot_approve(self):
        client = self.session_client("off6e")
        response = client.post(
            "/api/blocks/GR-6E/approve/",
            {"approval_status": "approved", "reason": "should fail"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.Block.objects(block_id="GR-6E").first().approval_status, "pending")

    def test_approval_audit_actor_comes_from_the_session(self):
        client = self.session_client("sup6e")
        client.post(
            "/api/blocks/GR-6E/approve/",
            {"approval_status": "approved", "reason": "6E", "actor": "somebody-else"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        entry = self.AuditLog.objects(action__startswith="block_approval").first()
        self.assertEqual(entry.actor, "sup6e")
        self.assertNotEqual(entry.actor, "somebody-else")

    def test_client_actor_cannot_override_the_session_identity(self):
        client = self.session_client("sup6e")
        self.make_block("GR-6E-2", assess=False)   # must have no assessment yet
        response = client.post(
            "/api/assessments/",
            {"block_id": "GR-6E-2", "granite_category": "Others", "actor": "rtgs_supervisor"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 201, response.content[:300])
        entry = self.AuditLog.objects(action="seigniorage_assessed").first()
        self.assertEqual(entry.actor, "sup6e")
        self.assertNotEqual(entry.actor, "rtgs_supervisor")

    # ---- 13: assessment over a session ---------------------------------------

    def test_assessment_post_works_with_the_session(self):
        client = self.session_client("sup6e")
        self.make_block("GR-6E-3", assess=False)
        response = client.post(
            "/api/assessments/", {"block_id": "GR-6E-3", "granite_category": "Others"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertEqual(response.data["rate_per_mt"], 720.0)
        self.assertEqual(response.data["gangsaw_classification"], "Within Gangsaw")
        self.assertEqual(response.data["classification_source"], "server")

    # ---- 17, 18, 19, 20: nothing secret leaves the backend --------------------

    def test_login_returns_no_password_hash_or_token(self):
        client = self.session_client()
        body = self.login(client, "sup6e").content.decode().lower()
        for marker in ("password", "pbkdf2", "sha256$", "token", "hash",
                       "is_superuser", "last_login", "email"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, body)

    def test_auth_me_returns_no_sensitive_fields(self):
        client = self.session_client("sup6e")
        body = client.get("/api/auth/me/").content.decode().lower()
        for marker in ("password", "pbkdf2", "token", "is_superuser", "last_login"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, body)

    def test_login_response_contract_matches_auth_me(self):
        client = self.session_client()
        login_body = self.login(client, "sup6e").data
        me_body = client.get("/api/auth/me/").data
        self.assertEqual(set(login_body.keys()), set(me_body.keys()))
        self.assertEqual(login_body, me_body)

    def test_password_is_never_accepted_from_the_url(self):
        """A GET with credentials in the query string must not authenticate."""
        client = Client(enforce_csrf_checks=True)
        response = client.get(f"/api/auth/login/?username=sup6e&password={PASSWORD}")
        self.assertIn(response.status_code, (401, 403, 405))
        self.assertNotIn("sessionid", client.cookies)

    def test_no_issued_token_is_created_by_logging_in(self):
        before = Token.objects.count()
        self.session_client("sup6e")
        self.assertEqual(Token.objects.count(), before,
                         "session login must not mint an API token")

    # ---- 22: token authentication still works --------------------------------

    def test_token_authentication_still_works_alongside_sessions(self):
        token = Token.objects.get_or_create(user=self.supervisor)[0].key
        client = Client(enforce_csrf_checks=True)
        response = client.get("/api/blocks/", HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(response.status_code, 200)

    def test_token_client_can_still_approve_without_csrf(self):
        """Token auth is not session auth, so CSRF does not apply to it - other
        API clients must keep working exactly as before."""
        token = Token.objects.get_or_create(user=self.supervisor)[0].key
        client = Client(enforce_csrf_checks=True)
        response = client.post(
            "/api/blocks/GR-6E/approve/",
            {"approval_status": "approved", "reason": "token path"},
            content_type="application/json", HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(response.status_code, 200, response.content[:300])

    # ---- 23: the frozen iOS app is untouched ---------------------------------

    def test_frozen_ios_anonymous_paths_are_unchanged(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post("/api/blocks/ar-measure/", {
            "image": make_image(), "block_id": "GR-6E-FROZEN", "quarry_id": "Q-9982",
            "officer_id": "Officer", "length_m": "0.3719", "breadth_m": "0.3556",
            "height_m": "0.2181", "volume_m3": "0.0288",
        })
        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertIsNotNone(self.Block.objects(block_id="GR-6E-FROZEN").first())
