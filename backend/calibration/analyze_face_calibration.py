#!/usr/bin/env python3
"""
Multi-face tolerance calibration: dataset analysis (Phase 7C-1).

    ./venv/bin/python calibration/analyze_face_calibration.py
    ./venv/bin/python calibration/analyze_face_calibration.py --csv path/to/other.csv

WHAT THIS IS
------------
A development-only analysis tool. It lives OUTSIDE blocks/ on purpose: the
reconciliation engine must stay unwired from production until calibration is
finished, and blocks/tests_face_reconciliation.py has a test that fails if any
file under blocks/ imports it. Nothing here touches Django, MongoDB, the AR
endpoint, the dashboard, the PDF, OMEPS or seigniorage. It reads a CSV and
prints. It writes nothing.

HOW TO COLLECT THE DATA (lowest-risk method, option A)
------------------------------------------------------
Measure each face of a real block with the existing single-face AR flow, one
face at a time, and write the readings into
calibration/face_calibration_dataset.csv by hand. This needs NO app change, NO
backend change and NO new schema - the current build already measures two
adjacent edges per trace, which is exactly what one face contributes.

Per block, four traces:

    Face 1  Front       -> length_face1,  height_face1
    Face 2  Right Side  -> breadth_face2, height_face2
    Face 3  Back        -> length_face3,  height_face3
    Face 4  Left Side   -> breadth_face4, height_face4

All dimensions in METRES. Leave tape_* blank unless a tape was actually used -
an invented reference measurement is worse than none, because it would make the
calibration look better founded than it is.

These are calibration readings, not assessments. Do not submit them through
/api/blocks/ar-measure/, do not create Assessments from them, and do not let
them touch any existing block.

WHAT IT REPORTS
---------------
Per dimension: the absolute and relative disagreement across blocks, with min /
median / p90 / p95 / max. Then, for each candidate tolerance pair, the share of
observations it would accept, and - the part that actually matters for a revenue
system - the WORST disagreement it would accept, converted into the volume and
rupee error that disagreement would silently carry into a bill.

It does not pick a tolerance. It reports evidence.
"""

import argparse
import csv
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blocks import face_reconciliation as fr  # noqa: E402  (after sys.path setup)

DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "face_calibration_dataset.csv")

# The brief targets ~20 blocks. Below this, percentile estimates are not
# meaningful and no tolerance may be called production-valid.
MIN_BLOCKS_FOR_CONCLUSION = 20

# Candidate (absolute_floor_m, relative_fraction) pairs to evaluate.
# These are CANDIDATES to test against evidence, not proposals.
CANDIDATES = [
    (Decimal("0.010"), Decimal("0.010")),
    (Decimal("0.015"), Decimal("0.015")),
    (Decimal("0.020"), Decimal("0.020")),   # the current CALIBRATION_PENDING placeholder
    (Decimal("0.030"), Decimal("0.020")),
    (Decimal("0.030"), Decimal("0.030")),
    (Decimal("0.050"), Decimal("0.030")),
]

# Used only to translate a dimensional error into a revenue error, so the
# consequence of a loose tolerance is visible in the units that matter.
# Mirrors the "Others / Within Gangsaw" band and the project default density.
DEMO_RATE_INR_PER_MT = Decimal("720")
DEMO_DENSITY_MT_PER_M3 = Decimal("2.7")

REQUIRED = [
    "block_id",
    "length_face1", "length_face3",
    "breadth_face2", "breadth_face4",
    "height_face1", "height_face2", "height_face3", "height_face4",
]


def load(path):
    if not os.path.exists(path):
        sys.exit(f"Dataset not found: {path}")
    with open(path, newline="") as handle:
        rows = [r for r in csv.DictReader(handle)
                if any((v or "").strip() for v in r.values())]
    problems = []
    parsed = []
    for line_no, row in enumerate(rows, start=2):
        missing = [c for c in REQUIRED if not (row.get(c) or "").strip()]
        if missing:
            problems.append(f"line {line_no}: missing {', '.join(missing)}")
            continue
        try:
            entry = {"block_id": row["block_id"].strip(),
                     "raw": row,
                     **{c: Decimal(row[c].strip()) for c in REQUIRED[1:]}}
        except Exception as exc:
            problems.append(f"line {line_no}: unparseable ({exc})")
            continue
        if any(entry[c] <= 0 for c in REQUIRED[1:]):
            problems.append(f"line {line_no}: non-positive dimension")
            continue
        parsed.append(entry)
    return parsed, problems


def percentile(sorted_values, fraction):
    """Nearest-rank percentile. Stated explicitly because with a ~20-sample
    dataset the interpolation method visibly changes the answer."""
    if not sorted_values:
        return None
    # Decimal throughout - mixing in a float 0.5 raises TypeError.
    rank = max(1, min(len(sorted_values),
                      int((fraction * Decimal(len(sorted_values)) + Decimal("0.5"))
                          .to_integral_value())))
    return sorted_values[rank - 1]


def stats(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = (ordered[mid] if len(ordered) % 2 else
              (ordered[mid - 1] + ordered[mid]) / Decimal(2))
    return {
        "n": len(ordered),
        "min": ordered[0],
        "median": median,
        "p90": percentile(ordered, Decimal("0.90")),
        "p95": percentile(ordered, Decimal("0.95")),
        "max": ordered[-1],
    }


def observations(dataset):
    """Per-block disagreement for each dimension, absolute and relative."""
    out = {"length": [], "breadth": [], "height": []}
    for entry in dataset:
        l_gap = abs(entry["length_face1"] - entry["length_face3"])
        l_ref = (entry["length_face1"] + entry["length_face3"]) / Decimal(2)
        b_gap = abs(entry["breadth_face2"] - entry["breadth_face4"])
        b_ref = (entry["breadth_face2"] + entry["breadth_face4"]) / Decimal(2)
        heights = [entry[f"height_face{i}"] for i in (1, 2, 3, 4)]
        h_spread = max(heights) - min(heights)
        h_ref = fr._median(heights)

        out["length"].append({"block_id": entry["block_id"], "gap": l_gap,
                              "reference": l_ref, "relative": l_gap / l_ref})
        out["breadth"].append({"block_id": entry["block_id"], "gap": b_gap,
                               "reference": b_ref, "relative": b_gap / b_ref})
        out["height"].append({"block_id": entry["block_id"], "gap": h_spread,
                              "reference": h_ref, "relative": h_spread / h_ref})
    return out


def revenue_impact(gap, reference):
    """A disagreement of `gap` on a dimension of `reference` that gets averaged
    away carries at most gap/2 of dimensional error into the billed volume."""
    if reference == 0:
        return None
    half = (gap / Decimal(2)) / reference          # fractional error in one dimension
    # Volume is a product of three dimensions; an error in one propagates ~1:1.
    return {
        "volume_error_pct": half * 100,
        "inr_per_m3": DEMO_DENSITY_MT_PER_M3 * DEMO_RATE_INR_PER_MT,
    }


def evaluate(dimension, entries, floor, fraction):
    tol = fr.Tolerances(absolute_floor_m=floor, relative_fraction=fraction)
    accepted, rejected = [], []
    for e in entries:
        (accepted if e["gap"] <= tol.tolerance_for(e["reference"]) else rejected).append(e)
    worst = max(accepted, key=lambda e: e["gap"]) if accepted else None
    return {"accepted": accepted, "rejected": rejected, "worst_accepted": worst}


def fmt(value, places=4):
    return "n/a" if value is None else f"{float(value):.{places}f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=DEFAULT_CSV)
    args = parser.parse_args()

    dataset, problems = load(args.csv)

    print("=" * 74)
    print("MULTI-FACE TOLERANCE CALIBRATION - DATASET ANALYSIS")
    print("=" * 74)
    print(f"dataset : {args.csv}")
    print(f"blocks  : {len(dataset)}   (target {MIN_BLOCKS_FOR_CONCLUSION})")
    if problems:
        print(f"\nrejected rows ({len(problems)}):")
        for p in problems:
            print(f"  - {p}")

    if not dataset:
        print()
        print("-" * 74)
        print("VERDICT: INSUFFICIENT DATA")
        print("-" * 74)
        print("No calibration observations present. Nothing can be concluded, and")
        print("no tolerance may be described as field-validated.")
        print()
        print("Tolerances remain CALIBRATION_PENDING. Collect four faces per block")
        print("for ~20 real blocks, record them in the CSV, and re-run this tool.")
        return 1

    obs = observations(dataset)
    tape_rows = [d for d in dataset
                 if (d["raw"].get("tape_length") or "").strip()]

    # ---- C. Raw statistics --------------------------------------------------
    print()
    print("-" * 74)
    print("DISAGREEMENT STATISTICS  (metres, and % of the dimension)")
    print("-" * 74)
    for dimension in ("length", "breadth", "height"):
        entries = obs[dimension]
        absolute = stats([e["gap"] for e in entries])
        relative = stats([e["relative"] for e in entries])
        label = "spread" if dimension == "height" else "gap"
        print(f"\n{dimension.upper()}  ({label}, n={absolute['n']})")
        print(f"  absolute  min {fmt(absolute['min'])}  median {fmt(absolute['median'])}  "
              f"p90 {fmt(absolute['p90'])}  p95 {fmt(absolute['p95'])}  max {fmt(absolute['max'])}")
        print(f"  relative  min {fmt(relative['min'] * 100, 2)}%  "
              f"median {fmt(relative['median'] * 100, 2)}%  "
              f"p90 {fmt(relative['p90'] * 100, 2)}%  "
              f"p95 {fmt(relative['p95'] * 100, 2)}%  "
              f"max {fmt(relative['max'] * 100, 2)}%")
        worst = max(entries, key=lambda e: e["gap"])
        print(f"  worst block: {worst['block_id']}  {label} {fmt(worst['gap'])} m "
              f"on {fmt(worst['reference'])} m")

    # ---- G. Reference / quality observations --------------------------------
    print()
    print("-" * 74)
    print("REFERENCE AND QUALITY OBSERVATIONS")
    print("-" * 74)
    print(f"  blocks with an independent tape measurement: {len(tape_rows)}")
    if not tape_rows:
        print("  No tape references recorded, so AR ACCURACY cannot be assessed -")
        print("  only AR REPEATABILITY between faces. A consistent bias affecting")
        print("  all four faces equally would be invisible to this dataset.")
    noted = [d for d in dataset
             if (d["raw"].get("ar_range_note") or d["raw"].get("quality_note") or "").strip()]
    print(f"  blocks with range/quality notes: {len(noted)}")

    # ---- D/E. Candidate evaluation ------------------------------------------
    print()
    print("-" * 74)
    print("CANDIDATE TOLERANCES  -  acceptance, and what the worst accepted")
    print("disagreement would silently carry into a bill")
    print("-" * 74)
    for floor, fraction in CANDIDATES:
        marker = "  <- current placeholder" if (floor, fraction) == (
            Decimal("0.020"), Decimal("0.020")) else ""
        print(f"\ncandidate: max({fmt(floor, 3)} m, {fmt(fraction * 100, 1)}% of dimension){marker}")
        for dimension in ("length", "breadth", "height"):
            entries = obs[dimension]
            result = evaluate(dimension, entries, floor, fraction)
            n = len(entries)
            n_acc = len(result["accepted"])
            pct = (Decimal(n_acc) / Decimal(n)) * 100
            line = (f"  {dimension:8} accepted {n_acc}/{n} ({fmt(pct, 1)}%)  "
                    f"rejected {n - n_acc}")
            worst = result["worst_accepted"]
            if worst:
                impact = revenue_impact(worst["gap"], worst["reference"])
                line += (f"  | worst accepted {fmt(worst['gap'])} m"
                         f" -> up to {fmt(impact['volume_error_pct'], 2)}% volume error")
            print(line)

    # ---- H. Sufficiency verdict ---------------------------------------------
    print()
    print("-" * 74)
    sufficient = len(dataset) >= MIN_BLOCKS_FOR_CONCLUSION
    print(f"VERDICT: {'CALIBRATION COMPLETE' if sufficient else 'INSUFFICIENT DATA'}")
    print("-" * 74)
    if not sufficient:
        print(f"Only {len(dataset)} block(s); {MIN_BLOCKS_FOR_CONCLUSION} targeted.")
        print("Percentiles from this few samples are not a sound basis for a")
        print("production tolerance. Tolerances stay CALIBRATION_PENDING.")
        return 1
    if not tape_rows:
        print("Sample size met, but with no tape references this establishes")
        print("REPEATABILITY only, not ACCURACY. Record that limitation alongside")
        print("any tolerance chosen from it.")
    print("Choose a candidate on the evidence above, then set")
    print("calibration_status=FIELD_VALIDATED in blocks/face_reconciliation.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
