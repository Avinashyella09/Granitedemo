package expo.modules.granitear

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Color
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.opengl.GLES11Ext
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import android.view.Surface
import android.view.ViewGroup

import androidx.core.app.ActivityCompat

import com.google.ar.core.Anchor
import com.google.ar.core.ArCoreApk
import com.google.ar.core.Config
import com.google.ar.core.Coordinates2d
import com.google.ar.core.Frame
import com.google.ar.core.HitResult
import com.google.ar.core.Plane
import com.google.ar.core.Pose
import com.google.ar.core.Session
import com.google.ar.core.TrackingState
import com.google.ar.core.exceptions.CameraNotAvailableException
import com.google.ar.core.exceptions.UnavailableApkTooOldException
import com.google.ar.core.exceptions.UnavailableArcoreNotInstalledException
import com.google.ar.core.exceptions.UnavailableDeviceNotCompatibleException
import com.google.ar.core.exceptions.UnavailableSdkTooOldException
import com.google.ar.core.exceptions.UnavailableUserDeclinedInstallationException

import expo.modules.kotlin.AppContext
import expo.modules.kotlin.viewevent.EventDispatcher
import expo.modules.kotlin.views.ExpoView

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import kotlin.math.abs
import kotlin.math.sqrt

class GraniteARView(
  context: Context,
  appContext: AppContext
) : ExpoView(context, appContext) {

  // ============================================================
  // EVENTS
  // ============================================================

  private val onStatus by EventDispatcher()
  private val onPointSelected by EventDispatcher()
  private val onOverlayUpdate by EventDispatcher()
  private val onSteadyChange by EventDispatcher()

  // ============================================================
  // AR SESSION
  // ============================================================

  private var session: Session? = null
  private var sessionRunning = false

  private lateinit var glSurfaceView: GLSurfaceView
  private lateinit var renderer: ARRenderer

  // ============================================================
  // LATEST AR FRAME
  // ============================================================

  @Volatile
  private var latestFrame: Frame? = null

  // Set from UI thread, consumed on GL thread
  @Volatile
  private var pendingTap = false

  // ============================================================
  // DISPLAY GEOMETRY
  // ============================================================

  @Volatile
  private var displayWidth = 1

  @Volatile
  private var displayHeight = 1

  @Volatile
  private var displayRotation = Surface.ROTATION_0

  // ============================================================
  // MEASUREMENT ANCHORS
  // ============================================================

  /**
   * Holds one confirmed measurement point.
   *
   * [anchor]        : ARCore surface-attached anchor created ONLY via
   *                   HitResult.createAnchor() — NEVER via Session.createAnchor(freePose)
   *                   which creates a free-floating anchor that drifts.
   * [number]        : 1-based index (P1..P4).
   * [lastKnownPose] : Last valid pose snapshot — used when anchor is PAUSED so the
   *                   point stays visible instead of disappearing temporarily.
   */
  private data class MeasurementAnchor(
    val anchor: Anchor?,
    val number: Int,
    val lockedPose: Pose
  )

  private val measurementAnchors = mutableListOf<MeasurementAnchor>()

  /**
   * Frozen world positions captured once P4 is confirmed.
   * Never recomputed from live anchor poses — used for final measurement and photo.
   */
  private var frozenPositions: List<FloatArray>? = null

  // ============================================================
  // CANDIDATE STATE (written/read on GL thread only)
  // ============================================================

  @Volatile
  private var candidateValid = false

  @Volatile
  private var candidatePose: Pose? = null

  // Throttle overlay updates to ~30 fps
  private var lastOverlayEmitTime = 0L

  // ============================================================
  // CAMERA BACKGROUND TEXTURE
  // ============================================================

  private var cameraTextureId = 0

  // ============================================================
  // ACCELEROMETER MOTION GATE
  // ============================================================
  //
  // This device has no LiDAR, so measurement accuracy depends entirely on
  // ARCore visual-inertial tracking staying converged. Placing a point while
  // the phone is in motion is the single largest source of error, so every
  // point placement is gated on an accelerometer-derived steadiness signal.
  //
  // Signal chain (raw TYPE_ACCELEROMETER -> steady/moving decision):
  //   1. Low-pass the raw reading to estimate the gravity vector.
  //   2. Linear acceleration = raw - gravity (removes the constant 9.81 m/s2).
  //   3. Magnitude of that residual is the instantaneous motion energy.
  //   4. Exponential moving average smooths out single-sample sensor noise.
  //   5. Schmitt trigger (two thresholds) converts it to a binary state, so a
  //      value hovering near one threshold cannot flap the popup on and off.
  //
  // Translation (panning/walking) shows up directly as linear acceleration.
  // Rotation in place also registers, because the gravity low-pass lags the
  // true gravity direction while the phone turns, leaving a residual.

  private var sensorManager: SensorManager? = null
  private var accelerometer: Sensor? = null

  /** Low-pass smoothing factor for the gravity estimate (higher = slower). */
  private val gravityAlpha = 0.8f

  /** EMA smoothing factor for the motion magnitude (higher = smoother/slower). */
  private val motionAlpha = 0.72f

  /** Above this smoothed magnitude (m/s2) the phone is declared MOVING. */
  @Volatile
  private var movingThreshold = 0.55f

  /** Below this smoothed magnitude (m/s2) the phone may settle back to STEADY. */
  @Volatile
  private var steadyThreshold = 0.28f

  /** Magnitude must stay below [steadyThreshold] this long before STEADY returns. */
  @Volatile
  private var steadyHoldMs = 650L

  /** Running gravity estimate; seeded from the first sample to avoid a startup spike. */
  private val gravity = FloatArray(3)
  private var gravityInitialized = false

  /** Smoothed linear-acceleration magnitude, in m/s2. */
  @Volatile
  private var motionMagnitude = 0f

  /** Peak magnitude seen since the current MOVING episode began (diagnostics only). */
  @Volatile
  private var motionPeak = 0f

  /**
   * True when the phone is steady enough to place a point.
   *
   * Starts optimistically true: the detector flips it within ~100 ms of real
   * motion, which is better UX than flashing the hold-steady popup every time
   * the AR screen opens.
   */
  @Volatile
  private var isSteady = true

  /** Timestamp when the magnitude first dropped below [steadyThreshold]. */
  private var calmSince = 0L

  /** Whether the accelerometer is actually delivering samples on this device. */
  @Volatile
  private var motionSensorAvailable = false

  private val motionListener = object : SensorEventListener {
    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onSensorChanged(event: SensorEvent) {
      if (event.sensor?.type != Sensor.TYPE_ACCELEROMETER) return
      handleAccelerometerSample(event.values)
    }
  }

  // ============================================================
  // INITIALIZATION
  // ============================================================

  init {
    setBackgroundColor(Color.BLACK)
    isClickable = true
    setupGLSurfaceView()
    setupMotionSensor()
    createSession()
  }

  // ============================================================
  // MOTION SENSOR LIFECYCLE
  // ============================================================

  private fun setupMotionSensor() {
    val manager = context.getSystemService(Context.SENSOR_SERVICE) as? SensorManager
    sensorManager = manager
    accelerometer = manager?.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)

    if (accelerometer == null) {
      // No accelerometer: fail open rather than blocking measurement outright.
      motionSensorAvailable = false
      isSteady = true
      postStatus(
        "MOTION_SENSOR_UNAVAILABLE",
        "No accelerometer on this device. Steadiness checking is disabled."
      )
    }
  }

  private fun startMotionSensor() {
    val manager = sensorManager ?: return
    val sensor = accelerometer ?: return

    // SENSOR_DELAY_GAME is ~50 Hz: fast enough to catch a flick of the wrist
    // without the battery cost of SENSOR_DELAY_FASTEST.
    val registered = manager.registerListener(
      motionListener,
      sensor,
      SensorManager.SENSOR_DELAY_GAME
    )

    motionSensorAvailable = registered
    if (registered) {
      // Reset the filter so a stale gravity estimate from a previous session
      // cannot produce a phantom motion spike on the first sample.
      gravityInitialized = false
      motionMagnitude = 0f
      motionPeak = 0f
      calmSince = 0L
      isSteady = true
    }
  }

  private fun stopMotionSensor() {
    try {
      sensorManager?.unregisterListener(motionListener)
    } catch (_: Exception) {}
    motionSensorAvailable = false
  }

  /**
   * Called on the sensor thread for every accelerometer sample.
   * Updates [motionMagnitude] and flips [isSteady] through a Schmitt trigger.
   */
  private fun handleAccelerometerSample(values: FloatArray) {
    if (values.size < 3) return

    if (!gravityInitialized) {
      // Seed gravity with the first sample. Starting from zero would make the
      // first residual ~9.81 m/s2 and falsely report violent motion.
      gravity[0] = values[0]
      gravity[1] = values[1]
      gravity[2] = values[2]
      gravityInitialized = true
      return
    }

    gravity[0] = gravityAlpha * gravity[0] + (1f - gravityAlpha) * values[0]
    gravity[1] = gravityAlpha * gravity[1] + (1f - gravityAlpha) * values[1]
    gravity[2] = gravityAlpha * gravity[2] + (1f - gravityAlpha) * values[2]

    val lx = values[0] - gravity[0]
    val ly = values[1] - gravity[1]
    val lz = values[2] - gravity[2]

    val raw = sqrt((lx * lx + ly * ly + lz * lz).toDouble()).toFloat()
    val smoothed = motionAlpha * motionMagnitude + (1f - motionAlpha) * raw
    motionMagnitude = smoothed

    val now = System.currentTimeMillis()

    if (isSteady) {
      if (smoothed > movingThreshold) {
        // Upper threshold crossed: measurement stops immediately.
        isSteady = false
        motionPeak = smoothed
        calmSince = 0L
        onMotionStateChanged(false, smoothed)
      }
    } else {
      if (smoothed > motionPeak) motionPeak = smoothed

      if (smoothed < steadyThreshold) {
        if (calmSince == 0L) calmSince = now
        if (now - calmSince >= steadyHoldMs) {
          // Held below the lower threshold long enough, so resuming is safe.
          isSteady = true
          calmSince = 0L
          onMotionStateChanged(true, smoothed)
        }
      } else {
        // Still moving: restart the hold timer.
        calmSince = 0L
      }
    }
  }

  /** Fires on every STEADY <-> MOVING transition. Hops to the UI thread to emit. */
  private fun onMotionStateChanged(steady: Boolean, magnitude: Float) {
    if (!steady) {
      // Stop the in-flight measurement: drop any queued tap and invalidate the
      // candidate so the reticle cannot lock onto a surface while shaking.
      pendingTap = false
      candidateValid = false
      candidatePose = null
    }

    val peak = motionPeak
    post {
      onSteadyChange(
        mapOf(
          "steady" to steady,
          "state" to (if (steady) "STEADY" else "MOVING"),
          "magnitude" to magnitude,
          "peakMagnitude" to peak,
          "movingThreshold" to movingThreshold,
          "steadyThreshold" to steadyThreshold,
          "pointsPlaced" to measurementAnchors.size,
          "sensorAvailable" to motionSensorAvailable
        )
      )
    }

    if (steady) {
      postStatus("STEADY", "Phone is steady. You can place the next point.")
    } else {
      postStatus("MOVING", "Hold the phone steady. Measurement paused.")
    }
  }

  /** True when a point may be placed right now. */
  private fun canMeasure(): Boolean = isSteady || !motionSensorAvailable

  /** Allows JS to tune motion sensitivity without a rebuild. */
  fun setMotionSensitivity(moving: Float, steady: Float, holdMs: Int) {
    if (moving > 0f) movingThreshold = moving
    if (steady > 0f) steadyThreshold = steady
    if (holdMs > 0) steadyHoldMs = holdMs.toLong()
  }

  // ============================================================
  // SETUP GLSURFACEVIEW
  // ============================================================

  private fun setupGLSurfaceView() {
    glSurfaceView = GLSurfaceView(context)
    glSurfaceView.setEGLContextClientVersion(2)
    glSurfaceView.preserveEGLContextOnPause = true

    renderer = ARRenderer()
    glSurfaceView.setRenderer(renderer)
    glSurfaceView.renderMode = GLSurfaceView.RENDERMODE_CONTINUOUSLY

    // Touch events are consumed but NOT used for AR point placement.
    // All AR hit-testing uses the screen center exclusively.
    glSurfaceView.setOnTouchListener { _, _ -> true }

    addView(
      glSurfaceView,
      LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.MATCH_PARENT
      )
    )
  }

  // ============================================================
  // CENTER-RETICLE TAP  (called from React Native via module prop)
  // ============================================================

  /** Triggers point placement at screen center on the next GL frame. */
  fun triggerCenterTap() {
    pendingTap = true
  }

  // ============================================================
  // CREATE ARCORE SESSION
  // ============================================================

  private fun createSession() {
    val activity = appContext.currentActivity
    if (activity == null) {
      postStatus("ERROR", "No active Android Activity")
      return
    }

    try {
      if (
        ActivityCompat.checkSelfPermission(
          activity,
          Manifest.permission.CAMERA
        ) != PackageManager.PERMISSION_GRANTED
      ) {
        ActivityCompat.requestPermissions(
          activity,
          arrayOf(Manifest.permission.CAMERA),
          1001
        )
        postStatus("CAMERA_PERMISSION", "Camera permission requested")
        return
      }

      val availability = ArCoreApk.getInstance().checkAvailability(activity)
      if (!availability.isSupported) {
        postStatus("AR_NOT_SUPPORTED", "This device does not support ARCore")
        return
      }

      try {
        ArCoreApk.getInstance().requestInstall(activity, true)
      } catch (_: UnavailableArcoreNotInstalledException) {
        postStatus("ARCORE_INSTALL", "Google Play Services for AR is not installed")
        return
      } catch (_: UnavailableUserDeclinedInstallationException) {
        postStatus("ARCORE_INSTALL_DECLINED", "ARCore installation was declined")
        return
      }

      session = Session(activity)
      configureSession()
      postStatus("READY", "ARCore session created")
      startPreview()

    } catch (_: UnavailableDeviceNotCompatibleException) {
      postStatus("AR_ERROR", "Device is not ARCore compatible")
    } catch (_: UnavailableSdkTooOldException) {
      postStatus("AR_ERROR", "ARCore SDK is too old")
    } catch (_: UnavailableApkTooOldException) {
      postStatus("AR_ERROR", "ARCore APK is too old")
    } catch (e: Exception) {
      postStatus("AR_ERROR", e.message ?: "Failed to create AR session")
    }
  }

  private fun configureSession() {
    val currentSession = session ?: return
    val config = Config(currentSession)
    config.planeFindingMode = Config.PlaneFindingMode.HORIZONTAL_AND_VERTICAL
    config.updateMode = Config.UpdateMode.BLOCKING
    config.focusMode = Config.FocusMode.AUTO

    if (currentSession.isDepthModeSupported(Config.DepthMode.AUTOMATIC)) {
      config.depthMode = Config.DepthMode.AUTOMATIC
    }
    currentSession.configure(config)
  }

  private fun updateDisplayGeometry(width: Int, height: Int) {
    if (width <= 0 || height <= 0) return
    displayWidth = width
    displayHeight = height
    val rotation = glSurfaceView.display?.rotation ?: Surface.ROTATION_0
    displayRotation = rotation

    try {
      session?.setDisplayGeometry(rotation, width, height)
    } catch (_: Exception) {}
  }

  private fun startPreview() {
    val currentSession = session ?: return
    try {
      updateDisplayGeometry(displayWidth, displayHeight)
      currentSession.resume()
      sessionRunning = true
      glSurfaceView.onResume()
      startMotionSensor()
      postStatus("TRACKING", "AR tracking started")
    } catch (_: CameraNotAvailableException) {
      sessionRunning = false
      postStatus("CAMERA_ERROR", "Camera is not available")
    }
  }

  override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
    super.onSizeChanged(w, h, oldw, oldh)
    displayWidth = w
    displayHeight = h
    glSurfaceView.post { updateDisplayGeometry(w, h) }
  }

  override fun onAttachedToWindow() {
    super.onAttachedToWindow()
    if (session == null) {
      createSession()
    } else if (!sessionRunning) {
      try {
        updateDisplayGeometry(width, height)
        session?.resume()
        sessionRunning = true
        glSurfaceView.onResume()
        startMotionSensor()
      } catch (_: Exception) {}
    }
  }

  override fun onDetachedFromWindow() {
    // Unregister first: an accelerometer callback firing after the GL surface
    // is torn down would emit events for a view that is on its way out.
    stopMotionSensor()
    glSurfaceView.onPause()
    try { session?.pause() } catch (_: Exception) {}
    sessionRunning = false
    latestFrame = null
    super.onDetachedFromWindow()
  }

  // ============================================================
  // PROCESS TAP — always hits against screen CENTER
  // ============================================================

  /**
   * Called on the GL thread from onDrawFrame.
   *
   * KEY: hitTest coordinates are always (displayWidth/2, displayHeight/2).
   * Synchronizes with the candidatePose validated in the current frame.
   */
  private fun processPendingTap(frame: Frame) {
    if (!pendingTap) return
    pendingTap = false

    // Motion gate: refuse to lock a point while the phone is moving. A point
    // placed mid-motion inherits the tracking error of that instant and
    // silently corrupts every dimension derived from it.
    if (!canMeasure()) {
      postStatus("NOT_STEADY", "Phone is moving. Hold it steady, then place the point.")
      return
    }

    if (frame.camera.trackingState != TrackingState.TRACKING) {
      postStatus("NO_HIT", "Move phone slowly to scan the block surface first.")
      return
    }

    if (measurementAnchors.size >= 4) {
      postStatus("COMPLETE", "All 4 points placed. Press Reset to start over.")
      return
    }

    val currentCand = candidatePose
    if (!candidateValid || currentCand == null) {
      postStatus("NO_HIT", "Unable to find a stable surface here. Aim target at a granite corner and try again.")
      return
    }

    try {
      val finalPose = currentCand
      val pointNumber = measurementAnchors.size + 1

      // Geometric validation for P4
      if (pointNumber == 4) {
        val msg = validateP4(finalPose)
        if (msg != null) {
          postStatus("POINT_REJECTED", msg)
          return
        }
      }

      val hits = frame.hitTest(displayWidth / 2.0f, displayHeight / 2.0f)
      val selectedHit = selectBestHit(hits)
      val anchor: Anchor? = try {
        selectedHit?.createAnchor()
      } catch (_: Exception) { null }

      // CRITICAL: Lock 3D coordinates at confirmation time (immutable lockedPose)
      measurementAnchors.add(
        MeasurementAnchor(
          anchor = anchor,
          number = pointNumber,
          lockedPose = finalPose
        )
      )

      // Log immutable point registration
      android.util.Log.i("AR-CONFIRM", "P$pointNumber locked at world=(${finalPose.tx()}, ${finalPose.ty()}, ${finalPose.tz()})")

      // Freeze positions once all 4 points are confirmed
      if (pointNumber == 4) {
        frozenPositions = measurementAnchors.map { m ->
          val p = getAnchorPose(m)
          floatArrayOf(p.tx(), p.ty(), p.tz())
        }
      }

      postPointSelected(
        finalPose.tx(),
        finalPose.ty(),
        finalPose.tz(),
        selectedHit?.trackable?.javaClass?.simpleName ?: "Plane"
      )

      when (pointNumber) {
        1 -> postStatus("POINT_SELECTED", "P1 locked. Now aim at bottom-front-right corner.")
        2 -> postStatus("POINT_SELECTED", "P2 locked. Now aim at top-front-right corner.")
        3 -> postStatus("POINT_SELECTED", "P3 locked. Now aim at top-back-right corner for depth.")
        4 -> postStatus("MEASUREMENT_COMPLETE", "All 4 corners locked. Block dimensions calculated.")
      }

    } catch (e: Exception) {
      postStatus("POINT_ERROR", e.message ?: "Unable to create measurement point")
    }
  }

  private fun getAnchorPose(m: MeasurementAnchor): Pose {
    val a = m.anchor
    if (a != null && a.trackingState == TrackingState.TRACKING) {
      return a.pose
    }
    return m.lockedPose
  }

  /**
   * Selects the physical surface hit closest to the camera along the center reticle ray.
   * Hits are already ordered by distance along the ray by ARCore.
   */
  private fun selectBestHit(hits: List<HitResult>): HitResult? {
    for (hit in hits) {
      if (hit.trackable.trackingState == TrackingState.TRACKING && hit.distance in 0.05f..8.0f) {
        return hit
      }
    }
    return null
  }

  /** Returns null if P4 is valid, rejection message string if invalid. */
  private fun validateP4(p4Pose: Pose): String? {
    val p1 = getAnchorPose(measurementAnchors[0])
    val p2 = getAnchorPose(measurementAnchors[1])
    val p3 = getAnchorPose(measurementAnchors[2])

    val v12 = floatArrayOf(p2.tx()-p1.tx(), p2.ty()-p1.ty(), p2.tz()-p1.tz())
    val v23 = floatArrayOf(p3.tx()-p2.tx(), p3.ty()-p2.ty(), p3.tz()-p2.tz())
    val v34 = floatArrayOf(p4Pose.tx()-p3.tx(), p4Pose.ty()-p3.ty(), p4Pose.tz()-p3.tz())

    val lenL = sqrt((v12[0]*v12[0]+v12[1]*v12[1]+v12[2]*v12[2]).toDouble()).toFloat()
    val lenH = sqrt((v23[0]*v23[0]+v23[1]*v23[1]+v23[2]*v23[2]).toDouble()).toFloat()
    val lenB = sqrt((v34[0]*v34[0]+v34[1]*v34[1]+v34[2]*v34[2]).toDouble()).toFloat()

    if (lenB < 0.05f)
      return "P4 is too close to P3. Aim at the top-back corner of the block."

    val maxFront = maxOf(lenL, lenH)
    if (maxFront > 0.01f && lenB > 3.0f * maxFront)
      return "P4 depth seems too large. Make sure you are aiming at the top-back corner."

    val dot12_34 = if (lenL>0f && lenB>0f) abs(v12[0]*v34[0]+v12[1]*v34[1]+v12[2]*v34[2])/(lenL*lenB) else 0f
    val dot23_34 = if (lenH>0f && lenB>0f) abs(v23[0]*v34[0]+v23[1]*v34[1]+v23[2]*v34[2])/(lenH*lenB) else 0f

    if (dot12_34 > 0.80f || dot23_34 > 0.80f)
      return "P4 is nearly parallel to an existing edge. Aim at the top-back corner."

    return null
  }

  // ============================================================
  // RESET
  // ============================================================

  fun resetMeasurement() {
    glSurfaceView.queueEvent {
      for (item in measurementAnchors) {
        try { item.anchor?.detach() } catch (_: Exception) {}
      }
      measurementAnchors.clear()
      frozenPositions = null
      pendingTap = false
      candidateValid = false
      candidatePose = null
      postStatus("RESET", "Measurement reset")
    }
  }

  private fun postStatus(status: String, message: String) {
    post {
      onStatus(mapOf("status" to status, "message" to message))
    }
  }

  private fun postPointSelected(x: Float, y: Float, z: Float, trackable: String) {
    post {
      onPointSelected(
        mapOf(
          "x" to x,
          "y" to y,
          "z" to z,
          "trackable" to trackable
        )
      )
    }
  }

  private fun postOverlayUpdate(
    trackingQuality: String,
    candidateValid: Boolean,
    candidateScreenX: Float,
    candidateScreenY: Float,
    labels: List<Map<String, Any>>,
    diagnostics: Map<String, Any?> = emptyMap()
  ) {
    // Steadiness rides along on every overlay frame (already throttled to
    // ~30 fps) so the UI can animate a live motion meter without a second
    // high-rate event stream. Discrete transitions still come via onSteadyChange.
    val steadyNow = canMeasure()
    val magnitudeNow = motionMagnitude

    post {
      onOverlayUpdate(
        mapOf(
          "trackingQuality" to trackingQuality,
          "candidateValid" to candidateValid,
          "candidateScreenX" to candidateScreenX,
          "candidateScreenY" to candidateScreenY,
          "labels" to labels,
          "steady" to steadyNow,
          "motionMagnitude" to magnitudeNow,
          "movingThreshold" to movingThreshold,
          "motionSensorAvailable" to motionSensorAvailable,
          "diagnostics" to diagnostics
        )
      )
    }
  }

  // ============================================================
  // OPENGL RENDERER
  // ============================================================

  private inner class ARRenderer : GLSurfaceView.Renderer {

    // Camera Shader
    private var cameraProgram = 0
    private var cameraPositionHandle = 0
    private var cameraTexCoordHandle = 0
    private var cameraTextureHandle = 0

    // Point Shader (iPhone Measure circular white points with soft edge)
    private var pointProgram = 0
    private var pointPositionHandle = 0
    private var pointColorHandle = 0
    private var pointSizeHandle = 0
    private var pointViewProjectionHandle = 0

    // Line Shader (Thin crisp white lines)
    private var lineProgram = 0
    private var linePositionHandle = 0
    private var lineColorHandle = 0
    private var lineViewProjectionHandle = 0

    // Geometry buffers
    private val cameraVertices = floatArrayOf(-1f, -1f, 1f, -1f, -1f, 1f, 1f, 1f)
    private val originalTexCoords = floatArrayOf(0f, 0f, 1f, 0f, 0f, 1f, 1f, 1f)
    private val transformedTexCoords = FloatArray(8)

    private lateinit var cameraVertexBuffer: FloatBuffer
    private lateinit var cameraTexCoordBuffer: FloatBuffer

    // Matrices
    private val viewMatrix = FloatArray(16)
    private val projectionMatrix = FloatArray(16)
    private val viewProjectionMatrix = FloatArray(16)


    override fun onSurfaceCreated(gl: javax.microedition.khronos.opengles.GL10?, config: javax.microedition.khronos.egl.EGLConfig?) {
      GLES20.glClearColor(0f, 0f, 0f, 1f)

      setupCameraShader()
      setupPointShader()
      setupLineShader()

      cameraVertexBuffer = createFloatBuffer(cameraVertices)
      cameraTexCoordBuffer = createFloatBuffer(originalTexCoords)
      setupCameraTexture()

      postStatus("GL_READY", "Camera renderer ready")
    }

    override fun onSurfaceChanged(gl: javax.microedition.khronos.opengles.GL10?, width: Int, height: Int) {
      GLES20.glViewport(0, 0, width, height)
      displayWidth = width
      displayHeight = height
      glSurfaceView.post { updateDisplayGeometry(width, height) }
    }

    override fun onDrawFrame(gl: javax.microedition.khronos.opengles.GL10?) {
      GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT or GLES20.GL_DEPTH_BUFFER_BIT)

      val currentSession = session ?: return
      if (!sessionRunning) return

      try {
        currentSession.setCameraTextureName(cameraTextureId)
        val frame = currentSession.update()
        latestFrame = frame

        processPendingTap(frame)

        val camera = frame.camera
        val trackingState = camera.trackingState

        if (trackingState != TrackingState.TRACKING) {
          drawCamera(frame)
          postOverlayUpdate(
            trackingQuality = "INSUFFICIENT",
            candidateValid = false,
            candidateScreenX = displayWidth / 2f,
            candidateScreenY = displayHeight / 2f,
            labels = emptyList()
          )
          return
        }

        camera.getViewMatrix(viewMatrix, 0)
        camera.getProjectionMatrix(projectionMatrix, 0, 0.01f, 100f)
        android.opengl.Matrix.multiplyMM(viewProjectionMatrix, 0, projectionMatrix, 0, viewMatrix, 0)

        // 1. Render camera background
        drawCamera(frame)

        // 2. Continuous candidate hit-test at SCREEN CENTER only (never touch coords)
        //    Suppressed entirely while the phone is moving, so the reticle goes
        //    dim and no preview line is drawn from a pose we do not trust.
        val steadyNow = canMeasure()
        val candidateHit = if (steadyNow) {
          selectBestHit(frame.hitTest(displayWidth / 2.0f, displayHeight / 2.0f))
        } else {
          null
        }
        candidateValid = (candidateHit != null)
        candidatePose = candidateHit?.hitPose
        val localCandidatePose = candidatePose

        // 4. Draw confirmed world-locked anchors and lines
        drawWorldMeasurements()

        // 5. Draw candidate dot & live preview segments (when points < 4 and candidate valid)
        val confirmedCount = measurementAnchors.size
        if (confirmedCount < 4 && localCandidatePose != null) {
          GLES20.glEnable(GLES20.GL_BLEND)
          GLES20.glBlendFunc(GLES20.GL_SRC_ALPHA, GLES20.GL_ONE_MINUS_SRC_ALPHA)

          val candVerts = floatArrayOf(
            localCandidatePose.tx(), localCandidatePose.ty(), localCandidatePose.tz()
          )
          drawPoints3D(candVerts, floatArrayOf(1f, 1f, 1f, 0.85f), 28.0f)

          if (confirmedCount in 1..2) {
            val lastPose = getAnchorPose(measurementAnchors.last())
            val lineVerts = floatArrayOf(
              lastPose.tx(), lastPose.ty(), lastPose.tz(),
              localCandidatePose.tx(), localCandidatePose.ty(), localCandidatePose.tz()
            )
            drawLines3D(lineVerts, floatArrayOf(1f, 1f, 1f, 0.65f), 2.5f)
          } else if (confirmedCount == 3) {
            val p3Pose = getAnchorPose(measurementAnchors[2])
            val p1Pose = getAnchorPose(measurementAnchors[0])
            val lineVerts = floatArrayOf(
              p3Pose.tx(), p3Pose.ty(), p3Pose.tz(),
              localCandidatePose.tx(), localCandidatePose.ty(), localCandidatePose.tz(),
              localCandidatePose.tx(), localCandidatePose.ty(), localCandidatePose.tz(),
              p1Pose.tx(), p1Pose.ty(), p1Pose.tz()
            )
            drawLines3D(lineVerts, floatArrayOf(1f, 1f, 1f, 0.65f), 2.5f)
          }

          GLES20.glDisable(GLES20.GL_BLEND)
        }

        // 6. Emit overlay update
        val now = System.currentTimeMillis()
        if (now - lastOverlayEmitTime > 33) {
          lastOverlayEmitTime = now
          buildAndEmitOverlay(localCandidatePose)
        }

      } catch (_: CameraNotAvailableException) {
        postStatus("CAMERA_ERROR", "Camera became unavailable")
      } catch (_: Exception) {}
    }

    // ==========================================================
    // BUILD OVERLAY LABELS & SCREEN PROJECTIONS
    // ==========================================================

    private fun buildAndEmitOverlay(currentCandidate: Pose?) {
      val labels = mutableListOf<Map<String, Any>>()

      val poses = if (frozenPositions != null) {
        frozenPositions!!.map { Pose.makeTranslation(it[0], it[1], it[2]) }
      } else {
        measurementAnchors.map { getAnchorPose(it) }
      }

      val formatDist: (Float) -> String = { d ->
        if (d < 1.0f) "${(d * 100).toInt()} cm" else String.format("%.2f m", d)
      }

      fun maybeAddLabel(pA: Pose, pB: Pose, id: String) {
        val mx = (pA.tx() + pB.tx()) / 2f
        val my = (pA.ty() + pB.ty()) / 2f
        val mz = (pA.tz() + pB.tz()) / 2f
        val dist = sqrt(
          ((pB.tx()-pA.tx()).let{it*it} + (pB.ty()-pA.ty()).let{it*it} + (pB.tz()-pA.tz()).let{it*it}).toDouble()
        ).toFloat()
        val screen = project3DToScreen(mx, my, mz) ?: return
        labels.add(mapOf(
          "id" to id, "text" to formatDist(dist), "distance" to dist,
          "screenX" to screen[0], "screenY" to screen[1], "visible" to true
        ))
      }

      if (poses.size >= 2) maybeAddLabel(poses[0], poses[1], "L12")
      if (poses.size >= 3) maybeAddLabel(poses[1], poses[2], "L23")
      if (poses.size >= 4) {
        maybeAddLabel(poses[2], poses[3], "L34")
        maybeAddLabel(poses[3], poses[0], "L41")
      }
      if (poses.isNotEmpty() && poses.size < 4 && currentCandidate != null) {
        maybeAddLabel(poses.last(), currentCandidate, "PREVIEW")
      }

      val candScreen = currentCandidate?.let {
        project3DToScreen(it.tx(), it.ty(), it.tz())
      }

      val diagnostics = mapOf(
        "reticleX" to (displayWidth / 2.0f),
        "reticleY" to (displayHeight / 2.0f),
        "viewportW" to displayWidth,
        "viewportH" to displayHeight,
        "rotation" to displayRotation,
        "candidateX" to (currentCandidate?.tx() ?: 0.0f),
        "candidateY" to (currentCandidate?.ty() ?: 0.0f),
        "candidateZ" to (currentCandidate?.tz() ?: 0.0f),
        "p1" to (if (poses.size >= 1) listOf(poses[0].tx(), poses[0].ty(), poses[0].tz()) else null),
        "p2" to (if (poses.size >= 2) listOf(poses[1].tx(), poses[1].ty(), poses[1].tz()) else null),
        "p3" to (if (poses.size >= 3) listOf(poses[2].tx(), poses[2].ty(), poses[2].tz()) else null),
        "p4" to (if (poses.size >= 4) listOf(poses[3].tx(), poses[3].ty(), poses[3].tz()) else null)
      )

      postOverlayUpdate(
        trackingQuality = "GOOD",
        candidateValid = candidateValid,
        candidateScreenX = candScreen?.get(0) ?: (displayWidth / 2f),
        candidateScreenY = candScreen?.get(1) ?: (displayHeight / 2f),
        labels = labels,
        diagnostics = diagnostics
      )
    }

    private fun project3DToScreen(x: Float, y: Float, z: Float): FloatArray? {
      val clipX = viewProjectionMatrix[0]*x + viewProjectionMatrix[4]*y + viewProjectionMatrix[8]*z + viewProjectionMatrix[12]
      val clipY = viewProjectionMatrix[1]*x + viewProjectionMatrix[5]*y + viewProjectionMatrix[9]*z + viewProjectionMatrix[13]
      val clipW = viewProjectionMatrix[3]*x + viewProjectionMatrix[7]*y + viewProjectionMatrix[11]*z + viewProjectionMatrix[15]

      if (clipW <= 0.001f) return null

      val ndcX = clipX / clipW
      val ndcY = clipY / clipW

      if (ndcX < -1.2f || ndcX > 1.2f || ndcY < -1.2f || ndcY > 1.2f) return null

      val screenX = (ndcX + 1.0f) * 0.5f * displayWidth
      val screenY = (1.0f - ndcY) * 0.5f * displayHeight
      return floatArrayOf(screenX, screenY)
    }

    // ==========================================================
    // DRAW CAMERA BACKGROUND
    // ==========================================================

    private fun setupCameraTexture() {
      val textures = IntArray(1)
      GLES20.glGenTextures(1, textures, 0)
      cameraTextureId = textures[0]

      GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, cameraTextureId)
      GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR)
      GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
      GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE)
      GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE)
      GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, 0)
    }

    private fun setupCameraShader() {
      val vertexShader = """
        attribute vec4 aPosition;
        attribute vec2 aTexCoord;
        varying vec2 vTexCoord;
        void main() {
          gl_Position = aPosition;
          vTexCoord = aTexCoord;
        }
      """.trimIndent()

      val fragmentShader = """
        #extension GL_OES_EGL_image_external : require
        precision mediump float;
        uniform samplerExternalOES uTexture;
        varying vec2 vTexCoord;
        void main() {
          gl_FragColor = texture2D(uTexture, vTexCoord);
        }
      """.trimIndent()

      cameraProgram = createProgram(vertexShader, fragmentShader)
      cameraPositionHandle = GLES20.glGetAttribLocation(cameraProgram, "aPosition")
      cameraTexCoordHandle = GLES20.glGetAttribLocation(cameraProgram, "aTexCoord")
      cameraTextureHandle = GLES20.glGetUniformLocation(cameraProgram, "uTexture")
    }

    private fun drawCamera(frame: Frame) {
      try {
        frame.transformCoordinates2d(
          Coordinates2d.OPENGL_NORMALIZED_DEVICE_COORDINATES,
          cameraVertices,
          Coordinates2d.TEXTURE_NORMALIZED,
          transformedTexCoords
        )
        cameraTexCoordBuffer.clear()
        cameraTexCoordBuffer.put(transformedTexCoords)
        cameraTexCoordBuffer.position(0)
      } catch (_: Exception) {}

      GLES20.glDisable(GLES20.GL_DEPTH_TEST)
      GLES20.glUseProgram(cameraProgram)

      cameraVertexBuffer.position(0)
      GLES20.glEnableVertexAttribArray(cameraPositionHandle)
      GLES20.glVertexAttribPointer(cameraPositionHandle, 2, GLES20.GL_FLOAT, false, 0, cameraVertexBuffer)

      cameraTexCoordBuffer.position(0)
      GLES20.glEnableVertexAttribArray(cameraTexCoordHandle)
      GLES20.glVertexAttribPointer(cameraTexCoordHandle, 2, GLES20.GL_FLOAT, false, 0, cameraTexCoordBuffer)

      GLES20.glActiveTexture(GLES20.GL_TEXTURE0)
      GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, cameraTextureId)
      GLES20.glUniform1i(cameraTextureHandle, 0)

      GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4)

      GLES20.glDisableVertexAttribArray(cameraPositionHandle)
      GLES20.glDisableVertexAttribArray(cameraTexCoordHandle)
      GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, 0)
    }

    // ==========================================================
    // SETUP POINT SHADER (iPhone Measure circular white points)
    // ==========================================================

    private fun setupPointShader() {
      val vertexShader = """
        uniform mat4 uViewProjection;
        attribute vec3 aPosition;
        uniform float uPointSize;
        void main() {
          gl_Position = uViewProjection * vec4(aPosition, 1.0);
          gl_PointSize = uPointSize;
        }
      """.trimIndent()

      val fragmentShader = """
        precision mediump float;
        uniform vec4 uColor;
        void main() {
          vec2 coord = gl_PointCoord - vec2(0.5);
          float dist = length(coord);
          if (dist > 0.5) {
            discard;
          }
          float core = smoothstep(0.24, 0.16, dist);
          float ring = smoothstep(0.48, 0.40, dist) * smoothstep(0.24, 0.32, dist);
          float alpha = max(core, ring * 0.75);
          gl_FragColor = vec4(uColor.rgb, uColor.a * alpha);
        }
      """.trimIndent()

      pointProgram = createProgram(vertexShader, fragmentShader)
      pointPositionHandle = GLES20.glGetAttribLocation(pointProgram, "aPosition")
      pointColorHandle = GLES20.glGetUniformLocation(pointProgram, "uColor")
      pointSizeHandle = GLES20.glGetUniformLocation(pointProgram, "uPointSize")
      pointViewProjectionHandle = GLES20.glGetUniformLocation(pointProgram, "uViewProjection")
    }

    // ==========================================================
    // SETUP LINE SHADER (Thin white lines)
    // ==========================================================

    private fun setupLineShader() {
      val vertexShader = """
        uniform mat4 uViewProjection;
        attribute vec3 aPosition;
        void main() {
          gl_Position = uViewProjection * vec4(aPosition, 1.0);
        }
      """.trimIndent()

      val fragmentShader = """
        precision mediump float;
        uniform vec4 uColor;
        void main() {
          gl_FragColor = uColor;
        }
      """.trimIndent()

      lineProgram = createProgram(vertexShader, fragmentShader)
      linePositionHandle = GLES20.glGetAttribLocation(lineProgram, "aPosition")
      lineColorHandle = GLES20.glGetUniformLocation(lineProgram, "uColor")
      lineViewProjectionHandle = GLES20.glGetUniformLocation(lineProgram, "uViewProjection")
    }

    // ==========================================================
    // DRAW WORLD MEASUREMENTS
    // ==========================================================

    private fun drawWorldMeasurements() {
      if (measurementAnchors.isEmpty() && frozenPositions == null) return

      val poses = if (frozenPositions != null) {
        frozenPositions!!.map { Pose.makeTranslation(it[0], it[1], it[2]) }
      } else {
        measurementAnchors.map { getAnchorPose(it) }
      }
      if (poses.isEmpty()) return

      val n = poses.size

      // Build line segments between consecutive confirmed points
      val lineVerts = mutableListOf<Float>()
      for (i in 0 until n - 1) {
        lineVerts += listOf(poses[i].tx(), poses[i].ty(), poses[i].tz())
        lineVerts += listOf(poses[i+1].tx(), poses[i+1].ty(), poses[i+1].tz())
      }
      if (n == 4) {
        lineVerts += listOf(poses[3].tx(), poses[3].ty(), poses[3].tz())
        lineVerts += listOf(poses[0].tx(), poses[0].ty(), poses[0].tz())
      }

      // Build point vertex array
      val pointVerts = FloatArray(n * 3)
      for (i in poses.indices) {
        pointVerts[i*3]   = poses[i].tx()
        pointVerts[i*3+1] = poses[i].ty()
        pointVerts[i*3+2] = poses[i].tz()
      }

      GLES20.glDisable(GLES20.GL_DEPTH_TEST)
      GLES20.glEnable(GLES20.GL_BLEND)
      GLES20.glBlendFunc(GLES20.GL_SRC_ALPHA, GLES20.GL_ONE_MINUS_SRC_ALPHA)

      if (lineVerts.isNotEmpty()) {
        drawLines3D(lineVerts.toFloatArray(), floatArrayOf(1f, 1f, 1f, 1f), 3.0f)
      }
      drawPoints3D(pointVerts, floatArrayOf(1f, 1f, 1f, 1f), 34.0f)

      GLES20.glDisable(GLES20.GL_BLEND)
    }

    private fun drawLines3D(vertices: FloatArray, color: FloatArray = floatArrayOf(1f, 1f, 1f, 1f), lineWidth: Float = 3.5f) {
      if (vertices.isEmpty()) return
      val buffer = createFloatBuffer(vertices)

      GLES20.glUseProgram(lineProgram)
      buffer.position(0)

      GLES20.glEnableVertexAttribArray(linePositionHandle)
      GLES20.glVertexAttribPointer(linePositionHandle, 3, GLES20.GL_FLOAT, false, 0, buffer)

      GLES20.glUniformMatrix4fv(lineViewProjectionHandle, 1, false, viewProjectionMatrix, 0)
      GLES20.glUniform4fv(lineColorHandle, 1, color, 0)

      GLES20.glLineWidth(lineWidth)
      GLES20.glDrawArrays(GLES20.GL_LINES, 0, vertices.size / 3)

      GLES20.glDisableVertexAttribArray(linePositionHandle)
    }

    private fun drawPoints3D(vertices: FloatArray, color: FloatArray = floatArrayOf(1f, 1f, 1f, 1f), pointSize: Float = 32.0f) {
      if (vertices.isEmpty()) return
      val buffer = createFloatBuffer(vertices)

      GLES20.glUseProgram(pointProgram)
      buffer.position(0)

      GLES20.glEnableVertexAttribArray(pointPositionHandle)
      GLES20.glVertexAttribPointer(pointPositionHandle, 3, GLES20.GL_FLOAT, false, 0, buffer)

      GLES20.glUniformMatrix4fv(pointViewProjectionHandle, 1, false, viewProjectionMatrix, 0)
      GLES20.glUniform4fv(pointColorHandle, 1, color, 0)
      GLES20.glUniform1f(pointSizeHandle, pointSize)

      GLES20.glDrawArrays(GLES20.GL_POINTS, 0, vertices.size / 3)

      GLES20.glDisableVertexAttribArray(pointPositionHandle)
    }


    private fun createFloatBuffer(values: FloatArray): FloatBuffer {
      return ByteBuffer.allocateDirect(values.size * 4)
        .order(ByteOrder.nativeOrder())
        .asFloatBuffer()
        .apply {
          put(values)
          position(0)
        }
    }

    private fun createProgram(vertexSource: String, fragmentSource: String): Int {
      fun compileShader(type: Int, source: String): Int {
        val shader = GLES20.glCreateShader(type)
        GLES20.glShaderSource(shader, source)
        GLES20.glCompileShader(shader)
        val status = IntArray(1)
        GLES20.glGetShaderiv(shader, GLES20.GL_COMPILE_STATUS, status, 0)
        if (status[0] == 0) {
          val log = GLES20.glGetShaderInfoLog(shader)
          GLES20.glDeleteShader(shader)
          throw RuntimeException("Shader compile failed: $log")
        }
        return shader
      }
      val vert = compileShader(GLES20.GL_VERTEX_SHADER, vertexSource)
      val frag = compileShader(GLES20.GL_FRAGMENT_SHADER, fragmentSource)
      val program = GLES20.glCreateProgram()
      GLES20.glAttachShader(program, vert)
      GLES20.glAttachShader(program, frag)
      GLES20.glLinkProgram(program)
      val linkStatus = IntArray(1)
      GLES20.glGetProgramiv(program, GLES20.GL_LINK_STATUS, linkStatus, 0)
      if (linkStatus[0] == 0) {
        val log = GLES20.glGetProgramInfoLog(program)
        GLES20.glDeleteProgram(program)
        throw RuntimeException("Program link failed: $log")
      }
      GLES20.glDeleteShader(vert)
      GLES20.glDeleteShader(frag)
      return program
    }
  }
}