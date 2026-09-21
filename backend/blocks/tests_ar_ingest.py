"""
Tests for POST /api/blocks/ar-measure/ - the live iOS AR ingestion path.

DATABASE AND MEDIA ISOLATION - read before changing anything here.

  * mongoengine is rebound to an in-memory mongomock instance in setUpClass,
    so these tests can never reach the real Atlas cluster.
  * MEDIA_ROOT is redirected to a temporary directory per class and removed in
    tearDownClass, so the real backend/media/ tree is never written to or
    deleted.

The legacy blocks/tests.py does neither - it writes to the live database and
its CVAPITestCase.tearDown calls shutil.rmtree(settings.MEDIA_ROOT). Do not
merge these into that module, and do not run it against Atlas.

Run only this module:
    ./venv/bin/python manage.py test blocks.tests_ar_ingest
"""

import io
import shutil
import tempfile

import mongoengine
import mongomock
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

AR_ENDPOINT = "/api/blocks/ar-measure/"


def make_image_bytes(fmt="JPEG", size=(64, 64)):
    """A genuine small image. Test fixture data, not fabricated field data."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, (120, 120, 120)).save(buffer, format=fmt)
    return buffer.getvalue()


def image_upload(name="photo.jpg", fmt="JPEG"):
    content_type = "image/png" if fmt == "PNG" else "image/jpeg"
    return SimpleUploadedFile(name, make_image_bytes(fmt), content_type=content_type)


_TEMP_MEDIA = tempfile.mkdtemp(prefix="graniteblocks-ar-test-media-")


@override_settings(MEDIA_ROOT=_TEMP_MEDIA)
class ARIngestionTests(SimpleTestCase):
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
        from blocks.models import AuditLog, Block, Officer, Quarry

        cls.Block, cls.Quarry, cls.Officer, cls.AuditLog = Block, Quarry, Officer, AuditLog

    @classmethod
    def tearDownClass(cls):
        mongoengine.disconnect(alias="default")
        shutil.rmtree(_TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.Block.objects.delete()
        self.Quarry.objects.delete()
        self.Officer.objects.delete()
        self.AuditLog.objects.delete()

    def payload(self, **overrides):
        data = {
            "block_id": "GR-TEST-001",
            "quarry_id": "Q-9982",
            "officer_id": "OFF-41",
            "length_m": "2.0",
            "breadth_m": "1.5",
            "height_m": "1.0",
            "volume_m3": "3.0",
            "image": image_upload(),
        }
        data.update(overrides)
        return {k: v for k, v in data.items() if v is not None}

    def post(self, **overrides):
        return self.client.post(AR_ENDPOINT, self.payload(**overrides), format="multipart")

    # ---- safety net ---------------------------------------------------------

    def test_not_pointed_at_real_cluster_or_real_media(self):
        from django.conf import settings

        conn = mongoengine.get_connection()
        self.assertIn("mongomock", type(conn).__module__.lower())
        self.assertEqual(mongoengine.get_db().name, "granite_blocks_test")
        self.assertTrue(str(settings.MEDIA_ROOT).startswith(tempfile.gettempdir()))

    # ---- happy path ---------------------------------------------------------

    def test_valid_ar_submission_creates_block(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.data)
        block = self.Block.objects(block_id="GR-TEST-001").first()
        self.assertIsNotNone(block)
        self.assertEqual(block.status, "measured")
        self.assertEqual(block.measurement.measurement_method, "ar")
        self.assertTrue(response.data["created_new_block"])
        # Existing mobile/dashboard contract keys must survive.
        for key in ("id", "block_id", "quarry_id", "status", "measurement",
                    "cv_status", "raw_image_path", "image_paths"):
            self.assertIn(key, response.data)

    def test_cv_status_is_honest_not_success(self):
        self.post()
        block = self.Block.objects(block_id="GR-TEST-001").first()
        self.assertEqual(block.cv_status, "not_applicable")
        self.assertNotEqual(block.cv_status, "success")

    # ---- dimensions and volume ---------------------------------------------

    def test_server_calculates_volume_and_ignores_client_value(self):
        # Client claims 99.0 for a 2.0 x 1.5 x 1.0 block.
        response = self.post(volume_m3="99.0")
        self.assertEqual(response.status_code, 201, response.data)
        block = self.Block.objects(block_id="GR-TEST-001").first()
        self.assertAlmostEqual(block.measurement.volume_m3, 3.0, places=6)
        self.assertNotAlmostEqual(block.measurement.volume_m3, 99.0, places=6)

    def test_client_volume_discrepancy_is_recorded_not_silently_dropped(self):
        response = self.post(volume_m3="99.0")
        check = response.data["volume_check"]
        self.assertFalse(check["agrees"])
        self.assertAlmostEqual(check["server_calculated_m3"], 3.0, places=6)
        self.assertAlmostEqual(check["client_reported_m3"], 99.0, places=6)
        self.assertIn("client_volume_mismatch", response.data["reference_warnings"])

    def test_matching_client_volume_agrees(self):
        response = self.post(volume_m3="3.0")
        self.assertTrue(response.data["volume_check"]["agrees"])
        self.assertNotIn("client_volume_mismatch", response.data["reference_warnings"])

    def test_missing_client_volume_is_accepted(self):
        response = self.post(volume_m3=None)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data["volume_check"]["agrees"])
        self.assertAlmostEqual(response.data["volume_check"]["server_calculated_m3"], 3.0, places=6)

    def test_invalid_dimensions_rejected(self):
        for field, value, why in [
            ("length_m", "0", "zero length"),
            ("breadth_m", "-1", "negative breadth"),
            ("height_m", "abc", "non-numeric height"),
            ("length_m", "", "empty length"),
        ]:
            with self.subTest(why=why):
                response = self.post(**{field: value})
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(self.Block.objects.count(), 0)

    # ---- GPS ----------------------------------------------------------------

    def test_zero_gps_is_preserved(self):
        """0.0 is the equator / prime meridian, not 'missing'."""
        response = self.post(gps_latitude="0.0", gps_longitude="0.0")
        self.assertEqual(response.status_code, 201, response.data)
        block = self.Block.objects(block_id="GR-TEST-001").first()
        self.assertEqual(block.gps_latitude, 0.0)
        self.assertEqual(block.gps_longitude, 0.0)
        self.assertIsNotNone(block.gps_latitude)

    def test_valid_gps_stored(self):
        response = self.post(gps_latitude="15.4021", gps_longitude="79.9865")
        self.assertEqual(response.status_code, 201, response.data)
        block = self.Block.objects(block_id="GR-TEST-001").first()
        self.assertAlmostEqual(block.gps_latitude, 15.4021, places=4)
        self.assertAlmostEqual(block.gps_longitude, 79.9865, places=4)

    def test_invalid_gps_rejected(self):
        for lat, lon, why in [
            ("91.0", "79.0", "latitude above 90"),
            ("-91.0", "79.0", "latitude below -90"),
            ("15.0", "181.0", "longitude above 180"),
            ("15.0", "-181.0", "longitude below -180"),
            ("abc", "79.0", "non-numeric latitude"),
        ]:
            with self.subTest(why=why):
                response = self.post(gps_latitude=lat, gps_longitude=lon)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(self.Block.objects.count(), 0)

    # ---- image --------------------------------------------------------------

    def test_missing_image_rejected(self):
        response = self.post(image=None)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.Block.objects.count(), 0)

    def test_non_image_file_rejected(self):
        bogus = SimpleUploadedFile("payload.jpg", b"<html>not an image at all</html>" * 10,
                                   content_type="image/jpeg")
        response = self.post(image=bogus)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "image_invalid")
        self.assertEqual(self.Block.objects.count(), 0)

    def test_empty_image_rejected(self):
        empty = SimpleUploadedFile("empty.jpg", b"", content_type="image/jpeg")
        response = self.post(image=empty)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.Block.objects.count(), 0)

    def test_png_upload_accepted(self):
        response = self.post(image=image_upload("photo.png", "PNG"))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(self.Block.objects(block_id="GR-TEST-001").first().raw_image_path.endswith(".png"))

    # ---- block_id safety ----------------------------------------------------

    def test_unsafe_block_id_rejected(self):
        for bad, why in [
            ("../../etc/passwd", "path traversal"),
            ("GR/TEST/001", "forward slashes"),
            ("GR\\TEST", "backslash"),
            ("..", "dot dot"),
            ("", "empty"),
            ("   ", "whitespace only"),
        ]:
            with self.subTest(why=why):
                response = self.post(block_id=bad)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(self.Block.objects.count(), 0)

    def test_traversal_block_id_writes_no_file_outside_media(self):
        import os
        from django.conf import settings

        self.post(block_id="../../escaped")
        for root, _dirs, files in os.walk(os.path.dirname(settings.MEDIA_ROOT)):
            for name in files:
                self.assertNotIn("escaped", name, f"file escaped MEDIA_ROOT: {root}/{name}")

    # ---- duplicates ---------------------------------------------------------

    def test_duplicate_block_id_rejected_and_original_preserved(self):
        first = self.post()
        self.assertEqual(first.status_code, 201)
        original_volume = self.Block.objects(block_id="GR-TEST-001").first().measurement.volume_m3

        second = self.post(length_m="9.0", breadth_m="9.0", height_m="9.0", volume_m3="729.0")
        self.assertEqual(second.status_code, 409, second.data)
        self.assertEqual(second.data["code"], "block_already_measured")

        block = self.Block.objects(block_id="GR-TEST-001").first()
        self.assertAlmostEqual(block.measurement.volume_m3, original_volume, places=6)
        self.assertEqual(self.Block.objects.count(), 1)

    def test_registered_but_unmeasured_block_can_be_measured(self):
        """The legitimate register-then-measure flow must still work."""
        self.Block(block_id="GR-TEST-002", status="pending").save()
        response = self.post(block_id="GR-TEST-002")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data["created_new_block"])
        self.assertEqual(self.Block.objects.count(), 1)

    # ---- unresolved references ---------------------------------------------

    def test_unknown_quarry_does_not_block_submission(self):
        response = self.post(quarry_id="Q-DOES-NOT-EXIST")
        self.assertEqual(response.status_code, 201, response.data)
        block = self.Block.objects(block_id="GR-TEST-001").first()
        self.assertIsNone(block.quarry)
        self.assertEqual(block.submitted_quarry_id, "Q-DOES-NOT-EXIST")
        self.assertIn("quarry_unresolved", block.reference_warnings)
        self.assertIn("quarry_unresolved", response.data["reference_warnings"])

    def test_known_quarry_links_normally(self):
        self.Quarry(id="Q-9982", name="Registered Quarry").save()
        response = self.post(quarry_id="Q-9982")
        self.assertEqual(response.status_code, 201, response.data)
        block = self.Block.objects(block_id="GR-TEST-001").first()
        self.assertIsNotNone(block.quarry)
        self.assertEqual(block.quarry.id, "Q-9982")
        self.assertNotIn("quarry_unresolved", block.reference_warnings)

    def test_unknown_officer_does_not_block_submission(self):
        response = self.post(officer_id="OFF-UNKNOWN")
        self.assertEqual(response.status_code, 201, response.data)
        block = self.Block.objects(block_id="GR-TEST-001").first()
        self.assertEqual(block.inspecting_officer_id, "OFF-UNKNOWN")
        self.assertIn("officer_unresolved", block.reference_warnings)

    def test_known_officer_resolves_without_warning(self):
        self.Officer(officer_id="OFF-41", name="Registered Officer").save()
        response = self.post(officer_id="OFF-41")
        self.assertEqual(response.status_code, 201, response.data)
        block = self.Block.objects(block_id="GR-TEST-001").first()
        self.assertNotIn("officer_unresolved", block.reference_warnings)

    def test_no_fabricated_reference_data(self):
        """An unresolved reference must never cause a Quarry/Officer to appear."""
        self.post(quarry_id="Q-GHOST", officer_id="OFF-GHOST")
        self.assertEqual(self.Quarry.objects.count(), 0)
        self.assertEqual(self.Officer.objects.count(), 0)

    # ---- audit --------------------------------------------------------------

    def test_audit_event_created_with_traceable_detail(self):
        self.post(quarry_id="Q-GHOST", officer_id="OFF-41", volume_m3="99.0")
        logs = list(self.AuditLog.objects(action="ar_measurement_submitted"))
        self.assertEqual(len(logs), 1)
        entry = logs[0]
        self.assertEqual(entry.actor, "OFF-41")
        for fragment in ("L=2.0", "B=1.5", "H=1.0", "V(server)=3.0",
                         "Q-GHOST", "OFF-41", "quarry_unresolved"):
            self.assertIn(fragment, entry.details, f"missing {fragment!r} in audit details")

    def test_failed_validation_creates_no_audit_and_no_block(self):
        response = self.post(length_m="-1")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.AuditLog.objects.count(), 0)
        self.assertEqual(self.Block.objects.count(), 0)

    def test_duplicate_rejection_creates_no_second_audit(self):
        self.post()
        self.post()
        self.assertEqual(self.AuditLog.objects(action="ar_measurement_submitted").count(), 1)
