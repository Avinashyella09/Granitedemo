/**
 * Multi-face granite block measurement: face geometry + reconciliation.
 *
 * PURE MODULE. No React, no native imports, no I/O. Takes points in, returns a
 * deterministic result. Kept out of App.js so it can be reasoned about and unit
 * tested on its own (see multiFaceReconciliation.test.mjs).
 *
 * ============================================================================
 * CORRESPONDENCE TO backend/blocks/face_reconciliation.py
 * ============================================================================
 * That Python module is the SPECIFICATION. It is not modified by this file and
 * this file must not drift from it. The rules implemented here are, line for
 * line with the engine:
 *
 *   FACE_PLAN         0 Front (length), 1 Right Side (breadth),
 *                     2 Back (length),  3 Left Side (breadth)
 *   Length            observations from Front + Back
 *   Breadth           observations from Right Side + Left Side
 *   Height            observations from all four faces
 *   Length / Breadth  gap <= tolerance -> arithmetic MEAN of the two
 *                     gap  > tolerance -> INCONSISTENT, name both faces
 *   Height            MEDIAN of four; blocked when (max - min) > tolerance
 *   tolerance         max(absoluteFloorM, meanOfObservations * relativeFraction)
 *   Volume            EXISTS ONLY when all three dimensions reconcile
 *   status            'reconciled' | 'inconsistent' | 'incomplete'
 *
 * A rejected disagreement is never averaged and never yields a volume.
 *
 * ----------------------------------------------------------------------------
 * WHY MICROMETRE INTEGERS INSTEAD OF PLAIN FLOATS
 * ----------------------------------------------------------------------------
 * The engine uses Python Decimal so that "exactly at tolerance is ACCEPTED"
 * actually holds. JavaScript has no Decimal and the same trap exists here:
 *
 *     1.53 - 1.50 === 0.030000000000000027      // > 0.03, wrongly REJECTED
 *
 * So every comparison and every mean/median is done on integer MICROMETRES,
 * which makes the boundary exact and the result reproducible. 1 um is four
 * orders of magnitude below anything LiDAR can resolve, so nothing real is lost.
 * This is the JS equivalent of the engine's Decimal(str(value)).
 */

// --- Units -------------------------------------------------------------------
// Everything crossing a comparison goes through micrometres.
const UM_PER_M = 1e6;
const toUm = (metres) => Math.round(metres * UM_PER_M);
const toM = (um) => um / UM_PER_M;

// --- Face plan (fixed order, mirrors FACE_PLAN in the engine) ----------------
export const LENGTH = 'length';
export const BREADTH = 'breadth';
export const HEIGHT = 'height';

export const FACE_PLAN = [
  { index: 0, name: 'Front', dimensionType: LENGTH },
  { index: 1, name: 'Right Side', dimensionType: BREADTH },
  { index: 2, name: 'Back', dimensionType: LENGTH },
  { index: 3, name: 'Left Side', dimensionType: BREADTH },
];

export const REQUIRED_FACE_COUNT = FACE_PLAN.length;
export const POINTS_PER_FACE = 4;

// Derived from FACE_PLAN so the plan and the groupings can never disagree.
export const LENGTH_FACES = FACE_PLAN.filter((f) => f.dimensionType === LENGTH).map((f) => f.index);
export const BREADTH_FACES = FACE_PLAN.filter((f) => f.dimensionType === BREADTH).map((f) => f.index);
export const HEIGHT_FACES = FACE_PLAN.map((f) => f.index);

// --- Result vocabulary (mirrors the engine) ----------------------------------
export const RECONCILED = 'reconciled';
export const INCONSISTENT = 'inconsistent';
export const INCOMPLETE = 'incomplete';

export const RULE_MEAN_OF_2 = 'mean_of_2';
export const RULE_MEDIAN_OF_4 = 'median_of_4';

export const CALIBRATION_PENDING = 'CALIBRATION_PENDING';

/**
 * PROVISIONAL tolerances. NOT government-approved and NOT field-validated.
 *
 * These mirror the placeholders in the Python engine so the two behave
 * identically during development. backend/calibration/face_calibration_dataset.csv
 * currently holds ZERO rows, so no value here has any empirical basis yet.
 *
 * Replacing them later must not require touching the state machine: everything
 * downstream reads this object, and reconcileFaces() accepts an override.
 */
export const DEFAULT_TOLERANCES = {
  absoluteFloorM: 0.020,
  relativeFraction: 0.02,
  calibrationStatus: CALIBRATION_PENDING,
  note: 'PROVISIONAL - calibration pending. Not field-validated, not approved.',
};

export function toleranceForM(referenceM, tolerances = DEFAULT_TOLERANCES) {
  // Reference is the MEAN of the observations being compared, matching the
  // engine. Mean rather than min/max because it is order-independent.
  return Math.max(
    toUm(tolerances.absoluteFloorM),
    Math.round(Math.abs(toUm(referenceM)) * tolerances.relativeFraction),
  );
}

// --- Vector helpers ----------------------------------------------------------
const sub = (a, b) => ({ x: a.x - b.x, y: a.y - b.y, z: a.z - b.z });
const dot = (u, v) => u.x * v.x + u.y * v.y + u.z * v.z;
const mag = (v) => Math.sqrt(dot(v, v));
export const distance3d = (a, b) => mag(sub(b, a));

const isFinitePositive = (n) => Number.isFinite(n) && n > 0;

// --- Per-face plausibility ---------------------------------------------------
// Retained from the single-face flow; the bounds are unchanged.
export const FACE_PLAUSIBILITY = {
  [LENGTH]: { min: 0.1, max: 10.0, label: 'Length' },
  [BREADTH]: { min: 0.1, max: 5.0, label: 'Breadth' },
  [HEIGHT]: { min: 0.1, max: 5.0, label: 'Height' },
};

// Adjacent edges of a face should be roughly perpendicular. Same constant the
// single-face chain check used, applied per face instead of across the chain.
export const MAX_ADJACENT_EDGE_ALIGNMENT = 0.85;

/**
 * Evaluate one face from its four confirmed points.
 *
 *     P1 -------- P2      P1->P2 = the face's horizontal dimension
 *     |            |      P2->P3 = height
 *     |            |      P3->P4 = opposite of P1->P2
 *     P4 -------- P3      P4->P1 = opposite of P2->P3
 *
 * Returns the dimensions plus every warning found. Warnings describe; they do
 * not silently discard a reading.
 */
export function evaluateFace(spec, points, tolerances = DEFAULT_TOLERANCES) {
  const base = {
    index: spec.index,
    name: spec.name,
    dimensionType: spec.dimensionType,
    points: points ? [...points] : [],
    dimension: null,
    height: null,
    edges: null,
    warnings: [],
    status: INCOMPLETE,
  };

  if (!points || points.length !== POINTS_PER_FACE) {
    return {
      ...base,
      warnings: [`${spec.name} needs ${POINTS_PER_FACE} points; ${points ? points.length : 0} captured.`],
    };
  }

  const [p1, p2, p3, p4] = points;
  const edge12 = distance3d(p1, p2);   // the face's horizontal dimension
  const edge23 = distance3d(p2, p3);   // height
  const edge34 = distance3d(p3, p4);   // opposite of edge12
  const edge41 = distance3d(p4, p1);   // opposite of edge23

  const warnings = [];

  if (![edge12, edge23, edge34, edge41].every(isFinitePositive)) {
    return {
      ...base,
      edges: { edge12, edge23, edge34, edge41 },
      warnings: [`${spec.name}: one or more edges are zero or not a finite number.`],
    };
  }

  const dimensionLabel = FACE_PLAUSIBILITY[spec.dimensionType].label;
  const cm = (m) => `${(m * 100).toFixed(0)} cm`;

  // Plausibility of the two dimensions this face contributes.
  for (const [value, key] of [[edge12, spec.dimensionType], [edge23, HEIGHT]]) {
    const { min, max, label } = FACE_PLAUSIBILITY[key];
    if (value < min || value > max) {
      warnings.push(`${spec.name}: ${label} is ${cm(value)} (expected ${cm(min)} to ${max} m).`);
    }
  }

  // Opposite-edge consistency. A real quadrilateral face has matching opposite
  // edges; a large mismatch means a corner was misplaced.
  const horizontalMeanM = (edge12 + edge34) / 2;
  const verticalMeanM = (edge23 + edge41) / 2;
  const horizontalGapUm = Math.abs(toUm(edge12) - toUm(edge34));
  const verticalGapUm = Math.abs(toUm(edge23) - toUm(edge41));
  const horizontalTolUm = toleranceForM(horizontalMeanM, tolerances);
  const verticalTolUm = toleranceForM(verticalMeanM, tolerances);

  if (horizontalGapUm > horizontalTolUm) {
    warnings.push(
      `${spec.name}: opposite ${dimensionLabel.toLowerCase()} edges differ by ` +
      `${cm(toM(horizontalGapUm))} (${cm(edge12)} vs ${cm(edge34)}). Check corners P1/P2/P3/P4.`,
    );
  }
  if (verticalGapUm > verticalTolUm) {
    warnings.push(
      `${spec.name}: opposite height edges differ by ${cm(toM(verticalGapUm))} ` +
      `(${cm(edge23)} vs ${cm(edge41)}). Check corners P1/P2/P3/P4.`,
    );
  }

  // Adjacent edges must be genuinely different directions, or the "face" is a
  // line and its two dimensions are the same edge measured twice.
  const alignment = Math.abs(dot(sub(p2, p1), sub(p3, p2)) / (edge12 * edge23));
  if (alignment > MAX_ADJACENT_EDGE_ALIGNMENT) {
    const degrees = (Math.acos(Math.min(alignment, 1)) * 180) / Math.PI;
    warnings.push(
      `${spec.name}: the ${dimensionLabel.toLowerCase()} and height edges are only ` +
      `${degrees.toFixed(0)} degrees apart, so they are not two independent dimensions.`,
    );
  }

  return {
    ...base,
    dimension: edge12,
    height: edge23,
    edges: {
      edge12, edge23, edge34, edge41,
      oppositeHorizontalGapM: toM(horizontalGapUm),
      oppositeVerticalGapM: toM(verticalGapUm),
      adjacentAlignment: alignment,
    },
    warnings,
    // A face is CAPTURED once four points exist. Warnings are recorded against
    // it, never used to throw the reading away - that was the failure mode the
    // single-face flow was explicitly fixed for.
    status: 'captured',
    lowConfidence: warnings.length > 0,
  };
}

// --- Statistics on micrometre integers ---------------------------------------
const meanUm = (values) => values.reduce((a, b) => a + b, 0) / values.length;

const medianUm = (values) => {
  const ordered = [...values].sort((a, b) => a - b);
  const mid = Math.floor(ordered.length / 2);
  return ordered.length % 2 === 1
    ? ordered[mid]
    : (ordered[mid - 1] + ordered[mid]) / 2;
};

function reconcilePair(dimension, faces, tolerances) {
  const valuesUm = faces.map((f) => toUm(f.dimension));
  const gapUm = Math.abs(valuesUm[0] - valuesUm[1]);
  const toleranceUm = toleranceForM(toM(meanUm(valuesUm)), tolerances);

  const detail = {
    dimension,
    rule: RULE_MEAN_OF_2,
    observations: faces.map((f) => ({
      faceIndex: f.index, faceName: f.name, valueM: f.dimension,
    })),
    gapM: toM(gapUm),
    toleranceM: toM(toleranceUm),
  };

  // <= so exactly at tolerance is ACCEPTED, exact because of integer um.
  if (gapUm <= toleranceUm) {
    return { ...detail, status: RECONCILED, valueM: toM(meanUm(valuesUm)), reason: null };
  }
  return {
    ...detail,
    status: INCONSISTENT,
    valueM: null,
    reason:
      `${dimension[0].toUpperCase()}${dimension.slice(1)} disagrees between ` +
      `${faces[0].name} (${faces[0].dimension.toFixed(3)} m) and ` +
      `${faces[1].name} (${faces[1].dimension.toFixed(3)} m) by ` +
      `${toM(gapUm).toFixed(3)} m, beyond the ${toM(toleranceUm).toFixed(3)} m tolerance. ` +
      `Re-measure one of those two faces.`,
  };
}

function reconcileHeight(faces, tolerances) {
  const valuesUm = faces.map((f) => toUm(f.height));
  const medUm = medianUm(valuesUm);
  const spreadUm = Math.max(...valuesUm) - Math.min(...valuesUm);
  const toleranceUm = toleranceForM(toM(medUm), tolerances);

  const detail = {
    dimension: HEIGHT,
    rule: RULE_MEDIAN_OF_4,
    observations: faces.map((f) => ({
      faceIndex: f.index,
      faceName: f.name,
      valueM: f.height,
      deviationFromMedianM: toM(toUm(f.height) - medUm),
    })),
    spreadM: toM(spreadUm),
    toleranceM: toM(toleranceUm),
    medianM: toM(medUm),
  };

  if (spreadUm <= toleranceUm) {
    return { ...detail, status: RECONCILED, valueM: toM(medUm), outliers: [], reason: null };
  }

  const worst = Math.max(...faces.map((f) => Math.abs(toUm(f.height) - medUm)));
  const outliers = faces
    .filter((f) => Math.abs(toUm(f.height) - medUm) === worst)
    .map((f) => ({ faceIndex: f.index, faceName: f.name, valueM: f.height }));

  return {
    ...detail,
    status: INCONSISTENT,
    valueM: null,
    outliers,
    reason:
      `Height varies by ${toM(spreadUm).toFixed(3)} m across the four faces, beyond the ` +
      `${toM(toleranceUm).toFixed(3)} m tolerance. Furthest from the median ` +
      `(${toM(medUm).toFixed(3)} m): ${outliers.map((o) => o.faceName).join(', ')}. ` +
      `Re-measure that face.`,
  };
}

/**
 * Reconcile the captured faces into final L/B/H and a volume.
 *
 * `faces` is a sparse array indexed by face index; a slot is null until that
 * face has been confirmed. volumeM3 is null unless every dimension reconciles.
 */
export function reconcileFaces(faces, tolerances = DEFAULT_TOLERANCES) {
  const captured = FACE_PLAN
    .map((spec) => faces[spec.index])
    .filter((f) => f && f.status === 'captured' && isFinitePositive(f.dimension) && isFinitePositive(f.height));

  const missing = FACE_PLAN.filter(
    (spec) => !captured.some((f) => f.index === spec.index),
  );

  const tolerancesUsed = { ...tolerances };

  if (missing.length > 0) {
    return {
      status: INCOMPLETE,
      finalLengthM: null,
      finalBreadthM: null,
      finalHeightM: null,
      volumeM3: null,
      length: null,
      breadth: null,
      height: null,
      facesCaptured: captured.map((f) => f.index),
      facesMissing: missing.map((s) => ({ index: s.index, name: s.name })),
      tolerancesUsed,
      rulesApplied: { length: RULE_MEAN_OF_2, breadth: RULE_MEAN_OF_2, height: RULE_MEDIAN_OF_4 },
      blockingReasons: [
        `Measurement incomplete: ${missing.map((s) => s.name).join(', ')} not captured. ` +
        `All four faces are required before a volume exists.`,
      ],
      warnings: captured.flatMap((f) => f.warnings || []),
    };
  }

  const byIndex = Object.fromEntries(captured.map((f) => [f.index, f]));
  const length = reconcilePair(LENGTH, LENGTH_FACES.map((i) => byIndex[i]), tolerances);
  const breadth = reconcilePair(BREADTH, BREADTH_FACES.map((i) => byIndex[i]), tolerances);
  const height = reconcileHeight(HEIGHT_FACES.map((i) => byIndex[i]), tolerances);

  const blockingReasons = [length, breadth, height]
    .filter((d) => d.status === INCONSISTENT)
    .map((d) => d.reason);

  const allReconciled = blockingReasons.length === 0;

  return {
    status: allReconciled ? RECONCILED : INCONSISTENT,
    finalLengthM: length.valueM,
    finalBreadthM: breadth.valueM,
    finalHeightM: height.valueM,
    // Volume exists ONLY when every dimension reconciled. The single most
    // important line in this module.
    volumeM3: allReconciled ? length.valueM * breadth.valueM * height.valueM : null,
    length,
    breadth,
    height,
    facesCaptured: captured.map((f) => f.index),
    facesMissing: [],
    tolerancesUsed,
    rulesApplied: { length: RULE_MEAN_OF_2, breadth: RULE_MEAN_OF_2, height: RULE_MEDIAN_OF_4 },
    blockingReasons,
    warnings: captured.flatMap((f) => f.warnings || []),
  };
}

/**
 * Predict whether native will REFUSE the 4th point, and say why.
 *
 * This is an exact mirror of MeasurementGeometry.validateP4 in the frozen
 * GraniteARMeasurement.swift. Native returns only a bool and, on refusal, a
 * generic "That point doesn't form a plausible block corner" - which on a face
 * the officer believes is correct reads as the app being broken. Recomputing
 * the same three conditions here lets the UI name the one that is failing
 * BEFORE the tap, instead of leaving the officer to guess.
 *
 * The constants below are copied from the Swift and must not drift from it.
 * Returns null when the point would be accepted.
 */
export const P4_MIN_EDGE_M = 0.05;      // lenB < 0.05        -> refuse
export const P4_MAX_EDGE_RATIO = 3.0;   // lenB > 3 * maxFront -> refuse
export const P4_MAX_ALIGNMENT = 0.80;   // |cos| > 0.80        -> refuse

export function predictP4Refusal(points, candidate) {
  if (!points || points.length !== 3 || !candidate) return null;
  if (![candidate.x, candidate.y, candidate.z].every(Number.isFinite)) return null;

  const [p1, p2, p3] = points;
  const cm = (m) => `${(m * 100).toFixed(0)} cm`;

  const lenB = distance3d(p3, candidate);
  if (lenB < P4_MIN_EDGE_M) {
    return {
      code: 'too_close',
      message:
        `P4 is only ${cm(lenB)} from P3 — the AR engine needs at least ` +
        `${cm(P4_MIN_EDGE_M)}. If this face really is that narrow, clear it and ` +
        `place the LONG horizontal edge as P1->P2 instead.`,
    };
  }

  const maxFront = Math.max(distance3d(p1, p2), distance3d(p2, p3));
  if (maxFront > 0.01 && lenB > P4_MAX_EDGE_RATIO * maxFront) {
    return {
      code: 'too_far',
      message:
        `P4 is ${cm(lenB)} from P3 — more than 3x this face's other edges ` +
        `(max ${cm(maxFront)}). You may be aiming past the block.`,
    };
  }

  const edgeH = sub(p3, p2);
  const edgeB = sub(candidate, p3);
  const lenEdgeH = mag(edgeH);
  const lenEdgeB = mag(edgeB);
  if (lenEdgeH <= 0.0001 || lenEdgeB <= 0.0001) {
    return { code: 'degenerate', message: 'P4 has no measurable offset from P3.' };
  }

  const alignment = Math.abs(dot(edgeH, edgeB) / (lenEdgeH * lenEdgeB));
  if (alignment > P4_MAX_ALIGNMENT) {
    const degrees = (Math.acos(Math.min(alignment, 1)) * 180) / Math.PI;
    return {
      code: 'not_perpendicular',
      message:
        `P4 runs back along the height edge (only ${degrees.toFixed(0)} degrees apart). ` +
        `P4 is the corner ACROSS from P3, level with P1 — not back at P3 or P2.`,
    };
  }

  return null;
}

/**
 * Detect, as soon as P2 lands, a face that can NEVER be closed.
 *
 * Native refuses the 4th point when the closing edge P3->P4 is under 5 cm
 * (validateP4: `if lenB < 0.05 { return false }`). On a rectangle the closing
 * edge mirrors P1->P2, so a short P1->P2 dooms the face before P3 is even
 * placed - and the officer only finds out two taps later, when P4 silently
 * refuses with a message about "a plausible block corner".
 *
 * Observed on a real capture: a 2.2 cm-thick object measured with the SHORT
 * edge first gave P1->P2 = 2.75 cm, so the closing edge would also have been
 * 2.75 cm. The same object measured long-edge-first closed fine at ~16 cm.
 *
 * Returns null when the face can be closed.
 */
export function predictFaceClosureProblem(points) {
  if (!points || points.length < 2) return null;
  const opening = distance3d(points[0], points[1]);
  if (opening >= P4_MIN_EDGE_M) return null;
  return {
    code: 'closing_edge_too_short',
    openingEdgeM: opening,
    message:
      `This face cannot be closed: P1->P2 is only ${(opening * 100).toFixed(1)} cm, so the ` +
      `closing edge P4 will be too short for the AR engine (minimum ` +
      `${(P4_MIN_EDGE_M * 100).toFixed(0)} cm). Clear this face and place the LONG ` +
      `horizontal edge as P1->P2, with height as P2->P3.`,
  };
}

export const emptyFaces = () => FACE_PLAN.map(() => null);
