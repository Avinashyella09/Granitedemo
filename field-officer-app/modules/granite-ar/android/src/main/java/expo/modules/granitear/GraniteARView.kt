package expo.modules.granitear

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Color
import android.opengl.GLES11Ext
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import android.view.MotionEvent
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
import com.google.ar.core.Point
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
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.math.PI


class GraniteARView(
  context: Context,
  appContext: AppContext
) : ExpoView(context, appContext) {

  // ============================================================
  // EVENTS
  // ============================================================

  private val onStatus by EventDispatcher()
  private val onPointSelected by EventDispatcher()

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

  // A tap is processed on the exact ARCore frame that arrives next.
  // This avoids hit-testing stale frames and prevents missed P3/P4 taps.
  @Volatile
  private var pendingTap = false

  @Volatile
  private var pendingTapX = 0f

  @Volatile
  private var pendingTapY = 0f

  // ============================================================
  // DISPLAY GEOMETRY
  // ============================================================

  @Volatile
  private var displayWidth = 1

  @Volatile
  private var displayHeight = 1

  @Volatile
  private var displayRotation =
    Surface.ROTATION_0

  // ============================================================
  // MEASUREMENT ANCHORS
  // ============================================================

  private data class MeasurementAnchor(
    val anchor: Anchor,
    val number: Int
  )

  private data class ProjectedPoint(
    val x: Float,
    val y: Float,
    val number: Int
  )

  private val measurementAnchors =
    mutableListOf<MeasurementAnchor>()

  // ============================================================
  // LIVE PREVIEW
  // ============================================================

  @Volatile
  private var previewActive = false

  @Volatile
  private var previewX = 0f

  @Volatile
  private var previewY = 0f

  // ============================================================
  // CAMERA BACKGROUND TEXTURE
  // ============================================================

  private var cameraTextureId = 0

  // ============================================================
  // INITIALIZATION
  // ============================================================

  init {

    setBackgroundColor(Color.BLACK)

    isClickable = true

    setupGLSurfaceView()

    createSession()
  }

  // ============================================================
  // SETUP GLSURFACEVIEW
  // ============================================================

  private fun setupGLSurfaceView() {

    glSurfaceView =
      GLSurfaceView(context)

    glSurfaceView.setEGLContextClientVersion(2)

    glSurfaceView.preserveEGLContextOnPause = true

    renderer =
      ARRenderer()

    glSurfaceView.setRenderer(renderer)

    glSurfaceView.renderMode =
      GLSurfaceView.RENDERMODE_CONTINUOUSLY

    // ----------------------------------------------------------
    // Touch events
    // ----------------------------------------------------------

    glSurfaceView.setOnTouchListener { _, event ->

      handleTouch(event)

      true
    }

    addView(
      glSurfaceView,
      LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.MATCH_PARENT
      )
    )
  }

  // ============================================================
  // TOUCH HANDLING
  // ============================================================

  private fun handleTouch(
    event: MotionEvent
  ) {

    when (event.actionMasked) {

      // --------------------------------------------------------
      // START
      // --------------------------------------------------------

      MotionEvent.ACTION_DOWN -> {

        previewActive = true

        previewX =
          event.x

        previewY =
          event.y

        renderer.setPreview(
          true,
          event.x,
          event.y
        )

        postStatus(
          "POINT_PREVIEW",
          "Move to a corner and release"
        )
      }

      // --------------------------------------------------------
      // DRAG
      // --------------------------------------------------------

      MotionEvent.ACTION_MOVE -> {

        previewX =
          event.x

        previewY =
          event.y

        renderer.setPreview(
          true,
          event.x,
          event.y
        )
      }

      // --------------------------------------------------------
      // RELEASE
      // --------------------------------------------------------

      MotionEvent.ACTION_UP -> {

        previewActive = false

        renderer.setPreview(
          false,
          event.x,
          event.y
        )

        // Store the tap. It is consumed by onDrawFrame() on the next
        // current ARCore frame, so hit testing never uses a stale frame.
        pendingTapX = event.x
        pendingTapY = event.y
        pendingTap = true
      }

      // --------------------------------------------------------
      // CANCEL
      // --------------------------------------------------------

      MotionEvent.ACTION_CANCEL -> {

        previewActive = false

        renderer.setPreview(
          false,
          0f,
          0f
        )
      }
    }
  }

  // ============================================================
  // CREATE ARCORE SESSION
  // ============================================================

  private fun createSession() {

    val activity =
      appContext.currentActivity

    if (activity == null) {

      postStatus(
        "ERROR",
        "No active Android Activity"
      )

      return
    }

    try {

      // --------------------------------------------------------
      // CAMERA PERMISSION
      // --------------------------------------------------------

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

        postStatus(
          "CAMERA_PERMISSION",
          "Camera permission requested"
        )

        return
      }

      // --------------------------------------------------------
      // ARCORE SUPPORT
      // --------------------------------------------------------

      val availability =
        ArCoreApk.getInstance()
          .checkAvailability(activity)

      if (!availability.isSupported) {

        postStatus(
          "AR_NOT_SUPPORTED",
          "This device does not support ARCore"
        )

        return
      }

      // --------------------------------------------------------
      // INSTALL ARCORE
      // --------------------------------------------------------

      try {

        ArCoreApk.getInstance()
          .requestInstall(
            activity,
            true
          )

      } catch (
        _: UnavailableArcoreNotInstalledException
      ) {

        postStatus(
          "ARCORE_INSTALL",
          "Google Play Services for AR is not installed"
        )

        return

      } catch (
        _: UnavailableUserDeclinedInstallationException
      ) {

        postStatus(
          "ARCORE_INSTALL_DECLINED",
          "ARCore installation was declined"
        )

        return
      }

      // --------------------------------------------------------
      // CREATE SESSION
      // --------------------------------------------------------

      session =
        Session(activity)

      configureSession()

      postStatus(
        "READY",
        "ARCore session created"
      )

      startPreview()

    } catch (
      _: UnavailableDeviceNotCompatibleException
    ) {

      postStatus(
        "AR_ERROR",
        "Device is not ARCore compatible"
      )

    } catch (
      _: UnavailableSdkTooOldException
    ) {

      postStatus(
        "AR_ERROR",
        "ARCore SDK is too old"
      )

    } catch (
      _: UnavailableApkTooOldException
    ) {

      postStatus(
        "AR_ERROR",
        "ARCore APK is too old"
      )

    } catch (e: Exception) {

      postStatus(
        "AR_ERROR",
        e.message ?: "Failed to create AR session"
      )
    }
  }

  // ============================================================
  // CONFIGURE SESSION
  // ============================================================

  private fun configureSession() {

    val currentSession =
      session ?: return

    val config =
      Config(currentSession)

    config.planeFindingMode =
      Config.PlaneFindingMode.HORIZONTAL_AND_VERTICAL

    config.updateMode =
      Config.UpdateMode.BLOCKING

    config.focusMode =
      Config.FocusMode.AUTO

    if (
      currentSession.isDepthModeSupported(
        Config.DepthMode.AUTOMATIC
      )
    ) {

      config.depthMode =
        Config.DepthMode.AUTOMATIC
    }

    currentSession.configure(config)
  }

  // ============================================================
  // APPLY DISPLAY GEOMETRY
  // ============================================================

  private fun updateDisplayGeometry(
    width: Int,
    height: Int
  ) {

    if (width <= 0 || height <= 0) {
      return
    }

    displayWidth = width
    displayHeight = height

    val rotation =
      glSurfaceView.display?.rotation
        ?: Surface.ROTATION_0

    displayRotation =
      rotation

    try {

      session?.setDisplayGeometry(
        rotation,
        width,
        height
      )

    } catch (_: Exception) {
    }
  }

  // ============================================================
  // START AR
  // ============================================================

  private fun startPreview() {

    val currentSession =
      session ?: return

    try {

      updateDisplayGeometry(
        displayWidth,
        displayHeight
      )

      currentSession.resume()

      sessionRunning = true

      glSurfaceView.onResume()

      postStatus(
        "TRACKING",
        "AR tracking started"
      )

    } catch (
      _: CameraNotAvailableException
    ) {

      sessionRunning = false

      postStatus(
        "CAMERA_ERROR",
        "Camera is not available"
      )
    }
  }

  // ============================================================
  // SURFACE SIZE / ORIENTATION
  // ============================================================

  override fun onSizeChanged(
    width: Int,
    height: Int,
    oldWidth: Int,
    oldHeight: Int
  ) {

    super.onSizeChanged(
      width,
      height,
      oldWidth,
      oldHeight
    )

    displayWidth = width
    displayHeight = height

    glSurfaceView.post {

      updateDisplayGeometry(
        width,
        height
      )
    }
  }

  // ============================================================
  // ATTACHED
  // ============================================================

  override fun onAttachedToWindow() {

    super.onAttachedToWindow()

    if (session == null) {

      createSession()

    } else if (!sessionRunning) {

      try {

        updateDisplayGeometry(
          width,
          height
        )

        session?.resume()

        sessionRunning = true

        glSurfaceView.onResume()

      } catch (_: Exception) {
      }
    }
  }

  // ============================================================
  // DETACHED
  // ============================================================

  override fun onDetachedFromWindow() {

    glSurfaceView.onPause()

    try {
      session?.pause()
    } catch (_: Exception) {
    }

    sessionRunning = false

    latestFrame = null

    super.onDetachedFromWindow()
  }

  // ============================================================
  // PROCESS TAP ON THE CURRENT ARCORE FRAME
  // ============================================================

  private fun processPendingTap(frame: Frame) {

    if (!pendingTap) {
      return
    }

    if (frame.camera.trackingState != TrackingState.TRACKING) {
      return
    }

    val tapX = pendingTapX
    val tapY = pendingTapY

    // Consume exactly one tap.
    pendingTap = false

    try {

      val hits = frame.hitTest(tapX, tapY)

      if (hits.isEmpty()) {
        postStatus(
          "NO_HIT",
          "No real-world surface found at that location"
        )
        return
      }

      // IMPORTANT:
      // Prefer a tracked PLANE before feature/depth points.
      // DepthPoint values can fluctuate on object edges, while a tracked
      // plane gives a much more stable world position for a corner on a flat face.
      var selectedHit: HitResult? = null

      // 1. Nearest valid plane hit.
      for (hit in hits) {
        val trackable = hit.trackable

        if (
          trackable is Plane &&
          trackable.trackingState == TrackingState.TRACKING &&
          trackable.isPoseInExtents(hit.hitPose)
        ) {
          selectedHit = hit
          break
        }
      }

      if (selectedHit == null) {
        postStatus(
          "NO_HIT",
          "No tracked surface found"
        )
        return
      }

      // Never create a fifth point. The UI must reset before another measurement.
      if (measurementAnchors.size >= 4) {
        postStatus(
          "COMPLETE",
          "P1-P4 already selected. Press Reset Measurement."
        )
        return
      }

      val anchor = selectedHit.createAnchor()
      val pointNumber = measurementAnchors.size + 1

      measurementAnchors.add(
        MeasurementAnchor(
          anchor = anchor,
          number = pointNumber
        )
      )

      // Send the anchored world position to React Native.
      val pose = anchor.pose

      postPointSelected(
        tapX,
        tapY,
        pose.tx(),
        pose.ty(),
        pose.tz(),
        selectedHit.trackable.javaClass.simpleName ?: "Unknown"
      )

      when (pointNumber) {
        1 -> postStatus("POINT_SELECTED", "P1 selected. Select P2.")
        2 -> postStatus("POINT_SELECTED", "P2 selected. Select P3.")
        3 -> postStatus("POINT_SELECTED", "P3 selected. Select P4.")
        4 -> postStatus("MEASUREMENT_COMPLETE", "P1, P2, P3 and P4 selected.")
      }

    } catch (e: Exception) {
      postStatus(
        "POINT_ERROR",
        e.message ?: "Unable to create measurement point"
      )
    }
  }

  // ============================================================
  // RESET
  // ============================================================

  fun resetMeasurement() {

    glSurfaceView.queueEvent {

      for (item in measurementAnchors) {

        try {
          item.anchor.detach()
        } catch (_: Exception) {
        }
      }

      measurementAnchors.clear()

      pendingTap = false
      pendingTapX = 0f
      pendingTapY = 0f

      previewActive = false

      renderer.setPreview(
        false,
        0f,
        0f
      )

      postStatus(
        "RESET",
        "Measurement reset"
      )
    }
  }

  // ============================================================
  // STATUS EVENT
  // ============================================================

  private fun postStatus(
    status: String,
    message: String
  ) {

    post {

      this@GraniteARView.onStatus(
        mapOf(
          "status" to status,
          "message" to message
        )
      )
    }
  }

  // ============================================================
  // POINT EVENT
  // ============================================================

  private fun postPointSelected(
    screenX: Float,
    screenY: Float,
    x: Float,
    y: Float,
    z: Float,
    trackable: String
  ) {

    post {

      this@GraniteARView.onPointSelected(
        mapOf(
          "screenX" to screenX,
          "screenY" to screenY,
          "x" to x,
          "y" to y,
          "z" to z,
          "trackable" to trackable
        )
      )
    }
  }

  // ============================================================
  // OPENGL RENDERER
  // ============================================================

  private inner class ARRenderer :
    GLSurfaceView.Renderer {

    // ==========================================================
    // CAMERA SHADER
    // ==========================================================

    private var cameraProgram = 0

    private var cameraPositionHandle = 0

    private var cameraTexCoordHandle = 0

    private var cameraTextureHandle = 0

    // ==========================================================
    // MEASUREMENT SHADER
    // ==========================================================

    private var measurementProgram = 0

    private var measurementPositionHandle = 0

    private var measurementColorHandle = 0

    private var measurementViewProjectionHandle = 0

    // ==========================================================
    // CAMERA VERTICES
    // ==========================================================

    private val cameraVertices =
      floatArrayOf(

        -1f, -1f,

         1f, -1f,

        -1f,  1f,

         1f,  1f
      )

    // ==========================================================
    // CAMERA UV
    // ==========================================================

    private val originalTexCoords =
      floatArrayOf(

        0f, 0f,

        1f, 0f,

        0f, 1f,

        1f, 1f
      )

    private val transformedTexCoords =
      FloatArray(8)

    // ==========================================================
    // BUFFERS
    // ==========================================================

    private lateinit var cameraVertexBuffer:
      FloatBuffer

    private lateinit var cameraTexCoordBuffer:
      FloatBuffer

    // ==========================================================
    // MATRICES
    // ==========================================================

    private val viewMatrix =
      FloatArray(16)

    private val projectionMatrix =
      FloatArray(16)

    private val viewProjectionMatrix =
      FloatArray(16)

    // ==========================================================
    // PREVIEW
    // ==========================================================

    @Volatile
    private var previewEnabled = false

    @Volatile
    private var previewScreenX = 0f

    @Volatile
    private var previewScreenY = 0f

    // ==========================================================
    // SURFACE CREATED
    // ==========================================================

    override fun onSurfaceCreated(
      gl: javax.microedition.khronos.opengles.GL10?,
      config: javax.microedition.khronos.egl.EGLConfig?
    ) {

      GLES20.glClearColor(
        0f,
        0f,
        0f,
        1f
      )

      setupCameraShader()

      setupMeasurementShader()

      cameraVertexBuffer =
        createFloatBuffer(
          cameraVertices
        )

      cameraTexCoordBuffer =
        createFloatBuffer(
          originalTexCoords
        )

      setupCameraTexture()

      postStatus(
        "GL_READY",
        "Camera renderer ready"
      )
    }

    // ==========================================================
    // SURFACE CHANGED
    // ==========================================================

    override fun onSurfaceChanged(
      gl: javax.microedition.khronos.opengles.GL10?,
      width: Int,
      height: Int
    ) {

      GLES20.glViewport(
        0,
        0,
        width,
        height
      )

      displayWidth =
        width

      displayHeight =
        height

      glSurfaceView.post {

        updateDisplayGeometry(
          width,
          height
        )
      }
    }

    // ==========================================================
    // DRAW FRAME
    // ==========================================================

    override fun onDrawFrame(
      gl: javax.microedition.khronos.opengles.GL10?
    ) {

      GLES20.glClear(
        GLES20.GL_COLOR_BUFFER_BIT or
          GLES20.GL_DEPTH_BUFFER_BIT
      )

      val currentSession =
        session ?: return

      if (!sessionRunning) {
        return
      }

      try {

        // ------------------------------------------------------
        // Give camera texture to ARCore.
        // ------------------------------------------------------

        currentSession.setCameraTextureName(
          cameraTextureId
        )

        // ------------------------------------------------------
        // Get ARCore frame.
        // ------------------------------------------------------

        val frame =
          currentSession.update()

        /*
         * Keep the latest frame for touch hit testing.
         */
        latestFrame =
          frame

        // Process any user tap against THIS exact current ARCore frame.
        processPendingTap(frame)

        val camera =
          frame.camera

        // ------------------------------------------------------
        // Camera not tracking yet.
        // ------------------------------------------------------

        if (
          camera.trackingState !=
          TrackingState.TRACKING
        ) {

          drawCamera(
            frame
          )

          return
        }

        // ------------------------------------------------------
        // View matrix
        // ------------------------------------------------------

        camera.getViewMatrix(
          viewMatrix,
          0
        )

        // ------------------------------------------------------
        // Projection matrix
        // ------------------------------------------------------

        camera.getProjectionMatrix(
          projectionMatrix,
          0,
          0.01f,
          100f
        )

        android.opengl.Matrix.multiplyMM(
          viewProjectionMatrix,
          0,
          projectionMatrix,
          0,
          viewMatrix,
          0
        )

        // ======================================================
        // 1. CAMERA
        // ======================================================

        drawCamera(
          frame
        )

        // ======================================================
        // 2. WORLD ANCHORED MEASUREMENTS
        // ======================================================

        drawWorldMeasurements()

        // ======================================================
        // 3. LIVE PREVIEW
        // ======================================================

        if (previewEnabled) {

          drawPreview()
        }

      } catch (
        _: CameraNotAvailableException
      ) {

        postStatus(
          "CAMERA_ERROR",
          "Camera became unavailable"
        )

      } catch (_: Exception) {

        // Keep renderer alive.
      }
    }

    // ==========================================================
    // CAMERA TEXTURE
    // ==========================================================

    private fun setupCameraTexture() {

      val textures =
        IntArray(1)

      GLES20.glGenTextures(
        1,
        textures,
        0
      )

      cameraTextureId =
        textures[0]

      GLES20.glBindTexture(
        GLES11Ext.GL_TEXTURE_EXTERNAL_OES,
        cameraTextureId
      )

      GLES20.glTexParameteri(
        GLES11Ext.GL_TEXTURE_EXTERNAL_OES,
        GLES20.GL_TEXTURE_MIN_FILTER,
        GLES20.GL_LINEAR
      )

      GLES20.glTexParameteri(
        GLES11Ext.GL_TEXTURE_EXTERNAL_OES,
        GLES20.GL_TEXTURE_MAG_FILTER,
        GLES20.GL_LINEAR
      )

      GLES20.glTexParameteri(
        GLES11Ext.GL_TEXTURE_EXTERNAL_OES,
        GLES20.GL_TEXTURE_WRAP_S,
        GLES20.GL_CLAMP_TO_EDGE
      )

      GLES20.glTexParameteri(
        GLES11Ext.GL_TEXTURE_EXTERNAL_OES,
        GLES20.GL_TEXTURE_WRAP_T,
        GLES20.GL_CLAMP_TO_EDGE
      )

      GLES20.glBindTexture(
        GLES11Ext.GL_TEXTURE_EXTERNAL_OES,
        0
      )
    }

    // ==========================================================
    // CAMERA SHADER
    // ==========================================================

    private fun setupCameraShader() {

      val vertexShader =
        """
        attribute vec4 aPosition;
        attribute vec2 aTexCoord;

        varying vec2 vTexCoord;

        void main() {

          gl_Position =
            aPosition;

          vTexCoord =
            aTexCoord;
        }
        """.trimIndent()

      val fragmentShader =
        """
        #extension GL_OES_EGL_image_external : require

        precision mediump float;

        uniform samplerExternalOES uTexture;

        varying vec2 vTexCoord;

        void main() {

          gl_FragColor =
            texture2D(
              uTexture,
              vTexCoord
            );
        }
        """.trimIndent()

      cameraProgram =
        createProgram(
          vertexShader,
          fragmentShader
        )

      cameraPositionHandle =
        GLES20.glGetAttribLocation(
          cameraProgram,
          "aPosition"
        )

      cameraTexCoordHandle =
        GLES20.glGetAttribLocation(
          cameraProgram,
          "aTexCoord"
        )

      cameraTextureHandle =
        GLES20.glGetUniformLocation(
          cameraProgram,
          "uTexture"
        )
    }

    // ==========================================================
    // DRAW CAMERA
    // ==========================================================

    private fun drawCamera(
      frame: Frame
    ) {

      try {

        /*
         * Transform the camera UV coordinates according to
         * ARCore display geometry.
         */
        frame.transformCoordinates2d(
          Coordinates2d.OPENGL_NORMALIZED_DEVICE_COORDINATES,
          cameraVertices,
          Coordinates2d.TEXTURE_NORMALIZED,
          transformedTexCoords
        )

        cameraTexCoordBuffer.clear()

        cameraTexCoordBuffer.put(
          transformedTexCoords
        )

        cameraTexCoordBuffer.position(0)

      } catch (_: Exception) {
      }

      GLES20.glDisable(
        GLES20.GL_DEPTH_TEST
      )

      GLES20.glUseProgram(
        cameraProgram
      )

      cameraVertexBuffer.position(0)

      GLES20.glEnableVertexAttribArray(
        cameraPositionHandle
      )

      GLES20.glVertexAttribPointer(
        cameraPositionHandle,
        2,
        GLES20.GL_FLOAT,
        false,
        0,
        cameraVertexBuffer
      )

      cameraTexCoordBuffer.position(0)

      GLES20.glEnableVertexAttribArray(
        cameraTexCoordHandle
      )

      GLES20.glVertexAttribPointer(
        cameraTexCoordHandle,
        2,
        GLES20.GL_FLOAT,
        false,
        0,
        cameraTexCoordBuffer
      )

      GLES20.glActiveTexture(
        GLES20.GL_TEXTURE0
      )

      GLES20.glBindTexture(
        GLES11Ext.GL_TEXTURE_EXTERNAL_OES,
        cameraTextureId
      )

      GLES20.glUniform1i(
        cameraTextureHandle,
        0
      )

      GLES20.glDrawArrays(
        GLES20.GL_TRIANGLE_STRIP,
        0,
        4
      )

      GLES20.glDisableVertexAttribArray(
        cameraPositionHandle
      )

      GLES20.glDisableVertexAttribArray(
        cameraTexCoordHandle
      )

      GLES20.glBindTexture(
        GLES11Ext.GL_TEXTURE_EXTERNAL_OES,
        0
      )
    }

    // ==========================================================
    // MEASUREMENT SHADER
    // ==========================================================

    private fun setupMeasurementShader() {

      val vertexShader =
        """
        uniform mat4 uViewProjection;
        attribute vec3 aPosition;

        void main() {
          gl_Position = uViewProjection * vec4(aPosition, 1.0);
          gl_PointSize = 20.0;
        }
        """.trimIndent()

      val fragmentShader =
        """
        precision mediump float;

        uniform vec4 uColor;

        void main() {

          gl_FragColor =
            uColor;
        }
        """.trimIndent()

      measurementProgram =
        createProgram(
          vertexShader,
          fragmentShader
        )

      measurementPositionHandle =
        GLES20.glGetAttribLocation(
          measurementProgram,
          "aPosition"
        )

      measurementColorHandle =
        GLES20.glGetUniformLocation(
          measurementProgram,
          "uColor"
        )

      measurementViewProjectionHandle =
        GLES20.glGetUniformLocation(
          measurementProgram,
          "uViewProjection"
        )
    }

    // ==========================================================
    // WORLD ANCHORED MEASUREMENTS
    // ==========================================================

    // ==========================================================
    // WORLD ANCHORED MEASUREMENTS
    // ==========================================================

    private fun drawWorldMeasurements() {

      if (measurementAnchors.isEmpty()) {
        return
      }

      // Collect 3D positions of all anchors
      val pointsList = mutableListOf<FloatArray>()
      for (item in measurementAnchors) {
        val anchor = item.anchor
        if (anchor.trackingState == TrackingState.TRACKING) {
          val pose = anchor.pose
          pointsList.add(floatArrayOf(pose.tx(), pose.ty(), pose.tz()))
        }
      }

      if (pointsList.isEmpty()) {
        return
      }

      // Prepare vertices for points
      val pointVertices = FloatArray(pointsList.size * 3)
      for (i in pointsList.indices) {
        pointVertices[i * 3] = pointsList[i][0]
        pointVertices[i * 3 + 1] = pointsList[i][1]
        pointVertices[i * 3 + 2] = pointsList[i][2]
      }

      // Prepare vertices for lines
      val lineVerticesList = mutableListOf<Float>()
      for (i in 0 until pointsList.size - 1) {
        lineVerticesList.add(pointsList[i][0])
        lineVerticesList.add(pointsList[i][1])
        lineVerticesList.add(pointsList[i][2])
        lineVerticesList.add(pointsList[i + 1][0])
        lineVerticesList.add(pointsList[i + 1][1])
        lineVerticesList.add(pointsList[i + 1][2])
      }

      // Close polygon if 4 points
      if (pointsList.size == 4) {
        lineVerticesList.add(pointsList.last()[0])
        lineVerticesList.add(pointsList.last()[1])
        lineVerticesList.add(pointsList.last()[2])
        lineVerticesList.add(pointsList.first()[0])
        lineVerticesList.add(pointsList.first()[1])
        lineVerticesList.add(pointsList.first()[2])
      }

      GLES20.glDisable(GLES20.GL_DEPTH_TEST)

      if (lineVerticesList.isNotEmpty()) {
        drawLines3D(lineVerticesList.toFloatArray())
      }

      drawPoints3D(pointVertices)
    }

    // ==========================================================
    // LIVE PREVIEW (3D)
    // ==========================================================

    private fun drawPreview() {

      if (!previewEnabled || measurementAnchors.isEmpty()) {
        return
      }

      val last = measurementAnchors.last()
      if (last.anchor.trackingState != TrackingState.TRACKING) {
        return
      }

      val frame = latestFrame ?: return
      if (frame.camera.trackingState != TrackingState.TRACKING) {
        return
      }

      // Hit test current screen coordinate to find 3D world pose
      val hits = frame.hitTest(previewScreenX, previewScreenY)
      var previewPose: com.google.ar.core.Pose? = null

      for (hit in hits) {
        val trackable = hit.trackable
        if (trackable is com.google.ar.core.Plane && trackable.trackingState == TrackingState.TRACKING) {
          previewPose = hit.hitPose
          break
        }
      }

      if (previewPose == null) {
        return
      }

      val p1 = last.anchor.pose
      val p2 = previewPose

      val lineVertices = floatArrayOf(
        p1.tx(), p1.ty(), p1.tz(),
        p2.tx(), p2.ty(), p2.tz()
      )

      val pointVertices = floatArrayOf(
        p2.tx(), p2.ty(), p2.tz()
      )

      drawLines3D(lineVertices)
      drawPoints3D(pointVertices)
    }

    // ==========================================================
    // DRAW 3D LINES
    // ==========================================================

    private fun drawLines3D(vertices: FloatArray) {

      val buffer = createFloatBuffer(vertices)

      GLES20.glUseProgram(measurementProgram)
      buffer.position(0)

      GLES20.glEnableVertexAttribArray(measurementPositionHandle)
      GLES20.glVertexAttribPointer(
        measurementPositionHandle, 3, GLES20.GL_FLOAT, false, 0, buffer
      )

      // Upload ViewProjection matrix
      GLES20.glUniformMatrix4fv(
        measurementViewProjectionHandle, 1, false, viewProjectionMatrix, 0
      )

      // Color White
      GLES20.glUniform4f(measurementColorHandle, 1f, 1f, 1f, 1f)

      // Draw lines
      GLES20.glLineWidth(5f)
      GLES20.glDrawArrays(GLES20.GL_LINES, 0, vertices.size / 3)

      GLES20.glDisableVertexAttribArray(measurementPositionHandle)
    }

    // ==========================================================
    // DRAW 3D POINTS
    // ==========================================================

    private fun drawPoints3D(vertices: FloatArray) {

      val buffer = createFloatBuffer(vertices)

      GLES20.glUseProgram(measurementProgram)
      buffer.position(0)

      GLES20.glEnableVertexAttribArray(measurementPositionHandle)
      GLES20.glVertexAttribPointer(
        measurementPositionHandle, 3, GLES20.GL_FLOAT, false, 0, buffer
      )

      // Upload ViewProjection matrix
      GLES20.glUniformMatrix4fv(
        measurementViewProjectionHandle, 1, false, viewProjectionMatrix, 0
      )

      // Color White
      GLES20.glUniform4f(measurementColorHandle, 1f, 1f, 1f, 1f)

      // Draw points
      GLES20.glDrawArrays(GLES20.GL_POINTS, 0, vertices.size / 3)

      GLES20.glDisableVertexAttribArray(measurementPositionHandle)
    }

    // ==========================================================
    // PREVIEW CONTROL
    // ==========================================================

    fun setPreview(
      enabled: Boolean,
      x: Float,
      y: Float
    ) {

      previewEnabled =
        enabled

      previewScreenX =
        x

      previewScreenY =
        y
    }

    // ==========================================================
    // FLOAT BUFFER
    // ==========================================================

    private fun createFloatBuffer(
      values: FloatArray
    ): FloatBuffer {

      return ByteBuffer
        .allocateDirect(
          values.size * 4
        )
        .order(
          ByteOrder.nativeOrder()
        )
        .asFloatBuffer()
        .apply {

          put(values)

          position(0)
        }
    }

    // ==========================================================
    // CREATE OPENGL PROGRAM
    // ==========================================================

    private fun createProgram(
      vertexSource: String,
      fragmentSource: String
    ): Int {

      // --------------------------------------------------------
      // VERTEX SHADER
      // --------------------------------------------------------

      val vertexShader =
        GLES20.glCreateShader(
          GLES20.GL_VERTEX_SHADER
        )

      GLES20.glShaderSource(
        vertexShader,
        vertexSource
      )

      GLES20.glCompileShader(
        vertexShader
      )

      val vertexStatus =
        IntArray(1)

      GLES20.glGetShaderiv(
        vertexShader,
        GLES20.GL_COMPILE_STATUS,
        vertexStatus,
        0
      )

      if (vertexStatus[0] == 0) {

        val error =
          GLES20.glGetShaderInfoLog(
            vertexShader
          )

        GLES20.glDeleteShader(
          vertexShader
        )

        throw RuntimeException(
          "Vertex shader failed: $error"
        )
      }

      // --------------------------------------------------------
      // FRAGMENT SHADER
      // --------------------------------------------------------

      val fragmentShader =
        GLES20.glCreateShader(
          GLES20.GL_FRAGMENT_SHADER
        )

      GLES20.glShaderSource(
        fragmentShader,
        fragmentSource
      )

      GLES20.glCompileShader(
        fragmentShader
      )

      val fragmentStatus =
        IntArray(1)

      GLES20.glGetShaderiv(
        fragmentShader,
        GLES20.GL_COMPILE_STATUS,
        fragmentStatus,
        0
      )

      if (fragmentStatus[0] == 0) {

        val error =
          GLES20.glGetShaderInfoLog(
            fragmentShader
          )

        GLES20.glDeleteShader(
          fragmentShader
        )

        throw RuntimeException(
          "Fragment shader failed: $error"
        )
      }

      // --------------------------------------------------------
      // LINK
      // --------------------------------------------------------

      val program =
        GLES20.glCreateProgram()

      GLES20.glAttachShader(
        program,
        vertexShader
      )

      GLES20.glAttachShader(
        program,
        fragmentShader
      )

      GLES20.glLinkProgram(
        program
      )

      val linkStatus =
        IntArray(1)

      GLES20.glGetProgramiv(
        program,
        GLES20.GL_LINK_STATUS,
        linkStatus,
        0
      )

      if (linkStatus[0] == 0) {

        val error =
          GLES20.glGetProgramInfoLog(
            program
          )

        GLES20.glDeleteProgram(
          program
        )

        throw RuntimeException(
          "OpenGL linking failed: $error"
        )
      }

      GLES20.glDeleteShader(
        vertexShader
      )

      GLES20.glDeleteShader(
        fragmentShader
      )

      return program
    }
  }
}