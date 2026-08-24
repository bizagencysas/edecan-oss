import SwiftUI
import VisionKit

/// Cámara nativa, limitada a QR. El contenido se entrega una sola vez y la
/// validación del enlace sigue viviendo en `PairingLink`; el visor nunca
/// interpreta ni persiste el token efímero.
struct QRScannerView: UIViewControllerRepresentable {
    let onScan: @MainActor (String) -> Void
    let onError: @MainActor (String) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onScan: onScan)
    }

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let scanner = DataScannerViewController(
            recognizedDataTypes: [.barcode(symbologies: [.qr])],
            qualityLevel: .accurate,
            recognizesMultipleItems: false,
            isHighFrameRateTrackingEnabled: false,
            isPinchToZoomEnabled: true,
            isGuidanceEnabled: true,
            isHighlightingEnabled: true
        )
        scanner.delegate = context.coordinator
        context.coordinator.scanner = scanner
        return scanner
    }

    func updateUIViewController(_ scanner: DataScannerViewController, context: Context) {
        guard !scanner.isScanning,
              !context.coordinator.didDeliverResult,
              !context.coordinator.didRequestStart
        else { return }
        context.coordinator.didRequestStart = true
        do {
            try scanner.startScanning()
        } catch {
            onError("No pude iniciar la cámara. Revisa el permiso de Cámara de Edecán en Ajustes e inténtalo otra vez.")
        }
    }

    static func dismantleUIViewController(
        _ scanner: DataScannerViewController,
        coordinator: Coordinator
    ) {
        scanner.stopScanning()
        coordinator.scanner = nil
    }

    @MainActor
    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        private let onScan: @MainActor (String) -> Void
        fileprivate weak var scanner: DataScannerViewController?
        fileprivate var didDeliverResult = false
        fileprivate var didRequestStart = false

        init(onScan: @escaping @MainActor (String) -> Void) {
            self.onScan = onScan
        }

        func dataScanner(
            _ dataScanner: DataScannerViewController,
            didAdd addedItems: [RecognizedItem],
            allItems: [RecognizedItem]
        ) {
            guard !didDeliverResult else { return }
            guard let value = addedItems.compactMap(Self.qrValue(from:)).first else { return }
            didDeliverResult = true
            dataScanner.stopScanning()
            onScan(value)
        }

        private static func qrValue(from item: RecognizedItem) -> String? {
            guard case let .barcode(barcode) = item else { return nil }
            return barcode.payloadStringValue
        }
    }
}
