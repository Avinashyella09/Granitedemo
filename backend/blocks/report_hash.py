"""
Reproducible verification hash for a generated assessment report.

WHAT IS HASHED, and why it is not the PDF bytes
-----------------------------------------------
ReportLab embeds a creation timestamp and document ID in every PDF, so hashing
the binary would produce a different value on every download and verify nothing.

Instead we hash a canonical text representation of the PERSISTED BUSINESS
RECORD - the values a dispute would actually turn on. Anyone holding the
database record can recompute the same digest and confirm the printed report
describes that record unaltered.

The canonical form is one `key=value` per line, in the fixed order below,
UTF-8, newline-separated, with floats rendered through repr-stable formatting so
0.023588 never becomes 0.0235880000001. Changing ANY hashed field changes the
digest; changing something unhashed (e.g. when the PDF was printed) does not.

Fields covered:
    block_id, length_m, breadth_m, height_m, volume_m3,
    granite_category, gangsaw_classification, density_mt_per_m3,
    tonnage_mt, rate_per_mt, seigniorage_amount,
    approval_status, approved_by, approved_at, receipt_id
"""

import hashlib

# Order is part of the contract - never reorder, only append.
CANONICAL_FIELDS = (
    "block_id",
    "length_m",
    "breadth_m",
    "height_m",
    "volume_m3",
    "granite_category",
    "gangsaw_classification",
    "density_mt_per_m3",
    "tonnage_mt",
    "rate_per_mt",
    "seigniorage_amount",
    "approval_status",
    "approved_by",
    "approved_at",
    "receipt_id",
)


def _render(value):
    if value is None:
        return ""
    if isinstance(value, float):
        # Fixed precision so float repr noise cannot change the digest.
        return f"{value:.9f}"
    return str(value)


def canonical_representation(record):
    """The exact text that gets hashed. Returned separately so a verifier can
    reproduce it by hand."""
    return "\n".join(f"{field}={_render(record.get(field))}" for field in CANONICAL_FIELDS)


def build_record(block, assessment, receipt_id=None):
    """Collect the hashed business values from the persisted documents.

    assessment may be None: a block that has been measured but not yet priced
    still gets a report (a MEASUREMENT RECORD rather than an assessment), and it
    still gets a digest. Every financial field is then genuinely absent and
    hashes as empty - which is the honest thing to cover, and means the digest
    changes the moment an assessment is created.
    """
    measurement = block.measurement
    return {
        "block_id": block.block_id,
        "length_m": measurement.length_m if measurement else None,
        "breadth_m": measurement.breadth_m if measurement else None,
        "height_m": measurement.height_m if measurement else None,
        "volume_m3": assessment.volume_m3 if assessment else (
            measurement.volume_m3 if measurement else None),
        "granite_category": assessment.granite_category if assessment else None,
        "gangsaw_classification": assessment.gangsaw_classification if assessment else None,
        "density_mt_per_m3": assessment.density_mt_per_m3 if assessment else None,
        "tonnage_mt": assessment.weight_mt if assessment else None,
        "rate_per_mt": assessment.rate_per_mt if assessment else None,
        "seigniorage_amount": assessment.indicative_seigniorage if assessment else None,
        "approval_status": block.approval_status,
        "approved_by": block.approved_by,
        "approved_at": block.approved_at.isoformat() if block.approved_at else None,
        "receipt_id": receipt_id,
    }


def verification_hash(block, assessment, receipt_id=None):
    """SHA-256 of the canonical business representation."""
    canonical = canonical_representation(build_record(block, assessment, receipt_id))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def report_number(block, assessment):
    """Deterministic, traceable report number built from persisted identifiers.

    Not random: RTGS/<block_id>/<assessment object id>. The same record always
    produces the same number, and the number resolves back to the assessment.

    With no assessment yet the number is anchored to the BLOCK id instead and
    suffixed -M for "measurement record", so an unpriced document can never be
    mistaken for a priced one - and so the number changes once the block is
    actually assessed.
    """
    if assessment is None:
        return f"RTGS/{block.block_id}/{str(block.id)[-8:].upper()}-M"
    return f"RTGS/{block.block_id}/{str(assessment.id)[-8:].upper()}"
