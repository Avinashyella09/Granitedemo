"""
The exact financial basis behind a stored Assessment, derived once and shared.

THE DEFECT THIS MODULE FIXES
----------------------------
seigniorage.calculate() computes the payable amount from the UNROUNDED
L x B x H x density product and quantises only once, at the end. The values it
persists on the Assessment are rounded for presentation:

    Assessment.volume_m3   quantised to 6 dp
    Assessment.weight_mt   quantised to 3 dp

A reporting layer that multiplies the ROUNDED stored volume by the density gets
a tonnage that is close but not equal to the one the money came from, and that
tonnage can round to a different amount. On the real block2 record:

    exact  : 0.023587785975 x 2.7 = 0.0636870221325 MT  x 720 = INR 45.85  <-- stored
    rounded: 0.023588       x 2.7 = 0.0636876      MT  x 720 = INR 45.86  <-- wrong

The PDF previously printed the second figure under the label "Tonnage (used for
amount)", so a verifier following the report's own arithmetic would compute
INR 45.86 and conclude the stored assessment was short by a paisa. The money was
always correct; the report was describing it wrongly.

Both the PDF and the OMEPS-ready export now call basis_for() so there is exactly
one derivation, and it reuses seigniorage.exact_tonnage_mt() - the engine's own
formula - rather than restating it.

IMPORTANT: this module derives the TONNAGE BASIS only. It never computes or
re-derives a monetary amount. The payable figure always comes from the persisted
Assessment.

STALENESS GUARD
---------------
The basis is recomputed from the block's CURRENT dimensions. If the block was
re-measured or overridden after the assessment was created, those dimensions are
no longer the ones the stored amount came from, and presenting them as "the
basis" would be a new fabrication. basis_for() detects that by re-quantising its
own result and comparing it to Assessment.volume_m3; on mismatch it reports
status 'stale_measurement' and withholds the exact figures instead of guessing.
"""

from decimal import Decimal, ROUND_HALF_UP

from . import seigniorage

# Reproduced from the current measurement and agrees with the stored assessment.
REPRODUCED = "reproduced"
# Block dimensions changed after the assessment was written; basis not derivable.
STALE = "stale_measurement"
# Not enough persisted data to derive a basis at all.
UNAVAILABLE = "unavailable"


def basis_for(block, assessment):
    """Return the exact tonnage basis for a persisted Assessment.

    Keys:
        status                  one of REPRODUCED / STALE / UNAVAILABLE
        stored_volume_m3        Assessment.volume_m3 (rounded, as persisted)
        stored_tonnage_mt       Assessment.weight_mt (rounded, as persisted)
        exact_volume_m3         Decimal or None
        exact_tonnage_mt        Decimal or None
        explanation             plain-language note safe to print in a report
    """
    stored_volume = getattr(assessment, "volume_m3", None)
    stored_tonnage = getattr(assessment, "weight_mt", None)
    density = getattr(assessment, "density_mt_per_m3", None)
    measurement = getattr(block, "measurement", None)

    result = {
        "status": UNAVAILABLE,
        "stored_volume_m3": stored_volume,
        "stored_tonnage_mt": stored_tonnage,
        "exact_volume_m3": None,
        "exact_tonnage_mt": None,
        "explanation": (
            "The exact tonnage basis could not be derived because the block has "
            "no stored measurement or the assessment has no density."
        ),
    }

    if measurement is None or density is None:
        return result

    try:
        exact_volume = seigniorage.exact_volume_m3(
            measurement.length_m, measurement.breadth_m, measurement.height_m
        )
        exact_tonnage = seigniorage.exact_tonnage_mt(
            measurement.length_m, measurement.breadth_m, measurement.height_m, density
        )
    except seigniorage.SeigniorageError:
        return result

    # Staleness guard: does our recomputed volume still match what was assessed?
    if stored_volume is not None:
        requantised = exact_volume.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        if requantised != Decimal(str(stored_volume)):
            result.update(
                status=STALE,
                explanation=(
                    "The block's stored dimensions no longer reproduce the volume "
                    "recorded on this assessment, so the original tonnage basis "
                    "cannot be restated. The assessment predates the current "
                    "measurement. The payable amount shown remains the assessed "
                    "and stored figure."
                ),
            )
            return result

    result.update(
        status=REPRODUCED,
        exact_volume_m3=exact_volume,
        exact_tonnage_mt=exact_tonnage,
        explanation=(
            "The payable amount was computed from the exact unrounded tonnage "
            "shown here and rounded once, at the end, to the nearest paisa. The "
            "stored volume and tonnage are rounded for presentation only; "
            "re-deriving the amount from those rounded values can differ by a "
            "paisa, so the exact basis above is the authoritative one."
        ),
    )
    return result


def decimal_text(value):
    """Full-precision text for a Decimal basis value, or None.

    Used by the JSON export: a 13-significant-digit tonnage cannot survive a
    round trip through a JSON float, so it is carried as a string.
    """
    return None if value is None else format(value, "f")


# --- Rate schedule provenance -------------------------------------------------
# Assessment does not persist which tariff version priced it; the version is
# recorded only in the 'seigniorage_assessed' audit entry. Reports must prefer
# THAT value, because naming the currently configured schedule on a historical
# assessment would assert a tariff that may not have been the one applied.

RECORDED_AT_ASSESSMENT = "recorded_at_assessment_time"
CURRENT_CONFIGURATION = "current_engine_configuration_not_recorded_on_assessment"


def schedule_provenance(assessed_audit):
    """Return (version, source) for the tariff that priced this assessment.

    assessed_audit is the persisted 'seigniorage_assessed' AuditLog entry, or
    None. Falls back to the engine's current constant, clearly labelled as such
    rather than presented as the recorded value.
    """
    details = getattr(assessed_audit, "details", None) or ""
    if "schedule=" in details:
        recorded = details.split("schedule=", 1)[1].split(",", 1)[0].strip()
        if recorded:
            return recorded, RECORDED_AT_ASSESSMENT
    return seigniorage.RATE_SCHEDULE_VERSION, CURRENT_CONFIGURATION
