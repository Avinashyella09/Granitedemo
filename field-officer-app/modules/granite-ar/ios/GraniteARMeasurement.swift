import simd

// Immutable confirmed point. Once created, its numeric coordinates are never
// rewritten - MeasurementState only ever appends new LockedPoints or clears
// the whole array on reset, never mutates one in place.
struct LockedPoint {
  let number: Int
  let x: Float
  let y: Float
  let z: Float

  var worldPosition: SIMD3<Float> { SIMD3<Float>(x, y, z) }
}

// What a confirmed marker is attached to. There is now exactly one answer, and
// the reason is measured rather than reasoned.
//
// The previous design ranked hosts - mesh anchor, then the raycast's own
// anchor, then a detected plane, with a plain world anchor as a last resort -
// on the theory that a marker parented to ARKit-tracked geometry would be
// carried along as ARKit refined that surface. Two runs on an iPhone 16 Pro
// Max (2026-09-18, 636 host samples) refuted it:
//
//   ARMeshAnchor host .... AnchorEntity(anchor:) NEVER resolves. isAnchored was
//       false in 364 of 364 samples, zero exceptions, across both runs. Worse,
//       every position-refresh and rescue loop in the renderer was guarded by
//       isAnchored, so those markers were invisible to this module's own
//       diagnostics: their cached position stayed frozen at the confirmation
//       coordinate and drift always read 0.0mm. That is how an earlier session
//       concluded "no drift" while the points visibly would not stay put.
//   ARPlaneAnchor host ... resolves (80/80), but the plane's own transform
//       accumulated 119-133mm of travel in a single run, with single updates up
//       to 13.8mm, dragging its markers up to 20.4mm off where they were placed.
//   own session ARAnchor . the most stable thing measured - 0.5-2.1mm total,
//       same session, same points, same camera motion. It was run as an inert
//       probe alongside the real markers so the comparison would be controlled
//       rather than theoretical.
//
// Hence one session-registered ARAnchor per confirmed point, and nothing else.
// This enum survives so the diagnostics contract App.js reads (p#Host) keeps
// its shape.
//
// The measured coordinates are unaffected by any of this: LockedPoint is
// written once at confirmation and never read back from an anchor.
enum MeasurementHostKind: String {
  case sessionAnchor = "sessionAnchor"
}

// Explicit candidate lifecycle, surfaced to JS diagnostics and used to gate
// confirmation - "no valid candidate" must always be preferred over "a wrong
// candidate that happens to look confirmable." SEARCHING (no raycast hit at
// all) is represented by GraniteARView holding latestCandidate == nil, not a
// case here, since there is no world position to attach it to.
enum CandidateState: String {
  case candidateFound = "CANDIDATE_FOUND"  // hit acquired, too few samples yet to judge
  case stabilizing = "STABILIZING"         // enough samples, not yet meeting the bar
  case ready = "READY"                     // meets every physical-support check below
}

// A raycast/depth-derived measurement candidate for the current frame. The
// displayed position (worldPosition) is always the latest raw hit - never
// smoothed. `state`/`isConfirmable` are the only things temporal/depth/mesh
// checks gate.
//
// isStable/isStableStrict come from two parallel trackers fed the same raw
// samples every frame (see GraniteARView.updateCandidate). This exists
// because a raycast landing on a real, currently-detected plane
// (existingPlaneGeometry) with LiDAR depth backing it up is trustworthy
// evidence - but a corner is precisely the one place plane-fitting breaks
// down (no single flat plane actually passes through a true corner), so an
// estimatedPlane hit with no depth support (the overwhelmingly common case
// right at a corner) can be *self-consistent* - the same wrong,
// plane-extrapolated position frame after frame - without being *correct*.
// Gating that path on the same quick/loose window that's fine for a flat,
// depth-verified wall let obviously-wrong corner hits lock in as confidently
// as good ones, which is what actually produced "points floating away" (the
// point was never drifting after being placed - it was placed slightly
// wrong to begin with, and normal camera-motion parallax on a wrong-but-
// fixed point looks exactly like drift). The strict path asks for more,
// longer-held evidence, plus (on LiDAR devices) corroborating scene-mesh
// proximity, before trusting an unverified corner hit.
struct ARCandidate {
  let worldPosition: SIMD3<Float>
  let source: String
  let hasDepthSupport: Bool
  let depthAgrees: Bool
  let depthConfidence: Float
  let depthVariance: Float?
  let raycastDepthDelta: Float?
  // Diagnostic-only alternate position from unprojecting the measured depth
  // directly (see GraniteARView.unprojectDepth) - not used in `state`/
  // `isConfirmable` yet. Surfaced so real-device DIAG data can show whether
  // it converges with `worldPosition` before it's ever trusted to drive
  // confirmation.
  let depthUnprojectedWorldPosition: SIMD3<Float>?
  let meshSupport: Bool
  let requiresMeshSupport: Bool
  let isStable: Bool
  let isStableStrict: Bool
  let stableSampleCount: Int
  // Distance between this candidate and the position LiDAR's own depth reading
  // says the aimed surface is at, in metres; nil when no depth reading was
  // usable. Measured on device: ~0.02m on flat faces (P1/P2) but 0.13-0.20m at
  // top/back corners (P3/P4), which is why a large disagreement now blocks
  // confirmation instead of being recorded and ignored.
  let depthDisagreement: Float?
  // False only when a depth reading exists AND disagrees beyond the validation
  // threshold. The candidate position is NEVER replaced by the depth-derived
  // one - this only decides whether the point may be confirmed at all.
  let depthValidationPassed: Bool
  // True when the depth patch that produced this candidate straddled a depth
  // discontinuity - i.e. the reticle is on a real edge or corner, and the
  // position came from the NEAR cluster rather than a single clean surface.
  //
  // This exists to stop the depth-support fix from quietly loosening two
  // gates. Placement takes a 3x3 raw-depth patch; the support/confidence check
  // used to take a 5x5 smoothed patch capped at 30mm spread, which at a corner
  // straddles surfaces, returns nothing, and reported depthSupport=false with
  // confidence 0.00 on a point that WAS placed from depth (measured on device:
  // P3, spread 151mm, whole-patch median 679mm vs near-cluster 540mm). Reusing
  // the placement sample fixes that false reporting - but it also flips
  // hasDepthSupport true at corners, which would switch stability from the
  // strict tracker to the quick one and drop the mesh-support requirement,
  // making corner confirmation EASIER than before. Neither loosening is
  // justified by any measurement, so the corner case keeps its strict gates,
  // keyed off the boundary signal the placement path already computes.
  let placementPatchWasBoundary: Bool
  // False only when this candidate is anchored to a real tracked plane that
  // ARKit detected very recently. A newly-created ARPlaneAnchor's pose is
  // often rough and gets a significant one-time correction within about a
  // second of continued tracking - real device logs showed a confirmed
  // point's rendered position jump 70mm shortly after being placed on a
  // plane that had just been detected. The existing stability trackers only
  // check that repeated RAYCAST SAMPLES agree with each other; they don't
  // catch this, because the samples can be perfectly self-consistent while
  // still being anchored to a plane pose that's about to move. This is a
  // separate, additional gate - see anchorIsMature at the call site.
  let anchorIsMature: Bool

  var isConfirmable: Bool { state == .ready }

  var state: CandidateState {
    // A clean, depth-backed patch may use the quick window; an edge/corner
    // patch keeps the strict one it had before the depth-support fix.
    let stabilityOK = (hasDepthSupport && !placementPatchWasBoundary) ? isStable : isStableStrict
    let depthOK = !hasDepthSupport || depthAgrees
    let meshOK = !requiresMeshSupport || meshSupport
    if stabilityOK && depthOK && meshOK && anchorIsMature && depthValidationPassed {
      return .ready
    }
    return stableSampleCount <= 1 ? .candidateFound : .stabilizing
  }
}

// Rolling-window temporal stability gate, parameterized so the quick
// (depth-verified) and strict (no depth backing) paths can use different
// windows - see the ARCandidate comment above for why two tiers exist.
final class CandidateStabilityTracker {
  private let maxSamples: Int
  private let stabilityThreshold: Float
  private var samples: [SIMD3<Float>] = []

  init(maxSamples: Int, stabilityThreshold: Float) {
    self.maxSamples = maxSamples
    self.stabilityThreshold = stabilityThreshold
  }

  var sampleCount: Int { samples.count }

  func push(_ position: SIMD3<Float>) -> Bool {
    samples.append(position)
    if samples.count > maxSamples {
      samples.removeFirst(samples.count - maxSamples)
    }
    guard samples.count >= maxSamples else { return false }

    var maxSpread: Float = 0
    for i in 0..<samples.count {
      for j in (i + 1)..<samples.count {
        maxSpread = max(maxSpread, simd_distance(samples[i], samples[j]))
      }
    }
    return maxSpread <= stabilityThreshold
  }

  func reset() {
    samples.removeAll()
  }
}

// Pure vector-math port of Android's validateP4() (GraniteARView.kt lines
// 419-446) - the only native-side geometry gate Android applies. P1-P3 rely
// on the existing shared App.js per-step distance check in
// handleARPointSelected, which already runs identically on both platforms,
// so it is intentionally not duplicated here.
enum MeasurementGeometry {
  static func validateP4(previous: [LockedPoint], candidate: SIMD3<Float>) -> Bool {
    guard previous.count == 3 else { return true }

    let p2 = previous[1].worldPosition
    let p3 = previous[2].worldPosition

    let lenB = simd_distance(p3, candidate)
    if lenB < 0.05 { return false }

    let lenL = simd_distance(previous[0].worldPosition, p2)
    let lenH = simd_distance(p2, p3)
    let maxFront = max(lenL, lenH)
    if maxFront > 0.01 && lenB > 3.0 * maxFront { return false }

    let edgeH = p3 - p2
    let edgeB = candidate - p3
    let lenEdgeH = simd_length(edgeH)
    let lenEdgeB = simd_length(edgeB)
    guard lenEdgeH > 0.0001, lenEdgeB > 0.0001 else { return false }

    let dot = simd_dot(edgeH, edgeB) / (lenEdgeH * lenEdgeB)
    if abs(dot) > 0.80 { return false }

    return true
  }
}

final class MeasurementState {
  private(set) var points: [LockedPoint] = []

  var isComplete: Bool { points.count >= 4 }
  var nextPointNumber: Int { points.count + 1 }

  @discardableResult
  func lock(worldPosition: SIMD3<Float>) -> LockedPoint? {
    guard !isComplete else { return nil }
    guard MeasurementGeometry.validateP4(previous: points, candidate: worldPosition) else { return nil }

    let point = LockedPoint(number: nextPointNumber, x: worldPosition.x, y: worldPosition.y, z: worldPosition.z)
    points.append(point)
    return point
  }

  func reset() {
    points.removeAll()
  }
}
