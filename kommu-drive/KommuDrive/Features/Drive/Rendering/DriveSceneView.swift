import SwiftUI
import SceneKit

/// SwiftUI wrapper for the SceneKit road view.
/// Hosts an `SCNView` that renders the `DriveSceneRenderer`'s scene, and
/// forwards each new `DriveFrame` from the ViewModel.
struct DriveSceneView: UIViewRepresentable {
  @ObservedObject var viewModel: DriveSessionViewModel
  let renderer: DriveSceneRenderer

  func makeUIView(context: Context) -> SCNView {
    let view = SCNView()
    view.scene = renderer.scene
    view.backgroundColor = UIColor(red: 8/255, green: 9/255, blue: 13/255, alpha: 1)
    view.antialiasingMode = .multisampling2X
    view.preferredFramesPerSecond = 60
    view.isUserInteractionEnabled = false
    view.autoenablesDefaultLighting = false
    return view
  }

  func updateUIView(_ uiView: SCNView, context: Context) {
    // Forward the latest frame to the renderer on every SwiftUI update.
    renderer.update(frame: viewModel.latestFrame)
  }
}
