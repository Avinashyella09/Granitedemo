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

      Events(
        "onStatus",
        "onPointSelected"
      )
    }
  }
}