"""
Phase 5A: governance, authentication and audit-integrity tests.

ISOLATION: mongoengine is rebound to in-memory mongomock; the real Atlas cluster
is never reached. Django auth users live in the test SQLite database that
TestCase creates and rolls back. The legacy blocks/tests.py writes to the live
database and rmtree's the real MEDIA_ROOT - do not run it.

    ./venv/bin/python manage.py test blocks.tests_security
"""

import io

import mongoengine
import mongomock
from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.authtoken.models import Token

from blocks.permissions import OFFICER_GROUP, SUPERVISOR_GROUP


def make_image():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (48, 48), (110, 110, 110)).save(buf, format="JPEG")
    return SimpleUploadedFile("photo.jpg", buf.getvalue(), content_type="image/jpeg")


class GovernanceSecurityTests(TestCase):
    """TestCase (not SimpleTestCase): Django auth users need the test SQL DB."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mongoengine.disconnect(alias="default")
        mongoengine.connect(
            db="granite_blocks_test",
            alias="default",
            mongo_client_class=mongomock.MongoClient,
            uuidRepresentation="standard",
        )
        from blocks.models import Assessment, AuditLog, Block, Measurement

        cls.Block, cls.Measurement = Block, Measurement
        cls.Assessment, cls.AuditLog = Assessment, AuditLog

    @classmethod
    def tearDownClass(cls):
        mongoengine.disconnect(alias="default")
        super().tearDownClass()

    def setUp(self):
        for model in (self.Block, self.Assessment, self.AuditLog):
            model.objects.delete()
        self.supervisor, self.sup_token = self.make_user("sup1", SUPERVISOR_GROUP)
        self.officer, self.off_token = self.make_user("off1", OFFICER_GROUP)

    def make_user(self, username, role):
        user = User.objects.create_user(username=username, password="x-test-pass-123")
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)
        token, _ = Token.objects.get_or_create(user=user)
        return user, token.key

    def auth(self, token):
        return {"HTTP_AUTHORIZATION": f"Token {token}"}

    def make_block(self, block_id="GR-SEC-1", assess=True):
        block = self.Block(block_id=block_id, status="measured")
        block.measurement = self.Measurement(
            length_m=3.0, breadth_m=2.0, height_m=1.0, volume_m3=6.0,
            measurement_method="ar",
        )
        block.save()
        # Phase 6G: a block cannot be APPROVED until it has been assessed. These
        # Phase 5A tests are about WHO may approve and WHAT gets audited, not
        # about the workflow order, so their fixtures now satisfy the
        # prerequisite. No assertion below changed - only the fixture.
        if assess:
            from blocks import seigniorage

            result = seigniorage.calculate(3.0, 2.0, 1.0, "Others", 2.7)
            self.Assessment(
                block=block,
                granite_category=result["granite_category"],
                gangsaw_classification=result["gangsaw_classification"],
                volume_m3=result["volume_m3"], weight_mt=result["tonnage_mt"],
                rate_per_mt=result["rate_per_mt"],
                indicative_seigniorage=result["seigniorage_amount"],
                density_mt_per_m3=result["density_mt_per_m3"],
            ).save()
        return block

    def approve(self, block_id="GR-SEC-1", token=None, **extra):
        body = {"approval_status": "approved"}
        body.update(extra)
        return self.client.post(
            f"/api/blocks/{block_id}/approve/", body,
            content_type="application/json", **(self.auth(token) if token else {}),
        )

    def override(self, block_id="GR-SEC-1", token=None, **extra):
        body = {"length_m": 4.0, "breadth_m": 2.0, "height_m": 1.0, "reason": "re-measured"}
        body.update(extra)
        return self.client.post(
            f"/api/blocks/{block_id}/override/", body,
            content_type="application/json", **(self.auth(token) if token else {}),
        )

    # ---- safety net ---------------------------------------------------------

    def test_not_pointed_at_real_cluster(self):
        self.assertIn("mongomock", type(mongoengine.get_connection()).__module__.lower())
        self.assertEqual(mongoengine.get_db().name, "granite_blocks_test")

    # ---- 1-3: authentication and roles --------------------------------------

    def test_unauthenticated_approval_rejected(self):
        self.make_block()
        response = self.approve()
        self.assertIn(response.status_code, (401, 403), response.content[:200])
        self.assertEqual(self.Block.objects.first().approval_status, "pending")

    def test_officer_cannot_approve(self):
        self.make_block()
        response = self.approve(token=self.off_token)
        self.assertEqual(response.status_code, 403, response.content[:200])
        self.assertEqual(self.Block.objects.first().approval_status, "pending")

    def test_supervisor_can_approve(self):
        self.make_block()
        response = self.approve(token=self.sup_token)
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(self.Block.objects.first().approval_status, "approved")

    def test_unauthenticated_override_rejected(self):
        block = self.make_block()
        response = self.override()
        self.assertIn(response.status_code, (401, 403))
        self.assertAlmostEqual(self.Block.objects.first().measurement.length_m, 3.0)

    def test_officer_cannot_override(self):
        self.make_block()
        response = self.override(token=self.off_token)
        self.assertEqual(response.status_code, 403)
        self.assertAlmostEqual(self.Block.objects.first().measurement.length_m, 3.0)

    def test_supervisor_can_override(self):
        self.make_block()
        response = self.override(token=self.sup_token)
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertAlmostEqual(self.Block.objects.first().measurement.length_m, 4.0)

    # ---- 4: the actor-spoofing hole -----------------------------------------

    def test_client_supplied_actor_cannot_control_audit(self):
        """The core fix: anyone could previously approve a block and sign it
        with any name via a free-text 'actor' field."""
        self.make_block()
        self.approve(token=self.sup_token, actor="Director General")
        entry = self.AuditLog.objects(action__startswith="block_approval").first()
        self.assertEqual(entry.actor, "sup1")
        self.assertNotEqual(entry.actor, "Director General")

    def test_approved_by_uses_authenticated_identity(self):
        self.make_block()
        self.approve(token=self.sup_token, actor="Someone Else")
        self.assertEqual(self.Block.objects.first().approved_by, "sup1")

    def test_override_actor_cannot_be_spoofed(self):
        self.make_block()
        self.override(token=self.sup_token, actor="Supervisor-1")
        entry = self.AuditLog.objects(action="manual_override").first()
        self.assertEqual(entry.actor, "sup1")

    # ---- 5-6: governance events ---------------------------------------------

    def test_approval_creates_audit_event(self):
        self.make_block()
        self.approve(token=self.sup_token)
        logs = self.AuditLog.objects(action__startswith="block_approval")
        self.assertEqual(logs.count(), 1)
        self.assertIn("GR-SEC-1", logs.first().details or logs.first().block_id_snapshot)

    def test_override_creates_audit_event(self):
        self.make_block()
        self.override(token=self.sup_token)
        self.assertEqual(self.AuditLog.objects(action="manual_override").count(), 1)

    def test_rejection_is_recorded_with_real_actor(self):
        self.make_block()
        response = self.approve(token=self.sup_token, approval_status="rejected", reason="bad photo")
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertEqual(self.Block.objects.first().approval_status, "rejected")
        self.assertEqual(self.AuditLog.objects.first().actor, "sup1")

    # ---- 7-8: existing audits still work ------------------------------------

    def test_seigniorage_assessment_audit_still_works(self):
        # Authenticated since Phase 6C: POST /api/assessments/ is the
        # money-determining write and no longer accepts anonymous callers.
        # assess=False: this test creates the assessment itself.
        self.make_block(assess=False)
        response = self.client.post(
            "/api/assessments/",
            {"block_id": "GR-SEC-1", "granite_category": "Black Galaxy"},
            content_type="application/json", **self.auth(self.off_token),
        )
        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertEqual(self.AuditLog.objects(action="seigniorage_assessed").count(), 1)

    def test_ar_measurement_audit_still_works(self):
        response = self.client.post("/api/blocks/ar-measure/", {
            "image": make_image(), "block_id": "GR-SEC-AR", "quarry_id": "Q-X",
            "officer_id": "OFF-X", "length_m": "2.0", "breadth_m": "1.5",
            "height_m": "1.0", "volume_m3": "3.0",
        }, format="multipart")
        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertEqual(self.AuditLog.objects(action="ar_measurement_submitted").count(), 1)

    # ---- 9: audit survives block deletion ------------------------------------

    def test_deleting_block_does_not_erase_its_audit_trail(self):
        """Previously reverse_delete_rule=CASCADE destroyed the audit trail
        along with the block it documented."""
        block = self.make_block()
        self.approve(token=self.sup_token)
        self.assertEqual(self.AuditLog.objects.count(), 1)

        block.delete()

        self.assertEqual(self.Block.objects.count(), 0)
        self.assertEqual(self.AuditLog.objects.count(), 1, "audit trail was destroyed with the block")
        entry = self.AuditLog.objects.first()
        self.assertEqual(entry.block_id_snapshot, "GR-SEC-1",
                         "block identity not preserved after deletion")
        self.assertEqual(entry.actor, "sup1")

    # ---- 10-11: receipt ------------------------------------------------------

    def test_ar_submission_returns_receipt_id(self):
        response = self.client.post("/api/blocks/ar-measure/", {
            "image": make_image(), "block_id": "GR-RCPT", "quarry_id": "Q-X",
            "officer_id": "OFF-X", "length_m": "2.0", "breadth_m": "1.5",
            "height_m": "1.0", "volume_m3": "3.0",
        }, format="multipart")
        self.assertEqual(response.status_code, 201)
        self.assertIn("receipt_id", response.data)
        self.assertTrue(response.data["receipt_id"])

    def test_receipt_id_resolves_to_a_real_audit_record(self):
        """Not a display-only random string - it must be a persisted id."""
        response = self.client.post("/api/blocks/ar-measure/", {
            "image": make_image(), "block_id": "GR-RCPT2", "quarry_id": "Q-X",
            "officer_id": "OFF-X", "length_m": "2.0", "breadth_m": "1.5",
            "height_m": "1.0", "volume_m3": "3.0",
        }, format="multipart")
        receipt = response.data["receipt_id"]
        entry = self.AuditLog.objects(id=receipt).first()
        self.assertIsNotNone(entry, "receipt_id does not resolve to a stored AuditLog")
        self.assertEqual(entry.action, "ar_measurement_submitted")
        self.assertEqual(entry.block_id_snapshot, "GR-RCPT2")

    # ---- 12: read access -----------------------------------------------------

    def test_dashboard_reads_now_require_authentication(self):
        """SUPERSEDES the Phase 5A expectation that these reads stay anonymous.

        Phase 5A deliberately gated only WRITES and left the read surface open,
        and this test asserted that. Phase 6B closed the read surface: the block
        register and the analytics endpoints disclose measurements, approval
        state and revenue, so they now require authentication. The old
        expectation is not 'broken' - it was withdrawn on purpose, and the
        replacement pins the new policy in both directions.
        """
        self.make_block()
        for path in ("/api/blocks/", "/api/analytics/overview/",
                     "/api/analytics/revenue/summary/", "/api/analytics/alerts/"):
            with self.subTest(path=path, caller="anonymous"):
                self.assertEqual(self.client.get(path).status_code, 401)
            with self.subTest(path=path, caller="authenticated"):
                self.assertEqual(
                    self.client.get(path, **self.auth(self.off_token)).status_code, 200)

    # ---- 13-14: existing protections intact ----------------------------------

    def test_duplicate_ar_protection_still_works(self):
        payload = lambda: {
            "image": make_image(), "block_id": "GR-DUP", "quarry_id": "Q-X",
            "officer_id": "OFF-X", "length_m": "2.0", "breadth_m": "1.5",
            "height_m": "1.0", "volume_m3": "3.0",
        }
        self.assertEqual(self.client.post("/api/blocks/ar-measure/", payload(), format="multipart").status_code, 201)
        second = self.client.post("/api/blocks/ar-measure/", payload(), format="multipart")
        self.assertEqual(second.status_code, 409)
        self.assertEqual(self.Block.objects(block_id="GR-DUP").count(), 1)

    def test_duplicate_assessment_protection_still_works(self):
        # Authenticated since Phase 6C (see above).
        # assess=False: this test creates the first assessment itself.
        self.make_block(assess=False)
        body = {"block_id": "GR-SEC-1", "granite_category": "Black Galaxy"}
        auth = self.auth(self.off_token)
        self.assertEqual(self.client.post(
            "/api/assessments/", body, content_type="application/json", **auth
        ).status_code, 201)
        second = self.client.post(
            "/api/assessments/", body, content_type="application/json", **auth)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(self.Assessment.objects.count(), 1)


class AuthMeEndpointTests(TestCase):
    """GET /api/auth/me/ - the identity endpoint the dashboard validates against."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mongoengine.disconnect(alias="default")
        mongoengine.connect(
            db="granite_blocks_test", alias="default",
            mongo_client_class=mongomock.MongoClient, uuidRepresentation="standard",
        )

    @classmethod
    def tearDownClass(cls):
        mongoengine.disconnect(alias="default")
        super().tearDownClass()

    def make_user(self, username, role):
        user = User.objects.create_user(username=username, password="x-test-pass-123")
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)
        return Token.objects.get_or_create(user=user)[0].key

    def me(self, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Token {token}"} if token else {}
        return self.client.get("/api/auth/me/", **headers)

    def test_anonymous_rejected(self):
        self.assertIn(self.me().status_code, (401, 403))

    def test_invalid_token_rejected(self):
        self.assertIn(self.me("not-a-real-token").status_code, (401, 403))

    def test_supervisor_token_accepted_with_role(self):
        response = self.me(self.make_user("sup2", SUPERVISOR_GROUP))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], "sup2")
        self.assertEqual(response.data["role"], SUPERVISOR_GROUP)
        self.assertTrue(response.data["is_supervisor"])

    def test_officer_token_accepted_but_not_supervisor(self):
        response = self.me(self.make_user("off2", OFFICER_GROUP))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["role"], OFFICER_GROUP)
        self.assertFalse(response.data["is_supervisor"])

    def test_no_sensitive_user_data_exposed(self):
        response = self.me(self.make_user("sup3", SUPERVISOR_GROUP))
        body = str(response.data).lower()
        for leak in ("password", "pbkdf2", "email", "last_login", "is_staff"):
            self.assertNotIn(leak, body, f"{leak} exposed by /api/auth/me/")


class AssessmentWriteSurfaceTests(TestCase):
    """Phase 6C: POST /api/assessments/ is the money-determining write.

    It fixes the tonnage, the official rate and the payable seigniorage for a
    block, and it was the last anonymous financial write in the system. These
    tests pin that it now requires authentication, that an authenticated caller
    still gets the existing behaviour, and that nothing a client sends - actor,
    rate, amount or classification - can influence what is stored.

    ISOLATION: mongomock in-memory; Django auth users live in the test SQL DB.
    The real Atlas cluster is never touched. Do not run blocks/tests.py.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        mongoengine.disconnect(alias="default")
        mongoengine.connect(
            db="granite_blocks_test", alias="default",
            mongo_client_class=mongomock.MongoClient, uuidRepresentation="standard",
        )
        from blocks.models import Assessment, AuditLog, Block, Measurement

        cls.Block, cls.Measurement = Block, Measurement
        cls.Assessment, cls.AuditLog = Assessment, AuditLog

    @classmethod
    def tearDownClass(cls):
        mongoengine.disconnect(alias="default")
        super().tearDownClass()

    def setUp(self):
        for model in (self.Block, self.Assessment, self.AuditLog):
            model.objects.delete()
        self.supervisor, self.sup_token = self.make_user("sup6c", SUPERVISOR_GROUP)
        self.officer, self.off_token = self.make_user("off6c", OFFICER_GROUP)
        self.plain, self.plain_token = self.make_user("plain6c", None)
        self.make_block("GR-6C")

    def make_user(self, username, role):
        user = User.objects.create_user(username=username, password="x-test-pass-123")
        if role:
            group, _ = Group.objects.get_or_create(name=role)
            user.groups.add(group)
        return user, Token.objects.get_or_create(user=user)[0].key

    def auth(self, token):
        return {"HTTP_AUTHORIZATION": f"Token {token}"}

    def make_block(self, block_id):
        """Real block2 dimensions, so the expected money is the real money."""
        block = self.Block(block_id=block_id, status="measured")
        block.measurement = self.Measurement(
            length_m=0.3455, breadth_m=0.3345, height_m=0.2041,
            volume_m3=0.3455 * 0.3345 * 0.2041, measurement_method="ar",
        )
        block.save()
        return block

    def payload(self, block_id="GR-6C", **overrides):
        """Exactly what the dashboard's createAssessment sends."""
        body = {
            "block_id": block_id,
            "granite_category": "Others",
            "gangsaw_classification": "Within Gangsaw",
            "density": 2.7,
        }
        body.update(overrides)
        return body

    def post(self, token=None, **kwargs):
        headers = self.auth(token) if token else {}
        return self.client.post(
            "/api/assessments/", self.payload(**kwargs),
            content_type="application/json", **headers)

    # ---- 1: anonymous is refused --------------------------------------------

    def test_anonymous_assessment_post_returns_401(self):
        response = self.post()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.Assessment.objects.count(), 0,
                         "an anonymous request created a financial record")

    def test_anonymous_post_writes_no_audit_entry(self):
        self.post()
        self.assertEqual(self.AuditLog.objects.count(), 0)

    def test_invalid_token_cannot_create_an_assessment(self):
        response = self.client.post(
            "/api/assessments/", self.payload(), content_type="application/json",
            HTTP_AUTHORIZATION="Token not-a-real-token")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(self.Assessment.objects.count(), 0)

    # ---- 2, 3, 4: authenticated callers succeed -----------------------------

    def test_authenticated_assessment_post_succeeds(self):
        response = self.post(self.off_token)
        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertEqual(self.Assessment.objects.count(), 1)

    def test_officer_may_create_an_assessment(self):
        """Chosen policy: IsAuthenticated, not IsSupervisor. Running the official
        calculation is a routine step, not a governance decision."""
        self.assertEqual(self.post(self.off_token).status_code, 201)

    def test_supervisor_may_create_an_assessment(self):
        self.assertEqual(self.post(self.sup_token).status_code, 201)

    def test_authenticated_user_without_a_role_may_create_an_assessment(self):
        self.assertEqual(self.post(self.plain_token).status_code, 201)

    # ---- 5: the client cannot choose the audit actor ------------------------

    def test_client_supplied_actor_cannot_control_the_audit_actor(self):
        response = self.post(self.off_token, actor="rtgs_supervisor")
        self.assertEqual(response.status_code, 201)
        entry = self.AuditLog.objects(action="seigniorage_assessed").first()
        self.assertEqual(entry.actor, "off6c",
                         "a client-supplied actor reached the audit trail")
        self.assertNotEqual(entry.actor, "rtgs_supervisor")

    def test_audit_actor_is_the_authenticated_identity_for_every_caller(self):
        for token, expected in ((self.off_token, "off6c"), (self.sup_token, "sup6c")):
            with self.subTest(actor=expected):
                self.Assessment.objects.delete()
                self.AuditLog.objects.delete()
                self.post(token, actor="somebody-else", username="spoofed")
                entry = self.AuditLog.objects(action="seigniorage_assessed").first()
                self.assertEqual(entry.actor, expected)

    def test_assessment_write_is_audited(self):
        self.post(self.off_token)
        entry = self.AuditLog.objects(action="seigniorage_assessed").first()
        self.assertIsNotNone(entry, "the money-determining write was not audited")
        self.assertEqual(entry.block_id_snapshot, "GR-6C")
        self.assertIn("amount=45.85INR", entry.details)

    # ---- 6: duplicate protection survives -----------------------------------

    def test_duplicate_assessment_protection_still_works(self):
        self.assertEqual(self.post(self.off_token).status_code, 201)
        second = self.post(self.off_token)
        self.assertEqual(second.status_code, 400)
        self.assertIn("already exists", str(second.data))
        self.assertEqual(self.Assessment.objects.count(), 1)

    def test_duplicate_attempt_by_a_different_user_is_still_refused(self):
        self.post(self.off_token)
        self.assertEqual(self.post(self.sup_token).status_code, 400)
        self.assertEqual(self.Assessment.objects.count(), 1)

    # ---- 7: the server owns the calculation ---------------------------------

    def test_seigniorage_calculation_remains_server_authoritative(self):
        response = self.post(self.off_token)
        data = response.data
        self.assertEqual(data["rate_per_mt"], 720.0)
        self.assertEqual(data["indicative_seigniorage"], 45.85)
        self.assertEqual(data["gangsaw_classification"], "Within Gangsaw")
        self.assertEqual(data["granite_category"], "Others")
        self.assertEqual(data["classification_source"], "server")
        self.assertTrue(data["is_official"])

        stored = self.Assessment.objects.first()
        self.assertEqual(stored.rate_per_mt, 720.0)
        self.assertEqual(stored.indicative_seigniorage, 45.85)

    # ---- 8, 9, 10: injected values are ignored ------------------------------

    def test_client_supplied_rate_is_ignored(self):
        self.post(self.off_token, rate_per_mt=1.0)
        self.assertEqual(self.Assessment.objects.first().rate_per_mt, 720.0)

    def test_client_supplied_amount_is_ignored(self):
        self.post(self.off_token, indicative_seigniorage=0.01)
        self.assertEqual(self.Assessment.objects.first().indicative_seigniorage, 45.85)

    def test_client_supplied_classification_is_ignored(self):
        """A block 34.55cm x 33.45cm is Within Gangsaw. Claiming Above Gangsaw
        would price it at the higher band - the server must refuse the claim."""
        self.post(self.off_token, gangsaw_classification="Above Gangsaw")
        stored = self.Assessment.objects.first()
        self.assertEqual(stored.gangsaw_classification, "Within Gangsaw")
        self.assertEqual(stored.rate_per_mt, 720.0)
        entry = self.AuditLog.objects(action="seigniorage_assessed").first()
        self.assertIn("client value ignored", entry.details)

    def test_client_supplied_volume_and_tonnage_are_ignored(self):
        self.post(self.off_token, volume_m3=999.0, weight_mt=999.0)
        stored = self.Assessment.objects.first()
        self.assertEqual(stored.volume_m3, 0.023588)
        self.assertEqual(stored.weight_mt, 0.064)

    def test_an_underpricing_attempt_changes_nothing_that_matters(self):
        """Every money lever a client could pull, pulled at once."""
        self.post(
            self.off_token, gangsaw_classification="Above Gangsaw",
            rate_per_mt=1.0, indicative_seigniorage=0.01, volume_m3=0.000001,
            weight_mt=0.000001, actor="rtgs_supervisor", is_official=False,
        )
        stored = self.Assessment.objects.first()
        self.assertEqual(stored.rate_per_mt, 720.0)
        self.assertEqual(stored.indicative_seigniorage, 45.85)
        self.assertEqual(stored.gangsaw_classification, "Within Gangsaw")
        self.assertEqual(self.AuditLog.objects.first().actor, "off6c")

    # ---- 11: the dashboard's real contract still works ----------------------

    def test_dashboard_payload_contract_remains_compatible(self):
        """The exact body supervisor-dashboard/src/services/api.js sends."""
        response = self.client.post(
            "/api/assessments/",
            {"block_id": "GR-6C", "granite_category": "Others",
             "gangsaw_classification": "Within Gangsaw", "density": 2.7},
            content_type="application/json", **self.auth(self.sup_token))
        self.assertEqual(response.status_code, 201, response.content[:300])
        for field in ("id", "block_id", "granite_category", "gangsaw_classification",
                      "volume_m3", "weight_mt", "rate_per_mt",
                      "indicative_seigniorage", "density_mt_per_m3", "status"):
            with self.subTest(field=field):
                self.assertIn(field, response.data)

    def test_assessment_get_remains_protected_and_readable(self):
        self.post(self.off_token)
        self.assertEqual(self.client.get("/api/assessments/").status_code, 401)
        authed = self.client.get("/api/assessments/", **self.auth(self.off_token))
        self.assertEqual(authed.status_code, 200)
        self.assertEqual(len(authed.data), 1)

    # ---- PART 7: the frozen field app is untouched --------------------------

    def test_frozen_app_write_paths_remain_anonymous(self):
        """The three POSTs the frozen iOS app makes must still work with no
        credentials. Changing them requires a deliberate field-app networking
        change, which this step explicitly does not make."""
        response = self.client.post("/api/blocks/ar-measure/", {
            "image": make_image(), "block_id": "GR-6C-AR", "quarry_id": "Q-X",
            "officer_id": "OFF-X", "length_m": "2.0", "breadth_m": "1.5",
            "height_m": "1.0", "volume_m3": "3.0",
        })
        self.assertEqual(response.status_code, 201, response.content[:300])

        created = self.client.post(
            "/api/blocks/", {"block_id": "GR-6C-CREATE", "status": "pending"},
            content_type="application/json")
        self.assertIn(created.status_code, (200, 201), created.content[:300])
