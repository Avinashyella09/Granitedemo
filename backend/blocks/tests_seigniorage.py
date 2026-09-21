"""
Phase 3 tests: official RTGS seigniorage calculation and the assessment endpoint.

DATABASE ISOLATION - read before changing anything here.

mongoengine is rebound to in-memory mongomock in setUpClass, so these tests can
never reach the real Atlas cluster. The real block2 record is NEVER touched: an
equivalent block is created inside mongomock using the same stored dimensions.

The legacy blocks/tests.py writes to the live database and rmtree's the real
MEDIA_ROOT. Do not merge these into it, and do not run it.

Run only this module:
    ./venv/bin/python manage.py test blocks.tests_seigniorage
"""

from decimal import Decimal

import mongoengine
import mongomock
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from rest_framework.authtoken.models import Token

from blocks import seigniorage

ASSESSMENT_ENDPOINT = "/api/assessments/"

# The real measurement proven end-to-end in Step 2E. Used as realistic input
# only - the Atlas document itself is never read or modified by these tests.
BLOCK2_L, BLOCK2_B, BLOCK2_H = 0.3455, 0.3345, 0.2041


class OfficialRateTableTests(SimpleTestCase):
    """Pure-function tests. No database at all."""

    def rate(self, category, length_m, breadth_m):
        classification = seigniorage.classify_gangsaw(length_m, breadth_m)
        return seigniorage.get_official_rate(category, classification), classification

    # ---- 1-10: every official band, both sides of the threshold -------------

    def test_black_galaxy_above(self):
        rate, cls = self.rate("Black Galaxy", 3.0, 2.0)
        self.assertEqual(cls, seigniorage.ABOVE_GANGSAW)
        self.assertEqual(rate, Decimal("1830"))

    def test_black_galaxy_within(self):
        rate, cls = self.rate("Black Galaxy", 2.0, 1.0)
        self.assertEqual(cls, seigniorage.WITHIN_GANGSAW)
        self.assertEqual(rate, Decimal("1530"))

    def test_black_granite_other_above(self):
        rate, _ = self.rate("Black Granite (Other)", 3.0, 2.0)
        self.assertEqual(rate, Decimal("1520"))

    def test_black_granite_other_within(self):
        rate, _ = self.rate("Black Granite (Other)", 2.0, 1.0)
        self.assertEqual(rate, Decimal("1300"))

    def test_colour_granite_above(self):
        for variety in ("Colour Granite", "Srikakulam Blue", "Moon White",
                        "River White Vizag", "Leptinites", "Black Pearl"):
            with self.subTest(variety=variety):
                rate, _ = self.rate(variety, 3.0, 2.0)
                self.assertEqual(rate, Decimal("1660"))

    def test_colour_granite_within(self):
        for variety in ("Colour Granite", "Srikakulam Blue", "Moon White",
                        "River White Vizag", "Leptinites", "Black Pearl"):
            with self.subTest(variety=variety):
                rate, _ = self.rate(variety, 2.0, 1.0)
                self.assertEqual(rate, Decimal("1410"))

    def test_silver_waves_group_above(self):
        for variety in ("Silver Waves", "Madanapalli White", "Iscon White",
                        "Silver Waves / Madanapalli White / Iscon White"):
            with self.subTest(variety=variety):
                rate, _ = self.rate(variety, 3.0, 2.0)
                self.assertEqual(rate, Decimal("1360"))

    def test_silver_waves_group_within(self):
        for variety in ("Silver Waves", "Madanapalli White", "Iscon White"):
            with self.subTest(variety=variety):
                rate, _ = self.rate(variety, 2.0, 1.0)
                self.assertEqual(rate, Decimal("1200"))

    def test_others_above(self):
        rate, _ = self.rate("Others", 3.0, 2.0)
        self.assertEqual(rate, Decimal("940"))

    def test_others_within(self):
        rate, _ = self.rate("Others", 2.0, 1.0)
        self.assertEqual(rate, Decimal("720"))

    # ---- 11-12: the boundary ------------------------------------------------

    def test_exact_threshold_is_within_gangsaw(self):
        """Exactly 270cm x 150cm. The brief's below/within band is '<=', so the
        boundary belongs to WITHIN, not above."""
        self.assertEqual(
            seigniorage.classify_gangsaw(2.70, 1.50), seigniorage.WITHIN_GANGSAW
        )

    def test_just_above_threshold(self):
        self.assertEqual(
            seigniorage.classify_gangsaw(2.7001, 1.5001), seigniorage.ABOVE_GANGSAW
        )

    def test_one_dimension_above_is_not_enough(self):
        """Both dimensions must exceed the threshold."""
        self.assertEqual(
            seigniorage.classify_gangsaw(3.00, 1.50), seigniorage.WITHIN_GANGSAW
        )
        self.assertEqual(
            seigniorage.classify_gangsaw(2.70, 2.00), seigniorage.WITHIN_GANGSAW
        )

    # ---- 13-16: arithmetic --------------------------------------------------

    def test_volume_is_length_times_breadth_times_height(self):
        result = seigniorage.calculate(2.0, 1.5, 1.0, "Black Galaxy", 2.7)
        self.assertAlmostEqual(result["volume_m3"], 3.0, places=6)

    def test_tonnage_is_volume_times_density(self):
        result = seigniorage.calculate(2.0, 1.5, 1.0, "Black Galaxy", 2.7)
        self.assertAlmostEqual(result["tonnage_mt"], 3.0 * 2.7, places=3)

    def test_rate_selection_matches_classification(self):
        above = seigniorage.calculate(3.0, 2.0, 1.0, "Black Galaxy", 2.7)
        within = seigniorage.calculate(2.0, 1.0, 1.0, "Black Galaxy", 2.7)
        self.assertEqual(above["rate_per_mt"], 1830.0)
        self.assertEqual(within["rate_per_mt"], 1530.0)

    def test_seigniorage_is_tonnage_times_official_rate(self):
        result = seigniorage.calculate(3.0, 2.0, 1.0, "Black Galaxy", 2.7)
        expected = (Decimal("3.0") * Decimal("2.0") * Decimal("1.0")
                    * Decimal("2.7") * Decimal("1830"))
        self.assertEqual(
            Decimal(str(result["seigniorage_amount"])),
            expected.quantize(Decimal("0.01")),
        )

    def test_real_block2_measurement_prices_deterministically(self):
        """Uses the real Step 2E dimensions as input. Reads nothing from Atlas."""
        result = seigniorage.calculate(BLOCK2_L, BLOCK2_B, BLOCK2_H, "Others", 2.7)
        self.assertEqual(result["gangsaw_classification"], seigniorage.WITHIN_GANGSAW)
        self.assertEqual(result["rate_per_mt"], 720.0)
        self.assertAlmostEqual(result["volume_m3"], 0.023588, places=6)

    # ---- 17: unknown category rejected --------------------------------------

    def test_unknown_category_raises_not_defaults(self):
        for bad in ("Premium", "Standard", "Commercial", "Granite", "", None, "   "):
            with self.subTest(category=bad):
                with self.assertRaises(seigniorage.SeigniorageError):
                    seigniorage.calculate(2.0, 1.0, 1.0, bad, 2.7)

    def test_old_poc_categories_are_no_longer_priced(self):
        """The retired POC table's names must not resolve to any rate."""
        from blocks.config import GRANITE_CATEGORIES

        for legacy in GRANITE_CATEGORIES:
            with self.subTest(legacy=legacy):
                with self.assertRaises(seigniorage.SeigniorageError):
                    seigniorage.resolve_category(legacy)

    def test_category_matching_is_case_and_space_insensitive_but_canonical(self):
        self.assertEqual(seigniorage.resolve_category("  black   GALAXY "), "Black Galaxy")

    def test_no_default_rate_fallback_exists(self):
        """The POC table's silent 1000.0 fallback must not survive anywhere."""
        rates = [r for pair in seigniorage.RATE_GROUPS.values() for r in pair]
        self.assertNotIn(Decimal("1000"), rates)


class AssessmentEndpointTests(TestCase):
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
        self.Block.objects.delete()
        self.Assessment.objects.delete()
        self.AuditLog.objects.delete()

        # Phase 6C: POST /api/assessments/ is the money-determining write and no
        # longer accepts anonymous callers. One default header authenticates
        # every request in this class, so no assertion below changed - these
        # tests are about what the SERVER derives, not about who may call it.
        user = User.objects.create_user(username="assess-6c", password="x-test-pass-123")
        token = Token.objects.get_or_create(user=user)[0].key
        self.client.defaults["HTTP_AUTHORIZATION"] = f"Token {token}"
        self.actor_username = user.username

    def make_block(self, block_id="GR-ASSESS-1", l=3.0, b=2.0, h=1.0):
        block = self.Block(block_id=block_id, status="measured")
        block.measurement = self.Measurement(
            length_m=l, breadth_m=b, height_m=h,
            volume_m3=l * b * h, measurement_method="ar",
        )
        block.save()
        return block

    def post(self, **payload):
        body = {"block_id": "GR-ASSESS-1", "granite_category": "Black Galaxy"}
        body.update(payload)
        return self.client.post(ASSESSMENT_ENDPOINT, body, content_type="application/json")

    # ---- safety net ---------------------------------------------------------

    def test_not_pointed_at_real_cluster(self):
        conn = mongoengine.get_connection()
        self.assertIn("mongomock", type(conn).__module__.lower())
        self.assertEqual(mongoengine.get_db().name, "granite_blocks_test")

    # ---- 18-20: server authority -------------------------------------------

    def test_client_supplied_rate_is_ignored(self):
        self.make_block()
        response = self.post(rate_per_mt=1.0, indicative_seigniorage=1.0)
        self.assertEqual(response.status_code, 201, response.data)
        assessment = self.Assessment.objects.first()
        self.assertEqual(assessment.rate_per_mt, 1830.0)
        self.assertNotEqual(assessment.indicative_seigniorage, 1.0)

    def test_client_supplied_classification_is_ignored(self):
        """A 3.0 x 2.0 block is above gangsaw. Claiming 'Within' must not
        reduce the rate."""
        self.make_block(l=3.0, b=2.0)
        response = self.post(gangsaw_classification=seigniorage.WITHIN_GANGSAW)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["gangsaw_classification"], seigniorage.ABOVE_GANGSAW)
        self.assertEqual(response.data["rate_per_mt"], 1830.0)
        self.assertEqual(response.data["classification_source"], "server")

    def test_client_supplied_volume_is_ignored(self):
        self.make_block(l=3.0, b=2.0, h=1.0)
        response = self.post(volume_m3=999.0)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertAlmostEqual(response.data["volume_m3"], 6.0, places=6)

    def test_server_recomputes_from_stored_block(self):
        block = self.make_block(l=3.0, b=2.0, h=1.0)
        # Corrupt the stored volume; the engine must still use L x B x H.
        block.measurement.volume_m3 = 12345.0
        block.save()
        response = self.post()
        self.assertAlmostEqual(response.data["volume_m3"], 6.0, places=6)

    # ---- 17 at the API boundary --------------------------------------------

    def test_unknown_category_rejected_by_endpoint(self):
        self.make_block()
        response = self.post(granite_category="Premium")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.Assessment.objects.count(), 0)

    # ---- 21: duplicates -----------------------------------------------------

    def test_repeated_assessment_creates_no_duplicate(self):
        self.make_block()
        first = self.post()
        self.assertEqual(first.status_code, 201)
        second = self.post()
        self.assertEqual(second.status_code, 400, second.data)
        self.assertEqual(self.Assessment.objects.count(), 1)

    # ---- 22: audit ----------------------------------------------------------

    def test_audit_contains_full_calculation_trace(self):
        self.make_block(l=3.0, b=2.0, h=1.0)
        self.post()
        logs = list(self.AuditLog.objects(action="seigniorage_assessed"))
        self.assertEqual(len(logs), 1)
        details = logs[0].details
        for fragment in ("block_id=GR-ASSESS-1", "volume=6.0", "density=2.7",
                         "tonnage=16.2", "category=Black Galaxy",
                         "classification=Above Gangsaw", "rate=1830.0",
                         "amount=29646.0", "schedule=RTGS-OFFICIAL-2026-09"):
            self.assertIn(fragment, details, f"missing {fragment!r}")

    def test_audit_records_that_a_client_classification_was_overridden(self):
        self.make_block(l=3.0, b=2.0)
        self.post(gangsaw_classification=seigniorage.WITHIN_GANGSAW)
        details = self.AuditLog.objects(action="seigniorage_assessed").first().details
        self.assertIn("client value ignored", details)

    def test_failed_assessment_creates_no_audit(self):
        self.make_block()
        self.post(granite_category="Premium")
        self.assertEqual(self.AuditLog.objects(action="seigniorage_assessed").count(), 0)

    # ---- 23: nothing fabricated --------------------------------------------

    def test_no_fabricated_data_created(self):
        from blocks.models import Officer, Quarry

        self.make_block()
        self.post()
        self.assertEqual(Quarry.objects.count(), 0)
        self.assertEqual(Officer.objects.count(), 0)
        self.assertEqual(self.Block.objects.count(), 1)

    def test_block_without_measurement_rejected(self):
        self.Block(block_id="GR-NO-MEAS", status="pending").save()
        response = self.post(block_id="GR-NO-MEAS")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.Assessment.objects.count(), 0)
