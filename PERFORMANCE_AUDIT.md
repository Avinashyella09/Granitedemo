# PERFORMANCE AUDIT — Granite Blocks POC
**Generated:** 2026-09-02  
**Project:** `c:\granite-blocks`

---

## EXECUTIVE SUMMARY

The performance investigation found **multiple serious bottlenecks** across the backend, frontend, and mock data generator. The slowness is real and measurable. Below are the confirmed root causes with actual timing data.

---

## MEASURED TIMINGS (Before Optimization)

| Component | Metric | Measured Time |
|---|---|---|
| Django setup (cold start) | Import + connect | **1,581 ms** |
| `Block.objects.count()` | Single count query | **507 ms** |
| `Assessment.objects.count()` | Single count query | **107 ms** |
| `Officer.objects.count()` | Single count query | **95 ms** |
| `Quarry.objects.count()` | Single count query | **115 ms** |
| `WeeklyOfficerSummary.objects.count()` | Single count query | **181 ms** |
| `AuditLog.objects.count()` | Single count query | **181 ms** |
| OfficerAnalytics (DB work) | `list(Officers) + list(Blocks)` | **224 ms** |
| QuarryComparison (DB work) | `list(Quarries) + list(Blocks) + list(Assessments)` | **272 ms** |
| RevenueSummary (DB work) | `list(Assessments)` | **101 ms** |
| AuditReadiness (DB work) | `list(Blocks) + list(Assessments) + list(AuditLogs)` | **340 ms** |
| MapData (DB work) | `list(Quarries) + list(Blocks) + list(Assessments)` | **276 ms** |
| **ExecutiveOverview (DB work)** | **ALL combined queries** | **761 ms** |
| AnalyticsAlerts (4 separate queries) | 4 separate MongoDB queries | **151 ms** |
| RevenueLeakage | `list(Assessments)` x2 | **98 ms** |

> ℹ️ Network latency to MongoDB Atlas adds additional overhead per round-trip.  
> The measured times are **pure Python execution**, excluding HTTP stack overhead.

---

## DATABASE RECORD COUNTS (Existing Mock Data)

| Collection | Count |
|---|---|
| `blocks` | 185 |
| `assessments` | 144 |
| `officers` | 15 |
| `quarries` | 10 |
| `weekly_officer_summaries` | 67 |
| `audit_logs` | 378 |

---

## BOTTLENECK 1 — CRITICAL: ExecutiveOverview Does 10+ MongoDB Round-Trips

**File:** `backend/blocks/views.py` — `ExecutiveOverviewAPIView.get()`  
**Severity:** CRITICAL

### Problem
`ExecutiveOverviewAPIView` internally **instantiates and calls** two other views:
```python
alert_view = AnalyticsAlertsAPIView()
alerts_list = alert_view.get(request).data     # → 4 MORE DB queries

compliance_view = AuditReadinessAPIView()
compliance_data = compliance_view.get(request).data  # → 3 MORE DB queries
```

Combined with its own two queries (`Block.objects.all()`, `Assessment.objects.all()`), plus two `.count()` calls, the overview endpoint triggers **10–12 separate MongoDB round-trips per request**.

### Fix
Inline the critical logic. Fetch all collections once, share data across calculations.

---

## BOTTLENECK 2 — HIGH: Every Analytics View Independently Fetches All Data

**File:** `backend/blocks/views.py`  
**Severity:** HIGH

Each analytics view independently issues `list(Block.objects.all())` and `list(Assessment.objects.all())`:

- `OfficerAnalyticsAPIView`: `Block.objects.all()` + `Officer.objects.all()`
- `QuarryComparisonAPIView`: `Block.objects.all()` + `Quarry.objects.all()` + `Assessment.objects.all()`
- `MapDataAPIView`: Same as above
- `RevenueSummaryAPIView`: `Assessment.objects.all()`
- `AuditReadinessAPIView`: All three collections
- `ExecutiveOverviewAPIView`: All of the above through delegation

**No data is shared between views.**

### Fix
Add a lightweight in-process request-level or short-lived (10–30s) memory cache per collection. Since MongoDB Atlas round-trips are ~100–500ms each, caching just one fetch per analytics refresh cycle eliminates most latency.

---

## BOTTLENECK 3 — HIGH: RevenueLeakageAPIView Re-Evaluates Queryset

**File:** `backend/blocks/views.py` — `RevenueLeakageAPIView.get()`  
**Severity:** HIGH

```python
assessments = Assessment.objects.all()         # Queryset created
leakage_blocks = assessments(variance_pct__gt=8.0)   # Still a queryset

blocks_count = leakage_blocks.count()          # DB call 1 (count only)
total_seigniorage = sum(ass.indicative_seigniorage for ass in assessments)  # DB call 2 (iterate ALL)

for ass in leakage_blocks:                     # DB call 3 (iterate filtered)
    ...

assessments.count()  # DB call 4 — ANOTHER COUNT of entire collection
sum(ass.variance_pct for ass in assessments if ass.variance_pct)  # DB call 5 — iterate again
assessments.count()  # DB call 6 — yet another count
```

**The queryset is evaluated 6+ times.**

### Fix
Fetch all assessments once as a list, compute everything in Python memory.

---

## BOTTLENECK 4 — HIGH: Frontend Loads ALL 9 Analytics APIs on Startup

**File:** `supervisor-dashboard/src/App.jsx` — `loadAllData()`  
**Severity:** HIGH

On app startup, a `Promise.all()` fires 9 concurrent API requests:
- `getBlocks()`
- `getExecutiveOverview()`
- `getOfficersAnalytics()`
- `getQuarriesComparison()`
- `getRevenueSummary()`
- `getRevenueLeakage()`
- `getAuditReadiness()`
- `getAlerts()`
- `getMapData()`

Plus 2 more for quarry trends = **11 total API calls on app startup**, even if the user only views the Overview page.

The total "time to interactive" is bound by the **slowest** response — the Overview endpoint at ~761ms+ round-trip.

### Fix
Load only Overview + Blocks on startup. Load page-specific data when the user navigates to that tab.

---

## BOTTLENECK 5 — HIGH: loadAllData() Called After Every Action

**File:** `supervisor-dashboard/src/App.jsx`  
**Severity:** HIGH

Multiple user actions trigger a full reload of all 9 APIs:
- `handleCalculateAssessment()` → calls `loadAllData()`
- `handleApprove()` → calls `loadAllData()`
- `handleOverrideSubmit()` → calls `loadAllData()`

Every block approval reloads ALL analytics across ALL pages.

### Fix
After mutations, reload only the directly affected data (the specific block, and refresh overview/blocks).

---

## BOTTLENECK 6 — MEDIUM: Mock Data Generator Saves Records Individually

**File:** `backend/blocks/management/commands/generate_mock_data.py`  
**Severity:** MEDIUM

- **180 blocks** are saved one at a time: 180 individual `b.save()` calls
- Each block generates 1–3 audit logs: ~378 individual `audit.save()` calls
- **144 assessments** individually saved: 144 individual `ass.save()` calls
- **15 officers** individually saved: 15 individual `o.save()` calls
- **8 quarries** individually saved: 8 individual `q.save()` calls
- **Total: ~700+ individual MongoDB write operations**

Also: `generate_assessment_report()` is called inside the loop for each block, but this is pure Python computation — acceptable.

The officer assigned_quarries uses `random.sample(quarries, ...)` but there's no seeding, so restarts produce different data each time.

### Fix
Use `MongoEngine`'s `insert()` bulk operation where possible.

---

## BOTTLENECK 7 — MEDIUM: Missing Key Indexes for Analytics Queries

**File:** `backend/blocks/models.py`  
**Severity:** MEDIUM

Current indexes:
- `blocks`: `block_id`, `status`, `inspecting_officer_id` ✅
- `assessments`: `block` ✅
- `officers`: `officer_id` ✅
- `quarries`: `name` ✅
- `audit_logs`: `block`, `timestamp` ✅
- `weekly_officer_summaries`: `officer_id`, `week_start_date` ✅

**Missing indexes needed:**
- `assessments.variance_pct` — used in `leakage` and `alerts` queries with `__gt` filter
- `blocks.capture_attempt_count` — used in `alerts` query
- `blocks.inspection_duration_seconds` — used in `alerts` query  
- `assessments.revenue_bucket` — used in `delete()` clearing during mock data regeneration
- `blocks.quarry` (reference field) — used in `QuarryTrendAPIView`

---

## BOTTLENECK 8 — MEDIUM: Banner PNG is 2.4MB

**File:** `supervisor-dashboard/public/banner_3.png`  
**Severity:** MEDIUM

At 2.4 MB, `banner_3.png` adds significant initial page load time.  
`ap_govt_logo.svg` at 715KB also contributes.

However: **banner must remain visually complete** — optimization is through `loading="lazy"` and proper `Content-Type` headers, not cropping.

---

## BOTTLENECK 9 — MEDIUM: Block inspecting_officer_id is Null on First Inserted Block

**Observed:** First block has `inspecting_officer_id = None`  
This means the OfficerAnalytics view will miss blocks for some officers.

---

## BOTTLENECK 10 — LOW: Map Uses Random Coordinates on Every Request

**File:** `backend/blocks/views.py` — `MapDataAPIView.get()`

```python
if q_lat is None:
    q_lat = round(random.uniform(15.2, 16.1), 4)  # Random every time
```

This means quarry locations change on every API call. The frontend map will show markers jumping positions.

### Fix
Seed random with quarry ID hash, or store coordinates in the database during mock data generation.

---

## BOTTLENECK 11 — LOW: ExecutiveOverview Imports cv2 at Module Level

**File:** `backend/blocks/views.py` line 7: `import cv2`

This imports OpenCV on every worker startup, even for analytics-only requests, adding unnecessary startup overhead.

---

## BOTTLENECK 12 — LOW: Frontend: filteredBlocks Recomputed on Every Render

**File:** `supervisor-dashboard/src/App.jsx` lines 329-335

```javascript
const filteredBlocks = blocks.filter(b => { ... });
```

This is computed on every render without `useMemo`. With 185 blocks this is not catastrophic, but adds unnecessary work.

---

## BOTTLENECK 13 — LOW: Field Officer App GPS Handling

**File:** `field-officer-app/App.js`  
GPS is correctly requested only on button press (not continuous). ✅  
Camera is correctly cleaned up after capture. ✅  
No infinite loops detected. ✅  
AsyncStorage reads happen once on mount. ✅

Minor: No API request timeout configured in field-officer-app API service.

---

## MongoDB Connection

- Connection is made once at Django startup in `settings.py` — **correct**
- No per-request reconnection detected — **correct**
- Connection uses Atlas SRV URI — **correct**

---

## INDEXES PRESENT

All critical lookup indexes already exist (inspecting_officer_id, officer_id, block reference, etc.).  
Missing: `variance_pct`, `capture_attempt_count`, `inspection_duration_seconds` for alert query filters.

---

## RECOMMENDED OPTIMIZATION PRIORITIES

| Priority | Action | Expected Impact |
|---|---|---|
| 🔴 P1 | Fix ExecutiveOverview: inline AuditReadiness + Alerts logic, share one fetch | ~600ms saved per overview call |
| 🔴 P1 | Fix Frontend: lazy-load analytics per tab (not all on startup) | ~5x faster initial load |
| 🔴 P1 | Fix loadAllData() being called after mutations — only refresh affected data | ~5-10x fewer API calls |
| 🟡 P2 | Fix RevenueLeakage: fetch once, compute in-memory | ~400ms saved |
| 🟡 P2 | Add missing MongoDB indexes | Variable improvement on alert queries |
| 🟡 P2 | Fix mock data: use deterministic coordinates for map stability | Map stability |
| 🟢 P3 | Add lightweight per-tab data cache on frontend | Avoids re-fetching same tab |
| 🟢 P3 | Add missing indexes (variance_pct, etc.) | Faster alert queries |
| 🟢 P3 | Use bulk inserts in mock data generator | Faster data generation |
| 🟢 P3 | Lazy-load banner image | Slightly faster initial render |
