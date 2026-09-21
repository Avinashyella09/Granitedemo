"""
Multi-face granite block measurement: the reconciliation engine.

PURE MODULE. No Django, no MongoDB, no network, no I/O, no writes. It takes
plain observations in and returns a deterministic result. Nothing in the running
Phase 6 system imports it yet - it is deliberately unwired (Phase 7B).

WHAT IT IS FOR
--------------
Today one face is traced and all three dimensions come from that single chain.
Multi-face measurement observes each dimension more than once:

    Length   Face 1 (Front) and Face 3 (Back)          -> 2 observations
    Breadth  Face 2 (Right) and Face 4 (Left)          -> 2 observations
    Height   every face                                -> 4 observations

Multiple observations only help if disagreement is surfaced rather than
averaged away. That is this module's whole job: decide whether the observations
agree, and refuse to produce a volume when they do not.

THE RULES (approved design, Phase 7A)
-------------------------------------
    Length / Breadth : within tolerance -> arithmetic mean of the two
                       outside         -> INCONSISTENT, name both faces and the gap
    Height           : median of the four (immune to one bad face)
                       blocked if (max - min) exceeds tolerance
    Volume           : exists ONLY when all three dimensions reconcile

A rejected disagreement is never averaged, never rounded away, and never
produces a volume. That is the property the whole feature rests on.

WHY Decimal AND NOT float
-------------------------
"Exactly at tolerance is accepted" is a stated requirement, and binary floats
cannot honour it. Measured:

    1.53 - 1.50 == 0.030000000000000027   ->  <= 0.03 is False

so an observation sitting exactly on the boundary would be rejected by an
arithmetic artefact rather than by the rule. Every comparison here goes through
Decimal(str(value)), which makes the boundary exact and the result reproducible.
This mirrors blocks/seigniorage.py, which uses Decimal for the same reason.

TOLERANCES ARE NOT CALIBRATED
-----------------------------
DEFAULT_TOLERANCES carries calibration_status == CALIBRATION_PENDING. The values
in it are PLACEHOLDERS chosen so unit tests have something deterministic to
exercise. They are NOT field-validated production tolerances and must not be
presented as such. Real values have to come from a field trial across real
blocks before this is wired to anything that bills anyone. Callers may inject
their own Tolerances; nothing here reads global configuration.
"""

from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field as dataclass_field

# --- Face plan ----------------------------------------------------------------
# Fixed order, approved in Phase 7A. dimension is what the face contributes in
# ADDITION to height, which every face contributes.

LENGTH = "length"
BREADTH = "breadth"
HEIGHT = "height"


@dataclass(frozen=True)
class FaceSpec:
    index: int
    label: str
    dimension: str


FACE_PLAN = (
    FaceSpec(0, "Front", LENGTH),
    FaceSpec(1, "Right Side", BREADTH),
    FaceSpec(2, "Back", LENGTH),
    FaceSpec(3, "Left Side", BREADTH),
)

REQUIRED_FACE_COUNT = len(FACE_PLAN)
FACE_BY_INDEX = {spec.index: spec for spec in FACE_PLAN}

# Which faces contribute each dimension. Derived from FACE_PLAN so the two can
# never disagree.
LENGTH_FACES = tuple(s.index for s in FACE_PLAN if s.dimension == LENGTH)
BREADTH_FACES = tuple(s.index for s in FACE_PLAN if s.dimension == BREADTH)
HEIGHT_FACES = tuple(s.index for s in FACE_PLAN)

# --- Result vocabulary --------------------------------------------------------

RECONCILED = "reconciled"
INCONSISTENT = "inconsistent"
INCOMPLETE = "incomplete"

RULE_MEAN_OF_2 = "mean_of_2"
RULE_MEDIAN_OF_4 = "median_of_4"

CALIBRATION_PENDING = "CALIBRATION_PENDING"
CALIBRATION_FIELD_VALIDATED = "FIELD_VALIDATED"

CROSS_FACE_AVAILABLE = "available"
CROSS_FACE_UNAVAILABLE = "unavailable"


class FaceReconciliationError(ValueError):
    """Structurally invalid input - a programming or transport fault, not a
    workflow state. Carries a machine-readable code, matching the convention in
    blocks/ar_ingest.py and blocks/seigniorage.py.

    Deliberately NOT raised for a merely unfinished measurement: an officer who
    has captured two of four faces is mid-workflow, not in error. That returns a
    result with status INCOMPLETE so a UI can render progress.
    """

    def __init__(self, message, code):
        super().__init__(message)
        self.message = message
        self.code = code


# --- Tolerances ---------------------------------------------------------------


@dataclass(frozen=True)
class Tolerances:
    """tolerance = max(absolute_floor_m, reference * relative_fraction)

    The same shape as blocks/ar_ingest.compare_volume, which already blends an
    absolute floor with a relative fraction, so the two read alike.

    The reference is the arithmetic MEAN of the observations being compared.
    Mean rather than min or max because it is order-independent, which keeps the
    tolerance itself deterministic: swapping Face 1 and Face 3 must not change
    whether they agree.
    """

    absolute_floor_m: Decimal = Decimal("0.020")
    relative_fraction: Decimal = Decimal("0.02")
    calibration_status: str = CALIBRATION_PENDING

    def tolerance_for(self, reference):
        relative = abs(_decimal(reference, "tolerance_reference")) * self.relative_fraction
        return max(self.absolute_floor_m, relative)

    def as_dict(self):
        return {
            "absolute_floor_m": float(self.absolute_floor_m),
            "relative_fraction": float(self.relative_fraction),
            "calibration_status": self.calibration_status,
            "note": (
                "PLACEHOLDER VALUES - not field-validated. Calibrate against real "
                "blocks before these gate any billable measurement."
                if self.calibration_status == CALIBRATION_PENDING
                else "Field-validated tolerances."
            ),
        }


# Placeholders only. See the module docstring.
DEFAULT_TOLERANCES = Tolerances()


# --- Input handling -----------------------------------------------------------


def _decimal(value, field_name):
    """Exact Decimal from a numeric input, or raise. str() first so a float's
    binary representation cannot leak into the comparison."""
    if isinstance(value, bool) or value is None:
        raise FaceReconciliationError(
            f"{field_name} must be a number; got {value!r}.", "value_not_numeric"
        )
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise FaceReconciliationError(
            f"{field_name} must be a number; got {value!r}.", "value_not_numeric"
        )
    if not number.is_finite():
        raise FaceReconciliationError(
            f"{field_name} must be a finite number; got {value!r}.", "value_not_finite"
        )
    return number


def _positive_decimal(value, field_name):
    number = _decimal(value, field_name)
    if number <= 0:
        raise FaceReconciliationError(
            f"{field_name} must be greater than zero; got {value}.", "value_not_positive"
        )
    return number


@dataclass(frozen=True)
class FaceObservation:
    """One captured face: the dimension it contributes, plus height.

    points is optional and used only by the non-blocking cross-face check. When
    absent, that check reports itself unavailable rather than inventing geometry.
    """

    index: int
    label: str
    dimension: str
    dimension_value: Decimal
    height: Decimal
    points: tuple = dataclass_field(default=())


def _parse_face(raw, seen_indices):
    if not isinstance(raw, dict):
        raise FaceReconciliationError(
            f"Each face must be a mapping; got {type(raw).__name__}.", "face_not_a_mapping"
        )

    if "face_index" not in raw:
        raise FaceReconciliationError("face_index is required.", "face_index_missing")

    try:
        index = int(raw["face_index"])
    except (TypeError, ValueError):
        raise FaceReconciliationError(
            f"face_index must be an integer; got {raw['face_index']!r}.", "face_index_invalid"
        )

    spec = FACE_BY_INDEX.get(index)
    if spec is None:
        raise FaceReconciliationError(
            f"Unknown face_index {index}. Expected one of "
            f"{sorted(FACE_BY_INDEX)} ({', '.join(s.label for s in FACE_PLAN)}).",
            "face_index_unknown",
        )

    if index in seen_indices:
        raise FaceReconciliationError(
            f"face_index {index} ({spec.label}) supplied more than once.", "face_duplicated"
        )

    # The dimension key is named, so a face sending the wrong dimension is
    # caught here rather than silently reconciled as the wrong quantity.
    dimension_key = f"{spec.dimension}_m"
    if dimension_key not in raw:
        raise FaceReconciliationError(
            f"Face {index} ({spec.label}) must supply '{dimension_key}'.",
            "face_dimension_missing",
        )
    if "height_m" not in raw:
        raise FaceReconciliationError(
            f"Face {index} ({spec.label}) must supply 'height_m'.", "face_height_missing"
        )

    return FaceObservation(
        index=index,
        label=spec.label,
        dimension=spec.dimension,
        dimension_value=_positive_decimal(
            raw[dimension_key], f"Face {index} ({spec.label}) {dimension_key}"
        ),
        height=_positive_decimal(raw["height_m"], f"Face {index} ({spec.label}) height_m"),
        points=_parse_points(raw.get("points"), index, spec.label),
    )


def _parse_points(raw, index, label):
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise FaceReconciliationError(
            f"Face {index} ({label}) points must be a list.", "points_not_a_list"
        )
    parsed = []
    for position, point in enumerate(raw):
        if isinstance(point, dict):
            triple = (point.get("x"), point.get("y"), point.get("z"))
        elif isinstance(point, (list, tuple)) and len(point) == 3:
            triple = tuple(point)
        else:
            raise FaceReconciliationError(
                f"Face {index} ({label}) point {position} must be [x, y, z] or "
                f"{{'x':..,'y':..,'z':..}}.",
                "point_malformed",
            )
        parsed.append(
            tuple(
                _decimal(component, f"Face {index} ({label}) point {position} component")
                for component in triple
            )
        )
    return tuple(parsed)


# --- Statistics (exact, on Decimal) -------------------------------------------


def _mean(values):
    return sum(values) / Decimal(len(values))


def _median(values):
    """Exact median. For an even count, the mean of the two middle values -
    which for four observations is the mean of the 2nd and 3rd, discarding the
    extremes. That is precisely why median was chosen over mean: one bad face
    cannot move the result."""
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


# --- Per-dimension reconciliation ---------------------------------------------


def _reconcile_pair(dimension, faces, tolerances):
    """Two observations of one dimension: mean when they agree, refusal when
    they do not. Never averages a disagreement."""
    observations = [
        {"face_index": f.index, "face_label": f.label, "value_m": float(f.dimension_value)}
        for f in faces
    ]
    values = [f.dimension_value for f in faces]
    gap = abs(values[0] - values[1])
    tolerance = tolerances.tolerance_for(_mean(values))

    detail = {
        "dimension": dimension,
        "rule": RULE_MEAN_OF_2,
        "observations": observations,
        "gap_m": float(gap),
        "tolerance_m": float(tolerance),
        "_gap": gap,
        "_tolerance": tolerance,
    }

    # <= so that exactly at tolerance is ACCEPTED. Exact because Decimal.
    if gap <= tolerance:
        value = _mean(values)
        detail.update(status=RECONCILED, value_m=float(value), _value=value, reason=None)
        return detail

    detail.update(
        status=INCONSISTENT,
        value_m=None,
        _value=None,
        reason=(
            f"{dimension.capitalize()} disagrees between "
            f"{faces[0].label} ({float(values[0]):.4f} m) and "
            f"{faces[1].label} ({float(values[1]):.4f} m) by {float(gap):.4f} m, "
            f"which exceeds the {float(tolerance):.4f} m tolerance. "
            f"Re-measure one of those two faces."
        ),
    )
    return detail


def _reconcile_height(faces, tolerances):
    """Four observations: median, gated on total spread."""
    values = [f.height for f in faces]
    median = _median(values)
    spread = max(values) - min(values)
    tolerance = tolerances.tolerance_for(median)

    observations = [
        {
            "face_index": f.index,
            "face_label": f.label,
            "value_m": float(f.height),
            "deviation_from_median_m": float(f.height - median),
        }
        for f in faces
    ]

    detail = {
        "dimension": HEIGHT,
        "rule": RULE_MEDIAN_OF_4,
        "observations": observations,
        "spread_m": float(spread),
        "tolerance_m": float(tolerance),
        "median_m": float(median),
        "_spread": spread,
        "_tolerance": tolerance,
    }

    if spread <= tolerance:
        detail.update(
            status=RECONCILED,
            value_m=float(median),
            _value=median,
            outliers=[],
            reason=None,
        )
        return detail

    # Name the faces furthest from the median. "Where practical" - with four
    # observations the extremes are the actionable ones.
    worst = max(abs(f.height - median) for f in faces)
    outliers = [
        {
            "face_index": f.index,
            "face_label": f.label,
            "value_m": float(f.height),
            "deviation_from_median_m": float(f.height - median),
        }
        for f in faces
        if abs(f.height - median) == worst
    ]
    named = ", ".join(f"{o['face_label']} ({o['value_m']:.4f} m)" for o in outliers)
    detail.update(
        status=INCONSISTENT,
        value_m=None,
        _value=None,
        outliers=outliers,
        reason=(
            f"Height varies by {float(spread):.4f} m across the four faces, which "
            f"exceeds the {float(tolerance):.4f} m tolerance. Furthest from the "
            f"median ({float(median):.4f} m): {named}. Re-measure that face."
        ),
    )
    return detail


# --- Cross-face integrity (non-blocking) --------------------------------------


def _vector(a, b):
    return tuple(b[i] - a[i] for i in range(3))


def _dot(u, v):
    return sum(u[i] * v[i] for i in range(3))


def _magnitude(v):
    # Decimal has no sqrt on the class; go through the context.
    squared = _dot(v, v)
    return squared.sqrt() if squared > 0 else Decimal(0)


def _midpoint(a, b):
    return tuple((a[i] + b[i]) / Decimal(2) for i in range(3))


def _cross_face_check(faces_by_index, breadth_value, near_parallel_threshold=Decimal("0.85")):
    """Front and Back length edges should be near-parallel and separated by
    roughly the block's breadth. Catches an officer measuring the same face
    twice - which no per-face check can detect, because each face is
    individually perfect.

    WARNING ONLY. Never contributes a blocking reason. Reports itself
    unavailable when points are absent rather than inventing geometry.
    """
    unavailable = {
        "status": CROSS_FACE_UNAVAILABLE,
        "blocking": False,
        "parallelism": None,
        "near_parallel": None,
        "midpoint_separation_m": None,
        "expected_separation_m": None,
        "separation_gap_m": None,
        "warnings": [],
    }

    front = faces_by_index.get(LENGTH_FACES[0])
    back = faces_by_index.get(LENGTH_FACES[1])
    if front is None or back is None:
        return {**unavailable, "reason": "Both length faces are required."}
    if len(front.points) < 2 or len(back.points) < 2:
        return {
            **unavailable,
            "reason": (
                "No captured points supplied for the length faces, so geometry "
                "cannot be checked. Nothing has been inferred."
            ),
        }

    # P1 -> P2 is the face's own dimension edge, by the 3-point face contract.
    front_edge = _vector(front.points[0], front.points[1])
    back_edge = _vector(back.points[0], back.points[1])
    front_len = _magnitude(front_edge)
    back_len = _magnitude(back_edge)
    if front_len == 0 or back_len == 0:
        return {**unavailable, "reason": "A length edge has zero extent."}

    parallelism = abs(_dot(front_edge, back_edge) / (front_len * back_len))
    near_parallel = parallelism >= near_parallel_threshold

    separation = _magnitude(
        _vector(_midpoint(front.points[0], front.points[1]),
                _midpoint(back.points[0], back.points[1]))
    )

    warnings = []
    if not near_parallel:
        warnings.append(
            f"Front and Back length edges are not near-parallel "
            f"(alignment {float(parallelism):.3f}). They may not be opposite faces."
        )

    expected = None
    separation_gap = None
    if breadth_value is not None:
        expected = breadth_value
        separation_gap = abs(separation - expected)
        # A separation far below the breadth is the signature of the same face
        # measured twice.
        if separation < expected / Decimal(2):
            warnings.append(
                f"Front and Back were captured only {float(separation):.4f} m apart, "
                f"well under the reconciled breadth of {float(expected):.4f} m. "
                f"The same face may have been measured twice."
            )

    return {
        "status": CROSS_FACE_AVAILABLE,
        "blocking": False,
        "parallelism": float(parallelism),
        "near_parallel": bool(near_parallel),
        "midpoint_separation_m": float(separation),
        "expected_separation_m": None if expected is None else float(expected),
        "separation_gap_m": None if separation_gap is None else float(separation_gap),
        "warnings": warnings,
        "reason": None,
    }


# --- Entry point --------------------------------------------------------------


def reconcile(faces, tolerances=None):
    """Reconcile four face observations into final L/B/H and a volume.

    faces: a sequence of mappings, each
        {"face_index": 0..3, "<dimension>_m": float, "height_m": float,
         "points": [[x,y,z], ...]  # optional, cross-face check only}
      where <dimension> is 'length' for faces 0 and 2, 'breadth' for 1 and 3.

    Returns a dict. volume_m3 is None unless every dimension reconciles.
    Raises FaceReconciliationError for structurally invalid input.
    """
    tolerances = tolerances or DEFAULT_TOLERANCES

    if faces is None or isinstance(faces, (str, bytes, dict)):
        raise FaceReconciliationError(
            "faces must be a sequence of face mappings.", "faces_not_a_sequence"
        )
    faces = list(faces)
    if len(faces) > REQUIRED_FACE_COUNT:
        raise FaceReconciliationError(
            f"Expected at most {REQUIRED_FACE_COUNT} faces; got {len(faces)}.",
            "too_many_faces",
        )

    seen = set()
    parsed = []
    for raw in faces:
        observation = _parse_face(raw, seen)
        seen.add(observation.index)
        parsed.append(observation)

    by_index = {o.index: o for o in parsed}
    missing = [FACE_BY_INDEX[i] for i in sorted(FACE_BY_INDEX) if i not in by_index]

    base = {
        "face_plan": [
            {"face_index": s.index, "label": s.label, "dimension": s.dimension}
            for s in FACE_PLAN
        ],
        "faces_captured": sorted(by_index),
        "faces_missing": [{"face_index": s.index, "label": s.label} for s in missing],
        "tolerances_used": tolerances.as_dict(),
        "rules_applied": {
            LENGTH: RULE_MEAN_OF_2,
            BREADTH: RULE_MEAN_OF_2,
            HEIGHT: RULE_MEDIAN_OF_4,
        },
    }

    # --- Incomplete: a normal mid-workflow state, not an error ---------------
    if missing:
        return {
            **base,
            "status": INCOMPLETE,
            "final_length_m": None,
            "final_breadth_m": None,
            "final_height_m": None,
            "volume_m3": None,
            "length_observations": _observations_for(by_index, LENGTH_FACES, "dimension_value"),
            "breadth_observations": _observations_for(by_index, BREADTH_FACES, "dimension_value"),
            "height_observations": _observations_for(by_index, HEIGHT_FACES, "height"),
            "length_gap_m": None,
            "breadth_gap_m": None,
            "height_spread_m": None,
            "length": None,
            "breadth": None,
            "height": None,
            "cross_face": {
                "status": CROSS_FACE_UNAVAILABLE,
                "blocking": False,
                "reason": "Reconciliation incomplete.",
                "warnings": [],
            },
            "warnings": [],
            "blocking_reasons": [
                "Measurement incomplete: "
                + ", ".join(f"{s.label} (face {s.index})" for s in missing)
                + " not captured. All four faces are required before a volume exists."
            ],
            "_decimal": {},
        }

    # --- All four present: reconcile each dimension --------------------------
    length = _reconcile_pair(LENGTH, [by_index[i] for i in LENGTH_FACES], tolerances)
    breadth = _reconcile_pair(BREADTH, [by_index[i] for i in BREADTH_FACES], tolerances)
    height = _reconcile_height([by_index[i] for i in HEIGHT_FACES], tolerances)

    blocking = [d["reason"] for d in (length, breadth, height) if d["status"] == INCONSISTENT]
    reconciled = not blocking

    cross_face = _cross_face_check(by_index, breadth["_value"] if reconciled else None)

    volume = None
    if reconciled:
        volume = length["_value"] * breadth["_value"] * height["_value"]

    return {
        **base,
        "status": RECONCILED if reconciled else INCONSISTENT,
        "final_length_m": length["value_m"],
        "final_breadth_m": breadth["value_m"],
        "final_height_m": height["value_m"],
        # Volume exists ONLY when every dimension reconciled. This is the single
        # most important line in the module.
        "volume_m3": None if volume is None else float(volume),
        "length_observations": _observations_for(by_index, LENGTH_FACES, "dimension_value"),
        "breadth_observations": _observations_for(by_index, BREADTH_FACES, "dimension_value"),
        "height_observations": _observations_for(by_index, HEIGHT_FACES, "height"),
        "length_gap_m": length["gap_m"],
        "breadth_gap_m": breadth["gap_m"],
        "height_spread_m": height["spread_m"],
        "length": _public(length),
        "breadth": _public(breadth),
        "height": _public(height),
        "cross_face": cross_face,
        "warnings": list(cross_face.get("warnings", [])),
        "blocking_reasons": blocking,
        # Full-precision originals, for a caller that must not go through float.
        # Mirrors the "_decimal" key seigniorage.calculate already returns.
        "_decimal": {
            "final_length_m": length["_value"],
            "final_breadth_m": breadth["_value"],
            "final_height_m": height["_value"],
            "volume_m3": volume,
        },
    }


def _observations_for(by_index, face_indices, attribute):
    return [
        {
            "face_index": i,
            "face_label": FACE_BY_INDEX[i].label,
            "value_m": float(getattr(by_index[i], attribute)),
        }
        for i in face_indices
        if i in by_index
    ]


def _public(detail):
    """Strip the Decimal working values from a per-dimension detail block."""
    return {k: v for k, v in detail.items() if not k.startswith("_")}
