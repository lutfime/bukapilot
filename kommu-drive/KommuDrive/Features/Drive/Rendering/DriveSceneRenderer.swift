import Foundation
import SceneKit
import UIKit

/// SceneKit renderer for the driving road view.
///
/// Owns a single `SCNScene` with an atmospheric perspective camera looking forward.
/// All road elements (surface, grid lines, lane ribbons, path corridor + glowing edge borders,
/// lead car + 3D floating HUD badge, detected cars) are 3D geometries.
///
/// Coordinate mapping (openpilot model → SceneKit):
///   model x (meters forward) → SceneKit -Z
///   model y (meters left)    → SceneKit -X
///   ground plane at Y = 0
@MainActor
final class DriveSceneRenderer {

  let scene: SCNScene

  // Persistent nodes
  private let skyNode: SCNNode
  private let roadSurface: SCNNode
  private let gridNode: SCNNode
  private let leadCarNode: SCNNode
  private let leadHudNode: SCNNode
  private let pathNode: SCNNode
  private let pathLeftEdgeNode: SCNNode
  private let pathRightEdgeNode: SCNNode
  private let laneLineNodes: [SCNNode]    // 4
  private let roadEdgeNodes: [SCNNode]    // 2
  private let detectedCarNodes: [SCNNode] // pool of 3

  private var leadWasVisible = false
  private var carSlotPositions: [SCNVector3] = Array(repeating: SCNVector3Zero, count: 3)

  // Camera constants — framed so the road flows cleanly to the bottom (comma 4 style)
  private let cameraHeight: CGFloat = 2.4
  private let cameraBack: CGFloat = 4.0
  private let cameraLookZ: CGFloat = -48.0

  init() {
    scene = SCNScene()

    // 1. Sky Gradient Background & Distance Fog
    scene.background.contents = UIColor(red: 6/255, green: 8/255, blue: 14/255, alpha: 1.0)
    scene.fogColor = UIColor(red: 16/255, green: 22/255, blue: 34/255, alpha: 1.0)
    scene.fogStartDistance = 35.0
    scene.fogEndDistance = 130.0
    scene.fogDensityExponent = 1.1

    // 2. Camera Setup
    let camera = SCNCamera()
    camera.fieldOfView = 52.0
    camera.zNear = 0.5
    camera.zFar = 250.0
    let camNode = SCNNode()
    camNode.camera = camera
    camNode.position = SCNVector3(0, cameraHeight, cameraBack)

    let targetNode = SCNNode()
    targetNode.position = SCNVector3(0, 0, cameraLookZ)
    scene.rootNode.addChildNode(targetNode)

    let constraint = SCNLookAtConstraint(target: targetNode)
    camNode.constraints = [constraint]
    scene.rootNode.addChildNode(camNode)

    // 3. Lighting Setup
    let ambient = SCNLight()
    ambient.type = .ambient
    ambient.color = UIColor(red: 0.40, green: 0.50, blue: 0.65, alpha: 1.0)
    ambient.intensity = 550
    let ambientNode = SCNNode()
    ambientNode.light = ambient
    scene.rootNode.addChildNode(ambientNode)

    let directional = SCNLight()
    directional.type = .directional
    directional.color = UIColor(red: 0.9, green: 0.95, blue: 1.0, alpha: 1.0)
    directional.intensity = 700
    let dirNode = SCNNode()
    dirNode.light = directional
    dirNode.eulerAngles = SCNVector3(-Float.pi / 4, Float.pi / 6, 0)
    scene.rootNode.addChildNode(dirNode)

    // 4. Sky Backdrop
    skyNode = DriveSceneRenderer.makeSkyBackdropNode()
    scene.rootNode.addChildNode(skyNode)

    // 5. Road Surface & Depth Grid
    roadSurface = DriveSceneRenderer.makeSurfaceNode()
    scene.rootNode.addChildNode(roadSurface)

    gridNode = DriveSceneRenderer.makeGridNode()
    scene.rootNode.addChildNode(gridNode)

    // 6. Lead Car & Floating AR HUD Badge
    leadCarNode = DriveSceneRenderer.makeCarNode(
      width: 1.80, height: 1.45, length: 3.7,
      bodyColor: UIColor(red: 0.0, green: 0.85, blue: 0.95, alpha: 0.95),
      isLead: true
    )
    leadCarNode.opacity = 0

    let hudPlane = SCNPlane(width: 3.0, height: 0.75)
    let hudMat = SCNMaterial()
    hudMat.lightingModel = .constant
    hudMat.isDoubleSided = true
    hudMat.writesToDepthBuffer = false
    hudPlane.materials = [hudMat]
    leadHudNode = SCNNode(geometry: hudPlane)
    leadHudNode.position = SCNVector3(0, 2.2, 0)
    leadHudNode.constraints = [SCNBillboardConstraint()]
    leadCarNode.addChildNode(leadHudNode)

    scene.rootNode.addChildNode(leadCarNode)

    // 7. Path Corridor + Comma 4 Glowing Edge Borders
    pathNode = SCNNode()
    pathLeftEdgeNode = SCNNode()
    pathRightEdgeNode = SCNNode()
    laneLineNodes = (0..<4).map { _ in SCNNode() }
    roadEdgeNodes = (0..<2).map { _ in SCNNode() }

    // 8. Detected Cars Pool (3)
    detectedCarNodes = (0..<3).map { _ in
      DriveSceneRenderer.makeCarNode(
        width: 1.75, height: 1.35, length: 3.5,
        bodyColor: UIColor(red: 0.0, green: 0.75, blue: 1.0, alpha: 0.5),
        isLead: false
      )
    }

    // Add nodes to scene
    for n in laneLineNodes { n.opacity = 0; scene.rootNode.addChildNode(n) }
    for n in roadEdgeNodes { n.opacity = 0; scene.rootNode.addChildNode(n) }
    scene.rootNode.addChildNode(pathNode)
    scene.rootNode.addChildNode(pathLeftEdgeNode)
    scene.rootNode.addChildNode(pathRightEdgeNode)
    for n in detectedCarNodes { n.opacity = 0; scene.rootNode.addChildNode(n) }
  }

  // MARK: - Update (called per frame from ViewModel)

  func update(frame: DriveFrame?) {
    guard let frame else { return }
    let engaged = frame.enabled

    roadSurface.isHidden = false
    gridNode.isHidden = false

    updateLaneLines(frame: frame, engaged: engaged)
    updateRoadEdges(frame: frame, engaged: engaged)
    updatePath(frame: frame, engaged: engaged)
    updateLeadCar(frame: frame)
    updateDetectedCars(frame: frame)
  }

  // MARK: - Lane lines (Dynamic width & opacity scaling with confidence)

  private func updateLaneLines(frame: DriveFrame, engaged: Bool) {
    let whiteLineColor = UIColor(red: 235/255, green: 245/255, blue: 255/255, alpha: 1.0)

    for (i, node) in laneLineNodes.enumerated() {
      if !engaged || i >= frame.laneLines.count {
        SCNTransaction.begin()
        SCNTransaction.animationDuration = 0.2
        node.opacity = 0
        SCNTransaction.commit()
        continue
      }
      let lane = frame.laneLines[i]
      let prob = i < frame.laneLineProbs.count ? frame.laneLineProbs[i] : 0.5

      // Line width & opacity scale dynamically with confidence (prob):
      // - High confidence (prob > 0.5): thick, bright line (halfWidth ~0.075m)
      // - Low/Fading confidence: thins out and fades down
      // - Absent/Low (< 0.15): hides completely
      if prob < 0.15 {
        SCNTransaction.begin()
        SCNTransaction.animationDuration = 0.2
        node.opacity = 0
        SCNTransaction.commit()
        continue
      }

      let halfWidth: CGFloat = CGFloat(0.03 + 0.045 * prob)
      let maxAlpha: CGFloat = (i == 1 || i == 2) ? 0.82 : 0.52
      let alpha = CGFloat(prob) * maxAlpha

      let color = whiteLineColor.withAlphaComponent(alpha)
      let ribbon = DriveSceneRenderer.makeRibbonGeometry(path: lane, halfWidth: halfWidth, color: color, yOffset: 0.008)
      node.geometry = ribbon
      SCNTransaction.begin()
      SCNTransaction.animationDuration = 0.15
      node.opacity = 1
      SCNTransaction.commit()
    }
  }

  // MARK: - Road edges

  private func updateRoadEdges(frame: DriveFrame, engaged: Bool) {
    for (i, node) in roadEdgeNodes.enumerated() {
      if !engaged || i >= frame.roadEdges.count {
        SCNTransaction.begin()
        SCNTransaction.animationDuration = 0.2
        node.opacity = 0
        SCNTransaction.commit()
        continue
      }
      let edge = frame.roadEdges[i]
      let edgeStd = i < frame.roadEdgeStds.count ? frame.roadEdgeStds[i] : 0.5
      let adjProb = (i + 1) < frame.laneLineProbs.count ? frame.laneLineProbs[i + 1] : 0.5
      let notConfident = adjProb < 0.25
      let alpha = min(max(1.0 - edgeStd, 0), 0.6)
      let color: UIColor = notConfident
        ? UIColor(red: 1.0, green: 0.4, blue: 0.2, alpha: CGFloat(alpha))
        : UIColor(red: 0.75, green: 0.85, blue: 0.95, alpha: CGFloat(alpha))

      let ribbon = DriveSceneRenderer.makeRibbonGeometry(path: edge, halfWidth: 0.04, color: color, yOffset: 0.006)
      node.geometry = ribbon
      SCNTransaction.begin()
      SCNTransaction.animationDuration = 0.15
      node.opacity = 1
      SCNTransaction.commit()
    }
  }

  // MARK: - Path corridor (Comma 4 Style: Uniform green fill + glowing green borders)

  private func updatePath(frame: DriveFrame, engaged: Bool) {
    guard engaged, let path = frame.path, path.x.count >= 2 else {
      SCNTransaction.begin()
      SCNTransaction.animationDuration = 0.2
      pathNode.opacity = 0
      pathLeftEdgeNode.opacity = 0
      pathRightEdgeNode.opacity = 0
      SCNTransaction.commit()
      return
    }

    // Path color: green when allowThrottle is true, blue when braking.
    // Uses longitudinalPlan.allowThrottle — same signal comma's UI uses.
    let pathWidth: CGFloat = 0.48
    let allowThrottle = frame.allowThrottle ?? true
    let corridorColor: UIColor = allowThrottle
      ? UIColor(red: 0/255, green: 225/255, blue: 115/255, alpha: 0.38)    // green when throttle OK
      : UIColor(red: 60/255, green: 140/255, blue: 255/255, alpha: 0.35)   // blue when braking
    let ribbon = DriveSceneRenderer.makeRibbonGeometry(path: path, halfWidth: pathWidth, color: corridorColor, yOffset: 0.012)
    pathNode.geometry = ribbon

    // Glowing borders match the corridor color
    let borderWidth: CGFloat = 0.035
    let brightColor: UIColor = allowThrottle
      ? UIColor(red: 0/255, green: 255/255, blue: 130/255, alpha: 0.90)
      : UIColor(red: 100/255, green: 170/255, blue: 255/255, alpha: 0.90)

    let leftPath = DriveSceneRenderer.offsetPath(path, offset: Double(pathWidth))
    let rightPath = DriveSceneRenderer.offsetPath(path, offset: Double(-pathWidth))

    pathLeftEdgeNode.geometry = DriveSceneRenderer.makeRibbonGeometry(path: leftPath, halfWidth: borderWidth, color: brightColor, yOffset: 0.014)
    pathRightEdgeNode.geometry = DriveSceneRenderer.makeRibbonGeometry(path: rightPath, halfWidth: borderWidth, color: brightColor, yOffset: 0.014)

    SCNTransaction.begin()
    SCNTransaction.animationDuration = 0.15
    pathNode.opacity = 1
    pathLeftEdgeNode.opacity = 1
    pathRightEdgeNode.opacity = 1
    SCNTransaction.commit()
  }

  // MARK: - Lead car + 3D Floating HUD (Prominent large distance display)

  private func updateLeadCar(frame: DriveFrame) {
    let lead = frame.leadOne
    let hasLead = lead?.hasLead ?? false

    if hasLead, let lead {
      let pos = modelToScene(xForward: lead.distance, yLeft: lead.yRel)
      let matchedCar = frame.detectedCars.first(where: { abs($0.x - lead.distance) < 6.0 })
      let leadSpeed = matchedCar?.speed

      let hudImg = DriveSceneRenderer.createLeadHudImage(
        distance: lead.distance,
        speedMps: leadSpeed,
        isMetric: frame.isMetric
      )

      let imgSize = hudImg.size
      let aspect = imgSize.width / max(imgSize.height, 1)
      let planeHeight: CGFloat = 1.15
      let planeWidth = planeHeight * aspect

      let geom = SCNPlane(width: planeWidth, height: planeHeight)
      let mat = SCNMaterial()
      mat.diffuse.contents = hudImg
      mat.lightingModel = .constant
      mat.isDoubleSided = true
      mat.writesToDepthBuffer = false
      geom.materials = [mat]

      leadHudNode.geometry = geom

      SCNTransaction.begin()
      SCNTransaction.animationDuration = 0.1
      leadCarNode.position = SCNVector3(pos.x, 0.0, pos.z)
      leadCarNode.opacity = 1
      SCNTransaction.commit()
      leadWasVisible = true
    } else {
      if leadWasVisible {
        SCNTransaction.begin()
        SCNTransaction.animationDuration = 0.3
        leadCarNode.opacity = 0
        SCNTransaction.commit()
        leadWasVisible = false
      }
    }
  }

  // MARK: - Detected cars (pool of 3)

  private func updateDetectedCars(frame: DriveFrame) {
    let cars = frame.detectedCars.prefix(3)
    for (i, node) in detectedCarNodes.enumerated() {
      if i < cars.count {
        let car = cars[i]
        let pos = modelToScene(xForward: car.x, yLeft: car.y)
        let alpha = min(max(car.probability, 0.15), 1.0) * 0.5
        SCNTransaction.begin()
        SCNTransaction.animationDuration = 0.12
        node.position = SCNVector3(pos.x, 0.0, pos.z)
        node.opacity = CGFloat(alpha)
        SCNTransaction.commit()
        carSlotPositions[i] = pos
      } else {
        SCNTransaction.begin()
        SCNTransaction.animationDuration = 0.3
        node.opacity = 0
        SCNTransaction.commit()
      }
    }
  }

  // MARK: - Coordinate mapping

  func modelToScene(xForward: Double, yLeft: Double) -> SCNVector3 {
    SCNVector3(-Float(yLeft), 0, -Float(xForward))
  }

  // MARK: - Static builders & procedural assets

  private static func offsetPath(_ path: PathData, offset: Double) -> PathData {
    let count = min(path.x.count, path.y.count)
    var newY: [Double] = []
    newY.reserveCapacity(count)
    for i in 0..<count {
      newY.append(path.y[i] + offset)
    }
    return PathData(x: path.x, y: newY)
  }

  private static func createLeadHudImage(distance: Double, speedMps: Double?, isMetric: Bool) -> UIImage {
    let distStr = String(format: "%.1f m", distance)
    let textStr: String
    if let speedMps, speedMps > 0 {
      let speedVal = isMetric ? speedMps * 3.6 : speedMps * 2.23693629
      let unit = isMetric ? "km/h" : "mph"
      textStr = String(format: "%@  ·  %.0f %@", distStr, speedVal, unit)
    } else {
      textStr = distStr
    }

    let font = UIFont.systemFont(ofSize: 42, weight: .bold)
    let attributes: [NSAttributedString.Key: Any] = [
      .font: font,
      .foregroundColor: UIColor.white
    ]
    let textSize = textStr.size(withAttributes: attributes)
    let paddingX: CGFloat = 32
    let paddingY: CGFloat = 16
    let canvasSize = CGSize(width: textSize.width + paddingX * 2, height: textSize.height + paddingY * 2)

    let renderer = UIGraphicsImageRenderer(size: canvasSize)
    return renderer.image { ctx in
      let rect = CGRect(origin: .zero, size: canvasSize)
      let path = UIBezierPath(roundedRect: rect, cornerRadius: canvasSize.height / 2)

      UIColor(red: 10/255, green: 14/255, blue: 24/255, alpha: 0.90).setFill()
      path.fill()
      UIColor(red: 0/255, green: 229/255, blue: 255/255, alpha: 0.95).setStroke()
      path.lineWidth = 4.0
      path.stroke()

      let textRect = CGRect(
        x: paddingX,
        y: paddingY,
        width: textSize.width,
        height: textSize.height
      )
      textStr.draw(in: textRect, withAttributes: attributes)
    }
  }

  private static func makeSkyBackdropNode() -> SCNNode {
    let plane = SCNPlane(width: 300, height: 160)
    let mat = SCNMaterial()
    mat.diffuse.contents = DriveSceneRenderer.createSkyGradientImage()
    mat.lightingModel = .constant
    mat.writesToDepthBuffer = false
    plane.materials = [mat]
    let node = SCNNode(geometry: plane)
    node.position = SCNVector3(0, 50, -180)
    return node
  }

  private static func createSkyGradientImage() -> UIImage {
    let size = CGSize(width: 256, height: 512)
    let renderer = UIGraphicsImageRenderer(size: size)
    return renderer.image { ctx in
      let colors = [
        UIColor(red: 4/255, green: 6/255, blue: 11/255, alpha: 1.0).cgColor,
        UIColor(red: 10/255, green: 15/255, blue: 26/255, alpha: 1.0).cgColor,
        UIColor(red: 22/255, green: 32/255, blue: 50/255, alpha: 1.0).cgColor,
        UIColor(red: 14/255, green: 18/255, blue: 28/255, alpha: 1.0).cgColor
      ] as CFArray
      let locations: [CGFloat] = [0.0, 0.35, 0.50, 1.0]
      if let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: colors, locations: locations) {
        ctx.cgContext.drawLinearGradient(
          gradient,
          start: CGPoint(x: 0, y: 0),
          end: CGPoint(x: 0, y: size.height),
          options: []
        )
      }
    }
  }

  private static func makeSurfaceNode() -> SCNNode {
    let plane = SCNPlane(width: 60, height: 300)
    let mat = SCNMaterial()
    mat.diffuse.contents = UIColor(red: 14/255, green: 18/255, blue: 26/255, alpha: 1.0)
    mat.lightingModel = .phong
    mat.roughness.contents = 0.8
    mat.isDoubleSided = true
    plane.materials = [mat]
    let node = SCNNode(geometry: plane)
    node.eulerAngles = SCNVector3(Float.pi / 2, 0, 0)
    node.position = SCNVector3(0, 0, -120)
    return node
  }

  private static func makeGridNode() -> SCNNode {
    let parent = SCNNode()
    for z in stride(from: -10, through: -180, by: -10) {
      let line = SCNPlane(width: 40, height: 0.12)
      let mat = SCNMaterial()
      mat.diffuse.contents = UIColor(red: 0.35, green: 0.55, blue: 0.75, alpha: 0.09)
      mat.lightingModel = .constant
      mat.writesToDepthBuffer = false
      line.materials = [mat]
      let node = SCNNode(geometry: line)
      node.eulerAngles = SCNVector3(Float.pi / 2, 0, 0)
      node.position = SCNVector3(0, 0.003, Float(z))
      parent.addChildNode(node)
    }
    return parent
  }

  private static func makeCarNode(
    width: CGFloat,
    height: CGFloat,
    length: CGFloat,
    bodyColor: UIColor,
    isLead: Bool
  ) -> SCNNode {
    let rootNode = SCNNode()

    // 1. Shadow / Ground Glow Disk
    let shadowPlane = SCNPlane(width: width * 1.3, height: length * 1.25)
    let shadowMat = SCNMaterial()
    shadowMat.diffuse.contents = isLead
      ? UIColor(red: 0.0, green: 0.9, blue: 1.0, alpha: 0.25)
      : UIColor.black.withAlphaComponent(0.45)
    shadowMat.lightingModel = .constant
    shadowMat.writesToDepthBuffer = false
    shadowPlane.materials = [shadowMat]
    let shadowNode = SCNNode(geometry: shadowPlane)
    shadowNode.eulerAngles = SCNVector3(Float.pi / 2, 0, 0)
    shadowNode.position = SCNVector3(0, 0.002, 0)
    rootNode.addChildNode(shadowNode)

    // 2. Main Chassis Box
    let bodyHeight = height * 0.55
    let bodyBox = SCNBox(width: width, height: bodyHeight, length: length, chamferRadius: 0.15)
    let bodyMat = SCNMaterial()
    bodyMat.diffuse.contents = bodyColor
    bodyMat.metalness.contents = 0.35
    bodyMat.roughness.contents = 0.35
    bodyMat.lightingModel = .phong
    bodyBox.materials = [bodyMat]
    let bodyNode = SCNNode(geometry: bodyBox)
    bodyNode.position = SCNVector3(0, bodyHeight / 2, 0)
    rootNode.addChildNode(bodyNode)

    // 3. Cabin / Windshield Box
    let cabinHeight = height * 0.45
    let cabinLength = length * 0.52
    let cabinBox = SCNBox(width: width * 0.86, height: cabinHeight, length: cabinLength, chamferRadius: 0.12)
    let cabinMat = SCNMaterial()
    cabinMat.diffuse.contents = UIColor(red: 15/255, green: 20/255, blue: 30/255, alpha: 0.88)
    cabinMat.metalness.contents = 0.8
    cabinMat.roughness.contents = 0.1
    cabinMat.lightingModel = .phong
    cabinBox.materials = [cabinMat]
    let cabinNode = SCNNode(geometry: cabinBox)
    cabinNode.position = SCNVector3(0, bodyHeight + cabinHeight / 2 - 0.02, -length * 0.06)
    rootNode.addChildNode(cabinNode)

    // 4. Rear LED Taillights (Emissive Glow)
    let tailWidth = width * 0.88
    let tailBox = SCNBox(width: tailWidth, height: 0.10, length: 0.06, chamferRadius: 0.02)
    let tailMat = SCNMaterial()
    let tailColor = isLead
      ? UIColor(red: 0.0, green: 0.9, blue: 1.0, alpha: 1.0)
      : UIColor(red: 1.0, green: 0.2, blue: 0.2, alpha: 1.0)
    tailMat.diffuse.contents = tailColor
    tailMat.emission.contents = tailColor
    tailMat.lightingModel = .constant
    tailBox.materials = [tailMat]
    let tailNode = SCNNode(geometry: tailBox)
    tailNode.position = SCNVector3(0, bodyHeight * 0.7, length / 2 + 0.01)
    rootNode.addChildNode(tailNode)

    // 5. Lead Car Tracking Halo
    if isLead {
      let haloBox = SCNBox(width: 1.4, height: 0.08, length: 0.8, chamferRadius: 0.04)
      let haloMat = SCNMaterial()
      let haloColor = UIColor(red: 0.0, green: 0.9, blue: 1.0, alpha: 0.9)
      haloMat.diffuse.contents = haloColor
      haloMat.emission.contents = haloColor
      haloMat.lightingModel = .constant
      haloBox.materials = [haloMat]
      let haloNode = SCNNode(geometry: haloBox)
      haloNode.position = SCNVector3(0, height + 0.4, 0)
      rootNode.addChildNode(haloNode)
    }

    return rootNode
  }

  private static func makeRibbonGeometry(
    path: PathData,
    halfWidth: CGFloat,
    color: UIColor,
    yOffset: Float
  ) -> SCNGeometry {
    let count = min(path.x.count, path.y.count)
    guard count >= 2 else { return SCNGeometry() }

    var vertices: [SCNVector3] = []
    vertices.reserveCapacity(count * 2)

    for i in 0..<count {
      let xF = Float(path.x[i])
      let yL = Float(path.y[i])
      let leftX = -(yL + Float(halfWidth))
      let rightX = -(yL - Float(halfWidth))
      let z = -xF
      vertices.append(SCNVector3(leftX, yOffset, z))
      vertices.append(SCNVector3(rightX, yOffset, z))
    }

    var indices: [Int32] = []
    indices.reserveCapacity((count - 1) * 6)
    for i in 0..<(count - 1) {
      let li = Int32(i * 2)
      let ri = Int32(i * 2 + 1)
      let li1 = Int32((i + 1) * 2)
      let ri1 = Int32((i + 1) * 2 + 1)
      indices.append(contentsOf: [li, ri, li1, ri, ri1, li1])
    }

    let source = SCNGeometrySource(vertices: vertices)
    let element = SCNGeometryElement(indices: indices, primitiveType: .triangles)
    let geom = SCNGeometry(sources: [source], elements: [element])
    let mat = SCNMaterial()
    mat.diffuse.contents = color
    mat.lightingModel = .constant
    mat.isDoubleSided = true
    mat.writesToDepthBuffer = false
    geom.materials = [mat]
    return geom
  }
}
