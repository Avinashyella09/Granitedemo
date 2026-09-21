import ExpoModulesCore

// Swift/ARKit counterpart to the Android GraniteARModule.kt. Exposes the same
// module name, props, and events so App.js can use GraniteARView without
// knowing which native platform backs it.
public class GraniteARModule: Module {
  public func definition() -> ModuleDefinition {
    Name("GraniteAR")

    View(GraniteARView.self) {
      Prop("tapToken") { (view: GraniteARView, token: Int) in
        view.tapToken = token
      }

      Prop("resetToken") { (view: GraniteARView, token: Int) in
        view.resetToken = token
      }

      Events("onStatus", "onPointSelected", "onOverlayUpdate")
    }
  }
}
