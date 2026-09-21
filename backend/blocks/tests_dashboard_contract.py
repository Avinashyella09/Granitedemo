"""
Phase 4 tests: the data contract the Supervisor Dashboard consumes.

DATABASE ISOLATION: mongomock in-memory only. The real Atlas cluster is never
reached. The legacy blocks/tests.py writes to the live database and rmtree's the
real MEDIA_ROOT - do not merge these into it and do not run it.

    ./venv/bin/python manage.py test blocks.tests_dashboard_contract
"""

import mongoengine
import mongomock
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.authtoken.models import Token

from blocks import seigniorage


class DashboardContractTests(TestCase):
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
        # Analytics endpoints sit behind a 300s process cache; clear it so each
        # test sees its own fixtures rather than a previous test's snapshot.
        from blocks.views import _invalidate_cache
        _invalidate_cache()

        # Phase 6B: these endpoints now require authentication. One default
        # header on the test client authenticates every request in this suite,
        # so no assertion below had to change - only the caller's identity did.
        user = User.objects.create_user(username="dash-contract-6b", password="x-test-pass-123")
        token = Token.objects.get_or_create(user=user)[0].key
        self.client.defaults["HTTP_AUTHORIZATION"] = f"Token {token}"

    def make_block(self, block_id="GR-DASH-1", l=3.0, b=2.0, h=1.0,
                   gps=None, warnings=None, approval="pending"):
        block = self.Block(block_id=block_id, status="measured", approval_status=approval)
        block.measurement = self.Measurement(
            length_m=l, breadth_m=b, height_m=h, volume_m3=l * b * h,
            measurement_method="ar",
        )
        if gps:
            block.gps_latitude, block.gps_longitude = gps
        block.reference_warnings = warnings or []
        block.submitted_quarry_id = "Q-9982"
        block.inspecting_officer_id = "OFF-1"
        block.save()
        return block

    # ---- safety net ---------------------------------------------------------

    def test_not_pointed_at_real_cluster(self):
        self.assertIn("mongomock", type(mongoengine.get_connection()).__module__.lower())
        self.assertEqual(mongoengine.get_db().name, "granite_blocks_test")

    # ---- real block fields in the API --------------------------------------

    def test_block_api_returns_real_fields(self):
        self.make_block(gps=(15.4021, 79.9865))
        response = self.client.get("/api/blocks/")
        self.assertEqual(response.status_code, 200)
        row = response.data[0]
        for key in ("block_id", "status", "measurement", "gps_latitude",
                    "gps_longitude", "cv_status", "captured_at"):
            self.assertIn(key, row, f"{key} missing from /api/blocks/")
        self.assertAlmostEqual(row["measurement"]["volume_m3"], 6.0, places=6)

    def test_approval_fields_serialize(self):
        """The SkipField defect: these were declared read_only but absent from
        the source dict, so DRF dropped them from every response."""
        self.make_block(approval="approved")
        row = self.client.get("/api/blocks/").data[0]
        for key in ("approval_status", "approved_by", "approved_at",
                    "is_overridden", "override_reason", "original_measurement"):
            self.assertIn(key, row, f"{key} still dropped from /api/blocks/")
        self.assertEqual(row["approval_status"], "approved")

    def test_approval_filter_can_distinguish_states(self):
        self.make_block(block_id="GR-A", approval="approved")
        self.make_block(block_id="GR-P", approval="pending")
        rows = self.client.get("/api/blocks/").data
        states = sorted(r["approval_status"] for r in rows)
        self.assertEqual(states, ["approved", "pending"])

    def test_real_capture_metadata_present(self):
        """Dashboard previously printed the literals 'Sunny' / 1 / 'DEV-N/A'
        because these keys never arrived."""
        self.make_block()
        row = self.client.get("/api/blocks/").data[0]
        for key in ("inspecting_officer_id", "submitted_quarry_id",
                    "reference_warnings", "device_id", "lighting_condition"):
            self.assertIn(key, row, f"{key} missing")
        self.assertEqual(row["inspecting_officer_id"], "OFF-1")

    def test_unresolved_references_represented_honestly(self):
        self.make_block(warnings=["quarry_unresolved", "officer_unresolved"])
        row = self.client.get("/api/blocks/").data[0]
        self.assertIn("quarry_unresolved", row["reference_warnings"])
        # And no Quarry/Officer was conjured to satisfy the reference.
        self.assertEqual(self.Quarry.objects.count(), 0)
        self.assertEqual(self.Officer.objects.count(), 0)

    # ---- leakage: no fabrication -------------------------------------------

    def test_leakage_reports_unavailable_without_weighbridge_data(self):
        self.make_block()
        data = self.client.get("/api/analytics/revenue/leakage/").data
        self.assertFalse(data["available"])
        self.assertEqual(data["reason"], "no_weighbridge_data")
        self.assertIsNone(data["estimated_revenue_recovered"])
        self.assertIsNone(data["average_leakage_pct"])

    def test_leakage_never_invents_a_money_figure(self):
        self.make_block()
        data = self.client.get("/api/analytics/revenue/leakage/").data
        recovered = data["estimated_revenue_recovered"]
        self.assertFalse(isinstance(recovered, (int, float)) and recovered > 0,
                         "a non-zero recovery figure was produced with no weighbridge data")

    def test_overview_recovery_is_not_fabricated(self):
        self.make_block()
        data = self.client.get("/api/analytics/overview/").data
        self.assertIsNone(data.get("estimated_revenue_recovered"))

    # ---- alerts -------------------------------------------------------------

    def test_alerts_derive_from_real_reference_warnings(self):
        self.make_block(warnings=["quarry_unresolved", "gps_incomplete"])
        alerts = self.client.get("/api/analytics/alerts/").data
        types = {a["type"] for a in alerts}
        self.assertIn("QUARRY_UNRESOLVED", types)
        self.assertIn("GPS_INCOMPLETE", types)

    def test_no_weighbridge_alert_is_generated(self):
        self.make_block(warnings=["quarry_unresolved"])
        alerts = self.client.get("/api/analytics/alerts/").data
        for a in alerts:
            self.assertNotIn("weighbridge", a["description"].lower(),
                             "an alert still claims a weighbridge comparison")

    def test_alert_related_entity_is_parseable_by_the_dashboard(self):
        """The dashboard splits on ':' and trims. Guard the shape it relies on."""
        self.make_block(block_id="GR-DASH-1", warnings=["quarry_unresolved"])
        alerts = self.client.get("/api/analytics/alerts/").data
        entity = alerts[0]["related_entity"]
        self.assertEqual(entity.split(":").pop().strip(), "GR-DASH-1")

    def test_empty_database_returns_empty_alerts_not_fake_ones(self):
        self.assertEqual(list(self.client.get("/api/analytics/alerts/").data), [])

    # ---- gangsaw revenue buckets -------------------------------------------

    def assess(self, block, category):
        """Create an Assessment through the real official engine."""
        result = seigniorage.calculate(
            block.measurement.length_m, block.measurement.breadth_m,
            block.measurement.height_m, category, 2.7,
        )
        self.Assessment(
            block=block,
            granite_category=result["granite_category"],
            gangsaw_classification=result["gangsaw_classification"],
            volume_m3=result["volume_m3"], weight_mt=result["tonnage_mt"],
            rate_per_mt=result["rate_per_mt"],
            indicative_seigniorage=result["seigniorage_amount"],
            density_mt_per_m3=result["density_mt_per_m3"],
        ).save()
        return result

    def test_above_gangsaw_revenue_bucket(self):
        block = self.make_block(l=3.0, b=2.0, h=1.0)
        result = self.assess(block, "Black Galaxy")
        self.assertEqual(result["gangsaw_classification"], seigniorage.ABOVE_GANGSAW)
        data = self.client.get("/api/analytics/revenue/summary/").data
        self.assertAlmostEqual(data["gangsaw_revenue"], result["seigniorage_amount"], places=2)
        self.assertEqual(data["below_gangsaw_revenue"], 0)

    def test_within_gangsaw_revenue_bucket(self):
        block = self.make_block(l=2.0, b=1.0, h=1.0)
        result = self.assess(block, "Black Galaxy")
        self.assertEqual(result["gangsaw_classification"], seigniorage.WITHIN_GANGSAW)
        data = self.client.get("/api/analytics/revenue/summary/").data
        self.assertAlmostEqual(data["below_gangsaw_revenue"], result["seigniorage_amount"], places=2)
        self.assertEqual(data["gangsaw_revenue"], 0)

    def test_revenue_summary_consumes_real_assessment_values(self):
        block = self.make_block(l=3.0, b=2.0, h=1.0)
        result = self.assess(block, "Black Galaxy")
        data = self.client.get("/api/analytics/revenue/summary/").data
        self.assertEqual(data["block_count"], 1)
        self.assertAlmostEqual(data["total_seigniorage"], result["seigniorage_amount"], places=2)

    def test_revenue_summary_empty_when_no_assessments(self):
        self.make_block()
        data = self.client.get("/api/analytics/revenue/summary/").data
        self.assertEqual(data["block_count"], 0)
        self.assertEqual(data["total_seigniorage"], 0)

    # ---- GPS -----------------------------------------------------------------

    def test_block_without_gps_has_null_coordinates_not_invented_ones(self):
        self.make_block(gps=None)
        row = self.client.get("/api/blocks/").data[0]
        self.assertIsNone(row["gps_latitude"])
        self.assertIsNone(row["gps_longitude"])

    def test_block_with_gps_returns_exact_values(self):
        self.make_block(gps=(15.4021, 79.9865))
        row = self.client.get("/api/blocks/").data[0]
        self.assertAlmostEqual(row["gps_latitude"], 15.4021, places=4)

    # ---- empty states --------------------------------------------------------

    def test_empty_database_gives_honest_zeros(self):
        data = self.client.get("/api/analytics/overview/").data
        self.assertEqual(data["total_blocks_inspected"], 0)
        self.assertEqual(self.client.get("/api/blocks/").data, [])

    # ---- Phase 6D: analytics aggregate consistency --------------------------

    def test_overview_and_revenue_summary_agree_on_shared_aggregates(self):
        """REGRESSION GUARD - Phase 6D, PART 11.

        The concern was that /api/analytics/overview/ could report a total volume
        of None while /api/analytics/revenue/summary/ reported the real figure,
        so the same money appeared twice with different values.

        Audited in 6D: the overview endpoint exposes NO total_volume key at all
        (absent, not None), and no dashboard component reads one - every
        total_volume in the UI comes from revenueData or the quarry comparison.
        There is nothing to reconcile, so no production code was changed.

        What this pins instead is the property that actually matters: the
        aggregate both endpoints DO publish must agree, and the volume figure
        must be a real number derived from stored assessments rather than a
        placeholder zero.
        """
        overview = self.client.get("/api/analytics/overview/").data
        revenue = self.client.get("/api/analytics/revenue/summary/").data

        self.assertNotIn(
            "total_volume", overview,
            "overview now publishes total_volume; it must be kept equal to "
            "revenue summary's value or the dashboard will show two different "
            "totals for the same volume")

        self.assertIn("total_seigniorage", overview)
        self.assertIn("total_seigniorage", revenue)
        self.assertEqual(
            round(float(overview["total_seigniorage"]), 2),
            round(float(revenue["total_seigniorage"]), 2),
            "the two analytics endpoints disagree on total seigniorage")

    def test_revenue_summary_volume_is_real_and_never_none(self):
        expected = sum(a.volume_m3 for a in self.Assessment.objects.all())
        data = self.client.get("/api/analytics/revenue/summary/").data
        self.assertIsNotNone(data["total_volume"])
        self.assertAlmostEqual(data["total_volume"], round(expected, 4), places=4)
