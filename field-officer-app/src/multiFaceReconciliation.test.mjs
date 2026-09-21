/**
 * Multi-face reconciliation (JS port) — pure unit tests.
 *
 *     node --test src/multiFaceReconciliation.test.mjs
 *
 * These exist because this module duplicates revenue-critical arithmetic that
 * already lives in backend/blocks/face_reconciliation.py. The Python engine is
 * the specification; these tests pin the JS port to the same behaviour so the
 * two cannot drift apart unnoticed.
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  FACE_PLAN, POINTS_PER_FACE, RECONCILED, INCONSISTENT, INCOMPLETE,
  RULE_MEAN_OF_2, RULE_MEDIAN_OF_4, CALIBRATION_PENDING, DEFAULT_TOLERANCES,
  evaluateFace, reconcileFaces, emptyFaces, distance3d,
} from './multiFaceReconciliation.js';

// Absolute-only tolerance so boundary arithmetic is obvious, matching the
// EXACT fixture used in blocks/tests_face_reconciliation.py.
const EXACT = { absoluteFloorM: 0.030, relativeFraction: 0, calibrationStatus: CALIBRATION_PENDING };

/** A clean planar face rectangle: P1 top-left -> P2 top-right -> P3 bottom-right -> P4 bottom-left. */
function rect(width, height, { z = 0, origin = { x: 0, y: 0 } } = {}) {
  const { x, y } = origin;
  return [
    { x, y: y + height, z },
    { x: x + width, y: y + height, z },
    { x: x + width, y, z },
    { x, y, z },
  ];
}

function faceFrom(index, width, height, opts) {
  return evaluateFace(FACE_PLAN[index], rect(width, height, opts), EXACT);
}

function fourFaces({ l1 = 1.5, b2 = 0.9, l3 = 1.5, b4 = 0.9, h = [0.85, 0.85, 0.85, 0.85] } = {}) {
  return [
    faceFrom(0, l1, h[0]),
    faceFrom(1, b2, h[1]),
    faceFrom(2, l3, h[2]),
    faceFrom(3, b4, h[3]),
  ];
}

// ---- face plan ---------------------------------------------------------------

test('face plan is the fixed approved order, 4 points per face', () => {
  assert.deepEqual(
    FACE_PLAN.map((f) => [f.index, f.name, f.dimensionType]),
    [[0, 'Front', 'length'], [1, 'Right Side', 'breadth'],
     [2, 'Back', 'length'], [3, 'Left Side', 'breadth']],
  );
  assert.equal(POINTS_PER_FACE, 4);
});

// ---- per-face geometry -------------------------------------------------------

test('a clean face yields its horizontal dimension and height with no warnings', () => {
  const face = faceFrom(0, 1.5, 0.85);
  assert.equal(face.status, 'captured');
  assert.ok(Math.abs(face.dimension - 1.5) < 1e-9);
  assert.ok(Math.abs(face.height - 0.85) < 1e-9);
  assert.deepEqual(face.warnings, []);
  assert.equal(face.lowConfidence, false);
});

test('a face is never complete with fewer than four points', () => {
  for (const n of [0, 1, 2, 3]) {
    const face = evaluateFace(FACE_PLAN[0], rect(1.5, 0.85).slice(0, n), EXACT);
    assert.equal(face.status, INCOMPLETE, `${n} points must not complete a face`);
    assert.equal(face.dimension, null);
    assert.match(face.warnings[0], /needs 4 points/);
  }
});

test('all four edges are measured, not just two', () => {
  const face = faceFrom(0, 1.5, 0.85);
  const { edge12, edge23, edge34, edge41 } = face.edges;
  assert.ok(Math.abs(edge12 - 1.5) < 1e-9);
  assert.ok(Math.abs(edge23 - 0.85) < 1e-9);
  assert.ok(Math.abs(edge34 - 1.5) < 1e-9);   // opposite of edge12
  assert.ok(Math.abs(edge41 - 0.85) < 1e-9);  // opposite of edge23
});

test('mismatched opposite edges are warned about', () => {
  // P3 pulled sideways so edge34 no longer matches edge12.
  const points = rect(1.5, 0.85);
  points[2] = { ...points[2], x: points[2].x + 0.20 };
  const face = evaluateFace(FACE_PLAN[0], points, EXACT);
  assert.equal(face.status, 'captured');
  assert.ok(face.warnings.some((w) => /opposite/.test(w)), face.warnings);
  assert.equal(face.lowConfidence, true);
});

test('a degenerate face whose two edges are the same direction is warned about', () => {
  // All four points on one line: dimension and height are the same edge.
  const points = [
    { x: 0, y: 0, z: 0 }, { x: 1.5, y: 0, z: 0 },
    { x: 3.0, y: 0, z: 0 }, { x: 1.5, y: 0, z: 0 },
  ];
  const face = evaluateFace(FACE_PLAN[0], points, EXACT);
  assert.ok(face.warnings.some((w) => /not two independent dimensions/.test(w)), face.warnings);
});

test('implausible dimensions are warned about but the reading is kept', () => {
  const face = faceFrom(0, 1.5, 0.04);   // 4 cm height, below the 10 cm floor
  assert.equal(face.status, 'captured');
  assert.ok(face.warnings.some((w) => /Height is 4 cm/.test(w)), face.warnings);
  assert.ok(face.height > 0, 'the reading must not be discarded');
});

// ---- volume gating -----------------------------------------------------------

test('no volume exists until all four faces are confirmed', () => {
  const faces = emptyFaces();
  const all = fourFaces();
  for (let n = 0; n < 4; n += 1) {
    const result = reconcileFaces(faces, EXACT);
    assert.equal(result.status, INCOMPLETE, `${n} faces must not reconcile`);
    assert.equal(result.volumeM3, null);
    assert.equal(result.finalLengthM, null);
    faces[n] = all[n];
  }
  const done = reconcileFaces(faces, EXACT);
  assert.equal(done.status, RECONCILED);
  assert.ok(done.volumeM3 > 0);
});

test('missing faces are named', () => {
  const faces = emptyFaces();
  faces[0] = faceFrom(0, 1.5, 0.85);
  const result = reconcileFaces(faces, EXACT);
  assert.deepEqual(result.facesMissing.map((f) => f.name), ['Right Side', 'Back', 'Left Side']);
  assert.match(result.blockingReasons[0], /All four faces are required/);
});

test('volume is the product of the reconciled dimensions', () => {
  const result = reconcileFaces(fourFaces(), EXACT);
  assert.ok(Math.abs(result.volumeM3 - 1.5 * 0.9 * 0.85) < 1e-9);
});

// ---- length / breadth reconciliation ----------------------------------------

test('length within tolerance uses the mean of Front and Back', () => {
  const result = reconcileFaces(fourFaces({ l1: 1.50, l3: 1.52 }), EXACT);
  assert.equal(result.status, RECONCILED);
  assert.ok(Math.abs(result.finalLengthM - 1.51) < 1e-9);
  assert.equal(result.length.rule, RULE_MEAN_OF_2);
});

test('EXACTLY at tolerance is accepted — the reason this port uses integer micrometres', () => {
  // In plain floats 1.53 - 1.50 === 0.030000000000000027, which would reject.
  assert.notEqual(1.53 - 1.50, 0.03);
  assert.ok(!((1.53 - 1.50) <= 0.03));

  const result = reconcileFaces(fourFaces({ l1: 1.50, l3: 1.53 }), EXACT);
  assert.equal(result.status, RECONCILED);
  assert.ok(Math.abs(result.finalLengthM - 1.515) < 1e-9);
  assert.equal(result.length.gapM, result.length.toleranceM);
});

test('just outside tolerance is rejected and never averaged', () => {
  const result = reconcileFaces(fourFaces({ l1: 1.50, l3: 1.90 }), EXACT);
  assert.equal(result.status, INCONSISTENT);
  assert.equal(result.finalLengthM, null);
  assert.equal(result.volumeM3, null);
  assert.match(result.blockingReasons.join(' '), /Front/);
  assert.match(result.blockingReasons.join(' '), /Back/);
});

test('breadth is reconciled from Right Side and Left Side', () => {
  const ok = reconcileFaces(fourFaces({ b2: 0.90, b4: 0.92 }), EXACT);
  assert.ok(Math.abs(ok.finalBreadthM - 0.91) < 1e-9);

  const bad = reconcileFaces(fourFaces({ b2: 0.90, b4: 1.40 }), EXACT);
  assert.equal(bad.status, INCONSISTENT);
  assert.equal(bad.finalBreadthM, null);
  assert.equal(bad.volumeM3, null);
});

// ---- height reconciliation ---------------------------------------------------

test('height uses the median of four, not the mean', () => {
  // 0.85 / 0.85 / 0.86 / 0.88  ->  median 0.855, mean 0.86
  const result = reconcileFaces(fourFaces({ h: [0.85, 0.85, 0.86, 0.88] }), EXACT);
  assert.equal(result.status, RECONCILED);
  assert.ok(Math.abs(result.finalHeightM - 0.855) < 1e-9);
  assert.ok(Math.abs(result.finalHeightM - 0.86) > 1e-6, 'must not be the mean');
  assert.equal(result.height.rule, RULE_MEDIAN_OF_4);
});

test('an excessive height spread blocks and names the outlying face', () => {
  const result = reconcileFaces(fourFaces({ h: [0.85, 0.85, 0.85, 1.10] }), EXACT);
  assert.equal(result.status, INCONSISTENT);
  assert.equal(result.finalHeightM, null);
  assert.equal(result.volumeM3, null);
  assert.deepEqual(result.height.outliers.map((o) => o.faceName), ['Left Side']);
});

test('height spread exactly at tolerance is accepted', () => {
  const result = reconcileFaces(fourFaces({ h: [0.85, 0.85, 0.86, 0.88] }), EXACT);
  assert.equal(result.height.spreadM, result.height.toleranceM);
  assert.equal(result.status, RECONCILED);
});

// ---- one failing dimension blocks everything --------------------------------

test('one failing dimension blocks the volume even when the others reconcile', () => {
  const result = reconcileFaces(fourFaces({ b2: 0.90, b4: 1.50 }), EXACT);
  assert.ok(Math.abs(result.finalLengthM - 1.5) < 1e-9);   // length still fine
  assert.equal(result.finalBreadthM, null);
  assert.equal(result.volumeM3, null);
});

// ---- contract / determinism --------------------------------------------------

test('tolerances are carried and labelled calibration-pending', () => {
  const result = reconcileFaces(fourFaces());
  assert.equal(result.tolerancesUsed.calibrationStatus, CALIBRATION_PENDING);
  assert.match(result.tolerancesUsed.note, /PROVISIONAL/);
  assert.match(result.tolerancesUsed.note, /not approved/i);
  assert.equal(DEFAULT_TOLERANCES.calibrationStatus, CALIBRATION_PENDING);
});

test('repeated reconciliation is identical', () => {
  const faces = fourFaces({ l1: 1.503, l3: 1.517, b2: 0.901, b4: 0.899, h: [0.851, 0.853, 0.852, 0.850] });
  const first = JSON.stringify(reconcileFaces(faces, EXACT));
  for (let i = 0; i < 10; i += 1) {
    assert.equal(JSON.stringify(reconcileFaces(faces, EXACT)), first);
  }
});

test('the same keys are returned in every status', () => {
  const keys = (r) => Object.keys(r).sort().join(',');
  const done = reconcileFaces(fourFaces(), EXACT);
  const bad = reconcileFaces(fourFaces({ l1: 1.5, l3: 2.5 }), EXACT);
  const partial = reconcileFaces([fourFaces()[0], null, null, null], EXACT);
  assert.equal(keys(done), keys(bad));
  assert.equal(keys(done), keys(partial));
});

test('rulesApplied names the actual rules', () => {
  const result = reconcileFaces(fourFaces(), EXACT);
  assert.deepEqual(result.rulesApplied, {
    length: RULE_MEAN_OF_2, breadth: RULE_MEAN_OF_2, height: RULE_MEDIAN_OF_4,
  });
});

test('distance3d is a plain euclidean distance', () => {
  assert.ok(Math.abs(distance3d({ x: 0, y: 0, z: 0 }, { x: 3, y: 4, z: 0 }) - 5) < 1e-12);
});

// --- predictP4Refusal: exact mirror of native validateP4 ---------------------

test('a proper 4th corner is accepted', async () => {
  const { predictP4Refusal } = await import('./multiFaceReconciliation.js');
  const pts = [{ x: 0, y: 0.85, z: 0 }, { x: 1.5, y: 0.85, z: 0 }, { x: 1.5, y: 0, z: 0 }];
  assert.equal(predictP4Refusal(pts, { x: 0, y: 0, z: 0 }), null);
});

test('P4 too close to P3 is caught with the native 5 cm rule', async () => {
  const { predictP4Refusal } = await import('./multiFaceReconciliation.js');
  const pts = [{ x: 0, y: 0.85, z: 0 }, { x: 1.5, y: 0.85, z: 0 }, { x: 1.5, y: 0, z: 0 }];
  const r = predictP4Refusal(pts, { x: 1.52, y: 0, z: 0 });
  assert.equal(r.code, 'too_close');
});

test('P4 beyond 3x the other edges is caught', async () => {
  const { predictP4Refusal } = await import('./multiFaceReconciliation.js');
  const pts = [{ x: 0, y: 0.3, z: 0 }, { x: 0.3, y: 0.3, z: 0 }, { x: 0.3, y: 0, z: 0 }];
  const r = predictP4Refusal(pts, { x: -5.0, y: 0, z: 0 });
  assert.equal(r.code, 'too_far');
});

test('P4 running back along the height edge is caught and explained', async () => {
  const { predictP4Refusal } = await import('./multiFaceReconciliation.js');
  // Tall narrow face; closing straight back up toward P2 is anti-parallel.
  const pts = [{ x: 0, y: 1.2, z: 0 }, { x: 0.25, y: 1.2, z: 0 }, { x: 0.25, y: 0, z: 0 }];
  const r = predictP4Refusal(pts, { x: 0.25, y: 1.15, z: 0 });
  assert.equal(r.code, 'not_perpendicular');
  assert.match(r.message, /ACROSS from P3/);
});

test('the predictor matches native on the REAL face-1 capture that worked', async () => {
  const { predictP4Refusal } = await import('./multiFaceReconciliation.js');
  // Coordinates taken verbatim from the device log of a face native ACCEPTED.
  const pts = [
    { x: -0.08622382581233978, y: -0.1127687394618988, z: -0.18163926899433136 },
    { x: 0.26710957288742065, y: -0.10886801779270172, z: -0.14433006942272186 },
    { x: 0.27238497138023376, y: 0.07758673280477524, z: -0.2429085373878479 },
  ];
  const p4 = { x: -0.06614238768815994, y: 0.08180217444896698, z: -0.27278679609298706 };
  assert.equal(predictP4Refusal(pts, p4), null, 'must not flag a point native accepted');
});

test('the predictor is inert until exactly three points exist', async () => {
  const { predictP4Refusal } = await import('./multiFaceReconciliation.js');
  const p = { x: 0, y: 0, z: 0 };
  assert.equal(predictP4Refusal([], p), null);
  assert.equal(predictP4Refusal([p, p], p), null);
  assert.equal(predictP4Refusal([p, p, p, p], p), null);
  assert.equal(predictP4Refusal([p, p, p], null), null);
});

// --- closure prediction, pinned to REAL device captures ---------------------

test('REAL capture: the stuck Face 3 is caught at P2, before P3 is wasted', async () => {
  const { predictFaceClosureProblem } = await import('./multiFaceReconciliation.js');
  // Verbatim from the device log of the face whose 4th point would not place.
  const p1 = { x: 0.01947406, y: -0.0881444, z: -0.18065354 };
  const p2 = { x: 0.017746732, y: -0.06070344, z: -0.1808005 };
  const r = predictFaceClosureProblem([p1, p2]);
  assert.ok(r, 'the doomed face must be flagged');
  assert.equal(r.code, 'closing_edge_too_short');
  assert.ok(r.openingEdgeM < 0.05, `opening edge was ${r.openingEdgeM}`);
  assert.match(r.message, /LONG horizontal edge/);
});

test('REAL captures: the two faces that worked are NOT flagged', async () => {
  const { predictFaceClosureProblem } = await import('./multiFaceReconciliation.js');
  const faces = [
    [{ x: 0.055851165, y: -0.08294884, z: -0.22938511 },
     { x: 0.2259933, y: -0.082387775, z: -0.2344245 }],
    [{ x: 0.01865691, y: -0.08513793, z: -0.24013115 },
     { x: 0.18100828, y: -0.08130154, z: -0.25974682 }],
  ];
  for (const [a, b] of faces) {
    assert.equal(predictFaceClosureProblem([a, b]), null,
      'a face native accepted must never be flagged');
  }
});

test('closure check is inert before P2 exists', async () => {
  const { predictFaceClosureProblem } = await import('./multiFaceReconciliation.js');
  assert.equal(predictFaceClosureProblem([]), null);
  assert.equal(predictFaceClosureProblem([{ x: 0, y: 0, z: 0 }]), null);
  assert.equal(predictFaceClosureProblem(null), null);
});

test('a face exactly at the 5 cm floor is allowed through', async () => {
  const { predictFaceClosureProblem } = await import('./multiFaceReconciliation.js');
  const r = predictFaceClosureProblem([{ x: 0, y: 0, z: 0 }, { x: 0.05, y: 0, z: 0 }]);
  assert.equal(r, null, 'exactly 5 cm must not be flagged — native uses < 0.05');
});
