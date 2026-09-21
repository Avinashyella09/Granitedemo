import ARKit
import AVFoundation
import Combine
import ExpoModulesCore
import Metal
import RealityKit

// Swift/ARKit+RealityKit counterpart to Android's GraniteARView.kt.
//
// Candidate detection (Phase 2) and P1-P4 locking (Phase 3) are implemented
// here. Dimension/volume math and geometry validation for P1-P3 stay in the
// existing shared App.js (handleARPointSelected) exactly as they do for
// Android - this file only ever emits raw {x,y,z,trackable} world points.
class GraniteARView: ExpoView {
  let onStatus = EventDispatcher()
  let onPointSelected = EventDispatcher()
  let onOverlayUpdate = EventDispatcher()

  private let arView: ARView
  private var isSessionRunning = false
  private var isLiDARDevice = false

  private let measurementState = MeasurementState()
  // Quick tracker gates the depth-verified path (a real plane + LiDAR
  // agreement backing it up); strict tracker gates everything else,
  // overwhelmingly corner hits with no depth backing - see the ARCandidate
  // comment in GraniteARMeasurement.swift for why these need to differ.
  private let quickStabilityTracker = CandidateStabilityTracker(maxSamples: 5, stabilityThreshold: 0.02)
  private let strictStabilityTracker = CandidateStabilityTracker(maxSamples: 10, stabilityThreshold: 0.01)
  private lazy var renderer = GraniteARRenderer(arView: arView)

  private var latestCandidate: ARCandidate?
  // The ARKit-tracked anchor (plane/mesh) the current candidate's raycast
  // landed on, if any. Kept alongside latestCandidate rather than inside it
  // so ARCandidate stays free of ARKit types. On confirmation this becomes
  // the marker's host - see GraniteARRenderer.addLockedPoint.
  private var latestCandidateHostAnchor: ARAnchor?
  private var lastEmittedTrackingQuality: String?
  private var lastOverlayEmitTime: CFTimeInterval = 0
  private var lastDriftCheckTime: CFTimeInterval = 0
  // When each ARPlaneAnchor identifier was first observed - see
  // isAnchorMature. Never cleared on measurement reset: a plane's own pose
  // stability is a property of ARKit's world tracking, unrelated to the
  // user's in-progress measurement, so a plane already known to be settled
  // shouldn't have to "re-earn" that after a Reset tap.
  private var anchorFirstSeenTime: [UUID: CFTimeInterval] = [:]
  // How long a newly-detected plane needs to be continuously tracked before
  // a point on it can be confirmed - see the ARCandidate.anchorIsMature
  // comment for the real-device evidence behind this.
  private let anchorMaturityDuration: CFTimeInterval = 1.0
  // Which raycast type most recently backed the candidate. A change here
  // means the candidate represents a different physical hypothesis, not a
  // continuation of the same one - see updateCandidate.
  private var lastCandidateSource: String?
  // De-dupes [IOS-AR-CANDIDATE] logging to state/source/depth-support
  // transitions only - see updateCandidate.
  private var lastLoggedCandidateSignature: String?
  // See hasMeshSupport's comment for what this radius does and doesn't mean.
  private let meshSupportRadius: Float = 1.0

  // How far along the reticle ray the scanned-mesh raycast looks. Beyond this
  // a "surface" is not something the officer is plausibly measuring, and the
  // plane sources take over.
  private let meshRaycastMaxDistance: Float = 10.0

  // MARK: - Depth validation (Option B: validate, never substitute)
  //
  // The candidate position always stays the ARKit ray/mesh hit. These only
  // decide whether it may be CONFIRMED, so a disagreement can never place a
  // point somewhere the officer did not aim - it can only refuse the placement
  // and ask them to reposition.

  // Spread across the depth patch beyond which the patch is straddling an
  // object boundary, so its median describes no single surface.
  private let depthPatchMaxSpread: Float = 0.03
  // How far the candidate may sit from the position LiDAR's depth reading
  // implies before confirmation is refused. Flat faces measured 14-26mm on
  // device; corners measured 133-201mm.
  private let depthValidationMaxDisagreement: Float = 0.05
  // At a boundary the median is untrustworthy, but the NEAREST reading in the
  // patch is still a real surface. A candidate sitting further away than that
  // surface by more than this is behind the thing being aimed at.
  private let depthBehindSurfaceTolerance: Float = 0.05
  private var lastDepthCheckLogTime: CFTimeInterval = 0

  // Readings within this of the nearest one are treated as the same surface.
  // Wider than LiDAR's noise on a flat patch (measured 1-23mm spread) and far
  // narrower than the object-to-background steps at a corner (112-164mm).
  // Per-frame investigation logging ([IOS-AR-DEPTHCHECK] every 0.5s and
  // [IOS-AR-POINTDEPTH] per confirmed point) proved where the P3/P4 error came
  // from - plane hits landing a median 152mm behind the surface LiDAR sees -
  // and is kept, switched off, for the next investigation. The logs that stay
  // on are event-driven, not per-frame: placement, anchor host, re-host, host
  // jump and marker drift.
  // STAGE 4: off for normal operation. Gates every PER-FRAME diagnostic
  // ([IOS-AR-DEPTHCHECK], [IOS-AR-POINTDEPTH], [IOS-AR-ANCHORCHURN],
  // [IOS-AR-HOSTTRACE], [IOS-AR-SOURCECOMPARE]) and the inert shadow-anchor
  // probe. The investigation code is gated rather than deleted: it is what
  // established that mesh anchors never bind and that session anchors hold, and
  // flipping this to true reinstates the full instrumentation if placement or
  // hosting is ever questioned again.
  //
  // Event-driven logs stay on regardless - they are low-volume and are the ones
  // worth having in a field build: [IOS-AR-CONFIRM], [IOS-AR-ANCHOR],
  // [IOS-AR-PLACEMENT], [IOS-AR-DRIFT], [IOS-AR-CANDIDATE] (transitions only),
  // [IOS-AR-CONFIRM-REFUSED], [IOS-AR-BINDFALLBACK].
  private let verboseDepthDiagnostics = false

  private let depthClusterTolerance: Float = 0.03
  // The near cluster must hold at least this fraction of the patch's valid
  // readings before its median is trusted to place a point, so a couple of
  // stray near pixels cannot pull the candidate off the surface.
  private let depthNearClusterMinFraction: Float = 0.25
  // 3x3. See sampleDepth's patchRadius note.
  private let depthPlacementPatchRadius: Int = 1
  // Drives the per-frame pipeline from RealityKit's render loop - see the
  // comment on subscribeToSceneUpdates() for why this replaced
  // ARSessionDelegate.session(_:didUpdate:).
  private var sceneUpdateSubscription: Cancellable?

  // MARK: - STAGE 1 DIAGNOSTICS (measurement only - nothing below places,
  // hosts, gates or renders anything)
  //
  // These exist to answer, with device numbers rather than argument, which of
  // four candidate mechanisms actually moves P3/P4:
  //   H1 mesh-anchor re-parameterisation dragging a parented marker
  //   H2 markers rebuilt with no session anchor, so ARKit never corrects them
  //   H3 sub-50mm host churn accumulating below the rescue threshold
  //   H4 wrong placement depth (parallax, looks identical to drift)
  // Removed or promoted to real mechanism in Stage 2.

  // Per-anchor transform churn, for every anchor type ARKit reports - the
  // direct test of H1, whose current code comment asserts (untested) that mesh
  // anchors are not re-parameterised.
  private struct AnchorChurnStats {
    let typeName: String
    var lastTranslation: SIMD3<Float>
    var updateCount: Int = 0
    var cumulativeTranslation: Float = 0
    var maxTranslationDelta: Float = 0
    // Reset each log window so the 2Hz line describes recent behaviour rather
    // than the whole session.
    var windowUpdateCount: Int = 0
    var windowSumDelta: Float = 0
    var windowMaxDelta: Float = 0
  }
  private var anchorChurn: [UUID: AnchorChurnStats] = [:]

  // An inert ARAnchor registered at each confirmed point's world transform.
  // NOT rendered, NOT a host, NOT read by any measurement, gates nothing -
  // it exists so ARKit's own corrections to a plain session anchor can be
  // measured on this device, in the same session and through the same camera
  // motion as the real (mesh-hosted) marker. This is what makes the proposed
  // Stage 2 hosting strategy falsifiable BEFORE it is built.
  private struct ShadowAnchorRecord {
    let anchor: ARAnchor
    let originalPosition: SIMD3<Float>
    var currentPosition: SIMD3<Float>
    var updateCount: Int = 0
    var cumulativeTranslation: Float = 0
    var maxTranslationDelta: Float = 0
  }
  // Keyed by 0-based point index, matching renderer marker indices.
  private var shadowAnchors: [Int: ShadowAnchorRecord] = [:]
  static let shadowAnchorName = "GraniteARShadowProbe"

  // When ARKit first reported each anchor, for mesh-maturity reporting. A
  // reconstruction mesh is coarse when first emitted and refines over the next
  // few seconds, so a mesh/candidate disagreement measured the instant a point
  // is confirmed means something quite different from the same disagreement
  // measured once that mesh has settled. Distinguishing those two is the whole
  // purpose of this run.
  private var anchorAddedTime: [UUID: CFTimeInterval] = [:]
  // When each confirmed point was placed, so every later sample can be
  // reported against time-since-confirmation and convergence (or the lack of
  // it) is visible directly rather than inferred.
  private var pointConfirmTime: [Int: CFTimeInterval] = [:]

  private var lastChurnLogTime: CFTimeInterval = 0
  private var lastHostTraceTime: CFTimeInterval = 0
  private var lastSourceCompareTime: CFTimeInterval = 0
  // Per-point cache for "moved since last HOSTTRACE sample", kept separate
  // from the renderer's own drift cache so the two throttles cannot interfere.
  private var lastHostTracePositions: [Int: SIMD3<Float>] = [:]

  // Expo may re-apply the current prop values on renders unrelated to this
  // prop actually changing (e.g. triggered by the ~frequent onOverlayUpdate
  // events driving React state elsewhere on screen). Without tracking the
  // last-seen value, a stale tapToken/resetToken getting re-applied would
  // silently re-fire confirm/reset - which is exactly what produced every
  // confirmed point being logged as "P1" during device testing (state was
  // being wiped between deliberate taps). Android avoids this by treating a
  // tap as a one-shot flag consumed once by its render loop; this mirrors
  // that by only acting on genuine value changes.
  var tapToken: Int = 0 {
    didSet {
      guard tapToken != oldValue, tapToken > 0 else { return }
      triggerCenterTap()
    }
  }
  var resetToken: Int = 0 {
    didSet {
      guard resetToken != oldValue, resetToken > 0 else { return }
      resetMeasurement()
    }
  }

  required init(appContext: AppContext? = nil) {
    arView = ARView(frame: .zero, cameraMode: .ar, automaticallyConfigureSession: false)
    super.init(appContext: appContext)

    clipsToBounds = true
    addSubview(arView)
    arView.session.delegate = self

    subscribeToSceneUpdates()
    requestCameraAccessAndStart()
  }

  // The per-frame candidate/render pipeline runs here, off RealityKit's own
  // render loop, NOT off ARSessionDelegate.session(_:didUpdate:).
  //
  // The delegate version was the root cause of the "points move while the
  // device moves" bug. It looked like this:
  //
  //   func session(_ session: ARSession, didUpdate frame: ARFrame) {
  //     DispatchQueue.main.async { self?.handleFrameUpdate(frame: frame) }
  //   }
  //
  // Two separate defects came out of that:
  //
  // 1. Pose desynchronisation. By the time the queued block ran on main, the
  //    session had already advanced. Inside it, arView.raycast(...) resolved
  //    against the session's CURRENT pose while sampleDepth(frame:) and
  //    frame.camera.transform read the STALE captured frame - so the ray and
  //    the depth measurement it was being validated against came from
  //    different moments in time. Holding the phone still hid this (the two
  //    poses coincide); any real movement pulled them apart, which is exactly
  //    why the measured raycast/depth disagreement scaled with motion
  //    (sub-millimetre when static, hundreds of millimetres while moving) and
  //    why candidates landed off the physical corner specifically when the
  //    device was moving.
  //
  // 2. Frame retention. ARFrame's captured image and depth buffers come from
  //    a finite pool and are explicitly not meant to be retained past the
  //    delegate call; capturing one in an async closure does retain it. With
  //    the main thread busy servicing the React Native bridge (this module
  //    emits onOverlayUpdate continuously), those blocks queue up, each
  //    pinning a frame, until ARKit throttles delivery - degrading tracking
  //    and making the reticle sluggish to settle.
  //
  // SceneEvents.Update fires once per rendered frame, already on the main
  // thread, in step with what is actually on screen. Reading
  // arView.session.currentFrame at that instant gives one frame that the
  // raycast, the depth sample and the camera transform all agree on, and it
  // is used and released within the call rather than retained.
  private func subscribeToSceneUpdates() {
    sceneUpdateSubscription = arView.scene.subscribe(to: SceneEvents.Update.self) { [weak self] _ in
      self?.handleSceneUpdate()
    }
  }

  override func layoutSubviews() {
    super.layoutSubviews()
    arView.frame = bounds
  }

  deinit {
    sceneUpdateSubscription?.cancel()
    arView.session.pause()
  }

  // MARK: - Session lifecycle

  private func requestCameraAccessAndStart() {
    switch AVCaptureDevice.authorizationStatus(for: .video) {
    case .authorized:
      startSession()
    case .notDetermined:
      AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
        DispatchQueue.main.async {
          if granted {
            self?.startSession()
          } else {
            self?.emitStatus("camera_permission", message: "Camera access was denied. Enable it in Settings to measure blocks.")
          }
        }
      }
    case .denied, .restricted:
      emitStatus("camera_permission", message: "Camera access is required to measure blocks. Enable it in Settings.")
    @unknown default:
      emitStatus("camera_permission", message: "Camera access is required to measure blocks. Enable it in Settings.")
    }
  }

  private func startSession() {
    guard ARWorldTrackingConfiguration.isSupported else {
      emitStatus("error", message: "This device does not support ARKit world tracking.")
      return
    }

    emitStatus("initializing", message: nil)

    let configuration = ARWorldTrackingConfiguration()
    configuration.planeDetection = [.horizontal, .vertical]
    configuration.isAutoFocusEnabled = true

    if ARWorldTrackingConfiguration.supportsFrameSemantics([.sceneDepth, .smoothedSceneDepth]) {
      configuration.frameSemantics.insert(.sceneDepth)
      configuration.frameSemantics.insert(.smoothedSceneDepth)
      isLiDARDevice = true
    } else if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
      configuration.frameSemantics.insert(.sceneDepth)
      isLiDARDevice = true
    }

    // .mesh (not .meshWithClassification) - the raycast-quality benefit this
    // module relies on comes from the reconstructed mesh geometry backing
    // estimatedPlane hits, not from semantic classification, which nothing
    // here reads. Classification adds real GPU/Neural-Engine load for zero
    // benefit to this feature, so skip it.
    if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) {
      configuration.sceneReconstruction = .mesh
      // Gives the reconstructed mesh collision shapes, which is what makes
      // scene.raycast able to intersect the REAL scanned surface (see
      // meshRaycastPosition). Without this the mesh exists only as anchors we
      // can measure against after the fact, and the only thing a ray can hit
      // is ARKit's plane estimate.
      arView.environment.sceneUnderstanding.options.insert(.collision)
    }

    arView.session.run(configuration)
    isSessionRunning = true
  }

  // MARK: - Prop-driven actions

  func triggerCenterTap() {
    // Defensive: keep all access to latestCandidate/measurementState/renderer
    // confined to main, matching handleSceneUpdate, even though Expo's Prop
    // closures are expected to already run on main for UIView-based props.
    DispatchQueue.main.async { [weak self] in
      self?.confirmCurrentCandidate()
    }
  }

  private func confirmCurrentCandidate() {
    guard isSessionRunning else {
      emitStatus("candidate_unavailable", message: "AR session is not ready yet.")
      return
    }
    guard let candidate = latestCandidate, candidate.isConfirmable else {
      // A depth disagreement is a different problem from an unsettled reading,
      // and needs different guidance: the aimed point is not on the surface
      // LiDAR can see, so holding steadier will not help - the officer has to
      // move so the corner is properly visible.
      if let candidate = latestCandidate, !candidate.depthValidationPassed {
        let disagreementText = candidate.depthDisagreement.map { String(format: "%.0fmm", $0 * 1000) } ?? "n/a"
        print(String(format: "[IOS-AR-CONFIRM-REFUSED] reason=depthValidation disagreement=%@ source=%@ world=(%.4f, %.4f, %.4f)", disagreementText, candidate.source, candidate.worldPosition.x, candidate.worldPosition.y, candidate.worldPosition.z))
        emitStatus(
          "candidate_unstable",
          message: "Point the camera so the corner surface is clearly in view - the depth sensor cannot see it from here."
        )
        return
      }
      emitStatus("candidate_unstable", message: "Move slowly and keep the corner in view until the point is stable.")
      return
    }
    guard let locked = measurementState.lock(worldPosition: candidate.worldPosition) else {
      emitStatus("candidate_unstable", message: "That point doesn't form a plausible block corner. Try again.")
      return
    }

    let points = measurementState.points
    // STAGE 2a: the point's own session-registered anchor, and nothing else.
    renderer.addLockedPoint(locked.worldPosition)
    // Independent control, kept from Stage 1: a second session anchor at the
    // same spot that nothing renders or reads. If the real marker now tracks
    // it, the 2a mechanism is behaving as the Stage 1 probe predicted.
    registerShadowAnchor(forPointIndex: points.count - 1, at: locked.worldPosition)
    if points.count >= 2 {
      renderer.addEdge(fromIndex: points.count - 2, toIndex: points.count - 1)
    }

    print("[IOS-AR-CONFIRM] P\(locked.number) world=(\(locked.x), \(locked.y), \(locked.z)) source=\(candidate.source) state=\(candidate.state.rawValue) depthSupport=\(candidate.hasDepthSupport) confidence=\(candidate.depthConfidence) meshSupport=\(candidate.meshSupport) anchorMature=\(candidate.anchorIsMature) samples=\(candidate.stableSampleCount) tracking=\(lastEmittedTrackingQuality ?? "unknown")")
    print("[IOS-AR-ANCHOR] P\(locked.number) host=sessionAnchor anchorId=\(renderer.hostAnchorIdentifier(at: points.count - 1)?.uuidString ?? "none")")
    // PLACEMENT INVESTIGATION: what every independent source says the surface
    // is at, compared against the position actually confirmed. Diagnostic only.
    logPlacementForensics(pointNumber: locked.number, confirmed: locked.worldPosition, candidate: candidate)

    onPointSelected([
      "x": locked.x,
      "y": locked.y,
      "z": locked.z,
      "trackable": candidate.source,
    ])

    if measurementState.isComplete {
      renderer.addEdge(fromIndex: 3, toIndex: 0)
      renderer.clearPreviewEdge()
      emitStatus("measurement_complete", message: nil)
    } else {
      emitStatus("ready_for_confirmation", message: "P\(locked.number) locked.")
    }
  }

  func resetMeasurement() {
    DispatchQueue.main.async { [weak self] in
      self?.performReset()
    }
  }

  private func performReset() {
    measurementState.reset()
    quickStabilityTracker.reset()
    strictStabilityTracker.reset()
    lastCandidateSource = nil
    lastLoggedCandidateSignature = nil
    renderer.reset()
    latestCandidate = nil
    latestCandidateHostAnchor = nil
    // STAGE 1: probes are per-measurement, so they go with the measurement.
    for record in shadowAnchors.values {
      arView.session.remove(anchor: record.anchor)
    }
    shadowAnchors.removeAll()
    lastHostTracePositions.removeAll()
    emitStatus("initializing", message: "Measurement reset.")
  }

  // MARK: - Stage 1 shadow-anchor probe

  private func registerShadowAnchor(forPointIndex index: Int, at worldPosition: SIMD3<Float>) {
    // Test-only control probe - see verboseDepthDiagnostics.
    guard verboseDepthDiagnostics else { return }
    var transform = matrix_identity_float4x4
    transform.columns.3 = SIMD4<Float>(worldPosition.x, worldPosition.y, worldPosition.z, 1)
    let anchor = ARAnchor(name: Self.shadowAnchorName, transform: transform)
    arView.session.add(anchor: anchor)
    shadowAnchors[index] = ShadowAnchorRecord(
      anchor: anchor,
      originalPosition: worldPosition,
      currentPosition: worldPosition
    )
    print("[IOS-AR-SHADOW-ADD] P\(index + 1) probeId=\(anchor.identifier.uuidString) at=(\(worldPosition.x), \(worldPosition.y), \(worldPosition.z))")
  }

  // MARK: - Status reporting

  private func emitStatus(_ status: String, message: String?) {
    var payload: [String: Any] = ["status": status]
    if let message = message {
      payload["message"] = message
    }
    onStatus(payload)
  }
}

// MARK: - ARSessionDelegate: tracking state + per-frame candidate pipeline

extension GraniteARView: ARSessionDelegate {
  func session(_ session: ARSession, cameraDidChangeTrackingState camera: ARCamera) {
    switch camera.trackingState {
    case .normal:
      emitStatus("tracking", message: nil)
    case .limited(let reason):
      emitStatus("limited", message: trackingLimitedMessage(for: reason))
    case .notAvailable:
      emitStatus("initializing", message: nil)
    }
  }

  func session(_ session: ARSession, didFailWithError error: Error) {
    emitStatus("error", message: error.localizedDescription)
  }

  func sessionWasInterrupted(_ session: ARSession) {
    emitStatus("limited", message: "AR session interrupted.")
  }

  func sessionInterruptionEnded(_ session: ARSession) {
    emitStatus("tracking", message: nil)
  }

  // ARKit adjusts the anchors it is tracking when its estimate of the world
  // improves (relocalisation, world-map refinement). Those corrections are
  // what keep a confirmed marker sitting on the physical corner instead of
  // sliding off it as the session's understanding shifts, so they are
  // applied to the rendered entity here. The immutable LockedPoint numbers
  // behind the measurement are deliberately NOT touched - only the visual.
  func session(_ session: ARSession, didAdd anchors: [ARAnchor]) {
    let now = CACurrentMediaTime()
    for anchor in anchors where anchorAddedTime[anchor.identifier] == nil {
      anchorAddedTime[anchor.identifier] = now
    }
  }

  // How well-established the reconstruction is around a point: how many mesh
  // anchors cover it, how long the oldest has existed, and how many refinement
  // passes they have received. A large candidate-vs-mesh disagreement against
  // a mesh that is 0.2s old with 1 update is weak evidence about the candidate;
  // the same disagreement against a mesh 10s old with 40 updates is strong.
  private func meshMaturity(near worldPosition: SIMD3<Float>, frame: ARFrame) -> (count: Int, oldestAge: Double, totalUpdates: Int) {
    let now = CACurrentMediaTime()
    var count = 0
    var oldestAge: Double = 0
    var totalUpdates = 0
    for anchor in frame.anchors {
      guard let meshAnchor = anchor as? ARMeshAnchor else { continue }
      let origin = SIMD3<Float>(
        meshAnchor.transform.columns.3.x,
        meshAnchor.transform.columns.3.y,
        meshAnchor.transform.columns.3.z
      )
      guard simd_distance(origin, worldPosition) <= 1.5 else { continue }
      count += 1
      if let added = anchorAddedTime[anchor.identifier] {
        oldestAge = max(oldestAge, now - added)
      }
      totalUpdates += anchorChurn[anchor.identifier]?.updateCount ?? 0
    }
    return (count, oldestAge, totalUpdates)
  }

  func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) {
    // Unchanged behaviour: only our own registered point anchors move a marker.
    for anchor in anchors where anchor.name == GraniteARRenderer.pointAnchorName {
      renderer.applyAnchorCorrection(anchor)
    }
    // STAGE 1 measurement only - records what ARKit does to every anchor it
    // reports, and to the inert probes. Moves nothing.
    recordAnchorChurn(anchors)
  }

  // H1's decisive measurement: does ARKit actually re-parameterise the anchor
  // types this module parents markers to, and by how much on this device?
  // Every anchor is tracked, not just hosts, so the answer is a property of
  // the anchor TYPE rather than of the one anchor a marker happened to pick.
  private func recordAnchorChurn(_ anchors: [ARAnchor]) {
    for anchor in anchors {
      let translation = SIMD3<Float>(
        anchor.transform.columns.3.x,
        anchor.transform.columns.3.y,
        anchor.transform.columns.3.z
      )
      guard translation.x.isFinite, translation.y.isFinite, translation.z.isFinite else { continue }

      if var stats = anchorChurn[anchor.identifier] {
        let delta = simd_distance(translation, stats.lastTranslation)
        stats.lastTranslation = translation
        stats.updateCount += 1
        stats.cumulativeTranslation += delta
        stats.maxTranslationDelta = max(stats.maxTranslationDelta, delta)
        stats.windowUpdateCount += 1
        stats.windowSumDelta += delta
        stats.windowMaxDelta = max(stats.windowMaxDelta, delta)
        anchorChurn[anchor.identifier] = stats
      } else {
        anchorChurn[anchor.identifier] = AnchorChurnStats(
          typeName: String(describing: type(of: anchor)),
          lastTranslation: translation
        )
      }

      // The probe's own correction history - the A/B half that tests whether a
      // plain session anchor is corrected at all on this device.
      if anchor.name == Self.shadowAnchorName,
         let index = shadowAnchors.first(where: { $0.value.anchor.identifier == anchor.identifier })?.key,
         var record = shadowAnchors[index] {
        let delta = simd_distance(translation, record.currentPosition)
        record.currentPosition = translation
        record.updateCount += 1
        record.cumulativeTranslation += delta
        record.maxTranslationDelta = max(record.maxTranslationDelta, delta)
        shadowAnchors[index] = record
        if delta > 0.001 {
          print(String(
            format: "[IOS-AR-SHADOW-CORRECTED] P%d delta=%.1fmm sinceOriginal=%.1fmm updates=%d",
            index + 1, delta * 1000,
            simd_distance(translation, record.originalPosition) * 1000,
            record.updateCount
          ))
        }
      }
    }
  }

  // Fires when ARKit removes an anchor it is no longer tracking - most
  // commonly because it merged two plane anchors it recognized as the same
  // physical surface into one. See GraniteARRenderer.handleHostAnchorRemoved
  // for why an unhandled removal here made a confirmed point disappear.
  func session(_ session: ARSession, didRemove anchors: [ARAnchor]) {
    for anchor in anchors {
      // STAGE 1: keep the churn table bounded - scene reconstruction creates
      // and retires mesh anchors continuously over a long session.
      anchorChurn.removeValue(forKey: anchor.identifier)
    }
  }

  // Runs on the main thread once per rendered frame - see
  // subscribeToSceneUpdates() for why the pipeline is driven from here.
  private func handleSceneUpdate() {
    guard isSessionRunning, let frame = arView.session.currentFrame else { return }
    guard case .normal = frame.camera.trackingState else {
      latestCandidate = nil
      latestCandidateHostAnchor = nil
      quickStabilityTracker.reset()
      strictStabilityTracker.reset()
      lastCandidateSource = nil
      return
    }

    updateCandidate(frame: frame)
    // Confirms RealityKit actually bound each marker to its own anchor, and
    // rebuilds any that did not - see GraniteARRenderer.resolveBindings.
    for index in renderer.resolveBindings() {
      print("[IOS-AR-BINDFALLBACK] P\(index + 1) AnchorEntity(anchor:) did not resolve within the grace period - rebuilt on a world-space entity driven by ARKit's corrections to the same session anchor")
    }
    renderer.refreshEdges()

    // updateCandidate/refreshEdges (which feed the stability trackers and the
    // live marker/edge positions) run every frame at full rate; only the
    // JS-bridge dispatch is throttled here, since App.js only needs to
    // re-render at human-perceptible speed, not ARKit's raw 30-60Hz.
    let now = CACurrentMediaTime()
    if now - lastOverlayEmitTime >= 0.05 {
      lastOverlayEmitTime = now
      emitOverlayUpdate(frame: frame)
    }

    // Direct evidence of whether a CONFIRMED marker is actually moving -
    // distinct from the live candidate, which is supposed to move while
    // aiming. Checked at ~2Hz (not every frame - this is diagnostic-only,
    // not a gate on anything) so a real, sustained drift shows up clearly in
    // the log without being drowned out by print volume.
    if now - lastDriftCheckTime >= 0.5 {
      lastDriftCheckTime = now
      // Separates "the marker moved" from "the marker is in the wrong place" -
      // the two look identical on screen.
      logConfirmedPointDepthAgreement(frame: frame)
      // STAGE 1 measurement block. Order matters only for readability of the
      // log: churn (what ARKit did) -> host trace (what it did to our markers)
      // -> source comparison (what placement would do under Option B).
      logAnchorChurn()
      logHostTrace()
      logSourceComparison(frame: frame)
      for entry in renderer.checkForDrift() {
        // host tells you WHY: a hosted marker moving a few mm is ARKit
        // correcting the surface and carrying the point with it (correct);
        // a worldFallback marker sitting at 0mm while the object moves under
        // it is the failure mode this phase exists to remove.
        print("[IOS-AR-DRIFT] P\(entry.index + 1) host=\(entry.host.rawValue) movedSinceLastCheck=\(Int(entry.deltaMeters * 1000))mm carriedSinceLocked=\(Int(entry.sinceLockedMeters * 1000))mm trackingState=\(lastEmittedTrackingQuality ?? "unknown")")
      }
    }
  }

  // MARK: - Placement forensics
  //
  // Answers, at the instant of confirmation, "where does every independent
  // source say the surface is, relative to the point we just placed?" - so the
  // 39-52mm corner error measured at P3/P4 can be attributed to a stage rather
  // than guessed at. Diagnostic only: nothing here moves or vetoes a point.
  //
  // Each source is reported as a SIGNED distance along the camera ray:
  //   negative -> that source puts the surface NEARER the camera than the
  //               confirmed point, i.e. the point is behind/inside the object
  //   positive -> the surface is further away; the point floats in front
  // Signed rather than absolute because the sign is what distinguishes a
  // ray overshooting past a corner from a depth reading latching onto
  // foreground.
  private func logPlacementForensics(pointNumber: Int, confirmed: SIMD3<Float>, candidate: ARCandidate) {
    guard let frame = arView.session.currentFrame else { return }
    let point = reticlePoint()
    let cameraPosition = SIMD3<Float>(
      frame.camera.transform.columns.3.x,
      frame.camera.transform.columns.3.y,
      frame.camera.transform.columns.3.z
    )
    let confirmedRange = simd_distance(cameraPosition, confirmed)

    func signedRangeDelta(_ position: SIMD3<Float>?) -> String {
      guard let position = position else { return "none" }
      return String(format: "%+.0fmm", (simd_distance(cameraPosition, position) - confirmedRange) * 1000)
    }
    func hitPosition(_ result: ARRaycastResult?) -> SIMD3<Float>? {
      guard let result = result else { return nil }
      let t = result.worldTransform.columns.3
      return SIMD3<Float>(t.x, t.y, t.z)
    }

    let meshHit = meshRaycastPosition(at: point)
    let planeHit = hitPosition(arView.raycast(from: point, allowing: .existingPlaneGeometry, alignment: .any).first)
    let estimatedHit = hitPosition(arView.raycast(from: point, allowing: .estimatedPlane, alignment: .any).first)
    // Reconstruction maturity AT the moment of placement - the baseline the
    // later convergence samples are compared against.
    let maturity = meshMaturity(near: confirmed, frame: frame)
    pointConfirmTime[pointNumber - 1] = CACurrentMediaTime()

    // Depth, reported three ways, because which reduction is used is itself a
    // candidate cause: whole-patch median blends surfaces at a corner, the
    // near cluster tracks the closer one, the single nearest pixel is the
    // rawest evidence of "something is at least this close".
    var depthText = "none"
    var depthMedianWorld: SIMD3<Float>?
    var depthNearWorld: SIMD3<Float>?
    if let raw = sampleDepth(frame: frame, at: point, cameraTransform: frame.camera.transform, maxSpread: .greatestFiniteMagnitude),
       let normalized = normalizedImagePoint(for: point, frame: frame) {
      depthMedianWorld = unprojectDepth(normalizedImagePoint: normalized, depth: raw.medianDepth, frame: frame)
      depthNearWorld = unprojectDepth(normalizedImagePoint: normalized, depth: raw.nearClusterMedian, frame: frame)
      depthText = String(
        format: "median=%.0fmm nearCluster=%.0fmm nearest=%.0fmm spread=%.0fmm valid=%d/%d nearClusterN=%d",
        raw.medianDepth * 1000, raw.nearClusterMedian * 1000, raw.nearestDepth * 1000,
        raw.spread * 1000, raw.sampleCount, raw.sampleCount, raw.nearClusterCount
      )
    }

    // Confirmed range vs each source, plus the pure scalar comparison that
    // isolates unprojection/frame-conversion error from geometry error: if
    // the confirmed point came from depth, its range MUST equal the depth
    // scalar it was built from. Any difference there is a conversion bug.
    print(String(
      format: "[IOS-AR-PLACEMENT] P%d confirmedRange=%.0fmm source=%@ depthSupportAtConfirm=%@ depthConfidence=%.2f depth=[%@] vsDepthMedian=%@ vsDepthNearCluster=%@ vsSceneMesh=%@ vsExistingPlane=%@ vsEstimatedPlane=%@ meshAnchorsNear=%d meshOldestAge=%.1fs meshTotalUpdates=%d tracking=%@",
      pointNumber, confirmedRange * 1000, candidate.source,
      candidate.hasDepthSupport ? "true" : "FALSE",
      candidate.depthConfidence,
      depthText,
      signedRangeDelta(depthMedianWorld),
      signedRangeDelta(depthNearWorld),
      signedRangeDelta(meshHit),
      signedRangeDelta(planeHit),
      signedRangeDelta(estimatedHit),
      maturity.count, maturity.oldestAge, maturity.totalUpdates,
      lastEmittedTrackingQuality ?? "unknown"
    ))
  }

  // MARK: - Stage 1 measurement logs (read-only; they change nothing)

  // H1. Aggregated per anchor TYPE, because a session holds hundreds of mesh
  // anchors and dumping each would be unreadable - plus a detail line for the
  // specific anchors that are currently hosting confirmed markers, which are
  // the ones whose churn actually reaches the screen.
  private func logAnchorChurn() {
    guard verboseDepthDiagnostics else { return }
    guard !anchorChurn.isEmpty else { return }

    var byType: [String: (anchors: Int, updates: Int, sumDelta: Float, maxDelta: Float)] = [:]
    for stats in anchorChurn.values {
      var entry = byType[stats.typeName] ?? (anchors: 0, updates: 0, sumDelta: 0, maxDelta: 0)
      entry.anchors += 1
      entry.updates += stats.windowUpdateCount
      entry.sumDelta += stats.windowSumDelta
      entry.maxDelta = max(entry.maxDelta, stats.windowMaxDelta)
      byType[stats.typeName] = entry
    }

    let summary = byType.keys.sorted().map { type -> String in
      let e = byType[type]!
      let mean = e.updates > 0 ? e.sumDelta / Float(e.updates) : 0
      return String(format: "%@[n=%d updates=%d meanDelta=%.1fmm maxDelta=%.1fmm]",
                    type, e.anchors, e.updates, mean * 1000, e.maxDelta * 1000)
    }.joined(separator: " ")
    print("[IOS-AR-ANCHORCHURN] \(summary)")

    // The host anchors that are actually carrying markers right now.
    for point in measurementState.points {
      let index = point.number - 1
      guard let hostId = renderer.hostAnchorIdentifier(at: index),
            let stats = anchorChurn[hostId] else { continue }
      print(String(
        format: "[IOS-AR-ANCHORCHURN-HOST] P%d host=%@ type=%@ windowUpdates=%d windowMaxDelta=%.1fmm totalUpdates=%d totalPath=%.1fmm maxEver=%.1fmm",
        point.number, hostId.uuidString.prefix(8).description, stats.typeName,
        stats.windowUpdateCount, stats.windowMaxDelta * 1000,
        stats.updateCount, stats.cumulativeTranslation * 1000, stats.maxTranslationDelta * 1000
      ))
    }

    for key in anchorChurn.keys {
      anchorChurn[key]?.windowUpdateCount = 0
      anchorChurn[key]?.windowSumDelta = 0
      anchorChurn[key]?.windowMaxDelta = 0
    }
  }

  // H2 + H3, per confirmed point: what it is attached to, whether ARKit can
  // correct it at all (hasOwnSessionAnchor), how far it has been carried, and
  // the sub-threshold movement the rescue path never sees.
  private func logHostTrace() {
    guard verboseDepthDiagnostics else { return }
    for point in measurementState.points {
      let index = point.number - 1
      guard let live = renderer.livePosition(at: index) else { continue }
      let sinceLast = lastHostTracePositions[index].map { simd_distance(live, $0) } ?? 0
      lastHostTracePositions[index] = live

      let stats = renderer.motionStats(at: index)
      let shadow = shadowAnchors[index]
      // The A/B number: how far the two strategies have diverged for the SAME
      // point, in the same session, under the same camera motion.
      let divergence = shadow.map { simd_distance(live, $0.currentPosition) }

      // anchorVsMarker separates "ARKit corrected the anchor" from "the visual
      // followed it" - with one anchor per point these must agree, and a
      // divergence would mean the binding is not actually carrying the marker.
      let anchorVsMarker = renderer.anchorPosition(at: index).map { simd_distance(live, $0) }

      print(String(
        format: "[IOS-AR-HOSTTRACE] P%d host=%@ anchorId=%@ anchored=%@ bound=%@ anchorVsMarker=%@ rebuilds=%d movedSinceLast=%.1fmm carriedSinceLocked=%.1fmm framePath=%.1fmm movingFrames=%d/%d maxFrameMove=%.1fmm shadowCarried=%@ shadowUpdates=%d realVsShadow=%@",
        point.number,
        renderer.hostKind(at: index)?.rawValue ?? "unknown",
        renderer.hostAnchorIdentifier(at: index)?.uuidString.prefix(8).description ?? "none",
        renderer.isAnchored(at: index) ? "true" : "FALSE",
        renderer.didBind(at: index) ? "true" : "false",
        anchorVsMarker.map { String(format: "%.1fmm", $0 * 1000) } ?? "n/a",
        stats?.hostChangeCount ?? -1,
        sinceLast * 1000,
        simd_distance(live, point.worldPosition) * 1000,
        (stats?.cumulativePathLength ?? 0) * 1000,
        stats?.movingFrameCount ?? -1,
        stats?.totalFrames ?? -1,
        (stats?.maxFrameMove ?? 0) * 1000,
        shadow.map { String(format: "%.1fmm", simd_distance($0.currentPosition, $0.originalPosition) * 1000) } ?? "n/a",
        shadow?.updateCount ?? -1,
        divergence.map { String(format: "%.1fmm", $0 * 1000) } ?? "n/a"
      ))
    }
  }

  // What strict Option B (Stage 2b) would actually do, measured before it is
  // built: how far each ray/mesh source sits from the depth-measured surface,
  // split by surface class. Placement currently comes from depth, so the ray
  // sources are computed here purely for comparison and drive nothing.
  private func logSourceComparison(frame: ARFrame) {
    guard verboseDepthDiagnostics, isLiDARDevice else { return }
    let point = reticlePoint()
    guard let raw = sampleDepth(
      frame: frame,
      at: point,
      cameraTransform: frame.camera.transform,
      maxSpread: .greatestFiniteMagnitude
    ) else { return }

    // Patch spread is the physical signature of what the reticle is on: a flat
    // face reads one surface, an edge straddles two, a corner three.
    let surfaceClass: String
    if raw.spread <= depthPatchMaxSpread {
      surfaceClass = "flat"
    } else if raw.spread <= 0.10 {
      surfaceClass = "edge"
    } else {
      surfaceClass = "corner"
    }

    guard let normalized = normalizedImagePoint(for: point, frame: frame) else { return }
    let depthWorld = unprojectDepth(normalizedImagePoint: normalized, depth: raw.nearClusterMedian, frame: frame)

    let meshHit = meshRaycastPosition(at: point)
    let planeHit = arView.raycast(from: point, allowing: .existingPlaneGeometry, alignment: .any).first
    let estimatedHit = arView.raycast(from: point, allowing: .estimatedPlane, alignment: .any).first

    func offset(_ position: SIMD3<Float>?) -> String {
      guard let position = position else { return "none" }
      return String(format: "%.0fmm", simd_distance(position, depthWorld) * 1000)
    }
    func hitPosition(_ result: ARRaycastResult?) -> SIMD3<Float>? {
      guard let result = result else { return nil }
      return SIMD3<Float>(
        result.worldTransform.columns.3.x,
        result.worldTransform.columns.3.y,
        result.worldTransform.columns.3.z
      )
    }

    print(String(
      format: "[IOS-AR-SOURCECOMPARE] surfaceClass=%@ patchSpread=%.0fmm depthNearCluster=%.0fmm sceneMeshAvailable=%@ sceneMeshVsDepth=%@ existingPlaneVsDepth=%@ estimatedPlaneVsDepth=%@ wouldOptionBRefuse(mesh)=%@",
      surfaceClass, raw.spread * 1000, raw.nearClusterMedian * 1000,
      meshHit != nil ? "true" : "false",
      offset(meshHit),
      offset(hitPosition(planeHit)),
      offset(hitPosition(estimatedHit)),
      meshHit.map { simd_distance($0, depthWorld) > depthValidationMaxDisagreement ? "REFUSE" : "allow" } ?? "noMeshHit"
    ))
  }

  private func trackingLimitedMessage(for reason: ARCamera.TrackingState.Reason) -> String {
    switch reason {
    case .initializing:
      return "Move the phone slowly to start tracking."
    case .excessiveMotion:
      return "Move the phone more slowly."
    case .insufficientFeatures:
      return "Move the phone to scan more of the block and surroundings."
    case .relocalizing:
      return "Resuming tracking..."
    @unknown default:
      return "Tracking quality is limited."
    }
  }

  // MARK: - Candidate pipeline
  //
  // screen center (reticle, in ARView point-space)
  //   -> ARKit raycast (existingPlaneGeometry, falling back to estimatedPlane;
  //      the latter already incorporates the reconstructed LiDAR mesh when
  //      scene reconstruction is active)
  //   -> source-change detection (a fallback-tier change restarts stability -
  //      see the source-lock comment below)
  //   -> world-space hit position (this is what gets displayed AND confirmed -
  //      never smoothed)
  //   -> on LiDAR devices, cross-check against a local sceneDepth patch
  //   -> on LiDAR devices with no depth backing, cross-check against nearby
  //      reconstructed scene mesh (hasMeshSupport)
  //   -> temporal stability gate, quick or strict tier depending on how much
  //      of the above actually backs this candidate (confirmable, not
  //      visible, depends on this - see ARCandidate.state)
  //
  // The guiding rule throughout: no physically-supported candidate must
  // always beat a candidate that merely looks self-consistent. A hit that
  // fails any of the above checks is not confirmable, full stop - it is
  // never averaged, smoothed toward a guess, or silently accepted anyway.

  // A hit with no anchor at all (a pure estimatedPlane extrapolation) has
  // nothing to "mature" - it's judged entirely on the existing stability/
  // depth/mesh checks instead. A hit WITH a tracked anchor is only mature
  // once that specific anchor has been continuously observed for at least
  // anchorMaturityDuration.
  private func isAnchorMature(_ anchor: ARAnchor?) -> Bool {
    guard let anchor = anchor else { return true }
    let now = CACurrentMediaTime()
    guard let firstSeen = anchorFirstSeenTime[anchor.identifier] else {
      anchorFirstSeenTime[anchor.identifier] = now
      return false
    }
    return now - firstSeen >= anchorMaturityDuration
  }

  private func reticlePoint() -> CGPoint {
    CGPoint(x: arView.bounds.midX, y: arView.bounds.midY)
  }

  // The LiDAR-reconstructed surface along the reticle ray, when scene
  // reconstruction has actually scanned it.
  //
  // This is the source that fixes top/back corners. A plane raycast returns a
  // point on ARKit's EXTRAPOLATED plane - aim at the top corner of a monitor
  // and the ray keeps going until it meets the extended desk/wall plane, so
  // the point is placed at the wrong depth. Measured on device: with the plane
  // sources only, P3 and P4 came back at y=-0.110, the exact height of the
  // desk P1/P2 were on, while the officer was aiming at corners ~10cm higher.
  // A point at the wrong depth is fixed in the world but sits off the object,
  // and parallax while walking around it is indistinguishable from drift -
  // which is why P3/P4 "wouldn't lock" even with a perfectly stable anchor.
  //
  // scene.raycast intersects the mesh ARKit actually built from LiDAR returns,
  // so the hit is on the physical surface the officer is pointing at. Nothing
  // is reconstructed or guessed: no mesh there means no hit, and the plane
  // sources below are used instead.
  private func meshRaycastPosition(at point: CGPoint) -> SIMD3<Float>? {
    guard isLiDARDevice, let ray = arView.ray(through: point) else { return nil }
    let hits = arView.scene.raycast(
      origin: ray.origin,
      direction: ray.direction,
      length: meshRaycastMaxDistance,
      query: .nearest,
      mask: .sceneUnderstanding
    )
    return hits.first?.position
  }

  // The surface LiDAR itself measures at the reticle, unprojected into world
  // space - now the primary candidate position on LiDAR devices.
  //
  // WHY THIS OUTRANKS THE RAYCAST, measured on the iPhone 16 Pro Max over 91
  // samples of the same aiming session:
  //
  //   depth vs reality on flat faces .......... 1-4mm   (validated conversion)
  //   sceneMesh hit vs depth ................. 18mm median, 129mm worst
  //   existingPlaneGeometry hit vs depth ..... 152mm median, 170mm worst
  //
  // and the resulting placed points sat 112mm (P3) and 158mm (P4) BEHIND the
  // corner they were aimed at. ARKit's ray continues past a corner and stops
  // on the plane's extrapolation, or on mesh that has rounded the edge off;
  // the depth reading is of the surface actually in front of the reticle.
  //
  // At a corner the patch legitimately straddles two surfaces, so the median
  // of the whole patch describes neither. The NEAR CLUSTER median is used
  // instead: the readings belonging to the closer surface, which is the object
  // being measured rather than the background behind it.
  //
  // This is a real per-frame measurement, never a reconstruction: no depth, no
  // depth candidate.
  private func depthCandidatePosition(frame: ARFrame, at point: CGPoint) -> (position: SIMD3<Float>, sample: DepthSample, usedNearCluster: Bool)? {
    guard isLiDARDevice else { return nil }
    guard let sample = sampleDepth(
      frame: frame,
      at: point,
      cameraTransform: frame.camera.transform,
      maxSpread: .greatestFiniteMagnitude,
      preferRawDepth: true,
      patchRadius: depthPlacementPatchRadius
    ) else { return nil }
    guard Float(sample.nearClusterCount) >= Float(sample.sampleCount) * depthNearClusterMinFraction else { return nil }
    guard let normalized = normalizedImagePoint(for: point, frame: frame) else { return nil }

    // The near cluster is used for EVERY placement, not only at a boundary.
    // Even on a flat face the patch covers real area, and the far half of it
    // biases a whole-patch median away from the camera - measured as points
    // sitting a median 20-29mm behind the surface.
    let usedNearCluster = sample.spread > depthPatchMaxSpread
    let position = unprojectDepth(normalizedImagePoint: normalized, depth: sample.nearClusterMedian, frame: frame)
    return (position: position, sample: sample, usedNearCluster: usedNearCluster)
  }

  private func updateCandidate(frame: ARFrame) {
    let point = reticlePoint()

    // Source order: the LiDAR depth measurement at the reticle, then real
    // scanned geometry, then a detected plane's own polygon, then a plane
    // extrapolation as the last resort.
    if let depthCandidate = depthCandidatePosition(frame: frame, at: point) {
      updateCandidate(
        frame: frame,
        point: point,
        worldPosition: depthCandidate.position,
        source: depthCandidate.usedNearCluster ? "lidarDepthNearSurface" : "lidarDepth",
        raycastAnchor: nil,
        // The EXACT sample this position came from, so support/confidence
        // describe the measurement that was actually used rather than a
        // separately-taken one that can disagree about whether depth existed.
        placementSample: depthCandidate.sample,
        placementWasBoundary: depthCandidate.usedNearCluster
      )
      return
    }

    var meshHitPosition: SIMD3<Float>?
    var results: [ARRaycastResult] = []
    var source: String

    if let meshPosition = meshRaycastPosition(at: point) {
      meshHitPosition = meshPosition
      source = "sceneMesh"
    } else {
      results = arView.raycast(from: point, allowing: .existingPlaneGeometry, alignment: .any)
      source = "existingPlaneGeometry"
      if results.isEmpty {
        results = arView.raycast(from: point, allowing: .estimatedPlane, alignment: .any)
        source = "estimatedPlane"
      }
    }

    if let meshHitPosition = meshHitPosition {
      updateCandidate(
        frame: frame,
        point: point,
        worldPosition: meshHitPosition,
        source: source,
        raycastAnchor: nil
      )
      return
    }

    guard let hit = results.first else {
      latestCandidate = nil
      latestCandidateHostAnchor = nil
      quickStabilityTracker.reset()
      strictStabilityTracker.reset()
      lastCandidateSource = nil
      if lastLoggedCandidateSignature != nil {
        lastLoggedCandidateSignature = nil
        print("[IOS-AR-CANDIDATE] state=SEARCHING (no raycast hit)")
      }
      return
    }

    let worldPosition = SIMD3<Float>(hit.worldTransform.columns.3.x, hit.worldTransform.columns.3.y, hit.worldTransform.columns.3.z)
    updateCandidate(
      frame: frame,
      point: point,
      worldPosition: worldPosition,
      source: source,
      raycastAnchor: hit.anchor
    )
  }

  // Shared tail of the candidate pipeline: every source (scanned mesh,
  // detected plane, plane extrapolation) is validated identically from here
  // on, so no source can bypass a check the others have to pass.
  private func updateCandidate(
    frame: ARFrame,
    point: CGPoint,
    worldPosition: SIMD3<Float>,
    source: String,
    raycastAnchor: ARAnchor?,
    placementSample: DepthSample? = nil,
    placementWasBoundary: Bool = false
  ) {
    if source != lastCandidateSource {
      // A raycast landing on real scanned geometry vs. a detected plane vs. an
      // extrapolated one is a different physical hypothesis about the
      // candidate, not a continuation of the same one. Without this, a run of
      // samples that silently crossed a source change could satisfy the
      // stability check while actually blending two different guesses -
      // source A -> B -> A reading as "one stable point." Restart both windows
      // whenever the source changes so every stability judgment is made on
      // same-source evidence only.
      quickStabilityTracker.reset()
      strictStabilityTracker.reset()
      lastCandidateSource = source
    }

    // Both trackers see every sample regardless of this frame's depth
    // support, so whichever gate ends up mattering (depth toggles frame to
    // frame near a corner) already has a full window of history by the time
    // it's consulted.
    let isStable = quickStabilityTracker.push(worldPosition)
    let isStableStrict = strictStabilityTracker.push(worldPosition)

    var hasDepthSupport = false
    var depthAgrees = true
    var depthConfidence: Float = 0
    var depthVariance: Float?
    var raycastDepthDelta: Float?
    var depthUnprojectedWorldPosition: SIMD3<Float>?
    var depthDisagreement: Float?
    var depthValidationPassed = true
    var depthValidationReason = "noDepthReading"

    // Sampled with the spread limit lifted purely so the boundary case can be
    // recognised and logged; `depthResult` below re-applies the real limit.
    let rawDepthReading = isLiDARDevice
      ? sampleDepth(frame: frame, at: point, cameraTransform: frame.camera.transform, maxSpread: .greatestFiniteMagnitude)
      : nil
    let isNearBoundary = (rawDepthReading?.spread ?? 0) > depthPatchMaxSpread
    let cameraPositionForDepth = SIMD3<Float>(
      frame.camera.transform.columns.3.x,
      frame.camera.transform.columns.3.y,
      frame.camera.transform.columns.3.z
    )
    let candidateDistance = simd_distance(cameraPositionForDepth, worldPosition)

    if let placementSample = placementSample {
      // THE FIX. The candidate came from this sample, so its own confidence and
      // spread are what describe it. Taking a second, differently-parameterised
      // sample here (5x5 vs 3x3, smoothed vs raw, 30mm spread cap vs none) is
      // what let a depth-placed corner point report depthSupport=false and
      // confidence=0.00, and made depthOK vacuously true - skipping the
      // agreement gate in exactly the case where depth had been used.
      //
      // Note the agreement figure below is near-tautological for a depth
      // placement: the position was built from this reading, so it necessarily
      // matches. That is honest rather than useful - it is reported, not relied
      // on. The real protection for these candidates is the stability window,
      // the mesh-support requirement, and the near-cluster fraction test inside
      // depthCandidatePosition, all of which the boundary flag keeps strict.
      hasDepthSupport = true
      depthConfidence = placementSample.confidence
      depthVariance = placementSample.spread
      if let normPoint = normalizedImagePoint(for: point, frame: frame) {
        depthUnprojectedWorldPosition = unprojectDepth(
          normalizedImagePoint: normPoint,
          depth: placementSample.nearClusterMedian,
          frame: frame
        )
      }
      let cameraPosition = SIMD3<Float>(frame.camera.transform.columns.3.x, frame.camera.transform.columns.3.y, frame.camera.transform.columns.3.z)
      raycastDepthDelta = abs(placementSample.nearClusterMedian - simd_distance(cameraPosition, worldPosition))
      depthAgrees = true
      depthDisagreement = depthUnprojectedWorldPosition.map { simd_distance($0, worldPosition) }
      depthValidationPassed = true
      depthValidationReason = String(format: "placementSampleReused(confidence=%.2f spread=%.0fmm boundary=%@)",
                                     placementSample.confidence, placementSample.spread * 1000,
                                     placementWasBoundary ? "true" : "false")
    } else if isLiDARDevice, let depthResult = sampleDepth(frame: frame, at: point, cameraTransform: frame.camera.transform) {
      hasDepthSupport = true
      depthConfidence = depthResult.confidence
      depthVariance = depthResult.spread
      let cameraPosition = SIMD3<Float>(frame.camera.transform.columns.3.x, frame.camera.transform.columns.3.y, frame.camera.transform.columns.3.z)
      let raycastDistance = simd_distance(cameraPosition, worldPosition)
      let delta = abs(depthResult.medianDepth - raycastDistance)
      raycastDepthDelta = delta
      let tolerance = max(0.02, raycastDistance * 0.05)
      depthAgrees = delta <= tolerance

      // Diagnostic-only cross-check, not yet used to place the candidate.
      // LiDAR time-of-flight depth isn't fooled by on-screen reflections the
      // way the RGB-feature-based plane raycast above can be, so unprojecting
      // the reticle's actual measured depth is a plausible fix for exactly
      // the reflective-surface failure mode this module keeps hitting. It is
      // deliberately NOT switched to be the primary candidate position yet:
      // an axis/sign mistake in the unprojection math would place candidates
      // at confidently-wrong locations instead of just refusing to confirm,
      // which is a worse failure mode than the current one. Surfaced here so
      // real-device DIAG data can confirm it converges with the raycast
      // position on trustworthy surfaces before it's ever trusted to drive
      // confirmation.
      if let normPoint = normalizedImagePoint(for: point, frame: frame) {
        depthUnprojectedWorldPosition = unprojectDepth(normalizedImagePoint: normPoint, depth: depthResult.medianDepth, frame: frame)
      }

      // OPTION B GATE (trusted patch): the candidate stays exactly where the
      // ray/mesh put it; a large disagreement only refuses confirmation.
      if let depthPosition = depthUnprojectedWorldPosition {
        let disagreement = simd_distance(depthPosition, worldPosition)
        depthDisagreement = disagreement
        depthValidationPassed = disagreement <= depthValidationMaxDisagreement
        depthValidationReason = depthValidationPassed
          ? "agreesWithDepth"
          : String(format: "depthDisagreement=%.0fmm>%.0fmm", disagreement * 1000, depthValidationMaxDisagreement * 1000)
      }
    } else if let raw = rawDepthReading {
      // Boundary patch: the median describes no single surface, so it is not
      // used. The NEAREST reading is still a real measurement of the closer
      // surface, and a candidate sitting well behind it is behind the thing
      // being aimed at - exactly the measured P3/P4 case.
      let behindBy = candidateDistance - raw.nearestDepth
      depthDisagreement = behindBy
      depthValidationPassed = behindBy <= depthBehindSurfaceTolerance
      depthValidationReason = depthValidationPassed
        ? "withinNearestSurface"
        : String(format: "behindNearestSurfaceBy=%.0fmm>%.0fmm", behindBy * 1000, depthBehindSurfaceTolerance * 1000)
    }

    // On a LiDAR device a point may only be CONFIRMED when its position came
    // from an actual depth measurement. This is the gap that let P3/P4 through
    // before: they were confirmed on frames where no trustworthy depth reading
    // existed, using a ray hit that turned out to be 112mm and 158mm behind
    // the corner. The candidate still renders from the ray fallback so the
    // officer can see where they are aiming; it simply cannot be placed until
    // LiDAR can see the surface.
    let positionFromDepth = source.hasPrefix("lidarDepth")
    if isLiDARDevice && !positionFromDepth {
      depthValidationPassed = false
      depthValidationReason = "noDepthMeasurementForPlacement(source=\(source))"
    }

    // On LiDAR devices, a hit with no depth backing (overwhelmingly the
    // corner case - see the header comment) gets one more physical check:
    // is there actually reconstructed scene mesh anywhere near it? If scene
    // reconstruction hasn't scanned this area yet, an estimatedPlane guess
    // there has nothing real corroborating it at all.
    // An edge/corner placement keeps the mesh-support requirement it had when
    // the second sample used to report no depth support - see
    // ARCandidate.placementPatchWasBoundary.
    let requiresMeshSupport = isLiDARDevice && (!hasDepthSupport || placementWasBoundary)
    let meshSupport = requiresMeshSupport ? hasMeshSupport(near: worldPosition, frame: frame) : true
    let stableSampleCount = hasDepthSupport ? quickStabilityTracker.sampleCount : strictStabilityTracker.sampleCount
    let anchorIsMature = isAnchorMature(raycastAnchor)

    let candidate = ARCandidate(
      worldPosition: worldPosition,
      source: source,
      hasDepthSupport: hasDepthSupport,
      depthAgrees: depthAgrees,
      depthConfidence: depthConfidence,
      depthVariance: depthVariance,
      raycastDepthDelta: raycastDepthDelta,
      depthUnprojectedWorldPosition: depthUnprojectedWorldPosition,
      meshSupport: meshSupport,
      requiresMeshSupport: requiresMeshSupport,
      isStable: isStable,
      isStableStrict: isStableStrict,
      stableSampleCount: stableSampleCount,
      depthDisagreement: depthDisagreement,
      depthValidationPassed: depthValidationPassed,
      placementPatchWasBoundary: placementWasBoundary,
      anchorIsMature: anchorIsMature
    )
    latestCandidate = candidate
    latestCandidateHostAnchor = raycastAnchor

    // Full ray-vs-depth evidence for one frame, at 2Hz. This is the line that
    // tells us WHERE the ~150mm corner discrepancy comes from: a coordinate
    // conversion error would show up on flat faces too, an edge-sampling
    // artefact shows up only when nearBoundary=true, and a genuine geometry
    // difference shows up with a clean patch and a large disagreement.
    let depthLogNow = CACurrentMediaTime()
    if verboseDepthDiagnostics, depthLogNow - lastDepthCheckLogTime >= 0.5 {
      lastDepthCheckLogTime = depthLogNow
      let camera = frame.camera
      let cameraPosition = cameraPositionForDepth
      let euler = camera.eulerAngles
      let depthText = rawDepthReading.map {
        String(format: "median=%.0fmm nearest=%.0fmm spread=%.0fmm confidence=%.2f", $0.medianDepth * 1000, $0.nearestDepth * 1000, $0.spread * 1000, $0.confidence)
      } ?? "none"
      let unprojText = depthUnprojectedWorldPosition.map {
        String(format: "(%.4f, %.4f, %.4f)", $0.x, $0.y, $0.z)
      } ?? "n/a"
      print(String(
        format: "[IOS-AR-DEPTHCHECK] reticlePx=(%.0f, %.0f) source=%@ rayWorld=(%.4f, %.4f, %.4f) rayDistance=%.0fmm depthSample=[%@] depthWorld=%@ rayVsDepth=%@ depthValid=%@ nearBoundary=%@ validation=%@ cameraPos=(%.4f, %.4f, %.4f) cameraEuler=(%.1f, %.1f, %.1f) tracking=%@",
        point.x, point.y, source,
        worldPosition.x, worldPosition.y, worldPosition.z,
        candidateDistance * 1000,
        depthText, unprojText,
        depthDisagreement.map { String(format: "%.0fmm", $0 * 1000) } ?? "n/a",
        hasDepthSupport ? "true" : "false",
        isNearBoundary ? "true" : "false",
        depthValidationReason,
        cameraPosition.x, cameraPosition.y, cameraPosition.z,
        euler.x * 180 / .pi, euler.y * 180 / .pi, euler.z * 180 / .pi,
        lastEmittedTrackingQuality ?? "unknown"
      ))
    }

    // Per the requested [IOS-AR-CANDIDATE] trace: log on every state/source/
    // depth-support transition (not every frame - that would be 30-60
    // lines/sec and unreadable) so the full history of what the pipeline
    // decided while aiming is visible after the fact, not just the final
    // confirm.
    let signature = "\(candidate.state.rawValue)|\(source)|\(hasDepthSupport)"
    if signature != lastLoggedCandidateSignature {
      lastLoggedCandidateSignature = signature
      print("[IOS-AR-CANDIDATE] state=\(candidate.state.rawValue) source=\(source) depthSupport=\(hasDepthSupport) depthAgrees=\(depthAgrees) meshSupport=\(meshSupport) confidence=\(depthConfidence) world=(\(worldPosition.x), \(worldPosition.y), \(worldPosition.z))")
    }

    if measurementState.points.last != nil, !measurementState.isComplete {
      renderer.updatePreviewEdge(to: worldPosition)
    } else {
      renderer.clearPreviewEdge()
    }
  }

  // Host resolution used to live here: a ladder that parented a confirmed
  // marker to a mesh anchor, the raycast's own anchor, or a detected plane,
  // plus the rehost/rescue machinery needed to survive ARKit removing or
  // re-parameterising those anchors. Device measurement retired all of it -
  // see MeasurementHostKind. Each point now owns one session-registered
  // ARAnchor, so there is no foreign host to lose, jump, or re-acquire.

  // Coarse, cheap corroboration that scene-reconstruction mesh has actually
  // been built near the candidate - deliberately NOT a precise triangle-
  // level membership test, which would mean walking every ARMeshAnchor's
  // full vertex buffer every frame (real per-frame cost this module already
  // had to cut elsewhere for responsiveness). An ARMeshAnchor's own
  // transform sits at the origin of the small scanned voxel it represents,
  // so "some mesh anchor's origin is within meshSupportRadius of the
  // candidate" is a genuine, unfaked signal that this area has actually
  // been scanned - not a fabricated pass. It only gates the no-depth-backing
  // path (see requiresMeshSupport in updateCandidate), where it matters most.
  private func hasMeshSupport(near worldPosition: SIMD3<Float>, frame: ARFrame) -> Bool {
    for anchor in frame.anchors {
      guard let meshAnchor = anchor as? ARMeshAnchor else { continue }
      let anchorPosition = SIMD3<Float>(meshAnchor.transform.columns.3.x, meshAnchor.transform.columns.3.y, meshAnchor.transform.columns.3.z)
      if simd_distance(anchorPosition, worldPosition) <= meshSupportRadius {
        return true
      }
    }
    return false
  }

  private struct DepthSample {
    let medianDepth: Float
    let confidence: Float
    let spread: Float
    // Closest valid depth in the patch. At a real edge the patch straddles two
    // surfaces and the median describes neither, but the nearest reading is
    // still a genuine measurement of the closer one - which is the surface the
    // officer is aiming at when they target a corner.
    let nearestDepth: Float
    // Median of just the NEAR cluster: the readings within
    // depthClusterTolerance of the nearest one. On a flat face every reading
    // belongs to it and this equals medianDepth. At a corner it is the depth
    // of the nearer surface - the object being measured - rather than a
    // meaningless average of the object and the background behind it.
    let nearClusterMedian: Float
    let nearClusterCount: Int
    let sampleCount: Int
  }

  // Maps a point in ARView point-space (e.g. the reticle) to a normalized
  // [0,1]x[0,1] point in the camera image's own coordinate space, via
  // ARFrame.displayTransform - the single source of truth for this
  // conversion, shared by both the depth-buffer lookup (sampleDepth) and the
  // camera-intrinsics unprojection (unprojectDepth) below, so they can never
  // silently disagree about where the reticle actually points in the image.
  private func normalizedImagePoint(for point: CGPoint, frame: ARFrame) -> CGPoint? {
    let viewportSize = arView.bounds.size
    guard viewportSize.width > 0, viewportSize.height > 0 else { return nil }
    let orientation = window?.windowScene?.interfaceOrientation ?? .portrait
    let displayTransform = frame.displayTransform(for: orientation, viewportSize: viewportSize)
    let normalizedReticle = CGPoint(x: point.x / viewportSize.width, y: point.y / viewportSize.height)
    return normalizedReticle.applying(displayTransform.inverted())
  }

  // Diagnostic-only: unprojects a normalized image point + measured depth
  // into a world-space position via camera intrinsics, independent of the
  // plane-fitting raycast. See the call site's comment for why this isn't
  // yet trusted as the primary candidate source. Pixel coordinates are in
  // the camera image's own resolution (frame.camera.imageResolution), which
  // is what the intrinsics matrix is defined relative to - NOT the lower-
  // resolution depth buffer's pixel grid used in sampleDepth.
  private func unprojectDepth(normalizedImagePoint: CGPoint, depth: Float, frame: ARFrame) -> SIMD3<Float> {
    let intrinsics = frame.camera.intrinsics
    let resolution = frame.camera.imageResolution
    let pixelX = Float(normalizedImagePoint.x) * Float(resolution.width)
    let pixelY = Float(normalizedImagePoint.y) * Float(resolution.height)
    let fx = intrinsics.columns.0.x
    let fy = intrinsics.columns.1.y
    let cx = intrinsics.columns.2.x
    let cy = intrinsics.columns.2.y
    let cameraX = (pixelX - cx) / fx * depth
    let cameraY = (pixelY - cy) / fy * depth
    // Image space is +Y down and +Z into the scene; ARKit camera space is
    // +Y up and -Z into the scene, so both axes flip on the way across.
    // (Only X carries straight through.) Getting the Y flip wrong puts the
    // point mirrored about the optical axis, which is why this conversion is
    // spelled out rather than left implicit.
    let cameraSpacePoint = SIMD4<Float>(cameraX, -cameraY, -depth, 1)
    let worldPoint = frame.camera.transform * cameraSpacePoint
    return SIMD3<Float>(worldPoint.x, worldPoint.y, worldPoint.z)
  }

  // A 5x5 patch is sampled (not a single pixel) and reduced with the median,
  // which is robust to the odd noisy outlier pixel while staying local
  // enough not to blend in depth from a neighboring, unrelated surface near
  // a real corner. Pixels below medium confidence are excluded before the
  // median is taken.
  // Is each CONFIRMED point actually on the physical surface, or floating in
  // front of / behind it?
  //
  // A point can be perfectly world-locked (its anchor holds it to within a
  // millimetre) and still look like it slides across the object while the
  // camera moves - that is what a point placed at the WRONG DEPTH does, and it
  // is indistinguishable from drift by eye. This measures the difference
  // directly: project the point back to the screen, read what LiDAR says the
  // distance to the real surface at that pixel is, and compare it with the
  // distance to the point itself.
  //
  //   surfaceMinusPointMm ~= 0   -> the point is on the surface (correct)
  //   surfaceMinusPointMm > 0    -> the real surface is FURTHER than the point:
  //                                 the point floats in front of the object
  //   surfaceMinusPointMm < 0    -> the real surface is NEARER than the point:
  //                                 the point sits behind/inside the object
  //
  // Diagnostic only - it never moves a point. spreadMm is reported too, since
  // a patch straddling a real edge mixes two surfaces and a large spread means
  // the comparison itself is less trustworthy at that pixel.
  // STAGE 1: promoted out from behind verboseDepthDiagnostics - this is the
  // ONLY scene-relative metric in the module, and therefore the ground truth
  // for "is the point still on the object". World-space drift numbers cannot
  // answer that: a marker holding a fixed world coordinate reports zero drift
  // while the physical scene slides away underneath it.
  //
  // CORRECTED DIAGNOSTIC. The previous version sampled with the default
  // preferRawDepth:false (frame.smoothedSceneDepth) while PLACEMENT samples
  // with preferRawDepth:true (frame.sceneDepth). Smoothed depth is temporally
  // filtered, so while the device moves it reports where the surface WAS -
  // comparing one stream against a point derived from the other produced a
  // systematic negative bias that was mistaken for a corner placement error.
  // Both streams are now measured and the difference between them is reported
  // as streamBias, so the size of that artefact is evidence rather than
  // assumption.
  //
  // IMPORTANT, and the reason the mesh/plane columns exist: the candidate is
  // itself derived from raw depth, so "candidate == raw depth" is circular and
  // proves nothing about physical accuracy. Independent-ish corroboration has
  // to come from geometry built by a DIFFERENT pipeline:
  //   sceneMesh  - temporally fused reconstruction; shares the LiDAR sensor but
  //                not the per-frame depth map. Partial independence.
  //   plane      - fitted from visual features plus depth. Reported for
  //                COMPARISON ONLY; the Stage 2a run measured plane hits 124-141mm
  //                past the corner, so it is not treated as truth anywhere.
  // Neither is fully independent. The only true ground truth is a physical
  // tape measurement, which no log can substitute for.
  private func logConfirmedPointDepthAgreement(frame: ARFrame) {
    guard verboseDepthDiagnostics, isLiDARDevice else { return }
    let cameraPosition = SIMD3<Float>(
      frame.camera.transform.columns.3.x,
      frame.camera.transform.columns.3.y,
      frame.camera.transform.columns.3.z
    )

    for point in measurementState.points {
      let index = point.number - 1
      guard let live = renderer.livePosition(at: index) else { continue }
      guard let screen = arView.project(live), arView.bounds.contains(screen) else { continue }
      let pointRange = simd_distance(cameraPosition, live)

      // Same stream as placement.
      guard let raw = sampleDepth(
        frame: frame, at: screen, cameraTransform: frame.camera.transform,
        maxSpread: .greatestFiniteMagnitude, preferRawDepth: true, patchRadius: depthPlacementPatchRadius
      ) else { continue }
      // The old stream, kept purely to quantify the artefact.
      let smoothed = sampleDepth(
        frame: frame, at: screen, cameraTransform: frame.camera.transform,
        maxSpread: .greatestFiniteMagnitude, preferRawDepth: false
      )

      // Independent geometry along the ray THROUGH THIS MARKER (not the
      // reticle), so the comparison is about this point rather than wherever
      // the officer happens to be aiming now.
      var meshText = "meshAvailable=false meshSurface=n/a candidateVsMesh=n/a"
      if let ray = arView.ray(through: screen) {
        let hits = arView.scene.raycast(
          origin: ray.origin, direction: ray.direction,
          length: meshRaycastMaxDistance, query: .nearest, mask: .sceneUnderstanding
        )
        if let hit = hits.first {
          let meshRange = simd_distance(cameraPosition, hit.position)
          meshText = String(format: "meshAvailable=true meshSurface=%.0fmm candidateVsMesh=%+.0fmm",
                            meshRange * 1000, (meshRange - pointRange) * 1000)
        }
      }
      var planeText = "planeSurface=n/a candidateVsPlane=n/a"
      if let hit = arView.raycast(from: screen, allowing: .existingPlaneGeometry, alignment: .any).first {
        let t = hit.worldTransform.columns.3
        let planeRange = simd_distance(cameraPosition, SIMD3<Float>(t.x, t.y, t.z))
        planeText = String(format: "planeSurface=%.0fmm candidateVsPlane=%+.0fmm",
                           planeRange * 1000, (planeRange - pointRange) * 1000)
      }

      let rawDelta = raw.medianDepth - pointRange
      let smoothedText = smoothed.map {
        String(format: "smoothedSurface=%.0fmm surfaceMinusPoint_SMOOTHED=%+.0fmm streamBias=%+.0fmm",
               $0.medianDepth * 1000, ($0.medianDepth - pointRange) * 1000,
               ($0.medianDepth - raw.medianDepth) * 1000)
      } ?? "smoothedSurface=n/a surfaceMinusPoint_SMOOTHED=n/a streamBias=n/a"

      // Time since this point was confirmed, and how mature the reconstruction
      // around it has become. Together with candidateVsMesh these separate:
      //   A - mesh was immature; disagreement SHRINKS as maturity rises
      //   B - candidate is wrong; disagreement PERSISTS or grows despite maturity
      let tSinceConfirm = pointConfirmTime[index].map { CACurrentMediaTime() - $0 } ?? -1
      let maturity = meshMaturity(near: live, frame: frame)

      print(String(
        format: "[IOS-AR-POINTDEPTH] P%d tSinceConfirm=%.1fs screen=(%.0f, %.0f) pointRange=%.0fmm rawSurface=%.0fmm surfaceMinusPoint_RAW=%+.0fmm %@ rawSpread=%.0fmm nearBoundary=%@ rawValidPixels=%d nearClusterN=%d %@ %@ meshAnchorsNear=%d meshOldestAge=%.1fs meshTotalUpdates=%d markerCarried=%.1fmm",
        point.number, tSinceConfirm, screen.x, screen.y,
        pointRange * 1000, raw.medianDepth * 1000, rawDelta * 1000,
        smoothedText,
        raw.spread * 1000,
        raw.spread > depthPatchMaxSpread ? "true" : "false",
        raw.sampleCount, raw.nearClusterCount,
        meshText, planeText,
        maturity.count, maturity.oldestAge, maturity.totalUpdates,
        simd_distance(live, point.worldPosition) * 1000
      ))
    }
  }

  // `preferRawDepth` picks frame.sceneDepth over frame.smoothedSceneDepth.
  // Smoothed depth is filtered over time, so while the device is moving it
  // reports where the surface WAS - a lag that reads as the point sitting
  // behind the object and shifting against it as the camera moves. Placement
  // therefore uses the raw stream; the corroboration paths keep the smoothed
  // one, where the temporal filtering is an advantage.
  //
  // `patchRadius` is 1 (a 3x3 patch) for placement and 2 (5x5) elsewhere. The
  // depth map is far lower resolution than the camera image, so a 5x5 patch
  // covers a wide area of the scene and on a slanted surface its median is
  // pulled towards the far side.
  private func sampleDepth(
    frame: ARFrame,
    at point: CGPoint,
    cameraTransform: simd_float4x4,
    maxSpread: Float = 0.03,
    preferRawDepth: Bool = false,
    patchRadius: Int = 2
  ) -> DepthSample? {
    let depthSource = preferRawDepth
      ? (frame.sceneDepth ?? frame.smoothedSceneDepth)
      : (frame.smoothedSceneDepth ?? frame.sceneDepth)
    guard let depthData = depthSource else { return nil }
    guard let normalizedImagePoint = normalizedImagePoint(for: point, frame: frame) else { return nil }

    let depthMap = depthData.depthMap
    let depthWidth = CVPixelBufferGetWidth(depthMap)
    let depthHeight = CVPixelBufferGetHeight(depthMap)
    let centerX = Int((normalizedImagePoint.x * CGFloat(depthWidth)).rounded())
    let centerY = Int((normalizedImagePoint.y * CGFloat(depthHeight)).rounded())

    CVPixelBufferLockBaseAddress(depthMap, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(depthMap, .readOnly) }
    guard let depthBase = CVPixelBufferGetBaseAddress(depthMap) else { return nil }
    let depthRowBytes = CVPixelBufferGetBytesPerRow(depthMap)

    var confidenceBase: UnsafeMutableRawPointer?
    var confidenceRowBytes = 0
    if let confidenceMap = depthData.confidenceMap {
      CVPixelBufferLockBaseAddress(confidenceMap, .readOnly)
      confidenceBase = CVPixelBufferGetBaseAddress(confidenceMap)
      confidenceRowBytes = CVPixelBufferGetBytesPerRow(confidenceMap)
    }
    defer {
      if let confidenceMap = depthData.confidenceMap {
        CVPixelBufferUnlockBaseAddress(confidenceMap, .readOnly)
      }
    }

    var validSamples: [Float] = []
    var totalConsidered = 0

    for dy in -patchRadius...patchRadius {
      for dx in -patchRadius...patchRadius {
        let x = centerX + dx
        let y = centerY + dy
        guard x >= 0, x < depthWidth, y >= 0, y < depthHeight else { continue }
        totalConsidered += 1

        if let confidenceBase = confidenceBase {
          let confidence = confidenceBase.load(fromByteOffset: y * confidenceRowBytes + x, as: UInt8.self)
          guard confidence >= UInt8(ARConfidenceLevel.medium.rawValue) else { continue }
        }

        let depthPointer = depthBase
          .advanced(by: y * depthRowBytes + x * MemoryLayout<Float32>.size)
          .assumingMemoryBound(to: Float32.self)
        let depthValue = depthPointer.pointee
        guard depthValue.isFinite, depthValue > 0 else { continue }
        validSamples.append(depthValue)
      }
    }

    guard totalConsidered > 0, validSamples.count >= totalConsidered * 4 / 10 else { return nil }

    validSamples.sort()
    // A patch straddling a real corner/edge discontinuity mixes depths from
    // two different surfaces (e.g. the block's face and the surface behind
    // it) - the median alone can look like a perfectly reasonable single
    // value even though it isn't a real measurement of anything. A wide
    // spread is the signature of that straddle, so treat it as "no reliable
    // depth here" rather than silently trusting a blended median.
    let spread = validSamples.last! - validSamples.first!
    guard spread <= maxSpread else { return nil }
    let median = validSamples[validSamples.count / 2]
    let confidence = Float(validSamples.count) / Float(totalConsidered)
    let nearest = validSamples.first!
    let nearCluster = validSamples.filter { $0 <= nearest + depthClusterTolerance }
    return DepthSample(
      medianDepth: median,
      confidence: confidence,
      spread: spread,
      nearestDepth: nearest,
      nearClusterMedian: nearCluster[nearCluster.count / 2],
      nearClusterCount: nearCluster.count,
      sampleCount: validSamples.count
    )
  }

  // MARK: - Overlay (screen-projected edge labels, matching Android's
  // onOverlayUpdate.labels contract so the existing App.js floating-badge UI
  // works unmodified for iOS)

  private func emitOverlayUpdate(frame: ARFrame) {
    let trackingQuality = isTrackingSufficient(frame: frame) ? "GOOD" : "INSUFFICIENT"
    lastEmittedTrackingQuality = trackingQuality

    let candidate = latestCandidate
    let candidateScreen = candidate.flatMap { arView.project($0.worldPosition) }

    var labels: [[String: Any]] = []
    let points = measurementState.points
    let edgeIds = ["L12", "L23", "L34", "L41"]
    for i in 0..<(points.count - 1 > 0 ? points.count - 1 : 0) {
      appendEdgeLabel(
        id: edgeIds[i],
        distanceFrom: points[i].worldPosition, distanceTo: points[i + 1].worldPosition,
        screenFrom: renderer.livePosition(at: i) ?? points[i].worldPosition,
        screenTo: renderer.livePosition(at: i + 1) ?? points[i + 1].worldPosition,
        into: &labels
      )
    }
    if points.count == 4 {
      appendEdgeLabel(
        id: edgeIds[3],
        distanceFrom: points[3].worldPosition, distanceTo: points[0].worldPosition,
        screenFrom: renderer.livePosition(at: 3) ?? points[3].worldPosition,
        screenTo: renderer.livePosition(at: 0) ?? points[0].worldPosition,
        into: &labels
      )
    } else if let last = points.last, let candidate = candidate {
      let lastLive = renderer.livePosition(at: points.count - 1) ?? last.worldPosition
      appendEdgeLabel(
        id: "PREVIEW",
        distanceFrom: lastLive, distanceTo: candidate.worldPosition,
        screenFrom: lastLive, screenTo: candidate.worldPosition,
        into: &labels
      )
    }

    var diagnostics: [String: Any] = [
      "reticleX": reticlePoint().x,
      "reticleY": reticlePoint().y,
      "viewportW": arView.bounds.width,
      "viewportH": arView.bounds.height,
      "rotation": (window?.windowScene?.interfaceOrientation ?? .portrait).rawValue,
      "lockedPointCount": points.count,
      "raycastValid": candidate != nil,
      "candidateState": candidate?.state.rawValue ?? "SEARCHING",
    ]
    if let candidate = candidate {
      diagnostics["candidateX"] = candidate.worldPosition.x
      diagnostics["candidateY"] = candidate.worldPosition.y
      diagnostics["candidateZ"] = candidate.worldPosition.z
      diagnostics["candidateSource"] = candidate.source
      diagnostics["depthPatchValid"] = candidate.hasDepthSupport
      diagnostics["depthConfidence"] = candidate.depthConfidence
      if let variance = candidate.depthVariance {
        diagnostics["depthVariance"] = variance
      }
      if let delta = candidate.raycastDepthDelta {
        diagnostics["raycastDepthDistance"] = delta
      }
      diagnostics["meshSupport"] = candidate.meshSupport
      diagnostics["temporalStability"] = candidate.hasDepthSupport ? candidate.isStable : candidate.isStableStrict
      diagnostics["anchorMature"] = candidate.anchorIsMature
      if let unprojected = candidate.depthUnprojectedWorldPosition {
        diagnostics["depthUnprojX"] = unprojected.x
        diagnostics["depthUnprojY"] = unprojected.y
        diagnostics["depthUnprojZ"] = unprojected.z
        diagnostics["depthUnprojDelta"] = simd_distance(unprojected, candidate.worldPosition)
      }
    }
    for point in points {
      diagnostics["p\(point.number)"] = [point.x, point.y, point.z]
      // Host + how far the visual has been carried from the confirmed
      // coordinate, so the DIAG HUD can show attachment quality per point
      // without opening Xcode. Existing keys are untouched, so App.js keeps
      // rendering exactly what it already did.
      let index = point.number - 1
      if let host = renderer.hostKind(at: index) {
        diagnostics["p\(point.number)Host"] = host.rawValue
      }
      if let live = renderer.livePosition(at: index) {
        diagnostics["p\(point.number)CarriedMm"] = simd_distance(live, point.worldPosition) * 1000
      }
    }

    var payload: [String: Any] = [
      "trackingQuality": trackingQuality,
      "candidateValid": candidate?.isConfirmable ?? false,
      "labels": labels,
      "diagnostics": diagnostics,
    ]
    if let candidateScreen = candidateScreen {
      payload["candidateScreenX"] = candidateScreen.x
      payload["candidateScreenY"] = candidateScreen.y
    }

    onOverlayUpdate(payload)
  }

  // distanceFrom/distanceTo drive the displayed measurement value (from the
  // immutable stored/locked points, per item 14 - never recomputed from live
  // tracking for a confirmed edge). screenFrom/screenTo drive only where the
  // label is drawn on screen, from the live-tracked marker positions, so the
  // label doesn't visually detach from a line that ARKit has since corrected.
  private func appendEdgeLabel(
    id: String,
    distanceFrom: SIMD3<Float>, distanceTo: SIMD3<Float>,
    screenFrom: SIMD3<Float>, screenTo: SIMD3<Float>,
    into labels: inout [[String: Any]]
  ) {
    let screenMidpoint = (screenFrom + screenTo) / 2
    guard let screen = arView.project(screenMidpoint) else { return }
    let distance = simd_distance(distanceFrom, distanceTo)

    // On-screen angle of the edge, for a label that reads along the line
    // (matching Apple Measure) instead of always sitting horizontal. Derived
    // from the SAME projected 2D endpoints as the line/marker rendering, not
    // recomputed from 3D vectors, so it can never disagree with what's
    // actually drawn on screen.
    var rotationDegrees: Double = 0
    if let fromScreen = arView.project(screenFrom), let toScreen = arView.project(screenTo) {
      let dx = Double(toScreen.x - fromScreen.x)
      let dy = Double(toScreen.y - fromScreen.y)
      if dx != 0 || dy != 0 {
        var degrees = atan2(dy, dx) * 180 / .pi
        // Keep text upright rather than upside-down - mirror anything
        // outside [-90, 90] by 180 degrees, same as Apple Measure's labels.
        if degrees > 90 { degrees -= 180 }
        if degrees < -90 { degrees += 180 }
        rotationDegrees = degrees
      }
    }

    labels.append([
      "id": id,
      "text": String(format: "%.2f m", distance),
      "distance": distance,
      "screenX": screen.x,
      "screenY": screen.y,
      "rotationDegrees": rotationDegrees,
      "visible": true,
    ])
  }

  private func isTrackingSufficient(frame: ARFrame) -> Bool {
    if case .normal = frame.camera.trackingState {
      return true
    }
    return false
  }
}
