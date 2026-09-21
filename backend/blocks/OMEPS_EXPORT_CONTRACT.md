# OMEPS-Ready Export Contract

`GET /api/export/omeps/<block_id>/`

**Status: OMEPS-READY, NOT OMEPS-INTEGRATED.**

## Read this before mapping anything

There is no confirmed OMEPS production schema and no live OMEPS endpoint available
to this project. **No field name in this contract is an official OMEPS field name.**
This endpoint publishes an internal, versioned, documented structure that packages
the complete RTGS workflow for one block so that an OMEPS adapter can be written
later by mapping these fields onto the real specification once it is supplied.

Every response carries:

```json
"integration_status": "OMEPS_READY_PENDING_OFFICIAL_SCHEMA",
"export_schema_version": "RTGS-OMEPS-READY-2026-09"
```

Nothing is transmitted anywhere. This is a read-only endpoint: it writes no
document, creates no audit entry, and leaves the database byte-identical.

## Why the schema is internal

Guessing at OMEPS field names would produce an adapter that looks finished and
silently mis-maps revenue data. Instead the contract is explicit about what RTGS
actually holds, and the mapping to OMEPS is deferred to the point where the real
specification is known. Pin to `export_schema_version` and the adapter can be
written once against a stable shape.

## Authoritative source per section — not negotiable

| Section | Source of truth | Note |
|---|---|---|
| `measurement` | `Block.measurement` | dimensions, volume, GPS |
| `assessment` | `Assessment` | **the financial source of truth** |
| `classification` | `Assessment` + seigniorage thresholds | classification is server-derived |
| `approval` | `Block` approval fields + approval `AuditLog` | |
| `audit` | `AuditLog` | |
| `validation` | derived booleans | no invented data |

The export **recomputes no monetary value and holds no tariff table.** The rate,
density and payable amount are echoed from the stored `Assessment` exactly as
persisted. If the stored rate were edited in the database, this export would
report the edited rate — it has no second opinion to offer, by design. A test
pins that behaviour, and another asserts no rate literal appears in the module.

## The one derived value: the exact tonnage basis

`Assessment.volume_m3` is quantised to 6 dp and `Assessment.weight_mt` to 3 dp for
presentation, but the payable amount is computed from the **unrounded**
`L × B × H × density` product and quantised once, at the end. Multiplying the
rounded stored volume by the density therefore does **not** reconcile to the
stored amount. On the real `block2` record:

```
exact  : 0.023587785975 × 2.7 = 0.0636870221325 MT × 720 = INR 45.85   <-- stored
rounded: 0.023588       × 2.7 = 0.0636876       MT × 720 = INR 45.86   <-- wrong
```

So the contract carries both, clearly distinguished:

- `tonnage_mt_exact` / `volume_m3_exact` — the basis the amount was derived from,
  as **strings**, because 13 significant digits do not survive a JSON float.
- `tonnage_mt_stored` / `volume_m3_stored` — the rounded values as persisted.
- `tonnage_basis_status` — `reproduced`, `stale_measurement`, or `unavailable`.

Tonnage is not money: the amount itself is still read, never computed. The
derivation lives in `blocks/report_basis.py` and reuses the seigniorage engine's
own formula (`seigniorage.exact_tonnage_mt`), so the PDF, the export and the
engine cannot drift apart.

**Staleness guard.** The basis is recomputed from the block's *current*
dimensions. If the block was re-measured or overridden after the assessment was
written, those dimensions are no longer the ones the stored amount came from;
presenting them as "the basis" would be a fresh fabrication. The guard detects
this by re-quantising its own result and comparing it to `Assessment.volume_m3`.
On mismatch the status becomes `stale_measurement` and the exact fields are
`null` — the stored payable amount is still reported.

## Field map: contract → RTGS entity

| Contract field | RTGS source |
|---|---|
| `block.block_id` | `Block.block_id` |
| `block.quarry_id` | `Block.quarry` (resolved reference) or `null` |
| `block.submitted_quarry_id` | `Block.submitted_quarry_id` (the verbatim field claim) |
| `block.officer_id` | `Officer.officer_id` if resolvable, else `null` |
| `block.inspecting_officer_id` | `Block.inspecting_officer_id` (verbatim claim) |
| `block.measurement_method` | `Block.measurement.measurement_method` |
| `block.captured_at` | `Block.captured_at` — **server receipt time** |
| `block.status`, `block.approval_status` | `Block.status`, `Block.approval_status` |
| `measurement.length_m` / `breadth_m` / `height_m` | `Block.measurement` |
| `measurement.volume_m3` | `Block.measurement.volume_m3` (unrounded) |
| `measurement.gps_latitude` / `gps_longitude` | `Block.gps_latitude` / `gps_longitude` |
| `measurement.ar_receipt_id` | `AuditLog` id of `ar_measurement_submitted` |
| `classification.*` | `Assessment` + `seigniorage` threshold constants |
| `assessment.*` | `Assessment` |
| `approval.*` | `Block` approval fields + approval `AuditLog` |
| `audit.events[]` | `AuditLog` entries for this block, oldest first |
| `validation.*` | derived booleans |

## Optional / possibly absent fields

| Field | Absent when | Reported as |
|---|---|---|
| `quarry_id` | the submitted quarry is not registered | `null` + `quarry_reference_status: unresolved_in_reference_data` |
| `officer_id` | the submitted officer is not registered | `null` + `officer_reference_status: unresolved_in_reference_data` |
| `gps_latitude` / `gps_longitude` | never captured | `null` + `gps_status: not_captured` |
| `approval.*` | before supervisor action | `approval_status: pending`, `approval_present: false` |
| `tonnage_mt_exact` | basis not restatable | `null` + `tonnage_basis_status` |
| `approval_reason` | no reason recorded | `null` |

Absent data is always `null` beside an explicit status field. Nothing is
substituted with a placeholder, a zero, or an invented value.

`captured_at` is **server receipt time**: the iOS client does not transmit a
device capture timestamp. No device time has been inferred.

`rate_schedule_version` is taken from what the `seigniorage_assessed` audit entry
recorded, because `Assessment` does not persist it. When that is unavailable the
engine's current constant is used and `rate_schedule_version_source` says so —
naming today's tariff on a historical assessment would assert a schedule that may
not have been applied.

## Failure modes — never a fabricated document

| Condition | HTTP | `code` |
|---|---|---|
| block does not exist | 404 | `block_not_found` |
| block has no measurement | 409 | `measurement_required` |
| block has no assessment | 409 | `assessment_required` |
| block not yet approved | **200** | exported with `approval_status: pending` |

Pre-approval export is intentionally permitted so an integrator can stage a
record, but `validation.approval_present` and
`validation.ready_for_omeps_submission` are both `false`.

`validation.ready_for_omeps_submission` reflects **RTGS-side completeness only**.
It does not assert conformance to any OMEPS specification.

## Writing an OMEPS adapter later

1. Pin to `export_schema_version`.
2. Map field-by-field onto the official schema. Do not infer a field's meaning
   from its RTGS name.
3. Treat `assessment.seigniorage_amount` as the authoritative payable figure and
   `assessment.tonnage_mt_exact` as the basis to reproduce it against
   `rate_per_mt`.
4. Use `audit.receipt_id` as the external correlation handle. It resolves to a
   persisted `AuditLog` entry, retrievable via
   `GET /api/blocks/<block_id>/audit-logs/`, so an integrator can trace
   **export → block → assessment → approval → audit** with no access to MongoDB
   internals.
5. If OMEPS requires a field this contract does not carry, add it in
   `blocks/omeps_export.py` at the source, rather than computing it in the
   adapter — that is how a second, divergent source of truth gets created.

## Known limitations

- The endpoint is **unauthenticated**, like the rest of the read surface. Any
  caller who can reach the API can export any block.
- No digital signature. Integrity of the *report* is covered by the SHA-256
  business-record digest in the PDF (`blocks/report_hash.py`); this JSON export
  carries no digest of its own.
- Audit `detail` strings are capped at 500 characters.
- Only `AuditLog` entries still linked to the block by reference are included;
  entries orphaned by a block deletion (kept via `block_id_snapshot`) are not.

## Tests

`blocks/tests_omeps_export.py` — 32 tests, mongomock-isolated.
Run: `./venv/bin/python manage.py test blocks.tests_omeps_export`

Do **not** run `blocks/tests.py`: it writes to the live database and deletes the
real `MEDIA_ROOT`.
