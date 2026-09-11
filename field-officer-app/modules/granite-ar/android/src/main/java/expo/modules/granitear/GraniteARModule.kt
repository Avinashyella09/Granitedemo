package expo.modules.granitear

import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

class GraniteARModule : Module() {

  override fun definition() = ModuleDefinition {

    Name("GraniteAR")

    Events(
      "onChange"
    )

    View(GraniteARView::class) {

      Prop("tapToken") { view: GraniteARView, token: Int ->
        if (token > 0) {
          view.triggerCenterTap()
        }
      }

      Prop("resetToken") { view: GraniteARView, token: Int ->
        if (token > 0) {
          view.resetMeasurement()
        }
      }

      // Accelerometer motion-gate tuning, exposed so thresholds can be dialled
      // in on real hardware without rebuilding the native module.
      // [0] = moving threshold (m/s2), [1] = steady threshold (m/s2),
      // [2] = hold time (ms) the phone must stay calm before measuring resumes.
      Prop("motionSensitivity") { view: GraniteARView, values: FloatArray? ->
        if (values != null && values.size >= 3) {
          view.setMotionSensitivity(values[0], values[1], values[2].toInt())
        }
      }

      Events(
        "onStatus",
        "onPointSelected",
        "onOverlayUpdate",
        "onSteadyChange"
      )
    }
  }
}
