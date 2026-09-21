"""
Official RTGS seigniorage calculation.

This replaces the placeholder tariff in blocks/config.py (POC_SEIGNIORAGE_RATES,
a 3x3 invented table of 900-3000 INR/MT with a silent 1000.0 fallback). Every
rate below comes from the official RTGS brief and nothing here invents a value:
an unrecognised granite category raises rather than falling back to a number.

Design rules, all deliberate:

  * The server is authoritative. Rate, gangsaw classification, tonnage and
    seigniorage are derived here from the STORED block measurement. A client
    cannot supply any of them.
  * Money is Decimal, never float. Binary floats cannot represent 0.1 exactly,
    and a revenue figure that fails to reconcile by a paisa is a revenue figure
    nobody trusts.
  * Categories are matched case/space-insensitively but never "closest match" -
    an unknown name is an error, because silently pricing an unknown granite at
    the cheapest rate is exactly how under-assessment happens.
"""

from decimal import Decimal, ROUND_HALF_UP

# --- Gangsaw threshold --------------------------------------------------------
# Official brief: above gangsaw is "> 270 cm x 150 cm"; below/within is
# "<= 270 cm x 150 cm".
#
# BOUNDARY (deterministic, tested): a block is ABOVE gangsaw only when it is
# strictly greater than BOTH thresholds. Exactly 270 x 150 is WITHIN gangsaw,
# because the brief's below/within band is inclusive ("<=").
GANGSAW_LENGTH_THRESHOLD_CM = Decimal("270")
GANGSAW_BREADTH_THRESHOLD_CM = Decimal("150")

ABOVE_GANGSAW = "Above Gangsaw"
WITHIN_GANGSAW = "Within Gangsaw"

# --- Official rate schedule (INR per metric tonne) ----------------------------
# Keyed by rate group -> (above gangsaw, within gangsaw).
RATE_GROUPS = {
    "Black Galaxy": (Decimal("1830"), Decimal("1530")),
    "Black Granite (Other)": (Decimal("1520"), Decimal("1300")),
    "Colour Granite": (Decimal("1660"), Decimal("1410")),
    "Silver Waves / Madanapalli White / Iscon White": (Decimal("1360"), Decimal("1200")),
    "Others": (Decimal("940"), Decimal("720")),
}

# Accepted granite category -> rate group.
#
# The brief lists the Colour Granite band by variety (Srikakulam Blue, Moon
# White, River White Vizag, Leptinites, Black Pearl) rather than as a single
# name, so each variety is accepted in its own right and maps to that band. The
# band names themselves are accepted too, since that is what an operator
# choosing from a dropdown would pick.
CATEGORY_TO_GROUP = {
    "Black Galaxy": "Black Galaxy",

    "Black Granite (Other)": "Black Granite (Other)",
    "Black Granite": "Black Granite (Other)",

    "Colour Granite": "Colour Granite",
    "Srikakulam Blue": "Colour Granite",
    "Moon White": "Colour Granite",
    "River White Vizag": "Colour Granite",
    "Leptinites": "Colour Granite",
    "Black Pearl": "Colour Granite",

    "Silver Waves / Madanapalli White / Iscon White":
        "Silver Waves / Madanapalli White / Iscon White",
    "Silver Waves": "Silver Waves / Madanapalli White / Iscon White",
    "Madanapalli White": "Silver Waves / Madanapalli White / Iscon White",
    "Iscon White": "Silver Waves / Madanapalli White / Iscon White",

    "Others": "Others",
}

# Stable, sorted list for serializer choices / UI dropdowns.
OFFICIAL_GRANITE_CATEGORIES = sorted(CATEGORY_TO_GROUP.keys())

# Schedule provenance, stamped onto every assessment's audit trail so a stored
# figure can always be traced to the tariff that produced it.
RATE_SCHEDULE_VERSION = "RTGS-OFFICIAL-2026-09"


class SeigniorageError(ValueError):
    """Raised for anything that must not be priced: unknown category, unusable
    measurement. Never swallowed into a default rate."""


def _normalise(value):
    """Collapse case and whitespace for lookup only. The CANONICAL name from the
    table is what gets stored, so a record never keeps an operator's typo."""
    if value is None:
        raise SeigniorageError("granite_category is required.")
    collapsed = " ".join(str(value).split()).lower()
    if not collapsed:
        raise SeigniorageError("granite_category is required.")
    return collapsed


_LOOKUP = {_normalise(name): name for name in CATEGORY_TO_GROUP}


def resolve_category(raw_category):
    """Return the canonical category name, or raise. Never guesses."""
    key = _normalise(raw_category)
    canonical = _LOOKUP.get(key)
    if canonical is None:
        raise SeigniorageError(
            f"Unknown granite category {raw_category!r}. "
            f"Accepted categories: {', '.join(OFFICIAL_GRANITE_CATEGORIES)}."
        )
    return canonical


def classify_gangsaw(length_m, breadth_m):
    """Apply the official > 270 cm x 150 cm rule.

    Dimensions are taken as the block's own length and breadth, matching the
    Measurement model's field semantics (length_m, breadth_m) and the brief's
    wording. Exactly at the threshold is WITHIN gangsaw.
    """
    length_cm = Decimal(str(length_m)) * 100
    breadth_cm = Decimal(str(breadth_m)) * 100
    is_above = (
        length_cm > GANGSAW_LENGTH_THRESHOLD_CM
        and breadth_cm > GANGSAW_BREADTH_THRESHOLD_CM
    )
    return ABOVE_GANGSAW if is_above else WITHIN_GANGSAW


def get_official_rate(granite_category, gangsaw_classification):
    """INR per MT from the official schedule. Raises on anything unrecognised."""
    canonical = resolve_category(granite_category)
    group = CATEGORY_TO_GROUP[canonical]
    above_rate, within_rate = RATE_GROUPS[group]
    if gangsaw_classification == ABOVE_GANGSAW:
        return above_rate
    if gangsaw_classification == WITHIN_GANGSAW:
        return within_rate
    raise SeigniorageError(
        f"Unknown gangsaw classification {gangsaw_classification!r}."
    )


def _positive_decimal(value, field):
    try:
        number = Decimal(str(value))
    except Exception:
        raise SeigniorageError(f"{field} must be a number; got {value!r}.")
    if not number.is_finite():
        raise SeigniorageError(f"{field} must be a finite number.")
    if number <= 0:
        raise SeigniorageError(f"{field} must be greater than zero; got {value}.")
    return number


def exact_volume_m3(length_m, breadth_m, height_m):
    """The UNROUNDED L x B x H product, as Decimal.

    This is the volume the payable amount is actually derived from. It is
    deliberately exposed so a reporting layer can state the true financial basis
    instead of re-deriving one from the rounded value stored on the Assessment.
    """
    return (
        _positive_decimal(length_m, "length_m")
        * _positive_decimal(breadth_m, "breadth_m")
        * _positive_decimal(height_m, "height_m")
    )


def exact_tonnage_mt(length_m, breadth_m, height_m, density_mt_per_m3):
    """The UNROUNDED tonnage that calculate() multiplies by the official rate.

    WHY THIS EXISTS. Assessment.volume_m3 is quantised to 6 dp and
    Assessment.weight_mt to 3 dp for presentation, but the amount is computed
    from the unrounded product and quantised ONCE, at the end. Multiplying the
    rounded stored volume by the density therefore yields a subtly different
    tonnage, which can round to a different payable amount - for the real
    block2 record, INR 45.86 instead of the correct INR 45.85. Any report that
    wants to show "the tonnage the money came from" must use this function, not
    stored_volume x density.
    """
    return exact_volume_m3(length_m, breadth_m, height_m) * _positive_decimal(
        density_mt_per_m3, "density_mt_per_m3"
    )


def calculate(length_m, breadth_m, height_m, granite_category, density_mt_per_m3):
    """The single deterministic entry point.

    Everything is derived here: volume from the stored dimensions, classification
    from the official rule, rate from the official schedule, amount from
    tonnage x rate. The caller supplies no money and no classification.
    """
    length = _positive_decimal(length_m, "length_m")
    breadth = _positive_decimal(breadth_m, "breadth_m")
    height = _positive_decimal(height_m, "height_m")
    density = _positive_decimal(density_mt_per_m3, "density_mt_per_m3")

    canonical_category = resolve_category(granite_category)
    classification = classify_gangsaw(length, breadth)
    rate = get_official_rate(canonical_category, classification)

    # Volume stays L x B x H, matching the AR ingestion path exactly. Routed
    # through the shared helpers so the basis a report prints is, by
    # construction, the same arithmetic the amount below is computed from.
    volume = exact_volume_m3(length, breadth, height)
    tonnage = exact_tonnage_mt(length, breadth, height, density)

    # Quantise only at the reporting boundary, so no intermediate rounding
    # compounds into the billed figure.
    volume_q = volume.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    tonnage_q = tonnage.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    amount_q = (tonnage * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "length_m": float(length),
        "breadth_m": float(breadth),
        "height_m": float(height),
        "volume_m3": float(volume_q),
        "density_mt_per_m3": float(density),
        "tonnage_mt": float(tonnage_q),
        "granite_category": canonical_category,
        "gangsaw_classification": classification,
        "rate_per_mt": float(rate),
        "seigniorage_amount": float(amount_q),
        "rate_schedule_version": RATE_SCHEDULE_VERSION,
        "is_official": True,
        # Decimal originals, for callers that must not go through float.
        "_decimal": {
            "volume_m3": volume_q,
            "tonnage_mt": tonnage_q,
            "rate_per_mt": rate,
            "seigniorage_amount": amount_q,
        },
    }
