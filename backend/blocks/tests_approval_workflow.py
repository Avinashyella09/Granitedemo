"""
Phase 6G: approval workflow hardening.

Two governance rules, and nothing else:

  1. A block cannot become APPROVED until an Assessment exists. Approving first
     endorses a record whose payable amount nobody has calculated.
  2. Re-sending a decision the block already carries is a no-op. The real
     database shows why this matters: block5 was approved three times and its
     audit trail claims three separate approval decisions for one act.

REJECTION is deliberately not gated on an assessment - see the test that pins
that decision.

ISOLATION: mongomock in-memory; Django auth users live in the test SQL DB. The
real Atlas cluster is never touched. Do not run the legacy blocks/tests.py.

    ./venv/bin/python manage.py test blocks.tests_approval_workflow
"""

import mongoengine
import mongomock
from django.contrib.auth.models import Group, User
from django.test import TestCase
from rest_framework.authtoken.models import Token

from blocks import seigniorage
from blocks.permissions import OFFICER_GROUP, SUPERVISOR_GROUP


class ApprovalWorkflowTests(TestCase):
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
        self.sup_token = self.make_user("sup6g", SUPERVISOR_GROUP)
        self.off_token = self.make_user("off6g", OFFICER_GROUP)

    def make_user(self, username, role):
        user = User.objects.create_user(username=username, password="x-test-pass-6g")
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)
        return Token.objects.get_or_create(user=user)[0].key

    def auth(self, token):
        return {"HTTP_AUTHORIZATION": f"Token {token}"}

    def make_block(self, block_id, assess=False):
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
        return block

    def decide(self, block_id, decision, token=None, **extra):
        body = {"approval_status": decision, "reason": "6G test"}
        body.update(extra)
        headers = self.auth(token) if token else {}
        return self.client.post(f"/api/blocks/{block_id}/approve/", body,
                                content_type="application/json", **headers)

    def approval_audits(self, block):
        return self.AuditLog.objects(block=block, action__startswith="block_approval").count()

    # --- safety net -----------------------------------------------------------

    def test_isolated_from_real_cluster(self):
        self.assertIn("mongomock", type(mongoengine.get_connection()).__module__.lower())
        self.assertEqual(mongoengine.get_db().name, "granite_blocks_test")

    # ---- 1, 2: authorisation unchanged ---------------------------------------

    def test_anonymous_approval_rejected(self):
        block = self.make_block("GR-6G-ANON", assess=True)
        self.assertEqual(self.decide("GR-6G-ANON", "approved").status_code, 401)
        block.reload()
        self.assertEqual(block.approval_status, "pending")

    def test_officer_approval_rejected(self):
        block = self.make_block("GR-6G-OFF", assess=True)
        self.assertEqual(self.decide("GR-6G-OFF", "approved", self.off_token).status_code, 403)
        block.reload()
        self.assertEqual(block.approval_status, "pending")

    # ---- 3: assessment prerequisite ------------------------------------------

    def test_supervisor_approval_without_assessment_is_refused(self):
        block = self.make_block("GR-6G-NOASSESS", assess=False)
        response = self.decide("GR-6G-NOASSESS", "approved", self.sup_token)
        self.assertEqual(response.status_code, 409, response.content[:200])
        self.assertEqual(response.data["code"], "assessment_required_before_approval")
        block.reload()
        self.assertEqual(block.approval_status, "pending")
        self.assertIsNone(block.approved_by)
        self.assertIsNone(block.approved_at)
        self.assertEqual(self.approval_audits(block), 0)

    # ---- 16: the refusal must not quietly create the assessment --------------

    def test_refused_approval_creates_no_assessment_and_no_audit(self):
        block = self.make_block("GR-6G-NOSIDE", assess=False)
        before = (self.Assessment.objects.count(), self.AuditLog.objects.count())
        for _ in range(3):
            self.decide("GR-6G-NOSIDE", "approved", self.sup_token)
        self.assertEqual(before, (self.Assessment.objects.count(), self.AuditLog.objects.count()))
        self.assertIsNone(self.Assessment.objects(block=block).first())

    # ---- 4, 5: the happy path -------------------------------------------------

    def test_supervisor_approval_with_assessment_succeeds(self):
        block = self.make_block("GR-6G-OK", assess=True)
        response = self.decide("GR-6G-OK", "approved", self.sup_token)
        self.assertEqual(response.status_code, 200, response.content[:200])
        block.reload()
        self.assertEqual(block.approval_status, "approved")
        self.assertEqual(block.approved_by, "sup6g")
        self.assertIsNotNone(block.approved_at)

    def test_successful_approval_creates_exactly_one_audit(self):
        block = self.make_block("GR-6G-ONE", assess=True)
        self.decide("GR-6G-ONE", "approved", self.sup_token)
        self.assertEqual(self.approval_audits(block), 1)

    # ---- 6, 7, 8, 9: idempotency ---------------------------------------------

    def test_repeated_approval_is_idempotent(self):
        block = self.make_block("GR-6G-IDEM", assess=True)
        self.decide("GR-6G-IDEM", "approved", self.sup_token)
        block.reload()
        first_by, first_at = block.approved_by, block.approved_at

        for _ in range(4):
            response = self.decide("GR-6G-IDEM", "approved", self.sup_token)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["code"], "already_approved")

        block.reload()
        self.assertEqual(self.approval_audits(block), 1, "repeat approval wrote a second audit")
        self.assertEqual(block.approved_by, first_by, "approved_by was overwritten")
        self.assertEqual(block.approved_at, first_at, "approved_at was overwritten")

    def test_repeated_approval_by_a_different_supervisor_changes_nothing(self):
        other = self.make_user("sup6g-other", SUPERVISOR_GROUP)
        block = self.make_block("GR-6G-IDEM2", assess=True)
        self.decide("GR-6G-IDEM2", "approved", self.sup_token)
        block.reload()
        original_by, original_at = block.approved_by, block.approved_at

        response = self.decide("GR-6G-IDEM2", "approved", other)
        self.assertEqual(response.data["code"], "already_approved")
        block.reload()
        self.assertEqual(block.approved_by, original_by,
                         "a second supervisor overwrote the original approver")
        self.assertEqual(block.approved_at, original_at)
        self.assertEqual(self.approval_audits(block), 1)

    def test_repeated_rejection_is_also_idempotent(self):
        block = self.make_block("GR-6G-REJ-IDEM", assess=True)
        self.decide("GR-6G-REJ-IDEM", "rejected", self.sup_token)
        response = self.decide("GR-6G-REJ-IDEM", "rejected", self.sup_token)
        self.assertEqual(response.data["code"], "already_rejected")
        self.assertEqual(self.approval_audits(block), 1)

    def test_an_already_approved_block_is_not_punished_by_the_new_rule(self):
        """A record approved under the OLDER rules may have no assessment. Asking
        again must report already_approved, not a 409 - the block's history is
        not retroactively invalidated."""
        block = self.make_block("GR-6G-HISTORIC", assess=False)
        block.approval_status = "approved"
        block.approved_by = "someone-historic"
        block.save()
        response = self.decide("GR-6G-HISTORIC", "approved", self.sup_token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "already_approved")
        block.reload()
        self.assertEqual(block.approved_by, "someone-historic")

    # ---- 10: the client cannot name the actor ---------------------------------

    def test_client_actor_cannot_control_the_audit_actor(self):
        block = self.make_block("GR-6G-ACTOR", assess=True)
        self.decide("GR-6G-ACTOR", "approved", self.sup_token, actor="rtgs_supervisor")
        entry = self.AuditLog.objects(block=block, action__startswith="block_approval").first()
        self.assertEqual(entry.actor, "sup6g")
        self.assertNotEqual(entry.actor, "rtgs_supervisor")
        block.reload()
        self.assertEqual(block.approved_by, "sup6g")

    # ---- 11, 12, 13: approval touches no measurement or money -----------------

    def test_approval_changes_no_measurement_assessment_or_money(self):
        block = self.make_block("GR-6G-INTACT", assess=True)
        assessment = self.Assessment.objects(block=block).first()
        before_m = (block.measurement.length_m, block.measurement.breadth_m,
                    block.measurement.height_m, block.measurement.volume_m3)
        before_a = (assessment.granite_category, assessment.gangsaw_classification,
                    assessment.rate_per_mt, assessment.indicative_seigniorage,
                    assessment.weight_mt, assessment.volume_m3)

        self.decide("GR-6G-INTACT", "approved", self.sup_token)
        self.decide("GR-6G-INTACT", "approved", self.sup_token)

        block.reload()
        assessment.reload()
        self.assertEqual(before_m, (block.measurement.length_m, block.measurement.breadth_m,
                                    block.measurement.height_m, block.measurement.volume_m3))
        self.assertEqual(before_a, (assessment.granite_category, assessment.gangsaw_classification,
                                    assessment.rate_per_mt, assessment.indicative_seigniorage,
                                    assessment.weight_mt, assessment.volume_m3))
        self.assertEqual(self.Assessment.objects.count(), 1)

    # ---- 5 (PART): rejection is deliberately NOT gated ------------------------

    def test_rejection_does_not_require_an_assessment(self):
        """Refusing a measurement you can see is wrong should not first require
        pricing it - that would create a financial record for a block being
        thrown out."""
        block = self.make_block("GR-6G-REJECT", assess=False)
        response = self.decide("GR-6G-REJECT", "rejected", self.sup_token)
        self.assertEqual(response.status_code, 200, response.content[:200])
        block.reload()
        self.assertEqual(block.approval_status, "rejected")
        self.assertEqual(block.approved_by, "sup6g")
        self.assertIsNone(self.Assessment.objects(block=block).first(),
                          "rejection must not create an assessment")

    def test_a_rejected_block_still_needs_an_assessment_to_be_approved(self):
        self.make_block("GR-6G-REJ-THEN-APP", assess=False)
        self.decide("GR-6G-REJ-THEN-APP", "rejected", self.sup_token)
        response = self.decide("GR-6G-REJ-THEN-APP", "approved", self.sup_token)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "assessment_required_before_approval")

    def test_approval_after_assessment_is_created_then_succeeds(self):
        """The intended order: measurement -> assessment -> approval."""
        block = self.make_block("GR-6G-ORDER", assess=False)
        self.assertEqual(self.decide("GR-6G-ORDER", "approved", self.sup_token).status_code, 409)

        self.client.post("/api/assessments/",
                         {"block_id": "GR-6G-ORDER", "granite_category": "Others"},
                         content_type="application/json", **self.auth(self.sup_token))

        self.assertEqual(self.decide("GR-6G-ORDER", "approved", self.sup_token).status_code, 200)
        block.reload()
        self.assertEqual(block.approval_status, "approved")

    # ---- 17: override is unaffected -------------------------------------------

    def test_override_remains_compatible_and_untouched_by_the_new_rule(self):
        """Override changes the MEASUREMENT; it does not set approval state, so
        it is not gated by the assessment prerequisite."""
        block = self.make_block("GR-6G-OVERRIDE", assess=False)
        response = self.client.post(
            "/api/blocks/GR-6G-OVERRIDE/override/",
            {"length_m": 0.40, "breadth_m": 0.34, "height_m": 0.21, "reason": "6G override"},
            content_type="application/json", **self.auth(self.sup_token))
        self.assertEqual(response.status_code, 200, response.content[:300])
        block.reload()
        self.assertTrue(block.is_overridden)
        self.assertEqual(block.approval_status, "pending",
                         "override must not silently approve a block")

    def test_invalid_decision_value_still_rejected(self):
        self.make_block("GR-6G-BAD", assess=True)
        for bad in ("APPROVED", "yes", "", None, "pending"):
            with self.subTest(value=bad):
                self.assertEqual(
                    self.decide("GR-6G-BAD", bad, self.sup_token).status_code, 400)

    def test_unknown_block_still_404(self):
        self.assertEqual(self.decide("GR-6G-NOPE", "approved", self.sup_token).status_code, 404)
