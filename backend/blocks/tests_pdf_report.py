"""
Phase 5D: the defensible PDF assessment report.

ISOLATION: mongomock in-memory + temporary MEDIA_ROOT. The real Atlas cluster
and the real backend/media/ tree are never touched. Do not run the legacy
blocks/tests.py.

    ./venv/bin/python manage.py test blocks.tests_pdf_report
"""

import io
import shutil
import tempfile

import mongoengine
import mongomock
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token

from blocks import report_hash, seigniorage

_TEMP_MEDIA = tempfile.mkdtemp(prefix="graniteblocks-pdf-test-media-")


def pdf_text(pdf_bytes):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


@override_settings(MEDIA_ROOT=_TEMP_MEDIA)
class PdfReportTests(TestCase):
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

        # Phase 6B: these endpoints now require authentication. One default
        # header on the test client authenticates every request in this suite,
        # so no assertion below had to change - only the caller's identity did.
        user = User.objects.create_user(username="pdf-report-6b", password="x-test-pass-123")
        token = Token.objects.get_or_create(user=user)[0].key
        self.client.defaults["HTTP_AUTHORIZATION"] = f"Token {token}"

    def make_assessed_block(self, block_id="block2-test"):
        """Mirrors the real block2 record: same dimensions, same assessment."""
        block = self.Block(
            block_id=block_id, status="measured", approval_status="approved",
            approved_by="rtgs_supervisor", cv_status="not_applicable",
            submitted_quarry_id="Q-9982", inspecting_officer_id="Off",
            reference_warnings=["quarry_unresolved", "officer_unresolved", "gps_incomplete"],
        )
        block.measurement = self.Measurement(
            length_m=0.3455, breadth_m=0.3345, height_m=0.2041,
            volume_m3=0.023587785975, measurement_method="ar",
        )
        import datetime
        block.approved_at = datetime.datetime(2026, 9, 19, 7, 41, 23)
        block.captured_at = datetime.datetime(2026, 9, 19, 6, 35, 46)
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
                      action="ar_measurement_submitted", actor="Off", details="AR inspection").save()
        self.AuditLog(block=block, block_id_snapshot=block_id,
                      action="seigniorage_assessed", actor="System", details="Official seigniorage").save()
        self.AuditLog(block=block, block_id_snapshot=block_id,
                      action="block_approval_approved", actor="rtgs_supervisor",
                      details="Approval decision: reason=PoC supervisor approval").save()
        return block, assessment

    def get_pdf(self, block_id="block2-test"):
        return self.client.get(f"/api/blocks/{block_id}/pdf/")

    # ---- safety net ---------------------------------------------------------

    def test_isolated_from_real_cluster_and_media(self):
        from django.conf import settings

        self.assertIn("mongomock", type(mongoengine.get_connection()).__module__.lower())
        self.assertEqual(mongoengine.get_db().name, "granite_blocks_test")
        self.assertTrue(str(settings.MEDIA_ROOT).startswith(tempfile.gettempdir()))

    # ---- 1-12: content ------------------------------------------------------

    def test_pdf_returned_for_assessed_block(self):
        self.make_assessed_block()
        response = self.get_pdf()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_pdf_contains_required_business_values(self):
        _, assessment = self.make_assessed_block()
        text = pdf_text(self.get_pdf().content)
        for needle, label in [
            ("block2-test", "block ID"),
            ("0.3455", "length"),
            ("0.3345", "breadth"),
            ("0.2041", "height"),
            ("0.023588", "volume"),
            ("Others", "granite category"),
            ("Within Gangsaw", "gangsaw classification"),
            ("720.00", "official rate"),
            ("45.85", "seigniorage amount"),
            ("APPROVED", "approval status"),
            ("rtgs_supervisor", "authenticated approver"),
            ("RTGS-OFFICIAL-2026-09", "rate schedule"),
            ("Verification SHA-256", "verification hash label"),
        ]:
            with self.subTest(field=label):
                self.assertIn(needle, text, f"{label} missing from report")

    def test_pdf_contains_the_actual_verification_hash(self):
        block, assessment = self.make_assessed_block()
        receipt = str(self.AuditLog.objects(action="ar_measurement_submitted").first().id)
        expected = report_hash.verification_hash(block, assessment, receipt)
        self.assertIn(expected, pdf_text(self.get_pdf().content))

    def test_pdf_contains_receipt_reference(self):
        self.make_assessed_block()
        receipt = str(self.AuditLog.objects(action="ar_measurement_submitted").first().id)
        self.assertIn(receipt, pdf_text(self.get_pdf().content))

    def test_pdf_shows_gangsaw_rule_and_centimetres(self):
        self.make_assessed_block()
        text = pdf_text(self.get_pdf().content)
        self.assertIn("34.55", text)
        self.assertIn("33.45", text)
        self.assertIn("270", text)
        self.assertIn("150", text)

    def test_pdf_shows_calculation_chain(self):
        self.make_assessed_block()
        text = pdf_text(self.get_pdf().content)
        self.assertIn("2.7", text)          # density
        self.assertIn("Density", text)
        self.assertIn("Tonnage", text)

    # ---- 13-16: honesty ------------------------------------------------------

    def test_pdf_does_not_claim_annotated_output_for_ar_record(self):
        """The old template titled the AR photo 'Visual Evidence (Annotated
        Output)' even though no CV inference ever ran."""
        self.make_assessed_block()
        text = pdf_text(self.get_pdf().content)
        self.assertNotIn("Annotated Output", text)
        self.assertIn("Submitted Field Image", text)

    def test_pdf_states_gps_not_captured(self):
        self.make_assessed_block()
        text = pdf_text(self.get_pdf().content)
        self.assertIn("GPS STATUS: NOT CAPTURED", text)

    def test_pdf_reports_unresolved_references_honestly(self):
        self.make_assessed_block()
        text = pdf_text(self.get_pdf().content)
        self.assertIn("Q-9982", text)
        self.assertIn("Unresolved", text)

    def test_pdf_contains_disclaimer(self):
        self.make_assessed_block()
        text = pdf_text(self.get_pdf().content)
        self.assertIn("proof-of-concept", text)
        self.assertIn("not", text.lower())
        self.assertIn("permit", text)

    def test_pdf_does_not_invent_coordinates(self):
        self.make_assessed_block()
        text = pdf_text(self.get_pdf().content)
        self.assertNotIn("Latitude", text)

    # ---- 17: no fake report --------------------------------------------------

    def test_unassessed_block_exports_a_measurement_record(self):
        """SUPERSEDES the earlier expectation of a 409.

        Phase 5D refused to generate a report without an assessment, so a
        measured-but-unpriced block could not be exported at all. That refusal
        existed to stop a document showing blanks where the money should be -
        and that concern is now met differently: the report is produced, but
        every financial field says "Not assessed" and the title and disclaimer
        state plainly that it is a measurement record. Nothing is fabricated,
        and the document cannot be mistaken for a priced assessment.
        """
        block = self.Block(block_id="GR-NO-ASSESS", status="measured")
        block.measurement = self.Measurement(
            length_m=1.0, breadth_m=1.0, height_m=1.0, volume_m3=1.0, measurement_method="ar")
        block.save()

        response = self.get_pdf("GR-NO-ASSESS")
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertEqual(response["Content-Type"], "application/pdf")
        text = pdf_text(response.content)

        self.assertIn("GR-NO-ASSESS", text)
        self.assertIn("Measurement Record", text)
        self.assertIn("NOT ASSESSED", text)
        self.assertIn("MEASUREMENT RECORD, NOT A SEIGNIORAGE ASSESSMENT", text)
        # ReportLab wraps paragraphs, so assert on fragments that survive a
        # line break rather than on the full sentence.
        self.assertIn("must not be used as a", text)
        self.assertIn("demand or payment", text)

    def test_unassessed_report_states_no_money_rather_than_zero(self):
        """A zero would be a fabricated figure; a blank would look like an
        omission. Neither may appear where the payable amount belongs."""
        block = self.Block(block_id="GR-NO-ASSESS-2", status="measured")
        block.measurement = self.Measurement(
            length_m=1.0, breadth_m=1.0, height_m=1.0, volume_m3=1.0, measurement_method="ar")
        block.save()
        text = pdf_text(self.get_pdf("GR-NO-ASSESS-2").content)
        self.assertNotIn("INR 0.00", text)
        self.assertNotIn("0.000 MT", text)
        self.assertIn("Not assessed", text)

    def test_unassessed_report_number_is_marked_and_differs_once_assessed(self):
        block = self.Block(block_id="GR-NO-ASSESS-3", status="measured")
        block.measurement = self.Measurement(
            length_m=1.0, breadth_m=1.0, height_m=1.0, volume_m3=1.0, measurement_method="ar")
        block.save()
        unpriced = report_hash.report_number(block, None)
        self.assertTrue(unpriced.endswith("-M"), unpriced)
        self.assertIn("GR-NO-ASSESS-3", unpriced)

        result = seigniorage.calculate(1.0, 1.0, 1.0, "Others", 2.7)
        assessment = self.Assessment(
            block=block, granite_category=result["granite_category"],
            gangsaw_classification=result["gangsaw_classification"],
            volume_m3=result["volume_m3"], weight_mt=result["tonnage_mt"],
            rate_per_mt=result["rate_per_mt"],
            indicative_seigniorage=result["seigniorage_amount"],
            density_mt_per_m3=result["density_mt_per_m3"])
        assessment.save()
        self.assertNotEqual(unpriced, report_hash.report_number(block, assessment))

    def test_verification_hash_changes_once_the_block_is_assessed(self):
        block = self.Block(block_id="GR-NO-ASSESS-4", status="measured")
        block.measurement = self.Measurement(
            length_m=1.0, breadth_m=1.0, height_m=1.0, volume_m3=1.0, measurement_method="ar")
        block.save()
        before = report_hash.verification_hash(block, None, "receipt-1")
        result = seigniorage.calculate(1.0, 1.0, 1.0, "Others", 2.7)
        assessment = self.Assessment(
            block=block, granite_category=result["granite_category"],
            gangsaw_classification=result["gangsaw_classification"],
            volume_m3=result["volume_m3"], weight_mt=result["tonnage_mt"],
            rate_per_mt=result["rate_per_mt"],
            indicative_seigniorage=result["seigniorage_amount"],
            density_mt_per_m3=result["density_mt_per_m3"])
        assessment.save()
        self.assertNotEqual(before, report_hash.verification_hash(block, assessment, "receipt-1"))

    def test_unknown_block_returns_404(self):
        self.assertEqual(self.get_pdf("NO-SUCH-BLOCK").status_code, 404)

    # ---- 18-19: hash determinism --------------------------------------------

    def test_verification_hash_is_deterministic(self):
        block, assessment = self.make_assessed_block()
        first = report_hash.verification_hash(block, assessment, "receipt-1")
        second = report_hash.verification_hash(block, assessment, "receipt-1")
        self.assertEqual(first, second)

    def test_changing_a_hashed_field_changes_the_hash(self):
        block, assessment = self.make_assessed_block()
        before = report_hash.verification_hash(block, assessment, "receipt-1")
        for mutate in (
            lambda: setattr(assessment, "indicative_seigniorage", 99.99),
            lambda: setattr(assessment, "rate_per_mt", 1830.0),
            lambda: setattr(block, "approved_by", "someone_else"),
            lambda: setattr(assessment, "gangsaw_classification", "Above Gangsaw"),
        ):
            with self.subTest(change=str(mutate)):
                block2, assess2 = self.Block.objects.first(), self.Assessment.objects.first()
                mutate()
                after = report_hash.verification_hash(block, assessment, "receipt-1")
                self.assertNotEqual(before, after)
                # restore for the next subtest
                block, assessment = self.make_assessed_block_restore(block, assessment)

    def make_assessed_block_restore(self, block, assessment):
        assessment.indicative_seigniorage = 45.85
        assessment.rate_per_mt = 720.0
        assessment.gangsaw_classification = "Within Gangsaw"
        block.approved_by = "rtgs_supervisor"
        return block, assessment

    def test_report_number_is_deterministic_and_traceable(self):
        block, assessment = self.make_assessed_block()
        first = report_hash.report_number(block, assessment)
        self.assertEqual(first, report_hash.report_number(block, assessment))
        self.assertIn("block2-test", first)
        self.assertTrue(first.startswith("RTGS/"))

    # ---- 20: download is side-effect free ------------------------------------

    def test_downloading_report_creates_no_database_records(self):
        self.make_assessed_block()
        before = (self.Block.objects.count(), self.Assessment.objects.count(),
                  self.AuditLog.objects.count())
        self.get_pdf(); self.get_pdf(); self.get_pdf()
        after = (self.Block.objects.count(), self.Assessment.objects.count(),
                 self.AuditLog.objects.count())
        self.assertEqual(before, after, "generating a report mutated the database")

    # ---- 21-23: Phase 6A tonnage-reporting regression -----------------------

    def test_pdf_states_the_exact_tonnage_basis_not_the_rounded_product(self):
        """REGRESSION - Phase 6A pre-flight audit.

        Section 4 used to print `assessment.volume_m3 * density` (the 6-dp
        ROUNDED volume) under the label "Tonnage (used for amount)". For the real
        block2 figures that is 0.0636876 MT, which at INR 720/MT rounds to
        INR 45.86 - a paisa above the correctly stored INR 45.85. A verifier
        following the report's own arithmetic would have judged the assessment
        wrong. The report must print the exact basis the amount came from.
        """
        self.make_assessed_block()
        text = pdf_text(self.get_pdf().content)
        self.assertIn("0.0636870221325", text, "exact tonnage basis missing")
        self.assertIn("0.023587785975", text, "exact volume basis missing")
        self.assertNotIn("0.0636876", text,
                         "report still prints the rounded-volume product as a tonnage")
        self.assertNotIn("Tonnage (used for amount)", text,
                         "the misleading label is still present")

    def test_pdf_labels_rounded_values_as_presentation_only(self):
        self.make_assessed_block()
        text = pdf_text(self.get_pdf().content)
        self.assertIn("Rounded for presentation", text)
        self.assertIn("exact basis", text)

    def test_pdf_withholds_the_basis_when_measurement_changed_after_assessment(self):
        block, _ = self.make_assessed_block()
        block.measurement.length_m = 0.9999
        block.save()
        text = pdf_text(self.get_pdf().content)
        self.assertIn("Not restatable", text)
        self.assertNotIn("0.0636870221325", text)
        self.assertIn("45.85", text, "the stored payable amount must still be shown")

    # ---- Phase 6F: EVERY registered block exports ---------------------------

    def test_an_arbitrary_newly_registered_block_exports(self):
        """The core 6F guarantee, with nothing hardcoded.

        Uses generated identifiers, not block2/Block3/Block4/block5, so this
        proves the property for ANY future field submission rather than for the
        blocks that happened to exist when it was written.
        """
        import uuid

        for _ in range(3):
            block_id = f"GR-{uuid.uuid4().hex[:10].upper()}"
            block = self.Block(
                block_id=block_id, status="measured", cv_status="not_applicable",
                submitted_quarry_id="Q-NEW", inspecting_officer_id="OFF-NEW",
                gps_latitude=16.414461, gps_longitude=80.564009,
                reference_warnings=["quarry_unresolved", "officer_unresolved"])
            block.measurement = self.Measurement(
                length_m=0.5, breadth_m=0.4, height_m=0.3,
                volume_m3=0.5 * 0.4 * 0.3, measurement_method="ar")
            block.save()
            self.AuditLog(block=block, block_id_snapshot=block_id,
                          action="ar_measurement_submitted", actor="OFF-NEW",
                          details="AR inspection").save()

            response = self.get_pdf(block_id)
            with self.subTest(block=block_id):
                self.assertEqual(response.status_code, 200, response.content[:200])
                self.assertEqual(response["Content-Type"], "application/pdf")
                text = pdf_text(response.content)
                self.assertIn(block_id, text)
                self.assertIn("0.5000", text)          # dimensions from the Block
                self.assertIn("0.060000", text)        # volume from the Block
                self.assertIn("16.414461", text)       # real GPS
                self.assertIn("AR", text)              # measurement method
                self.assertIn("Q-NEW", text)
                self.assertIn("Unresolved", text)      # honest reference status
                self.assertIn("Verification SHA-256", text)
                self.assertIn("NOT ASSESSED", text)    # no invented money

    def test_export_requires_no_assessment_and_writes_nothing(self):
        block = self.Block(block_id="GR-6F-NOWRITE", status="measured")
        block.measurement = self.Measurement(
            length_m=0.5, breadth_m=0.4, height_m=0.3, volume_m3=0.06,
            measurement_method="ar")
        block.save()
        before = (self.Block.objects.count(), self.Assessment.objects.count(),
                  self.AuditLog.objects.count())
        for _ in range(3):
            self.assertEqual(self.get_pdf("GR-6F-NOWRITE").status_code, 200)
        self.assertEqual(before, (self.Block.objects.count(), self.Assessment.objects.count(),
                                  self.AuditLog.objects.count()))
        self.assertIsNone(self.Assessment.objects(block=block).first(),
                          "exporting must not create an assessment")

    def test_filename_states_which_document_it_is(self):
        unassessed = self.Block(block_id="GR-6F-FILENAME", status="measured")
        unassessed.measurement = self.Measurement(
            length_m=0.5, breadth_m=0.4, height_m=0.3, volume_m3=0.06,
            measurement_method="ar")
        unassessed.save()
        disposition = self.get_pdf("GR-6F-FILENAME")["Content-Disposition"]
        self.assertIn("measurement_record_GR-6F-FILENAME.pdf", disposition)

        self.make_assessed_block("GR-6F-FILENAME-2")
        disposition = self.get_pdf("GR-6F-FILENAME-2")["Content-Disposition"]
        self.assertIn("inspection_report_GR-6F-FILENAME-2.pdf", disposition)

    def test_gps_absent_is_reported_as_not_captured(self):
        block = self.Block(block_id="GR-6F-NOGPS", status="measured")
        block.measurement = self.Measurement(
            length_m=0.5, breadth_m=0.4, height_m=0.3, volume_m3=0.06,
            measurement_method="ar")
        block.save()
        text = pdf_text(self.get_pdf("GR-6F-NOGPS").content)
        self.assertIn("GPS STATUS: NOT CAPTURED", text)
        self.assertNotIn("Latitude", text)

    def test_assessed_and_unassessed_titles_are_distinct(self):
        self.make_assessed_block("GR-6F-TITLE-A")
        unassessed = self.Block(block_id="GR-6F-TITLE-U", status="measured")
        unassessed.measurement = self.Measurement(
            length_m=0.5, breadth_m=0.4, height_m=0.3, volume_m3=0.06,
            measurement_method="ar")
        unassessed.save()

        assessed_text = pdf_text(self.get_pdf("GR-6F-TITLE-A").content)
        unassessed_text = pdf_text(self.get_pdf("GR-6F-TITLE-U").content)
        self.assertIn("Seigniorage Assessment", assessed_text)
        self.assertNotIn("Not Yet Assessed", assessed_text)
        self.assertIn("Measurement Record", unassessed_text)
        self.assertIn("Not Yet Assessed", unassessed_text)

    def test_unauthenticated_export_is_still_refused(self):
        """6F widened WHAT can be exported, not WHO may export it."""
        self.make_assessed_block("GR-6F-AUTH")
        from django.test import Client

        self.assertEqual(Client().get("/api/blocks/GR-6F-AUTH/pdf/").status_code, 401)
