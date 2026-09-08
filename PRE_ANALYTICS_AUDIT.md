# Pre-Analytics Implementation Audit Report
## AP RTGS Sizing & Seigniorage POC

* **Audit Date:** August 31, 2026
* **Workspace Path:** `c:\granite-blocks`
* **Audit Objective:** Backup, document, and record the exact state of the system before writing the analytics and mock data layers.

---

## 1. EXISTING DATABASE MODELS (MongoEngine)
All database configurations are defined in [models.py](file:///c:/granite-blocks/backend/blocks/models.py).

### A. Quarry
* **Collection Name:** `quarries`
* **Fields:**
  * `id` (`StringField`, primary_key=True, max_length=100)
  * `name` (`StringField`, required=True, max_length=255)
  * `location` (`StringField`, max_length=500)
  * `created_at` (`DateTimeField`, default=datetime.datetime.utcnow)

### B. Measurement (Embedded Document)
* **Fields:**
  * `length_m` (`FloatField`, required=True)
  * `breadth_m` (`FloatField`, required=True)
  * `height_m` (`FloatField`, required=True)
  * `volume_m3` (`FloatField`, required=True)
  * `confidence` (`FloatField`, default=1.0)
  * `measurement_method` (`StringField`, default='manual', max_length=50)
  * `measured_at` (`DateTimeField`, default=datetime.datetime.utcnow)

### C. Block
* **Collection Name:** `blocks`
* **Fields:**
  * `block_id` (`StringField`, required=True, unique=True, max_length=100)
  * `quarry` (`ReferenceField` to Quarry)
  * `image_paths` (`ListField` of `StringField`)
  * `captured_at` (`DateTimeField`)
  * `gps_latitude` (`FloatField`)
  * `gps_longitude` (`FloatField`)
  * `status` (`StringField`, default='pending', max_length=50)
  * `measurement` (`EmbeddedDocumentField` to Measurement)
  * `cv_status` (`StringField`, default='pending', max_length=50)
  * `cv_error_message` (`StringField`, max_length=500)
  * `raw_image_path` (`StringField`, max_length=500)
  * `annotated_image_path` (`StringField`, max_length=500)
  * `is_overridden` (`BooleanField`, default=False)
  * `original_measurement` (`EmbeddedDocumentField` to Measurement)
  * `override_reason` (`StringField`, max_length=500)
  * `approval_status` (`StringField`, default='pending', max_length=50)
  * `approved_by` (`StringField`, max_length=100)
  * `approved_at` (`DateTimeField`)
  * `created_at` (`DateTimeField`, default=datetime.datetime.utcnow)
  * `updated_at` (`DateTimeField`, default=datetime.datetime.utcnow)

### D. Assessment
* **Collection Name:** `assessments`
* **Fields:**
  * `block` (`ReferenceField` to Block, required=True, delete=CASCADE)
  * `granite_category` (`StringField`, required=True, max_length=100)
  * `gangsaw_classification` (`StringField`, required=True, max_length=100)
  * `volume_m3` (`FloatField`, required=True)
  * `weight_mt` (`FloatField`, required=True)
  * `rate_per_mt` (`FloatField`, required=True)
  * `indicative_seigniorage` (`FloatField`, required=True)
  * `density_mt_per_m3` (`FloatField`, required=True)
  * `weighbridge_weight_mt` (`FloatField`)
  * `variance_pct` (`FloatField`)
  * `dispatch_return_weight_mt` (`FloatField`)
  * `dispatch_variance_pct` (`FloatField`)
  * `status` (`StringField`, default='draft', max_length=50)
  * `created_at` (`DateTimeField`, default=datetime.datetime.utcnow)

### E. AuditLog
* **Collection Name:** `audit_logs`
* **Fields:**
  * `block` (`ReferenceField` to Block, delete=CASCADE)
  * `action` (`StringField`, required=True, max_length=100)
  * `actor` (`StringField`, required=True, max_length=100)
  * `details` (`StringField`)
  * `timestamp` (`DateTimeField`, default=datetime.datetime.utcnow)

---

## 2. EXISTING REST API ENDPOINTS
Mapped from [urls.py](file:///c:/granite-blocks/backend/blocks/urls.py):

* `GET /api/blocks/` -> `BlockListCreateAPIView` (List Blocks)
* `POST /api/blocks/` -> `BlockListCreateAPIView` (Create Block)
* `GET /api/blocks/<block_id>/` -> `BlockDetailAPIView` (Retrieve Block)
* `POST /api/blocks/<block_id>/measure-cv/` -> `BlockMeasureCVAPIView` (Run CV)
* `POST /api/blocks/<block_id>/override/` -> `BlockOverrideAPIView` (Manual override)
* `POST /api/blocks/<block_id>/approve/` -> `BlockApproveAPIView` (Approve/Reject)
* `GET /api/blocks/<block_id>/pdf/` -> `BlockPDFAPIView` (ReportLab report stream)
* `GET /api/blocks/<block_id>/audit-logs/` -> `BlockAuditLogsAPIView` (Audit history)
* `GET /api/assessments/` -> `AssessmentListCreateAPIView` (List assessments)
* `POST /api/assessments/` -> `AssessmentListCreateAPIView` (Run seigniorage calculations)
* `GET /api/assessments/<block_id>/` -> `AssessmentDetailAPIView` (Retrieve assessment)

---

## 3. EXISTING SUPERVISOR DASHBOARD ROUTES
The Supervisor Dashboard UI is implemented entirely inside [App.jsx](file:///c:/granite-blocks/supervisor-dashboard/src/App.jsx).
It is a single-screen layout navigating through states:
* Blocks list filters sidebar panel.
* Geometric metadata preview panel.
* Sizing override modal inputs dialog.
* Tax assessment rate calculate panel.
* Transaction log timeline auditing feed.
* Document download triggers.

There are currently no routers (e.g. React Router) or mapping/charting library layers (e.g. Recharts, Leaflet) installed.

---

## 4. EXISTING MOBILE & CV WORKFLOW
* **Mobile Workflow:** Field Officer app ([App.js](file:///c:/granite-blocks/field-officer-app/App.js)) logs Block IDs, captures longitude/latitude coordinates via expo-location, takes photographs via expo-image-picker, previews images, and initiates uploads.
* **CV Workflow:** Pipeline ([pipeline.py](file:///c:/granite-blocks/cv_pipeline/pipeline.py)) executes ArUco marker corner checks, YOLO segment contours (`yolov8n-seg.pt`), and maps perspective homography scales to calculate dimensions.
* **PDF Workflow:** Views call `generate_block_pdf` ([pdf_generator.py](file:///c:/granite-blocks/backend/blocks/pdf_generator.py)), compiling metadata, measurements, overrides, and assessments into ReportLab stream objects.

---

## 5. EXISTING UNIT TESTS
* [`test_pipeline.py`](file:///c:/granite-blocks/cv_pipeline/test_pipeline.py): Verifies marker pose detection and scaling using synthetic images.
* [`tests.py`](file:///c:/granite-blocks/backend/blocks/tests.py): Tests models and REST endpoints (currently fails 12 tests due to missing ID fields in Quarry test instances).

---
