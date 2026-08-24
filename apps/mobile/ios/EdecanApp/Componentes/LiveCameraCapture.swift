import AVFoundation
import CoreImage
import Foundation
import Observation
import UIKit

/// Captura viva acotada para acompañar un turno de voz.
@MainActor
@Observable
final class LiveCameraCapture {
    let session = AVCaptureSession()
    var ultimoFrameJPEG: Data?
    var errorMensaje: String?
    private var configurada = false
    private let outputDelegate: LiveCameraOutputDelegate

    init() {
        outputDelegate = LiveCameraOutputDelegate()
        outputDelegate.onFrame = { [weak self] data in
            Task { @MainActor [weak self] in
                self?.ultimoFrameJPEG = data
            }
        }
    }

    func iniciar() async {
        guard !configurada else {
            if !session.isRunning { session.startRunning() }
            return
        }
        let permitido = await AVCaptureDevice.requestAccess(for: .video)
        guard permitido else {
            errorMensaje = "Activa el permiso de Cámara para usar la visión en vivo."
            return
        }
        do {
            session.beginConfiguration()
            session.sessionPreset = .medium
            guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
                throw CameraError.noDisponible
            }
            let input = try AVCaptureDeviceInput(device: device)
            guard session.canAddInput(input) else { throw CameraError.noDisponible }
            session.addInput(input)

            let output = AVCaptureVideoDataOutput()
            output.videoSettings = [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
            ]
            output.alwaysDiscardsLateVideoFrames = true
            guard session.canAddOutput(output) else { throw CameraError.noDisponible }
            session.addOutput(output)
            output.setSampleBufferDelegate(
                outputDelegate,
                queue: DispatchQueue(label: "cc.edecan.camera")
            )
            session.commitConfiguration()
            configurada = true
            session.startRunning()
        } catch {
            session.commitConfiguration()
            errorMensaje = error.localizedDescription
        }
    }

    func detener() {
        if session.isRunning { session.stopRunning() }
        ultimoFrameJPEG = nil
    }
}

private enum CameraError: LocalizedError {
    case noDisponible

    var errorDescription: String? {
        "La cámara no está disponible en este dispositivo."
    }
}

private final class LiveCameraOutputDelegate: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate, @unchecked Sendable {
    var onFrame: (@Sendable (Data) -> Void)?
    private let lock = NSLock()
    private var ultimoFrame = Date.distantPast
    private let context = CIContext()

    func captureOutput(
        _ output: AVCaptureOutput,
        didOutput sampleBuffer: CMSampleBuffer,
        from connection: AVCaptureConnection
    ) {
        _ = output
        _ = connection
        let ahora = Date()
        lock.lock()
        guard ahora.timeIntervalSince(ultimoFrame) >= 0.33 else {
            lock.unlock()
            return
        }
        ultimoFrame = ahora
        lock.unlock()
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let image = CIImage(cvPixelBuffer: pixelBuffer)
        guard let cgImage = context.createCGImage(image, from: image.extent),
              let jpeg = UIImage(cgImage: cgImage).jpegData(compressionQuality: 0.65)
        else { return }
        onFrame?(jpeg)
    }
}
