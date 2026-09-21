"""
OMEPS-ready export contract for a granite block.

STATUS: OMEPS-READY, NOT OMEPS-INTEGRATED. READ THIS FIRST.
--------------------------------------------------------------------
There is no confirmed OMEPS production schema and no live OMEPS endpoint
available to this project. Nothing here is an official OMEPS field definition.
This module publishes an INTERNAL, versioned, documented contract that packages
the complete RTGS workflow for a block so that an OMEPS adapter can be written
later by mapping these fields onto the real specification once it is supplied.

Consequently:
  * every response carries integration_status = OMEPS_READY_PENDING_OFFICIAL_SCHEMA
  * no data is transmitted to any external system - this is a read endpoint
  * no field name here should be assumed to match an OMEPS field name
  * the schema is versioned (export_schema_version) so an adapter can pin to it

SOURCE OF TRUTH - deliberate, and not negotiable per layer
----------------------------------------------------------
    measurement  -> Block.measurement          (dimensions, volume, GPS)
    money        -> Assessment                 (tonnage, rate, amount)
    approval     -> Block approval fields + the persisted approval AuditLog
    audit        -> AuditLog

This module NEVER recalculates a monetary value and holds NO tariff table. The
payable amount, the rate and the density are echoed from the stored Assessment
exactly as persisted. If the stored rate were edited in the database, this export
would report the edited rate - it has no second opinion to offer, by design.

The one thing it does derive is the exact unrounded TONNAGE BASIS, via
report_basis.basis_for(), which reuses the seigniorage engine's own formula.
That exists because Assessment.volume_m3 / weight_mt are rounded for
presentation and multiplying the rounded volume by density does not reconcile to
the stored amount (see report_basis for the worked block2 case). Tonnage is not
money; the amount itself is still read, never computed.

FIELD MAP: contract field -> RTGS entity
----------------------------------------
    block.block_id                      Block.block_id
    block.quarry_id                     Block.quarry (resolved ref, else null)
    block.submitted_quarry_id           Block.submitted_quarry_id (verbatim claim)
    block.officer_id                    Officer.officer_id if resolvable, else null
    block.inspecting_officer_id         Block.inspecting_officer_id (verbatim claim)
    block.measurement_method            Block.measurement.measurement_method
    block.captured_at                   Block.captured_at (server receipt time)
    block.status / approval_status       Block.status / Block.approval_status
    measurement.*_m                     Block.measurement dimensions
    measurement.volume_m3               Block.measurement.volume_m3 (unrounded)
    measurement.gps_*                   Block.gps_latitude / gps_longitude
    measurement.ar_receipt_id           AuditLog id of 'ar_measurement_submitted'
    classification.*                    Assessment + seigniorage engine thresholds
    assessment.*                        Assessment (authoritative for money)
    approval.*                          Block approval fields + approval AuditLog
    audit.*                             AuditLog entries for this block
    validation.*                        derived booleans, no invented data

OPTIONAL / POSSIBLY ABSENT FIELDS
---------------------------------
    quarry_id, officer_id               null when the reference is unresolved
    gps_latitude, gps_longitude         null when never captured
    approval.*                          null / 'pending' before supervisor action
    assessment.tonnage_mt_exact         null when the basis is not restatable
Absent data is reported as null alongside an explicit status field. Nothing is
substituted with a placeholder, a zero, or an invented value.

WRITING AN OMEPS ADAPTER LATER
------------------------------
Pin to export_schema_version, then map field-by-field onto the official schema.
Treat assessment.seigniorage_amount_inr as the authoritative payable figure and
assessment.tonnage_mt_exact as the basis to reproduce it against the rate. Use
audit.receipt_id as the external correlation handle: it resolves to a persisted
AuditLog entry and lets an integrator trace export -> block -> assessment ->
approval -> audit without any access to MongoDB internals. If OMEPS requires a
field this contract does not carry, add it here at the source rather than
computing it inside the adapter.
"""

import datetime

from . import report_basis, seigniorage

EXPORT_SCHEMA_VERSION = "RTGS-OMEPS-READY-2026-09"
INTEGRATION_STATUS = "OMEPS_READY_PENDING_OFFICIAL_SCHEMA"
SOURCE_SYSTEM = "RTGS Granite Block PoC"
INTEGRATION_NOTE = (
    "OMEPS-ready export / integration contract - pending official OMEPS schema. "
    "Field names are internal to RTGS and are not official OMEPS field names. "
    "No data has been transmitted to any external system."
)

GPS_CAPTURED = "captured"
GPS_NOT_CAPTURED = "not_captured"
GPS_PARTIAL = "partial_incomplete"

REFERENCE_RESOLVED = "resolved"
REFERENCE_UNRESOLVED = "unresolved_in_reference_data"
REFERENCE_NOT_SUPPLIED = "not_supplied"

# Audit detail strings are business descriptions, never credentials, but they are
# capped so one verbose entry cannot dominate the payload.
AUDIT_DETAIL_MAX_CHARS = 500


class ExportPreconditionError(Exception):
    """Raised when required persisted data is missing. Carries the HTTP code the
    view should return, so the failure is a clear integration error rather than a
    partially fabricated document."""

    def __init__(self, code, message, status_code):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _iso(value):
    """Naive UTC datetime -> explicit ISO-8601 UTC string, or None.

    Every timestamp in this system is written with datetime.utcnow(), so the
    values are UTC without a tzinfo. The 'Z' is therefore accurate rather than
    assumed, and an integrator does not have to guess the zone.
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.isoformat() + "Z"
    return str(value)


def _truncate(text):
    if not text:
        return None
    if len(text) <= AUDIT_DETAIL_MAX_CHARS:
        return text
    return text[:AUDIT_DETAIL_MAX_CHARS] + " ...(truncated)"


def _audit_entries(block):
    from .models import AuditLog

    return list(AuditLog.objects(block=block).order_by("timestamp"))


def _first(entries, action_prefix):
    for entry in entries:
        if entry.action and entry.action.startswith(action_prefix):
            return entry
    return None


def _last(entries, action_prefix):
    found = None
    for entry in entries:
        if entry.action and entry.action.startswith(action_prefix):
            found = entry
    return found


def build_export(block, assessment):
    """Assemble the OMEPS-ready document from persisted data only.

    Raises ExportPreconditionError when a required section cannot be sourced.
    Performs no writes: no save, no audit entry, no cache mutation.
    """
    if block is None:
        raise ExportPreconditionError(
            "block_not_found", "Block not found.", 404
        )

    measurement = block.measurement
    if measurement is None:
        raise ExportPreconditionError(
            "measurement_required",
            f"Block '{block.block_id}' has no recorded measurement. An OMEPS "
            f"export requires measured dimensions; none have been fabricated.",
            409,
        )

    if assessment is None:
        raise ExportPreconditionError(
            "assessment_required",
            f"Block '{block.block_id}' has no seigniorage assessment. An OMEPS "
            f"export carries the assessed payable amount as its financial source "
            f"of truth; create one via POST /api/assessments/ first.",
            409,
        )

    entries = _audit_entries(block)
    receipt_audit = _first(entries, "ar_measurement_submitted")
    assessed_audit = _last(entries, "seigniorage_assessed")
    approval_audit = _last(entries, "block_approval")
    receipt_id = str(receipt_audit.id) if receipt_audit else None

    # --- references, reported honestly ------------------------------------
    quarry_resolved = block.quarry is not None
    submitted_quarry_id = block.submitted_quarry_id or (
        str(block.quarry.id) if quarry_resolved else None
    )
    if quarry_resolved:
        quarry_status = REFERENCE_RESOLVED
    elif submitted_quarry_id:
        quarry_status = REFERENCE_UNRESOLVED
    else:
        quarry_status = REFERENCE_NOT_SUPPLIED

    officer_ref = block.inspecting_officer_id
    officer_doc = None
    if officer_ref:
        from .models import Officer

        officer_doc = Officer.objects(officer_id=officer_ref).first()
    if officer_doc is not None:
        officer_status = REFERENCE_RESOLVED
    elif officer_ref:
        officer_status = REFERENCE_UNRESOLVED
    else:
        officer_status = REFERENCE_NOT_SUPPLIED

    # --- GPS, never invented -----------------------------------------------
    has_lat = block.gps_latitude is not None
    has_lon = block.gps_longitude is not None
    if has_lat and has_lon:
        gps_status = GPS_CAPTURED
    elif has_lat or has_lon:
        gps_status = GPS_PARTIAL
    else:
        gps_status = GPS_NOT_CAPTURED
    gps_available = gps_status == GPS_CAPTURED

    # --- financial basis (tonnage only; the amount is never recomputed) ----
    basis = report_basis.basis_for(block, assessment)
    schedule_version, schedule_source = report_basis.schedule_provenance(assessed_audit)

    # --- approval -----------------------------------------------------------
    approval_status = block.approval_status or "pending"
    approval_present = approval_status in ("approved", "rejected")
    approval_reason = None
    if approval_audit and approval_audit.details and "reason=" in approval_audit.details:
        approval_reason = approval_audit.details.split("reason=", 1)[1].strip() or None

    length_cm = measurement.length_m * 100
    breadth_cm = measurement.breadth_m * 100

    return {
        "export_metadata": {
            "export_schema_version": EXPORT_SCHEMA_VERSION,
            "integration_status": INTEGRATION_STATUS,
            "integration_note": INTEGRATION_NOTE,
            "source_system": SOURCE_SYSTEM,
            "generated_at": _iso(datetime.datetime.utcnow()),
        },
        "block": {
            "block_id": block.block_id,
            "quarry_id": str(block.quarry.id) if quarry_resolved else None,
            "submitted_quarry_id": submitted_quarry_id,
            "quarry_reference_status": quarry_status,
            "officer_id": officer_doc.officer_id if officer_doc is not None else None,
            "inspecting_officer_id": officer_ref or None,
            "officer_reference_status": officer_status,
            "measurement_method": measurement.measurement_method or None,
            "captured_at": _iso(block.captured_at),
            "captured_at_note": (
                "Server receipt time. The field client does not transmit a device "
                "capture timestamp; no device time has been inferred."
            ),
            "status": block.status,
            "approval_status": approval_status,
            "cv_status": block.cv_status,
            "reference_warnings": list(block.reference_warnings or []),
        },
        "measurement": {
            "length_m": measurement.length_m,
            "breadth_m": measurement.breadth_m,
            "height_m": measurement.height_m,
            "volume_m3": measurement.volume_m3,
            "volume_source": "block_measurement_unrounded_length_x_breadth_x_height",
            "confidence": measurement.confidence,
            "measured_at": _iso(measurement.measured_at),
            "gps_latitude": block.gps_latitude,
            "gps_longitude": block.gps_longitude,
            "gps_status": gps_status,
            "ar_receipt_id": receipt_id,
            "ar_receipt_reference": (
                f"AuditLog/{receipt_id}" if receipt_id else None
            ),
        },
        "classification": {
            "granite_category": assessment.granite_category,
            "gangsaw_classification": assessment.gangsaw_classification,
            "classification_source": "server_derived_deterministic_threshold_rule",
            "classification_note": (
                "Derived server-side from the measured dimensions. This is a "
                "threshold comparison, not a machine-learning prediction."
            ),
            "threshold_rule": (
                f"length_cm > {seigniorage.GANGSAW_LENGTH_THRESHOLD_CM} AND "
                f"breadth_cm > {seigniorage.GANGSAW_BREADTH_THRESHOLD_CM} "
                f"=> {seigniorage.ABOVE_GANGSAW}; otherwise "
                f"{seigniorage.WITHIN_GANGSAW}"
            ),
            "threshold_length_cm": float(seigniorage.GANGSAW_LENGTH_THRESHOLD_CM),
            "threshold_breadth_cm": float(seigniorage.GANGSAW_BREADTH_THRESHOLD_CM),
            "dimensions_in_cm_used_for_rule": {
                "length_cm": round(length_cm, 2),
                "breadth_cm": round(breadth_cm, 2),
            },
            "comparison": (
                f"{length_cm:.2f} > {seigniorage.GANGSAW_LENGTH_THRESHOLD_CM} = "
                f"{'yes' if length_cm > float(seigniorage.GANGSAW_LENGTH_THRESHOLD_CM) else 'no'}; "
                f"{breadth_cm:.2f} > {seigniorage.GANGSAW_BREADTH_THRESHOLD_CM} = "
                f"{'yes' if breadth_cm > float(seigniorage.GANGSAW_BREADTH_THRESHOLD_CM) else 'no'}"
            ),
        },
        "assessment": {
            "assessment_id": str(assessment.id),
            "assessment_status": assessment.status,
            "assessed_at": _iso(assessment.created_at),
            "density_mt_per_m3": assessment.density_mt_per_m3,
            "volume_m3_stored": assessment.volume_m3,
            "volume_m3_exact": report_basis.decimal_text(basis["exact_volume_m3"]),
            "tonnage_mt_stored": assessment.weight_mt,
            "tonnage_mt_exact": report_basis.decimal_text(basis["exact_tonnage_mt"]),
            "tonnage_basis_status": basis["status"],
            "tonnage_basis_note": basis["explanation"],
            "rate_per_mt": assessment.rate_per_mt,
            "seigniorage_amount": assessment.indicative_seigniorage,
            "currency": "INR",
            "rate_schedule_version": schedule_version,
            "rate_schedule_version_source": schedule_source,
            "financial_source": "persisted_assessment_document",
            "financial_source_note": (
                "The rate and payable amount are echoed from the stored "
                "Assessment exactly as persisted. This export holds no tariff "
                "table and recomputes no monetary value. Values rounded for "
                "presentation are suffixed _stored; the unrounded basis the "
                "amount was derived from is suffixed _exact and is carried as a "
                "string to survive JSON without precision loss."
            ),
        },
        "approval": {
            "approval_status": approval_status,
            "approval_present": approval_present,
            "approved_by": block.approved_by or None,
            "approved_by_note": (
                "Authenticated backend identity recorded at approval time."
                if block.approved_by
                else "No approver recorded."
            ),
            "approved_at": _iso(block.approved_at),
            "approval_reason": approval_reason,
            "approval_audit_ref": str(approval_audit.id) if approval_audit else None,
            "is_overridden": bool(block.is_overridden),
            "override_reason": block.override_reason or None,
        },
        "audit": {
            "receipt_id": receipt_id,
            "event_count": len(entries),
            "traceability_note": (
                "Each reference resolves to a persisted AuditLog entry and can "
                "be retrieved via GET /api/blocks/<block_id>/audit-logs/, so an "
                "integrator can trace export -> block -> assessment -> approval "
                "-> audit without access to MongoDB internals."
            ),
            "events": [
                {
                    "reference": str(entry.id),
                    "event": entry.action,
                    "actor": entry.actor,
                    "timestamp": _iso(entry.timestamp),
                    "severity": entry.severity,
                    "detail": _truncate(entry.details),
                }
                for entry in entries
            ],
        },
        "validation": {
            "gps_available": gps_available,
            "quarry_reference_resolved": quarry_resolved,
            "officer_reference_resolved": officer_doc is not None,
            "measurement_present": True,
            "assessment_present": True,
            "approval_present": approval_present,
            "receipt_present": receipt_id is not None,
            "financial_basis_reproduced": basis["status"] == report_basis.REPRODUCED,
            "audit_events_present": len(entries) > 0,
            "reference_warnings": list(block.reference_warnings or []),
            "ready_for_omeps_submission": bool(
                approval_present
                and receipt_id is not None
                and basis["status"] == report_basis.REPRODUCED
            ),
            "readiness_note": (
                "ready_for_omeps_submission reflects RTGS-side completeness only. "
                "It does not assert conformance to any OMEPS specification, which "
                "has not been provided."
            ),
        },
    }
