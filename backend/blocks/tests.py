from django.test import SimpleTestCase, TestCase
from .calculations import calculate_volume, calculate_weight
from .services import get_applicable_rate, generate_assessment_report
from .models import Quarry, Block, Measurement, Assessment, AuditLog
import datetime

class BlockCalculationsTestCase(SimpleTestCase):
    def test_volume_calculation_valid(self):
        # 1.54m x 1.60m x 2.90m = 7.1456 m3
        vol = calculate_volume(1.54, 1.60, 2.90)
        self.assertAlmostEqual(vol, 7.1456, places=5)

        # Single integer dimension
        vol2 = calculate_volume(2, 3, 4)
        self.assertEqual(vol2, 24.0)

    def test_volume_calculation_invalid_values(self):
        # Zero dimension
        with self.assertRaises(ValueError) as ctx:
            calculate_volume(0, 1.5, 2.0)
        self.assertIn("strictly positive", str(ctx.exception))

        # Negative dimension
        with self.assertRaises(ValueError) as ctx:
            calculate_volume(1.5, -1.0, 2.0)
        self.assertIn("strictly positive", str(ctx.exception))

    def test_volume_calculation_invalid_types(self):
        # String instead of float
        with self.assertRaises(ValueError) as ctx:
            calculate_volume("1.5", 1.6, 2.0)
        self.assertIn("must be a float or integer", str(ctx.exception))

        # Boolean instead of float (isinstance checks should fail)
        with self.assertRaises(ValueError) as ctx:
            calculate_volume(1.5, True, 2.0)
        self.assertIn("must be a float or integer", str(ctx.exception))

    def test_weight_calculation_valid(self):
        # Volume 7.1456 m3, density 2.7 MT/m3 -> 19.29312 MT
        weight = calculate_weight(7.1456, 2.7)
        self.assertAlmostEqual(weight, 19.29312, places=5)

    def test_weight_calculation_invalid(self):
        # Negative density
        with self.assertRaises(ValueError):
            calculate_weight(5.0, -2.7)
        # String volume
        with self.assertRaises(ValueError):
            calculate_weight("5.0", 2.7)

    def test_applicable_rate_lookup(self):
        # Premium + Gangsaw Size -> 3000.0
        rate = get_applicable_rate("Premium", "Gangsaw Size")
        self.assertEqual(rate, 3000.0)

        # Standard + Mini Gangsaw Size -> 1800.0
        rate2 = get_applicable_rate("Standard", "Mini Gangsaw Size")
        self.assertEqual(rate2, 1800.0)

        # Fallback default rate for unknown combo
        rate3 = get_applicable_rate("UnknownCategory", "UnknownClassification")
        self.assertEqual(rate3, 1000.0)

    def test_generate_assessment_report(self):
        # Test full report generation logic
        report = generate_assessment_report(
            block_id="GR-001",
            length_m=1.54,
            breadth_m=1.60,
            height_m=2.90,
            category="Premium",
            classification="Gangsaw Size"
        )
        self.assertEqual(report["block_id"], "GR-001")
        self.assertEqual(report["volume_m3"], 7.1456)
        self.assertEqual(report["estimated_weight_mt"], 19.293)
        self.assertEqual(report["applicable_rate_per_mt"], 3000.0)
        self.assertEqual(report["indicative_seigniorage"], 57879.36) # 19.29312 * 3000
        self.assertFalse(report["is_official"])
        self.assertIn("POC ONLY", report["disclaimer"])


class MongoEngineModelsTestCase(TestCase):
    # This test verifies that the model classes are correctly defined and can be instantiated.
    def test_model_fields(self):
        q = Quarry(name="Test Quarry", location="Zone A")
        self.assertEqual(q.name, "Test Quarry")
        self.assertEqual(q.location, "Zone A")

        meas = Measurement(
            length_m=1.54,
            breadth_m=1.60,
            height_m=2.90,
            volume_m3=7.1456,
            confidence=0.95,
            measurement_method="cv"
        )

        b = Block(
            block_id="GR-TEST-002",
            quarry=q,
            image_paths=["/path/to/img1.png"],
            status="measured",
            measurement=meas
        )
        self.assertEqual(b.block_id, "GR-TEST-002")
        self.assertEqual(b.measurement.volume_m3, 7.1456)


from rest_framework.test import APITestCase
from django.urls import reverse

class BlockAPITestCase(APITestCase):
    def setUp(self):
        self.test_prefix = "TEST-API-CASE-"
        # Cleanup prior test blocks
        Block.objects(block_id__startswith=self.test_prefix).delete()
        
        # Setup a test Quarry
        self.quarry = Quarry(id="Q-TEST-BLOCK-API", name="API Test Quarry", location="Zone B")
        self.quarry.save()

    def tearDown(self):
        Block.objects(block_id__startswith=self.test_prefix).delete()
        Quarry.objects(name="API Test Quarry").delete()

    def test_create_block_success(self):
        url = reverse('block-list-create')
        data = {
            "block_id": f"{self.test_prefix}001",
            "quarry_id": str(self.quarry.id),
            "status": "pending",
            "measurement": {
                "length_m": 1.5,
                "breadth_m": 2.0,
                "height_m": 3.0,
                "volume_m3": 9.0,
                "confidence": 0.9,
                "measurement_method": "manual"
            }
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["block_id"], f"{self.test_prefix}001")
        self.assertEqual(response.data["measurement"]["volume_m3"], 9.0)

        # Check document actually persisted in MongoDB
        db_block = Block.objects(block_id=f"{self.test_prefix}001").first()
        self.assertIsNotNone(db_block)

    def test_create_block_invalid_input(self):
        url = reverse('block-list-create')
        data = {
            "block_id": f"{self.test_prefix}002",
            "measurement": {
                "length_m": -1.5,  # Invalid value
                "breadth_m": 2.0,
                "height_m": 3.0,
                "volume_m3": 9.0
            }
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn("measurement", response.data)

    def test_create_block_duplicate_id(self):
        url = reverse('block-list-create')
        data = {
            "block_id": f"{self.test_prefix}DUP",
            "measurement": {
                "length_m": 1.0,
                "breadth_m": 1.0,
                "height_m": 1.0,
                "volume_m3": 1.0
            }
        }
        # First creation
        response1 = self.client.post(url, data, format='json')
        self.assertEqual(response1.status_code, 201)

        # Second creation (duplicate block_id)
        response2 = self.client.post(url, data, format='json')
        self.assertEqual(response2.status_code, 400)
        self.assertIn("block_id", response2.data)

    def test_list_blocks(self):
        # Insert test block
        b = Block(block_id=f"{self.test_prefix}LIST1", status="pending")
        b.save()

        url = reverse('block-list-create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        block_ids = [block["block_id"] for block in response.data]
        self.assertIn(f"{self.test_prefix}LIST1", block_ids)

    def test_retrieve_block(self):
        b = Block(block_id=f"{self.test_prefix}RET1", status="pending")
        b.save()

        url = reverse('block-detail', kwargs={"block_id": f"{self.test_prefix}RET1"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["block_id"], f"{self.test_prefix}RET1")

    def test_retrieve_nonexistent_block(self):
        url = reverse('block-detail', kwargs={"block_id": f"{self.test_prefix}NONEXISTENT"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


class AssessmentAPITestCase(APITestCase):
    def setUp(self):
        self.test_prefix = "TEST-API-CASE-"
        # Cleanup leftover test blocks/assessments
        Block.objects(block_id__startswith=self.test_prefix).delete()
        
        # Setup a test Quarry
        self.quarry = Quarry(id="Q-TEST-ASS-API", name="API Test Quarry", location="Zone B")
        self.quarry.save()

        # Setup a measured Block
        self.measured_block = Block(
            block_id=f"{self.test_prefix}MEASURED",
            quarry=self.quarry,
            status="measured",
            measurement=Measurement(
                length_m=1.54,
                breadth_m=1.60,
                height_m=2.90,
                volume_m3=7.1456,
                confidence=0.95,
                measurement_method="manual"
            )
        )
        self.measured_block.save()

        # Setup an unmeasured Block
        self.unmeasured_block = Block(
            block_id=f"{self.test_prefix}UNMEASURED",
            quarry=self.quarry,
            status="pending"
        )
        self.unmeasured_block.save()

    def tearDown(self):
        # Cascades to Assessment documents as well
        Block.objects(block_id__startswith=self.test_prefix).delete()
        Quarry.objects(name="API Test Quarry").delete()

    def test_create_assessment_success(self):
        url = reverse('assessment-list-create')
        data = {
            "block_id": self.measured_block.block_id,
            "granite_category": "Premium",
            "gangsaw_classification": "Gangsaw Size",
            "density": 2.7
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["block_id"], self.measured_block.block_id)
        self.assertEqual(response.data["volume_m3"], 7.1456)
        self.assertEqual(response.data["weight_mt"], 19.293)
        self.assertEqual(response.data["rate_per_mt"], 3000.0)
        self.assertEqual(response.data["indicative_seigniorage"], 57879.36)
        self.assertEqual(response.data["density_mt_per_m3"], 2.7)

        # Verify document persisted in MongoDB Atlas
        db_ass = Assessment.objects(block=self.measured_block).first()
        self.assertIsNotNone(db_ass)
        self.assertEqual(db_ass.density_mt_per_m3, 2.7)

    def test_create_assessment_unmeasured_block_fails(self):
        url = reverse('assessment-list-create')
        data = {
            "block_id": self.unmeasured_block.block_id,
            "granite_category": "Premium",
            "gangsaw_classification": "Gangsaw Size"
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn("block_id", response.data)

    def test_create_assessment_nonexistent_block_fails(self):
        url = reverse('assessment-list-create')
        data = {
            "block_id": f"{self.test_prefix}NONEXISTENT",
            "granite_category": "Premium",
            "gangsaw_classification": "Gangsaw Size"
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, 400)

    def test_create_assessment_duplicate_fails(self):
        url = reverse('assessment-list-create')
        data = {
            "block_id": self.measured_block.block_id,
            "granite_category": "Premium",
            "gangsaw_classification": "Gangsaw Size"
        }
        # First creation
        response1 = self.client.post(url, data, format='json')
        self.assertEqual(response1.status_code, 201)

        # Duplicate creation
        response2 = self.client.post(url, data, format='json')
        self.assertEqual(response2.status_code, 400)

    def test_list_assessments(self):
        # Pre-create assessment
        url = reverse('assessment-list-create')
        data = {
            "block_id": self.measured_block.block_id,
            "granite_category": "Standard",
            "gangsaw_classification": "Mini Gangsaw Size"
        }
        self.client.post(url, data, format='json')

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(len(response.data) >= 1)
        block_ids = [ass["block_id"] for ass in response.data]
        self.assertIn(self.measured_block.block_id, block_ids)

    def test_retrieve_assessment(self):
        # Pre-create assessment
        create_url = reverse('assessment-list-create')
        data = {
            "block_id": self.measured_block.block_id,
            "granite_category": "Standard",
            "gangsaw_classification": "Mini Gangsaw Size"
        }
        self.client.post(create_url, data, format='json')

        detail_url = reverse('assessment-detail', kwargs={"block_id": self.measured_block.block_id})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["block_id"], self.measured_block.block_id)
        self.assertEqual(response.data["granite_category"], "Standard")


from django.core.files.uploadedfile import SimpleUploadedFile
import cv2
import numpy as np
import shutil
import os
from unittest.mock import patch

class CVAPITestCase(APITestCase):
    def setUp(self):
        self.test_prefix = "TEST-API-CV-"
        # Cleanup leftover blocks
        Block.objects(block_id__startswith=self.test_prefix).delete()
        
        # Setup a test block
        self.block = Block(block_id=f"{self.test_prefix}001", status="pending")
        self.block.save()

        # Temporary files list for cleanup
        self.temp_files = []

    def tearDown(self):
        Block.objects(block_id__startswith=self.test_prefix).delete()
        # Cleanup temporary files
        for f in self.temp_files:
            if os.path.exists(f):
                os.remove(f)
        # Clear media test directory if created
        from django.conf import settings
        if os.path.exists(settings.MEDIA_ROOT):
            shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)

    @patch('cv_pipeline.pipeline.BlockSegmentor.segment')
    def test_measure_cv_success(self, mock_segment):
        # Import synthetic generator from our cv_pipeline library
        from cv_pipeline.test_pipeline import generate_synthetic_test_image
        
        temp_img_name = "temp_synthetic_test.png"
        self.temp_files.append(temp_img_name)
        
        # Generate the synthetic image (has block + markers)
        generate_synthetic_test_image(temp_img_name)

        # Setup mock to return the ground truth mask
        gt_mask = np.zeros((600, 800), dtype=np.uint8)
        # Front/Primary face: (150, 150) to (650, 450)
        cv2.rectangle(gt_mask, (150, 150), (650, 450), 255, -1)
        # Side face
        pts = np.array([[650, 150], [750, 100], [750, 400], [650, 450]], dtype=np.int32)
        cv2.fillPoly(gt_mask, [pts], 255)
        # Top face
        pts_top = np.array([[150, 150], [250, 100], [750, 100], [650, 150]], dtype=np.int32)
        cv2.fillPoly(gt_mask, [pts_top], 255)
        
        mock_segment.return_value = gt_mask
        
        with open(temp_img_name, 'rb') as f:
            image_bytes = f.read()
            
        uploaded_file = SimpleUploadedFile("block.png", image_bytes, content_type="image/png")
        url = reverse('block-measure-cv', kwargs={"block_id": self.block.block_id})
        
        response = self.client.post(url, {"image": uploaded_file}, format='multipart')
        
        if response.status_code != 200:
            print(f"API Error Response: {response.data}")
            
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["cv_status"], "success")
        self.assertEqual(response.data["status"], "measured")
        self.assertIsNotNone(response.data["measurement"])
        self.assertTrue(response.data["measurement"]["volume_m3"] > 0)
        self.assertTrue(len(response.data["raw_image_path"]) > 0)
        self.assertTrue(len(response.data["annotated_image_path"]) > 0)

        # Check document in database
        db_block = Block.objects(block_id=self.block.block_id).first()
        self.assertEqual(db_block.cv_status, "success")
        self.assertEqual(db_block.status, "measured")
        self.assertIsNotNone(db_block.measurement)

    def test_measure_cv_no_markers_fails(self):
        # Create blank image with no markers
        blank = np.zeros((200, 200, 3), dtype=np.uint8)
        _, encoded = cv2.imencode('.png', blank)
        image_bytes = encoded.tobytes()
        
        uploaded_file = SimpleUploadedFile("blank.png", image_bytes, content_type="image/png")
        url = reverse('block-measure-cv', kwargs={"block_id": self.block.block_id})
        
        response = self.client.post(url, {"image": uploaded_file}, format='multipart')
        
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

        # Check database preserves failure state
        db_block = Block.objects(block_id=self.block.block_id).first()
        self.assertEqual(db_block.cv_status, "failed")
        self.assertTrue(len(db_block.cv_error_message) > 0)


class SupervisionAPITestCase(APITestCase):
    def setUp(self):
        self.test_prefix = "TEST-API-SUP-"
        # Cleanup leftover blocks
        Block.objects(block_id__startswith=self.test_prefix).delete()
        
        # Setup a block with a measurement
        from .models import Measurement
        self.measured_block = Block(
            block_id=f"{self.test_prefix}001",
            status="measured",
            measurement=Measurement(
                length_m=1.5,
                breadth_m=2.0,
                height_m=3.0,
                volume_m3=9.0,
                confidence=0.9,
                measurement_method="cv"
            )
        )
        self.measured_block.save()

    def tearDown(self):
        Block.objects(block_id__startswith=self.test_prefix).delete()

    def test_override_dimensions(self):
        url = reverse('block-override', kwargs={"block_id": self.measured_block.block_id})
        data = {
            "length_m": 2.0,
            "breadth_m": 2.5,
            "height_m": 3.0,
            "reason": "Occluded side face corner",
            "actor": "Mining Inspector"
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["is_overridden"], True)
        self.assertEqual(response.data["override_reason"], "Occluded side face corner")
        self.assertEqual(response.data["measurement"]["volume_m3"], 15.0)
        
        # Confirm original measurement is preserved
        self.assertIsNotNone(response.data["original_measurement"])
        self.assertEqual(response.data["original_measurement"]["volume_m3"], 9.0)

        # Verify audit log was created
        from .models import AuditLog
        db_block = Block.objects(block_id=self.measured_block.block_id).first()
        log = AuditLog.objects(block=db_block).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.action, "manual_override")
        self.assertEqual(log.actor, "Mining Inspector")

    def test_approve_block(self):
        url = reverse('block-approve', kwargs={"block_id": self.measured_block.block_id})
        data = {
            "approval_status": "approved",
            "actor": "District Director"
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["approval_status"], "approved")
        self.assertEqual(response.data["approved_by"], "District Director")

        # Verify audit log
        from .models import AuditLog
        db_block = Block.objects(block_id=self.measured_block.block_id).first()
        log = AuditLog.objects(block=db_block, action="block_approval_approved").first()
        self.assertIsNotNone(log)

    def test_download_pdf_report(self):
        # Create an assessment to ensure PDF reports print assessment details
        from .models import Assessment
        assessment = Assessment(
            block=self.measured_block,
            granite_category="Premium",
            gangsaw_classification="Gangsaw Size",
            volume_m3=9.0,
            weight_mt=24.3,
            rate_per_mt=3000.0,
            indicative_seigniorage=72900.0,
            density_mt_per_m3=2.7,
            status="finalized"
        )
        assessment.save()

        url = reverse('block-pdf', kwargs={"block_id": self.measured_block.block_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(len(response.content) > 0)
        
        # Cleanup assessment
        assessment.delete()




