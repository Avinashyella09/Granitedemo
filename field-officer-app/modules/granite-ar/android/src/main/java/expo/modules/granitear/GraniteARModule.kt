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

      Events(
        "onStatus",
        "onPointSelected",
        "onOverlayUpdate"
      )
    }
  }
}