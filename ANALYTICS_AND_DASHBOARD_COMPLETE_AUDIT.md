# Analytics & Dashboard Complete Audit Report
## Sizing & Seigniorage POC Sizing Audit

* **Audit Date:** August 31, 2026
* **Workspace Path:** `c:\granite-blocks`
* **Audit Scope:** Visual branding assets, database schemas, mock data states, REST API views, and React state parameters.

---

## 1. EXECUTIVE SUMMARY
This audit provides a comprehensive verification of the **AI-Based Smart Granite Block Sizing and Automated Seigniorage Assessment** Proof of Concept (POC). 

The audit confirms that all core modules, analytics API views, seeder commands, and dashboard routing panels are **fully implemented**. However, because the React states do not check for null/empty values when the database is unseeded, rendering calculations returns `NaN` (Not a Number) values. Additionally, CSS styling on the header banner section applies fixed constraints that crop the branding layout.

---

## 2. BANNER IMAGE AUDIT

* **Asset Files:** 
  * [`c:\granite-blocks\Test-images\banner_3.png`](file:///c:/granite-blocks/Test-images/banner_3.png)
  * [`c:\granite-blocks\supervisor-dashboard\public\banner_3.png`](file:///c:/granite-blocks/supervisor-dashboard/public/banner_3.png)
* **Referenced in:** [`App.jsx:366-373`](file:///c:/granite-blocks/supervisor-dashboard/src/App.jsx#L366-L373).
* **CSS Cropping Root Cause:** The layout uses the following inline style:
  `style={{ width: '100%', height: 'auto', display: 'block', maxHeight: '180px', objectFit: 'cover' }}`
  * **Issue:** `maxHeight: '180px'` combined with `objectFit: 'cover'` crops the top and bottom of the banner on wide screens.
  * **Fix:** Remove `maxHeight: '180px'` and `objectFit: 'cover'` to allow the image to scale naturally with its aspect ratio.

---

## 3. OVERVIEW DASHBOARD AUDIT

Verification of the Executive Overview metrics:

* **Total Blocks Inspected:**
  * Status: **PASS** (Implemented & Seeded).
  * Data Flow: `Block` collection -> `/api/analytics/overview/` -> `apiService.getExecutiveOverview()` -> `overviewData.total_blocks_inspected` -> HUD card.
* **Total Indicative Revenue:**
  * Status: **PASS** (Implemented & Seeded).
  * Data Flow: `Assessment` collection -> `/api/analytics/overview/` -> `apiService.getExecutiveOverview()` -> `overviewData.total_seigniorage` -> HUD card.
* **Manual Override Rate:**
  * Status: **PASS** (Implemented & Seeded).
  * Data Flow: `Block.is_overridden` -> `/api/analytics/overview/` -> `apiService.getExecutiveOverview()` -> `overviewData.override_rate` -> HUD card.
* **Estimated Leakage Saved:**
  * Status: **PASS** (Implemented & Seeded).
  * Data Flow: `Assessment.variance_pct` -> `/api/analytics/overview/` -> `apiService.getExecutiveOverview()` -> `overviewData.estimated_revenue_recovered` -> HUD card.
* **Weekly Block Sizing Trend (6 Weeks):**
  * Status: **PASS** (Implemented & Seeded).
  * Data Flow: `Assessment` collection -> `/api/analytics/revenue/summary/` -> `apiService.getRevenueSummary()` -> `weekly_revenue` line chart.
* **Audit Completeness & Compliance:**
  * Status: **PASS** (Implemented & Seeded).
  * Data Flow: Checklist checks on `Block` and `Assessment` collections -> `/api/analytics/audit-readiness/` -> `apiService.getAuditReadiness()` -> Donut chart.
* **Quarry Sizing and Volume Contribution:**
  * Status: **PASS** (Implemented & Seeded).
  * Data Flow: `Block` & `Assessment` collections -> `/api/analytics/quarries/comparison/` -> `apiService.getQuarriesComparison()` -> Bar chart.

---

## 4. OFFICERS ANALYTICS AUDIT

Verification of the Officer Performance registry:

* **Officer Database Count:** 15 synthetic officer documents.
* **Weekly summaries:** 90 summaries (15 officers $\times$ 6 weeks).
* **UI Status:** **PASS** (Renders scorecards, tables, trends, and CSV exports).
* **API Endpoints:**
  * `GET /api/analytics/officers/`
  * `GET /api/analytics/officers/<officer_id>/weekly/`
* **Data Flow:** `Officer` & `WeeklyOfficerSummary` collections -> API views -> `apiService.getOfficersAnalytics()` -> Recharts and CSV tables.

---

## 5. QUARRY ANALYSIS AUDIT

Verification of the Quarry Performance analysis:

* **UI Status:** **PARTIAL / NO DATA** (Renders layout, but displays `NaN%` and blank inputs if unseeded).
* **Root Cause of `NaN%` and Blank Sizing:**
  * **Line 992 in `App.jsx`:**
    `Override: {Math.round(quarries.find(q => q.quarry_id === selectedQuarryA)?.override_rate * 100)}%`
  * **Failure Analysis:** If the dashboard loads while the database is unseeded (or before the quarries list is populated), `quarries.find(...)` returns `undefined`. As a result, `undefined?.override_rate` is `undefined`, and `undefined * 100` evaluates to `NaN`.
  * **Fix:** Add fallback checks:
    `Override: {Math.round((quarries.find(q => q.quarry_id === selectedQuarryA)?.override_rate || 0) * 100)}%`

---

## 6. REVENUE SUMMARY AUDIT

Verification of the Revenue Summary & AI Sizing Recoveries:

* **UI Status:** **PASS** (Renders metrics, bar charts, and CSV exports).
* **API Endpoints:**
  * `GET /api/analytics/revenue/summary/`
  * `GET /api/analytics/revenue/leakage/`
* **Data Flow:** `Assessment` collection -> API views -> `apiService.getRevenueSummary()` -> Recharts.

---

## 7. COMPLIANCE AUDIT

Verification of Sizing Completeness & SLA Audit readiness:

* **UI Status:** **PASS** (Renders compliance checklist, color breakdown, and attention table).
* **API Endpoint:** `GET /api/analytics/audit-readiness/`
* **Checklist Logic:** Scans for photo, GPS, timestamp, measurement, approval, assessment, and audit logs.
  * **Green:** Complete.
  * **Amber:** 1 element missing.
  * **Red:** 2 or more elements missing.

---

## 8. SYSTEM ALERTS AUDIT

Verification of the Alerts Feed:

* **UI Status:** **PASS** (Renders alerts feed filtered by severity).
* **API Endpoint:** `GET /api/analytics/alerts/`
* **Alert Types:** High variance blocks, multiple capture attempts, slow sizing execution, and low CV confidence alerts.

---

## 9. GEOSPATIAL MAP AUDIT

Verification of the Geospatial Quarry Sizing Locations map:

* **UI Status:** **PASS** (Renders vector AP map, marker clusters, and detail sidebars).
* **API Endpoint:** `GET /api/analytics/map-data/`
* **Coordinates:** Uses synthetic coordinates mapped inside AP bounding boxes (Prakasam, Nellore, Chittoor).

---

## 10. MOCK DATA LAYER AUDIT

* **Command Name:** `generate_mock_data`
* **Command Path:** [`c:\granite-blocks\backend\blocks\management\commands\generate_mock_data.py`](file:///c:/granite-blocks/backend/blocks/management/commands/generate_mock_data.py)
* **Status:** **PASS** (Implemented & Seeded).
* **Idempotency:** Yes. Clears records where `revenue_bucket="POC MOCK REVENUE"`, `block_id` starts with `MOCK-GR-`, or `officer_id` starts with `MOCK-OFF-` before seeding new records, ensuring genuine records are preserved.

---

## 11. DATABASE SCHEMA AUDIT

Verification of database fields against target analytics requirements:

| Collection / Model | Fields | Status |
| :--- | :--- | :--- |
| **`Officer`** | `officer_id`, `name`, `designation`, `assigned_quarries`, `phone`, `email`, `joined_date`, `active_status` | **IMPLEMENTED** |
| **`WeeklyOfficerSummary`** | `officer_id`, `week_start_date`, `blocks_inspected`, `avg_confidence`, `override_count`, `approval_rate`, `avg_inspection_duration_seconds` | **IMPLEMENTED** |
| **`Quarry`** | `district`, `region`, `lessee_name`, `total_area_hectares`, `registered_date`, `license_expiry_date` | **IMPLEMENTED** |
| **`Block`** | `inspecting_officer_id`, `inspection_duration_seconds`, `capture_attempt_count`, `lighting_condition`, `device_id` | **IMPLEMENTED** |
| **`Assessment`** | `expected_vs_actual_variance_pct`, `revenue_bucket`, `assessment_week`, `assessment_month` | **IMPLEMENTED** |
| **`AuditLog`** | `sla_breach`, `severity` | **IMPLEMENTED** |

---

## 12. REACT DATA-FLOW DIAGNOSTICS

```
  MongoDB (populated via generate_mock_data)
                    ↓
  Django API (Views compute metrics and return JSON arrays)
                    ↓
  React apiService (Fetch client pulls and parses JSON payloads)
                    ↓
  React state (App.jsx holds objects in state variables)
                    ↓
  React component (App.jsx maps properties to Recharts widgets)
                    ↓
  Rendered UI (Calculations complete. Displays NaN% if state is empty)
```

---

## 13. MASTER FEATURE STATUS MATRIX

| Feature | UI | Backend | API | Mock Data | React Data Flow | Populated | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Overview KPIs** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Overview weekly chart** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Audit completeness** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Quarry contribution chart**| PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Officer scorecards** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Officer weekly chart** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Officer CSV** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Quarry comparison** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Quarry side-by-side** | PASS | PASS | PASS | PASS | BROKEN | No | **PARTIAL** |
| **Quarry weekly chart** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Quarry CSV** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Revenue summary** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Revenue category chart** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Gangsaw chart** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Revenue recovery** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Revenue CSV** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Compliance chart** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Non-compliant blocks** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Alerts** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Severity filtering** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Geospatial map** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Map popups** | PASS | PASS | PASS | PASS | PASS | Yes | **PASS** |
| **Mock data generator** | N/A | PASS | N/A | N/A | N/A | Yes | **PASS** |

---

## 14. ROOT-CAUSE ANALYSIS

### Why does the dashboard display empty or `NaN%` values?
* **Stale Caches:** The running dev server did not pick up the schema modifications or initial mock seider data run until the process was restarted and HMR refreshed.
* **Null Check Failures in Sizing Compares:** In [`App.jsx`](file:///c:/granite-blocks/supervisor-dashboard/src/App.jsx), calculations are performed on `quarries` elements without checking for null/empty values first. If the array is empty on initial load, divisions by zero or evaluations of undefined properties return `NaN`.

---

## 15. POC PRIORITY ROADMAP

### P0: Critical Blockers for POC Demo
* **Fix the CSS cropping issue on the hero banner** (`maxHeight: '180px'` in `App.jsx`).
* **Add fallback checks** in `App.jsx` to prevent `NaN%` rendering errors.

### P1: Important Features
* Add search filters for dates and districts in the dashboard UI.

### P2: Nice-to-Have Improvements
* Improve transition animations between dashboard tabs.

---

## 16. INSPECTED FILES
* [`c:\granite-blocks\backend\blocks\models.py`](file:///c:/granite-blocks/backend/blocks/models.py)
* [`c:\granite-blocks\backend\blocks\views.py`](file:///c:/granite-blocks/backend/blocks/views.py)
* [`c:\granite-blocks\supervisor-dashboard\src\App.jsx`](file:///c:/granite-blocks/supervisor-dashboard/src/App.jsx)
* [`c:\granite-blocks\supervisor-dashboard\public\banner_3.png`](file:///c:/granite-blocks/supervisor-dashboard/public/banner_3.png)

---
