import ARKit
import RealityKit
import UIKit

// RealityKit-only rendering for confirmed P1-P4 markers and the connecting
// edges. No custom OpenGL/Metal - mirrors what Android's GLES20 renderer
// draws, but as world-space RealityKit entities.
//
// There is deliberately no 3D-rendered marker for the live (unconfirmed)
// candidate - App.js already draws a screen-centered reticle that changes
// color with candidateValid (from onOverlayUpdate), matching Apple Measure's
// single-reticle UX. A separately-rendered 3D candidate sphere, projected
// back to 2D every frame, visibly lags/diverges from that fixed reticle under
// normal hand movement - two dots that don't quite coincide reads as "the
// point is unstable" even when the underlying raycast is fine.
//
// STAGE 2a: every confirmed marker is hosted on its OWN ARAnchor, registered
// with session.add(anchor:). Markers are never parented to ARKit's own mesh or
// plane anchors. See MeasurementHostKind for the device measurements behind
// that, and addLockedPoint for the mechanism.
final class GraniteARRenderer {
  private weak var arView: ARView?

  // anchorEntity is what RealityKit tracks; markerEntity is the sphere the
  // user actually sees. World positions are always read from markerEntity.
  // ownAnchor is this marker's own session-registered anchor - always present
  // now, which is what makes every marker correctable by ARKit.
  private struct LockedMarker {
    var anchorEntity: AnchorEntity
    var markerEntity: ModelEntity
    let ownAnchor: ARAnchor
    // The confirmed measurement coordinate, kept purely so drift diagnostics
    // can report how far the VISUAL has been carried from where the point was
    // placed. Never written back to, and never used to move the marker.
    let lockedWorldPosition: SIMD3<Float>
    // Last world position read while the marker's anchor was actually
    // resolved, refreshed every frame. Used for edges and diagnostics while a
    // transform is momentarily unresolvable, so nothing leaps across the room.
    var lastGoodWorldPosition: SIMD3<Float>
    // True once RealityKit has confirmed it bound the entity to ownAnchor.
    // See addLockedPoint's binding note.
    var didBind: Bool
    // Frames spent waiting for that binding, for the self-heal below.
    var framesAwaitingBind: Int
  }
  private var markers: [LockedMarker] = []

  // Per-marker motion accounting. For a marker parented to an anchor, its
  // world position can ONLY change when that anchor's transform changes, so
  // the per-frame path length measured here IS the correction the anchor
  // imposed - the number Stage 3 is judged on.
  struct MarkerMotionStats {
    var cumulativePathLength: Float = 0
    var movingFrameCount: Int = 0
    var maxFrameMove: Float = 0
    var totalFrames: Int = 0
    var hostChangeCount: Int = 0
  }
  private var motionStats: [MarkerMotionStats] = []
  // Below this a per-frame delta is float noise in the transform chain, not
  // movement; counted into the path length but not into movingFrameCount.
  private let motionNoiseFloor: Float = 0.001

  private struct Edge {
    let fromIndex: Int
    let toIndex: Int
    let anchor: AnchorEntity
    let dashModels: [ModelEntity]
  }
  private var edges: [Edge] = []
  private var previewEdgeAnchor: AnchorEntity?
  private var previewDashModels: [ModelEntity] = []

  private let pointRadius: Float = 0.008
  private let lineThickness: Float = 0.0025
  // Dash/gap pattern for the ruler-tape line style (Test-images/IMG_8971.MP4)
  // - see positionDashes for how these are used.
  private let dashSegmentsPerEdge = 14
  private let dashFillRatio: Float = 0.55
  // How long AnchorEntity(anchor:) is given to resolve before the marker is
  // rebuilt on a world-space entity instead. See the binding note in
  // addLockedPoint for why this exists rather than being assumed unnecessary.
  private let bindGraceFrames = 30

  init(arView: ARView) {
    self.arView = arView
  }

  // MARK: - Confirmed points

  static let pointAnchorName = "GraniteARPoint"

  // Each confirmed point gets its OWN ARAnchor, registered with the session,
  // and the marker is parented to that anchor alone.
  //
  // Measured on an iPhone 16 Pro Max over two runs (2026-09-18, 636 host
  // samples), the three available strategies behaved like this:
  //
  //   parented to ARMeshAnchor ... never binds at all. isAnchored was false in
  //       364 of 364 samples. Because the position-refresh and jump-rescue
  //       loops here are guarded by isAnchored, those markers were also
  //       invisible to this module's own diagnostics - their cached position
  //       stayed frozen at the confirmation coordinate, so drift always read
  //       0.0mm. That is how an earlier session concluded "no drift" while the
  //       points visibly would not stay put.
  //   parented to ARPlaneAnchor .. binds (80/80), but the plane's own
  //       transform accumulated 119-133mm of travel across a single run, with
  //       single updates up to 13.8mm, and dragged its markers up to 20.4mm
  //       from where they were placed.
  //   own session ARAnchor ...... the most stable thing measured: 0.5-2.1mm
  //       total movement over the same session, same points, same camera
  //       motion. ARKit corrects it when it refines the world map; nothing
  //       re-parameterises it the way plane merges and reconstruction passes
  //       re-parameterise ARKit's own anchors.
  //
  // BINDING NOTE: AnchorEntity(anchor:) is asynchronous - RealityKit has to
  // match the anchor in the session before the entity resolves, and on this
  // device it demonstrably never resolves for mesh anchors. Rather than assume
  // our own anchors behave differently, a marker that has not bound within
  // bindGraceFrames is rebuilt on a world-space entity driven by
  // applyAnchorCorrection, which is the path measured working. Either way the
  // marker stays visible and stays correctable.
  func addLockedPoint(_ worldPosition: SIMD3<Float>) {
    guard let arView = arView else { return }

    let arAnchor = ARAnchor(name: Self.pointAnchorName, transform: translationTransform(worldPosition))
    arView.session.add(anchor: arAnchor)

    let markerEntity = makeMarkerEntity()
    let anchorEntity = AnchorEntity(anchor: arAnchor)
    // The anchor IS the point, so the marker sits at the anchor's origin.
    markerEntity.position = .zero
    anchorEntity.addChild(markerEntity)
    arView.scene.addAnchor(anchorEntity)

    markers.append(LockedMarker(
      anchorEntity: anchorEntity,
      markerEntity: markerEntity,
      ownAnchor: arAnchor,
      lockedWorldPosition: worldPosition,
      lastGoodWorldPosition: worldPosition,
      didBind: false,
      framesAwaitingBind: 0
    ))
    motionStats.append(MarkerMotionStats())
  }

  private func makeMarkerEntity() -> ModelEntity {
    let mesh = MeshResource.generateSphere(radius: pointRadius)
    return ModelEntity(mesh: mesh, materials: [UnlitMaterial(color: .white)])
  }

  private func translationTransform(_ position: SIMD3<Float>) -> simd_float4x4 {
    var transform = matrix_identity_float4x4
    transform.columns.3 = SIMD4<Float>(position.x, position.y, position.z, 1)
    return transform
  }

  // Rebuilds a marker that never bound onto a plain world-space entity at its
  // last known-good position. The session anchor is KEPT: it is still what
  // ARKit corrects, and applyAnchorCorrection now drives the entity directly.
  // Returns the indices rebuilt, for the caller to log.
  @discardableResult
  func resolveBindings() -> [Int] {
    guard let arView = arView else { return [] }
    var rebuilt: [Int] = []

    for index in markers.indices where !markers[index].didBind {
      if markers[index].anchorEntity.isAnchored {
        markers[index].didBind = true
        continue
      }
      markers[index].framesAwaitingBind += 1
      guard markers[index].framesAwaitingBind >= bindGraceFrames else { continue }

      let preserved = markers[index].lastGoodWorldPosition
      markers[index].anchorEntity.removeFromParent()

      let replacementMarker = makeMarkerEntity()
      let replacementAnchor = AnchorEntity(world: preserved)
      replacementAnchor.addChild(replacementMarker)
      arView.scene.addAnchor(replacementAnchor)

      markers[index].anchorEntity = replacementAnchor
      markers[index].markerEntity = replacementMarker
      markers[index].didBind = true
      motionStats[index].hostChangeCount += 1
      rebuilt.append(index)
    }
    return rebuilt
  }

  // ARKit refined where it believes this marker's own anchor physically is.
  // When RealityKit has bound the entity to that anchor it moves the marker
  // itself and this must not fight it; when it has not (the world-space
  // fallback above), this is what carries the correction through. Only the
  // visual moves either way - the measurement's stored coordinates are never
  // rewritten.
  func applyAnchorCorrection(_ anchor: ARAnchor) {
    guard let index = markers.firstIndex(where: { $0.ownAnchor.identifier == anchor.identifier }) else { return }
    let corrected = SIMD3<Float>(
      anchor.transform.columns.3.x,
      anchor.transform.columns.3.y,
      anchor.transform.columns.3.z
    )
    guard corrected.x.isFinite, corrected.y.isFinite, corrected.z.isFinite else { return }
    guard !markers[index].anchorEntity.isAnchored else { return }
    markers[index].anchorEntity.setPosition(corrected, relativeTo: nil)
    markers[index].lastGoodWorldPosition = corrected
  }

  // MARK: - Diagnostic accessors (read-only)

  func hostKind(at index: Int) -> MeasurementHostKind? {
    guard index >= 0, index < markers.count else { return nil }
    return .sessionAnchor
  }

  func hostAnchorIdentifier(at index: Int) -> UUID? {
    guard index >= 0, index < markers.count else { return nil }
    return markers[index].ownAnchor.identifier
  }

  func lockedPosition(at index: Int) -> SIMD3<Float>? {
    guard index >= 0, index < markers.count else { return nil }
    return markers[index].lockedWorldPosition
  }

  // The world position ARKit currently believes this marker's own anchor is
  // at - independent of whether RealityKit has bound the visual to it.
  func anchorPosition(at index: Int) -> SIMD3<Float>? {
    guard index >= 0, index < markers.count else { return nil }
    let t = markers[index].ownAnchor.transform.columns.3
    return SIMD3<Float>(t.x, t.y, t.z)
  }

  func isAnchored(at index: Int) -> Bool {
    guard index >= 0, index < markers.count else { return false }
    return markers[index].anchorEntity.isAnchored
  }

  func didBind(at index: Int) -> Bool {
    guard index >= 0, index < markers.count else { return false }
    return markers[index].didBind
  }

  func motionStats(at index: Int) -> MarkerMotionStats? {
    guard index >= 0, index < motionStats.count else { return nil }
    return motionStats[index]
  }

  // MARK: - Confirmed edges
  //
  // Endpoints are looked up live from the tracked point entities every frame
  // (refreshEdges, called once per frame from GraniteARView) rather than
  // baked in once, so an edge stays visually attached to both its ARKit-
  // corrected endpoints instead of freezing at their positions at edge-
  // creation time.

  func addEdge(fromIndex: Int, toIndex: Int) {
    guard let arView = arView else { return }
    let anchor = AnchorEntity(world: .zero)
    let dashModels = (0..<dashSegmentsPerEdge).map { _ -> ModelEntity in
      let mesh = MeshResource.generateBox(size: SIMD3<Float>(lineThickness, lineThickness, 1))
      let model = ModelEntity(mesh: mesh, materials: [UnlitMaterial(color: .white)])
      anchor.addChild(model)
      return model
    }
    arView.scene.addAnchor(anchor)
    edges.append(Edge(fromIndex: fromIndex, toIndex: toIndex, anchor: anchor, dashModels: dashModels))
    refreshEdges()
  }

  // Direct, unambiguous evidence of whether a CONFIRMED marker is actually
  // moving, as distinct from the live candidate/reticle (which is supposed
  // to move while aiming - that's not a bug). Called periodically (not every
  // frame - see the caller) and compares each marker's current live world
  // position against the last time it was checked, returning only markers
  // that moved more than `threshold`.
  private var lastCheckedPositions: [SIMD3<Float>] = []

  // `sinceLockedMeters` is the total distance the VISUAL has been carried from
  // the coordinate that was confirmed - with one anchor per point that is
  // exactly ARKit's accumulated correction to that anchor, and nothing else.
  func checkForDrift(threshold: Float = 0.001) -> [(index: Int, deltaMeters: Float, sinceLockedMeters: Float, host: MeasurementHostKind)] {
    var drifted: [(index: Int, deltaMeters: Float, sinceLockedMeters: Float, host: MeasurementHostKind)] = []
    while lastCheckedPositions.count < markers.count {
      lastCheckedPositions.append(markers[lastCheckedPositions.count].lastGoodWorldPosition)
    }
    for i in 0..<markers.count {
      guard let current = livePosition(at: i) else { continue }
      let delta = simd_distance(current, lastCheckedPositions[i])
      if delta > threshold {
        drifted.append((
          index: i,
          deltaMeters: delta,
          sinceLockedMeters: simd_distance(current, markers[i].lockedWorldPosition),
          host: .sessionAnchor
        ))
      }
      lastCheckedPositions[i] = current
    }
    return drifted
  }

  // Current live world position of a confirmed marker. While the entity is
  // resolved this is the live tracked position; while it is not, RealityKit's
  // reported transform is meaningless, so the last known-good position is
  // returned instead.
  func livePosition(at index: Int) -> SIMD3<Float>? {
    guard index >= 0, index < markers.count else { return nil }
    if markers[index].anchorEntity.isAnchored {
      return markers[index].markerEntity.position(relativeTo: nil)
    }
    return markers[index].lastGoodWorldPosition
  }

  // Refreshes the known-good cache for every resolved marker, then redraws the
  // edges from those same positions. Called once per frame.
  func refreshEdges() {
    while motionStats.count < markers.count { motionStats.append(MarkerMotionStats()) }

    for index in markers.indices where markers[index].anchorEntity.isAnchored {
      let position = markers[index].markerEntity.position(relativeTo: nil)
      if position.x.isFinite, position.y.isFinite, position.z.isFinite {
        // Measured BEFORE lastGoodWorldPosition is overwritten, so this is the
        // movement the anchor imposed on the marker this frame.
        let frameMove = simd_distance(position, markers[index].lastGoodWorldPosition)
        motionStats[index].totalFrames += 1
        motionStats[index].cumulativePathLength += frameMove
        if frameMove > motionNoiseFloor {
          motionStats[index].movingFrameCount += 1
          motionStats[index].maxFrameMove = max(motionStats[index].maxFrameMove, frameMove)
        }
        markers[index].lastGoodWorldPosition = position
      }
    }

    for edge in edges {
      guard let from = livePosition(at: edge.fromIndex),
            let to = livePosition(at: edge.toIndex) else { continue }
      positionDashes(edge.dashModels, from: from, to: to)
    }
  }

  // Lays `models.count` short dash segments end-to-end along from->to, each
  // filling `dashFillRatio` of its own even slice of the total distance and
  // leaving a gap for the rest - the ruler-tape line style from Apple
  // Measure (Test-images/IMG_8971.MP4), instead of one continuous bar. Fixed
  // segment count rather than a fixed physical dash length so the pattern
  // stays proportional whether the edge is 5cm or several metres.
  private func positionDashes(_ models: [ModelEntity], from: SIMD3<Float>, to: SIMD3<Float>) {
    let delta = to - from
    let totalDistance = simd_length(delta)
    guard totalDistance > 0.0001, !models.isEmpty else {
      models.forEach { $0.isEnabled = false }
      return
    }

    let direction = delta / totalDistance
    let orientation = simd_quatf(from: SIMD3<Float>(0, 0, 1), to: direction)
    let cellLength = totalDistance / Float(models.count)
    let dashLength = cellLength * dashFillRatio

    for (index, model) in models.enumerated() {
      let cellStart = Float(index) * cellLength
      let dashCenterDistance = cellStart + dashLength / 2
      model.isEnabled = true
      model.position = from + direction * dashCenterDistance
      model.orientation = orientation
      model.scale = SIMD3<Float>(1, 1, dashLength)
    }
  }

  // MARK: - Live preview edge (last locked point -> current candidate; removed once P4 locks)
  // The "from" endpoint here is the live tracked entity position for the last
  // locked point (not its frozen creation-time position), so the preview
  // segment starts exactly where that point is currently rendered.

  func updatePreviewEdge(to: SIMD3<Float>?) {
    guard let arView = arView, let to = to, let from = livePosition(at: markers.count - 1) else {
      clearPreviewEdge()
      return
    }

    if previewEdgeAnchor == nil {
      let anchor = AnchorEntity(world: .zero)
      previewDashModels = (0..<dashSegmentsPerEdge).map { _ -> ModelEntity in
        let mesh = MeshResource.generateBox(size: SIMD3<Float>(lineThickness, lineThickness, 1))
        let model = ModelEntity(mesh: mesh, materials: [UnlitMaterial(color: .white)])
        anchor.addChild(model)
        return model
      }
      arView.scene.addAnchor(anchor)
      previewEdgeAnchor = anchor
    }

    positionDashes(previewDashModels, from: from, to: to)
  }

  func clearPreviewEdge() {
    previewEdgeAnchor?.removeFromParent()
    previewEdgeAnchor = nil
    previewDashModels = []
  }

  // MARK: - Reset (resetToken - remove every entity, nothing stale survives)

  func reset() {
    markers.forEach { marker in
      marker.anchorEntity.removeFromParent()
      arView?.session.remove(anchor: marker.ownAnchor)
    }
    markers.removeAll()
    lastCheckedPositions.removeAll()
    motionStats.removeAll()

    edges.forEach { $0.anchor.removeFromParent() }
    edges.removeAll()

    clearPreviewEdge()
  }
}
