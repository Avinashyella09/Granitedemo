"""
Phase 6A: the OMEPS-ready export contract.

ISOLATION: mongomock in-memory only. The real Atlas cluster is never touched and
nothing here writes to backend/media/. Do not run the legacy blocks/tests.py.

    ./venv/bin/python manage.py test blocks.tests_omeps_export
"""

import datetime
import json
from decimal import Decimal, ROUND_HALF_UP

import mongoengine
import mongomock
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.authtoken.models import Token

from blocks import omeps_export, report_basis, seigniorage


class OmepsExportTests(TestCase):
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
        super().tearDownClass()

    def setUp(self):
        for model in (self.Block, self.Assessment, self.AuditLog, self.Quarry, self.Officer):
            model.objects.delete()

    # --- fixtures ------------------------------------------------------------

        # Phase 6B: these endpoints now require authentication. One default
        # header on the test client authenticates every request in this suite,
        # so no assertion below had to change - only the caller's identity did.
        user = User.objects.create_user(username="omeps-export-6b", password="x-test-pass-123")
        token = Token.objects.get_or_create(user=user)[0].key
        self.client.defaults["HTTP_AUTHORIZATION"] = f"Token {token}"

    def make_block2_like(self, block_id="block2-test", approve=True):
        """Mirrors the real block2 record exactly: same dimensions, same
        unresolved references, no GPS, same three audit events."""
        block = self.Block(
            block_id=block_id, status="measured", cv_status="not_applicable",
            submitted_quarry_id="Q-9982", inspecting_officer_id="Off",
            reference_warnings=["quarry_unresolved", "officer_unresolved", "gps_incomplete"],
            captured_at=datetime.datetime(2026, 9, 19, 6, 35, 46),
        )
        block.measurement = self.Measurement(
            length_m=0.3455, breadth_m=0.3345, height_m=0.2041,
            volume_m3=0.3455 * 0.3345 * 0.2041, measurement_method="ar",
            measured_at=datetime.datetime(2026, 9, 19, 6, 35, 46),
        )
        if approve:
            block.approval_status = "approved"
            block.approved_by = "rtgs_supervisor"
            block.approved_at = datetime.datetime(2026, 9, 19, 7, 41, 23)
        block.save()

        result = seigniorage.calculate(0.3455, 0.3345, 0.2041, "Others", 2.7)
        assessment = self.Assessment(
            block=block, granite_category=result["granite_category"],
            gangsaw_classification=result["gangsaw_classification"],
            volume_m3=result["volume_m3"], weight_mt=result["tonnage_mt"],
            rate_per_mt=result["rate_per_mt"],
            indicative_seigniorage=result["seigniorage_amount"],
            density_mt_per_m3=result["density_mt_per_m3"],
        )
        assessment.save()

        self.AuditLog(block=block, block_id_snapshot=block_id,
                      action="ar_measurement_submitted", actor="Off",
                      details="AR inspection: L=0.3455m, B=0.3345m, H=0.2041m").save()
        self.AuditLog(
            block=block, block_id_snapshot=block_id, action="seigniorage_assessed",
            actor="System",
            details=(f"Official seigniorage: block_id={block_id}, amount=45.85INR, "
                     f"schedule={seigniorage.RATE_SCHEDULE_VERSION}, x=y"),
        ).save()
        if approve:
            self.AuditLog(block=block, block_id_snapshot=block_id,
                          action="block_approval_approved", actor="rtgs_supervisor",
                          details="Approval decision: reason=PoC supervisor approval").save()
        return block, assessment

    def export(self, block_id="block2-test"):
        return self.client.get(f"/api/export/omeps/{block_id}/")

    # --- safety net -----------------------------------------------------------

    def test_isolated_from_real_cluster(self):
        self.assertIn("mongomock", type(mongoengine.get_connection()).__module__.lower())
        self.assertEqual(mongoengine.get_db().name, "granite_blocks_test")

    # --- 1 & 2: valid export, schema/version ---------------------------------

    def test_valid_export_returns_all_contract_sections(self):
        self.make_block2_like()
        response = self.export()
        self.assertEqual(response.status_code, 200, response.content[:300])
        for section in ("export_metadata", "block", "measurement", "classification",
                        "assessment", "approval", "audit", "validation"):
            with self.subTest(section=section):
                self.assertIn(section, response.data)

    def test_export_declares_version_and_pending_schema_status(self):
        self.make_block2_like()
        meta = self.export().data["export_metadata"]
        self.assertEqual(meta["export_schema_version"], "RTGS-OMEPS-READY-2026-09")
        self.assertEqual(meta["integration_status"], "OMEPS_READY_PENDING_OFFICIAL_SCHEMA")
        self.assertEqual(meta["source_system"], "RTGS Granite Block PoC")
        self.assertIn("pending official OMEPS schema", meta["integration_note"])
        self.assertIsNotNone(meta["generated_at"])

    def test_export_does_not_claim_completed_integration(self):
        self.make_block2_like()
        blob = json.dumps(self.export().data).lower()
        for forbidden in ("integration_complete", "omeps_integrated",
                          "submitted_to_omeps", "omeps_confirmed"):
            with self.subTest(claim=forbidden):
                self.assertNotIn(forbidden, blob)

    # --- 3: block matches persisted ------------------------------------------

    def test_block_section_matches_persisted_block(self):
        block, _ = self.make_block2_like()
        data = self.export().data["block"]
        self.assertEqual(data["block_id"], block.block_id)
        self.assertEqual(data["status"], block.status)
        self.assertEqual(data["approval_status"], block.approval_status)
        self.assertEqual(data["cv_status"], block.cv_status)
        self.assertEqual(data["measurement_method"], block.measurement.measurement_method)
        self.assertEqual(data["submitted_quarry_id"], block.submitted_quarry_id)
        self.assertEqual(data["inspecting_officer_id"], block.inspecting_officer_id)
        self.assertEqual(data["reference_warnings"], block.reference_warnings)

    # --- 4 (measurement) ------------------------------------------------------

    def test_measurement_section_matches_persisted_block_measurement(self):
        block, _ = self.make_block2_like()
        m = block.measurement
        data = self.export().data["measurement"]
        self.assertEqual(data["length_m"], m.length_m)
        self.assertEqual(data["breadth_m"], m.breadth_m)
        self.assertEqual(data["height_m"], m.height_m)
        self.assertEqual(data["volume_m3"], m.volume_m3)

    # --- 4: assessment matches persisted -------------------------------------

    def test_assessment_section_matches_persisted_assessment(self):
        _, assessment = self.make_block2_like()
        data = self.export().data["assessment"]
        self.assertEqual(data["assessment_id"], str(assessment.id))
        self.assertEqual(data["density_mt_per_m3"], assessment.density_mt_per_m3)
        self.assertEqual(data["volume_m3_stored"], assessment.volume_m3)
        self.assertEqual(data["tonnage_mt_stored"], assessment.weight_mt)
        self.assertEqual(data["rate_per_mt"], assessment.rate_per_mt)
        self.assertEqual(data["seigniorage_amount"], assessment.indicative_seigniorage)
        self.assertEqual(data["currency"], "INR")
        self.assertEqual(data["rate_schedule_version"], seigniorage.RATE_SCHEDULE_VERSION)

    def test_classification_matches_persisted_and_shows_the_rule(self):
        _, assessment = self.make_block2_like()
        data = self.export().data["classification"]
        self.assertEqual(data["granite_category"], assessment.granite_category)
        self.assertEqual(data["gangsaw_classification"], assessment.gangsaw_classification)
        self.assertEqual(data["dimensions_in_cm_used_for_rule"],
                         {"length_cm": 34.55, "breadth_cm": 33.45})
        self.assertIn("270", data["threshold_rule"])
        self.assertIn("150", data["threshold_rule"])
        self.assertIn("34.55 > 270 = no", data["comparison"])

    # --- 5: approval matches persisted ---------------------------------------

    def test_approval_section_matches_persisted_approval(self):
        block, _ = self.make_block2_like()
        data = self.export().data["approval"]
        self.assertEqual(data["approval_status"], "approved")
        self.assertTrue(data["approval_present"])
        self.assertEqual(data["approved_by"], "rtgs_supervisor")
        self.assertEqual(data["approved_at"], block.approved_at.isoformat() + "Z")
        self.assertEqual(data["approval_reason"], "PoC supervisor approval")
        approval_audit = self.AuditLog.objects(action="block_approval_approved").first()
        self.assertEqual(data["approval_audit_ref"], str(approval_audit.id))

    def test_unapproved_block_is_marked_pending_not_fabricated(self):
        self.make_block2_like(approve=False)
        data = self.export().data
        self.assertEqual(data["approval"]["approval_status"], "pending")
        self.assertFalse(data["approval"]["approval_present"])
        self.assertIsNone(data["approval"]["approved_by"])
        self.assertIsNone(data["approval"]["approved_at"])
        self.assertFalse(data["validation"]["approval_present"])
        self.assertFalse(data["validation"]["ready_for_omeps_submission"])

    # --- 6 & 7: audit events and receipt -------------------------------------

    def test_audit_events_included_with_actors_and_timestamps(self):
        self.make_block2_like()
        audit = self.export().data["audit"]
        self.assertEqual(audit["event_count"], 3)
        events = {e["event"]: e for e in audit["events"]}
        self.assertEqual(set(events), {"ar_measurement_submitted",
                                       "seigniorage_assessed",
                                       "block_approval_approved"})
        self.assertEqual(events["block_approval_approved"]["actor"], "rtgs_supervisor")
        for event in audit["events"]:
            with self.subTest(event=event["event"]):
                self.assertTrue(event["reference"])
                self.assertTrue(event["timestamp"])
                self.assertTrue(event["detail"])

    def test_real_ar_receipt_is_reused_not_regenerated(self):
        self.make_block2_like()
        receipt = str(self.AuditLog.objects(action="ar_measurement_submitted").first().id)
        data = self.export().data
        self.assertEqual(data["audit"]["receipt_id"], receipt)
        self.assertEqual(data["measurement"]["ar_receipt_id"], receipt)
        self.assertEqual(data["measurement"]["ar_receipt_reference"], f"AuditLog/{receipt}")
        self.assertTrue(data["validation"]["receipt_present"])

    # --- 8, 9, 10: honest absence --------------------------------------------

    def test_unresolved_quarry_represented_honestly(self):
        self.make_block2_like()
        data = self.export().data
        self.assertIsNone(data["block"]["quarry_id"])
        self.assertEqual(data["block"]["submitted_quarry_id"], "Q-9982")
        self.assertEqual(data["block"]["quarry_reference_status"],
                         "unresolved_in_reference_data")
        self.assertFalse(data["validation"]["quarry_reference_resolved"])
        self.assertEqual(self.Quarry.objects.count(), 0, "export invented a Quarry")

    def test_unresolved_officer_represented_honestly(self):
        self.make_block2_like()
        data = self.export().data
        self.assertIsNone(data["block"]["officer_id"])
        self.assertEqual(data["block"]["inspecting_officer_id"], "Off")
        self.assertEqual(data["block"]["officer_reference_status"],
                         "unresolved_in_reference_data")
        self.assertFalse(data["validation"]["officer_reference_resolved"])
        self.assertEqual(self.Officer.objects.count(), 0, "export invented an Officer")

    def test_missing_gps_represented_honestly_without_coordinates(self):
        self.make_block2_like()
        data = self.export().data
        self.assertIsNone(data["measurement"]["gps_latitude"])
        self.assertIsNone(data["measurement"]["gps_longitude"])
        self.assertEqual(data["measurement"]["gps_status"], "not_captured")
        self.assertFalse(data["validation"]["gps_available"])

    def test_resolved_references_and_gps_are_reported_when_they_exist(self):
        """The honest-absence path must not be the only path that works."""
        quarry = self.Quarry(id="Q-REAL", name="Real Quarry").save()
        self.Officer(officer_id="OFF-REAL", name="Real Officer").save()
        block, _ = self.make_block2_like(block_id="GR-RESOLVED")
        block.quarry = quarry
        block.submitted_quarry_id = "Q-REAL"
        block.inspecting_officer_id = "OFF-REAL"
        block.gps_latitude, block.gps_longitude = 14.4426, 78.8242
        block.save()
        data = self.export("GR-RESOLVED").data
        self.assertEqual(data["block"]["quarry_id"], "Q-REAL")
        self.assertEqual(data["block"]["quarry_reference_status"], "resolved")
        self.assertEqual(data["block"]["officer_id"], "OFF-REAL")
        self.assertEqual(data["block"]["officer_reference_status"], "resolved")
        self.assertEqual(data["measurement"]["gps_status"], "captured")
        self.assertTrue(data["validation"]["gps_available"])

    # --- 11: no credential leakage -------------------------------------------

    def test_export_leaks_no_credentials_or_tokens(self):
        from django.conf import settings

        self.make_block2_like()
        blob = json.dumps(self.export().data)
        lowered = blob.lower()
        for marker in ("mongodb+srv", "mongodb://", "password", "secret_key",
                       "api_token", "authorization", "passwd", "credential"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, lowered)
        self.assertNotIn(str(settings.SECRET_KEY), blob)
        mongo = getattr(settings, "MONGODB_SETTINGS", None) or {}
        host = mongo.get("host") if isinstance(mongo, dict) else None
        if host:
            self.assertNotIn(str(host), blob)

    # --- 12 & 13: clear failure, never a fabricated document -----------------

    def test_missing_block_returns_404_with_code(self):
        response = self.export("NO-SUCH-BLOCK")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "block_not_found")
        self.assertNotIn("assessment", response.data)

    def test_missing_assessment_returns_409_with_code(self):
        block = self.Block(block_id="GR-NO-ASSESS", status="measured")
        block.measurement = self.Measurement(
            length_m=1.0, breadth_m=1.0, height_m=1.0, volume_m3=1.0,
            measurement_method="ar")
        block.save()
        response = self.export("GR-NO-ASSESS")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "assessment_required")
        self.assertEqual(response.data["integration_status"],
                         "OMEPS_READY_PENDING_OFFICIAL_SCHEMA")

    def test_missing_measurement_returns_409_with_code(self):
        self.Block(block_id="GR-NO-MEAS", status="pending").save()
        response = self.export("GR-NO-MEAS")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "measurement_required")

    # --- 14 & 15: read-only ---------------------------------------------------

    def test_export_creates_no_database_records(self):
        self.make_block2_like()
        before = (self.Block.objects.count(), self.Assessment.objects.count(),
                  self.AuditLog.objects.count(), self.Quarry.objects.count(),
                  self.Officer.objects.count())
        self.export(); self.export(); self.export()
        after = (self.Block.objects.count(), self.Assessment.objects.count(),
                 self.AuditLog.objects.count(), self.Quarry.objects.count(),
                 self.Officer.objects.count())
        self.assertEqual(before, after, "export mutated the database")

    def test_export_creates_no_duplicate_audit_entries(self):
        self.make_block2_like()
        before = [str(e.id) for e in self.AuditLog.objects.order_by("timestamp")]
        for _ in range(5):
            self.export()
        after = [str(e.id) for e in self.AuditLog.objects.order_by("timestamp")]
        self.assertEqual(before, after, "export wrote or duplicated audit entries")

    def test_export_does_not_mutate_the_block_or_assessment(self):
        block, assessment = self.make_block2_like()
        snapshot = (block.status, block.approval_status, block.approved_by,
                    assessment.indicative_seigniorage, assessment.weight_mt,
                    block.measurement.volume_m3)
        self.export()
        block.reload(); assessment.reload()
        self.assertEqual(
            (block.status, block.approval_status, block.approved_by,
             assessment.indicative_seigniorage, assessment.weight_mt,
             block.measurement.volume_m3), snapshot)

    # --- 16: determinism ------------------------------------------------------

    def test_export_is_deterministic_apart_from_generated_at(self):
        self.make_block2_like()
        first = json.loads(json.dumps(self.export().data))
        second = json.loads(json.dumps(self.export().data))
        first["export_metadata"].pop("generated_at")
        second["export_metadata"].pop("generated_at")
        self.assertEqual(first, second)

    def test_generated_at_is_the_only_varying_field(self):
        self.make_block2_like()
        first, second = self.export().data, self.export().data
        differing = [k for k in first["export_metadata"]
                     if first["export_metadata"][k] != second["export_metadata"][k]]
        self.assertTrue(set(differing) <= {"generated_at"}, differing)

    # --- 17 & 18: financial source of truth ----------------------------------

    def test_financial_values_are_echoed_from_the_assessment(self):
        """Edit the stored money to a value no tariff could produce; the export
        must report the stored value, proving it holds no second opinion."""
        _, assessment = self.make_block2_like()
        assessment.rate_per_mt = 12345.67
        assessment.indicative_seigniorage = 98765.43
        assessment.save()
        data = self.export().data["assessment"]
        self.assertEqual(data["rate_per_mt"], 12345.67)
        self.assertEqual(data["seigniorage_amount"], 98765.43)
        self.assertEqual(data["financial_source"], "persisted_assessment_document")

    def test_export_module_holds_no_independent_tariff_table(self):
        source = open(omeps_export.__file__).read()
        for rate in ("1830", "1530", "1520", "1300", "1660", "1410",
                     "1360", "1200", "940", "720"):
            with self.subTest(rate=rate):
                self.assertNotIn(rate, source,
                                 f"rate literal {rate} found in the export module")
        for name in ("RATE_GROUPS", "CATEGORY_TO_GROUP", "get_official_rate", "calculate("):
            with self.subTest(symbol=name):
                self.assertNotIn(name, source)

    # --- 19: regression for the Phase 6A tonnage-reporting defect ------------

    def test_exact_tonnage_basis_reconciles_to_the_stored_amount(self):
        """REGRESSION - Phase 6A pre-flight audit.

        The report layer used to derive its 'tonnage used for amount' from the
        6-dp ROUNDED Assessment.volume_m3. For the real block2 figures that gives
        0.0636876 MT, which at INR 720/MT rounds to INR 45.86 - a paisa above the
        correctly stored INR 45.85. This test pins BOTH halves: the exact basis
        must reconcile, and the rounded product must not, so the two can never be
        quietly conflated again.
        """
        _, assessment = self.make_block2_like()
        data = self.export().data["assessment"]

        self.assertEqual(data["tonnage_basis_status"], report_basis.REPRODUCED)
        self.assertEqual(data["tonnage_mt_exact"], "0.0636870221325")
        self.assertEqual(data["volume_m3_exact"], "0.023587785975")

        rate = Decimal(str(data["rate_per_mt"]))
        stored_amount = Decimal(str(assessment.indicative_seigniorage))

        exact = Decimal(data["tonnage_mt_exact"])
        self.assertEqual(
            (exact * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            stored_amount,
            "the exported exact tonnage does not reproduce the stored amount")

        rounded = Decimal(str(data["volume_m3_stored"])) * Decimal(str(data["density_mt_per_m3"]))
        self.assertEqual(rounded, Decimal("0.0636876"))
        self.assertNotEqual(
            (rounded * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            stored_amount,
            "rounded basis now agrees; this regression test has lost its teeth")

    def test_exact_and_stored_tonnage_are_reported_as_distinct_fields(self):
        self.make_block2_like()
        data = self.export().data["assessment"]
        self.assertEqual(data["tonnage_mt_stored"], 0.064)
        self.assertNotEqual(str(data["tonnage_mt_stored"]), data["tonnage_mt_exact"])
        self.assertIn("rounded", data["tonnage_basis_note"].lower())

    def test_stale_measurement_withholds_the_basis_instead_of_guessing(self):
        """If the block is re-measured after assessment, the original basis is no
        longer derivable and must not be restated from the new dimensions."""
        block, _ = self.make_block2_like()
        block.measurement.length_m = 0.9999
        block.measurement.volume_m3 = 0.9999 * 0.3345 * 0.2041
        block.save()
        data = self.export().data
        self.assertEqual(data["assessment"]["tonnage_basis_status"],
                         report_basis.STALE)
        self.assertIsNone(data["assessment"]["tonnage_mt_exact"])
        self.assertIsNone(data["assessment"]["volume_m3_exact"])
        self.assertFalse(data["validation"]["financial_basis_reproduced"])
        self.assertFalse(data["validation"]["ready_for_omeps_submission"])

    def test_schedule_version_provenance_is_the_recorded_one(self):
        self.make_block2_like()
        data = self.export().data["assessment"]
        self.assertEqual(data["rate_schedule_version_source"],
                         report_basis.RECORDED_AT_ASSESSMENT)

    def test_schedule_version_falls_back_labelled_when_not_recorded(self):
        self.make_block2_like()
        self.AuditLog.objects(action="seigniorage_assessed").update(set__details="no schedule here")
        data = self.export().data["assessment"]
        self.assertEqual(data["rate_schedule_version_source"],
                         report_basis.CURRENT_CONFIGURATION)
        self.assertEqual(data["rate_schedule_version"], seigniorage.RATE_SCHEDULE_VERSION)
