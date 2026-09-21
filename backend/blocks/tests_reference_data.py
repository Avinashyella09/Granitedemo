"""
Tests for the reference-data management commands (create_quarry, create_officer).

DATABASE ISOLATION - read before changing anything here.

These tests never touch the real Atlas cluster. setUpClass disconnects whatever
connection settings.py established at import time and rebinds the default
mongoengine alias to an in-memory mongomock instance; tearDownClass restores
nothing, because the process ends. The existing blocks/tests.py does NOT do
this - it writes to the live database and its CVAPITestCase.tearDown calls
shutil.rmtree(settings.MEDIA_ROOT). Do not merge these into that module.

Run only this module:
    ./venv/bin/python manage.py test blocks.tests_reference_data
"""

from io import StringIO

import mongoengine
import mongomock
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class ReferenceDataCommandTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Drop the real connection established by settings.py and replace the
        # default alias with an in-memory one. Every Document in blocks.models
        # uses the default alias, so this redirects all of them at once.
        mongoengine.disconnect(alias="default")
        # mongoengine >= 0.27 removed the "mongomock://" URI form in favour of
        # passing the client class explicitly.
        mongoengine.connect(
            db="granite_blocks_test",
            alias="default",
            mongo_client_class=mongomock.MongoClient,
            uuidRepresentation="standard",
        )
        from blocks.models import Officer, Quarry  # imported after rebinding

        cls.Quarry = Quarry
        cls.Officer = Officer

    @classmethod
    def tearDownClass(cls):
        mongoengine.disconnect(alias="default")
        super().tearDownClass()

    def setUp(self):
        self.Quarry.objects.delete()
        self.Officer.objects.delete()

    def run_cmd(self, *args, **kwargs):
        out = StringIO()
        call_command(*args, stdout=out, stderr=StringIO(), **kwargs)
        return out.getvalue()

    # ---- safety net ---------------------------------------------------------

    def test_tests_are_not_pointed_at_the_real_cluster(self):
        """If this ever fails, stop: the suite is talking to real data."""
        conn = mongoengine.get_connection()
        self.assertIn("mongomock", type(conn).__module__.lower())
        self.assertEqual(mongoengine.get_db().name, "granite_blocks_test")

    # ---- quarry -------------------------------------------------------------

    def test_valid_quarry_creation(self):
        out = self.run_cmd(
            "create_quarry",
            "--id", "Q-TEST-1",
            "--name", "Test Quarry One",
            "--district", "Prakasam",
            "--total-area-hectares", "12.5",
            "--registered-date", "2024-01-15",
        )
        self.assertIn("created", out.lower())
        quarry = self.Quarry.objects(id="Q-TEST-1").first()
        self.assertIsNotNone(quarry)
        self.assertEqual(quarry.name, "Test Quarry One")
        self.assertEqual(quarry.district, "Prakasam")
        self.assertEqual(quarry.total_area_hectares, 12.5)
        self.assertEqual(quarry.registered_date.date().isoformat(), "2024-01-15")

    def test_duplicate_quarry_rejected_and_original_untouched(self):
        self.run_cmd("create_quarry", "--id", "Q-TEST-1", "--name", "Original Name")
        with self.assertRaises(CommandError) as ctx:
            self.run_cmd("create_quarry", "--id", "Q-TEST-1", "--name", "Replacement Name")
        self.assertIn("already exists", str(ctx.exception))
        # The critical assertion: the stored record was not overwritten.
        self.assertEqual(self.Quarry.objects(id="Q-TEST-1").first().name, "Original Name")
        self.assertEqual(self.Quarry.objects.count(), 1)

    def test_quarry_dry_run_writes_nothing(self):
        out = self.run_cmd(
            "create_quarry", "--id", "Q-TEST-DRY", "--name", "Never Written", "--dry-run"
        )
        self.assertIn("DRY RUN", out)
        self.assertIsNone(self.Quarry.objects(id="Q-TEST-DRY").first())
        self.assertEqual(self.Quarry.objects.count(), 0)

    def test_quarry_blank_name_rejected(self):
        with self.assertRaises(CommandError):
            self.run_cmd("create_quarry", "--id", "Q-TEST-2", "--name", "   ")
        self.assertEqual(self.Quarry.objects.count(), 0)

    def test_quarry_rejects_bad_numbers_and_dates(self):
        for args, why in [
            (["--total-area-hectares", "-5"], "negative area"),
            (["--total-area-hectares", "0"], "zero area"),
            (["--total-area-hectares", "abc"], "non-numeric area"),
            (["--registered-date", "15-01-2024"], "wrong date format"),
            (["--registered-date", "2024-13-45"], "impossible date"),
        ]:
            with self.subTest(why=why):
                with self.assertRaises(CommandError):
                    self.run_cmd(
                        "create_quarry", "--id", "Q-BAD", "--name", "Bad Quarry", *args
                    )
        self.assertEqual(self.Quarry.objects.count(), 0)

    def test_quarry_expiry_before_registration_rejected(self):
        with self.assertRaises(CommandError) as ctx:
            self.run_cmd(
                "create_quarry", "--id", "Q-BAD", "--name", "Bad Dates",
                "--registered-date", "2024-06-01",
                "--license-expiry-date", "2023-06-01",
            )
        self.assertIn("earlier than", str(ctx.exception))
        self.assertEqual(self.Quarry.objects.count(), 0)

    # ---- officer ------------------------------------------------------------

    def test_valid_officer_creation(self):
        out = self.run_cmd(
            "create_officer",
            "--officer-id", "OFF-TEST-1",
            "--name", "Test Officer",
            "--designation", "Mining Inspector",
            "--email", "officer@apmines.gov.in",
            "--active-status", "true",
        )
        self.assertIn("created", out.lower())
        officer = self.Officer.objects(officer_id="OFF-TEST-1").first()
        self.assertIsNotNone(officer)
        self.assertEqual(officer.name, "Test Officer")
        self.assertTrue(officer.active_status)

    def test_duplicate_officer_rejected_and_original_untouched(self):
        self.run_cmd("create_officer", "--officer-id", "OFF-TEST-1", "--name", "Original")
        with self.assertRaises(CommandError) as ctx:
            self.run_cmd("create_officer", "--officer-id", "OFF-TEST-1", "--name", "Replacement")
        self.assertIn("already exists", str(ctx.exception))
        self.assertEqual(self.Officer.objects(officer_id="OFF-TEST-1").first().name, "Original")
        self.assertEqual(self.Officer.objects.count(), 1)

    def test_officer_unknown_assigned_quarry_rejected(self):
        with self.assertRaises(CommandError) as ctx:
            self.run_cmd(
                "create_officer",
                "--officer-id", "OFF-TEST-2",
                "--name", "Test Officer",
                "--assigned-quarry", "Q-DOES-NOT-EXIST",
            )
        self.assertIn("Unknown quarry", str(ctx.exception))
        # No partial write: the officer must not exist despite valid id/name.
        self.assertEqual(self.Officer.objects.count(), 0)

    def test_officer_reports_all_unknown_quarries_at_once(self):
        self.run_cmd("create_quarry", "--id", "Q-REAL", "--name", "Real Quarry")
        with self.assertRaises(CommandError) as ctx:
            self.run_cmd(
                "create_officer", "--officer-id", "OFF-3", "--name", "Officer",
                "--assigned-quarry", "Q-REAL",
                "--assigned-quarry", "Q-MISSING-A",
                "--assigned-quarry", "Q-MISSING-B",
            )
        message = str(ctx.exception)
        self.assertIn("Q-MISSING-A", message)
        self.assertIn("Q-MISSING-B", message)
        self.assertEqual(self.Officer.objects.count(), 0)

    def test_officer_with_existing_quarry_links_correctly(self):
        self.run_cmd("create_quarry", "--id", "Q-LINK", "--name", "Linked Quarry")
        self.run_cmd(
            "create_officer", "--officer-id", "OFF-LINK", "--name", "Linked Officer",
            "--assigned-quarry", "Q-LINK",
        )
        officer = self.Officer.objects(officer_id="OFF-LINK").first()
        self.assertEqual(len(officer.assigned_quarries), 1)
        self.assertEqual(officer.assigned_quarries[0].id, "Q-LINK")

    def test_officer_dry_run_writes_nothing(self):
        out = self.run_cmd(
            "create_officer", "--officer-id", "OFF-DRY", "--name", "Never Written", "--dry-run"
        )
        self.assertIn("DRY RUN", out)
        self.assertIsNone(self.Officer.objects(officer_id="OFF-DRY").first())
        self.assertEqual(self.Officer.objects.count(), 0)

    def test_officer_rejects_malformed_input(self):
        for args, why in [
            (["--name", "   "], "blank name"),
            (["--name", "Valid", "--email", "not-an-email"], "malformed email"),
            (["--name", "Valid", "--active-status", "maybe"], "non-boolean active_status"),
            (["--name", "Valid", "--joined-date", "01/01/2024"], "wrong date format"),
        ]:
            with self.subTest(why=why):
                with self.assertRaises(CommandError):
                    self.run_cmd("create_officer", "--officer-id", "OFF-BAD", *args)
        self.assertEqual(self.Officer.objects.count(), 0)

    def test_no_random_values_are_generated(self):
        """Optional fields left unspecified must stay unset, not be invented."""
        self.run_cmd("create_quarry", "--id", "Q-MIN", "--name", "Minimal Quarry")
        quarry = self.Quarry.objects(id="Q-MIN").first()
        for field in ("location", "district", "region", "lessee_name",
                      "total_area_hectares", "registered_date", "license_expiry_date"):
            self.assertIsNone(getattr(quarry, field), f"{field} was populated without being supplied")
