# Analytics Implementation Audit Report
## AP RTGS AI Hackathon Sizing & Seigniorage POC

* **Audit Date:** August 31, 2026
* **Workspace Path:** `c:\granite-blocks`
* **Objective:** Audit the existing codebase to identify what has been built versus what is missing, and design the analytics layers to be implemented.

---

## 1. ANALYSIS OF CURRENT STATE

Based on a complete codebase inspection:

### A. ALREADY IMPLEMENTED
* **MongoDB Collections & Models:** Basic MongoEngine schemas for `Quarry`, `Block`, `Measurement` (embedded), `Assessment`, and `AuditLog` are implemented in `models.py`.
* **Field Officer Mobile App:** Expo app with camera/gallery image capture, preview, GPS tagging, local drafts (AsyncStorage), and multipart upload endpoints.
* **Supervisor Web Dashboard:** Core dashboard displaying lists, filter menus, override dialogs, approval status triggers, seigniorage calculations, and ReportLab PDF downloads.
* **Sizing pipeline:** Marker detectors, homography matrix estimation, and YOLO segmenter integration layers are functional.

### B. PARTIALLY IMPLEMENTED
* **DB Schemas:** Models exist but lack extended analytics fields like `district`, `lessee_name`, `assigned_quarries`, `expected_vs_actual_variance_pct`, `sla_breach`, or `severity`.
* **YOLO Segmentation:** Runs `yolov8n-seg.pt` (pre-trained on COCO), which executes but does not detect granite.

### C. COMPLETELY MISSING
* **Officer Profile Entity:** No `Officer` model or collection exists.
* **Weekly summaries:** No `WeeklyOfficerSummary` or summary pipelines are implemented.
* **Mock-data engine:** No CLI tools or seed generator scripts exist (e.g. Django management commands).
* **Analytics REST API:** No `/api/analytics/...` endpoint paths are defined in URL routes or views.
* **Supervisor Dashboard Analytics Screens:** Screens/routes for `/analytics/officers`, `/analytics/quarries`, `/analytics/revenue`, `/analytics/compliance`, `/analytics/alerts`, `/analytics/map`, and `/analytics/overview` do not exist.
* **Visualization Libraries:** No charting libraries (e.g., Recharts) or mapping libraries (e.g., Leaflet) are installed or imported.

---

## 2. UNSAFE / INCORRECT ASSUMPTIONS IN SPECIFICATION
* **Assumption:** The unit test suite is fully functional.
  * *Correction:* The unit tests are currently broken because `Quarry` test instances are saved without required ID fields, raising a `ValidationError`. This must be documented and addressed during implementation.
* **Assumption:** Django handles CORS by default.
  * *Correction:* No CORS configuration exists in Django settings. Vite's proxy works for localhost, but production deployment requires explicit CORS middleware.

---

## 3. PROPOSED IMPLEMENTATION PLAN

### Step 1: Schema Updates
Add the required analytics fields and new models (`Officer`, `WeeklyOfficerSummary`) directly into `backend/blocks/models.py`.

### Step 2: Idempotent Mock Data Generator
Create a Django management command `generate_mock_data.py` to seed 8 quarries, 15 officers, 150-250 blocks, and 6 weeks of historical inspection, override, assessment, and compliance records.

### Step 3: Analytics API Views
Implement the REST endpoints under a new routing path `/api/analytics/...`:
* `/api/analytics/officers/` (list/scores)
* `/api/analytics/officers/<officer_id>/weekly/` (trends)
* `/api/analytics/quarries/comparison/` (compare metrics)
* `/api/analytics/quarries/<quarry_id>/trend/` (revenue/block counts)
* `/api/analytics/revenue/summary/` (categories & totals)
* `/api/analytics/revenue/leakage/` (estimated recovery)
* `/api/analytics/audit-readiness/` (Green/Amber/Red stats)
* `/api/analytics/alerts/` (compliance notices)
* `/api/analytics/map-data/` (coordinate plotting)

### Step 4: Dashboard UI Updates
* Install `recharts` and `leaflet`/`react-leaflet` dependencies.
* Build the dashboard views for `/analytics/...` matching the visual style.
* Connect dashboards to the new APIs, adding demo banners to indicate synthetic data usage.

---
