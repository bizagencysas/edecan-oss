// pcm_playback_offline_render_test.swift
//
// Standalone macOS harness that validates, WITHOUT a mic or a device, the two
// pieces of `ReproductorPCMStream` that caused "no audio" on build 195:
//
//   1. The s16le → Float32 non-interleaved conversion (including the odd-byte
//      boundary leftover handling). This is a faithful copy of
//      `ReproductorPCMStream.rellenar(_:conPCM16:)` + the leftover logic in
//      `agregar(_:)`. It mirrors the production algorithm; drift between the
//      two copies is a known maintenance risk, not a proof of identical code.
//
//   2. That a Float32 non-interleaved mono buffer scheduled into an
//      AVAudioPlayerNode connected to the main mixer actually renders to
//      NON-SILENT audio, using AVAudioEngine manual (offline) rendering. This
//      exercises the exact buffer format + mixer path used in production
//      (real-time `.dataPlayedBack` completion is NOT reproducible offline —
//      see the summary at the end).
//
// Run from the repo root:
//   swiftc -O apps/mobile/ios/tests/pcm_playback_offline_render_test.swift \
//     -o /tmp/pcm_playback_offline_render_test && /tmp/pcm_playback_offline_render_test
//
// Exit code 0 = all executed checks passed. A check that cannot run in this
// environment is reported as SKIPPED with a reason, never as a pass.

import AVFoundation
import Darwin
import Foundation

// Asegura que cada `print` se vacíe antes de un SIGTRAP/abort del engine, para
// que el progreso sea visible incluso si AVAudioEngine aborta el proceso.
setlinebuf(stdout)

// MARK: - Minimal test harness

var fallos = 0
var totales = 0
var saltos = 0

func comprobar(_ nombre: String, _ condicion: Bool, _ detalle: @autoclosure () -> String = "") {
    totales += 1
    if condicion {
        print("PASS  \(nombre)")
    } else {
        fallos += 1
        let extra = detalle()
        print("FAIL  \(nombre)\(extra.isEmpty ? "" : " — \(extra)")")
    }
}

func comprobarCerca(_ nombre: String, _ actual: Float, _ esperado: Float, tolerancia: Float = 0.0001) {
    totales += 1
    let diff = abs(actual - esperado)
    if diff <= tolerancia {
        print("PASS  \(nombre)")
    } else {
        fallos += 1
        print("FAIL  \(nombre) — actual=\(actual) esperado=\(esperado) diff=\(diff)")
    }
}

// MARK: - 1. Conversion s16le → Float32 (mirror of production algorithm)

struct ConversorPCM16 {
    private var sobrante = Data()

    /// Mirror of `ReproductorPCMStream.agregar`'s leftover handling + `rellenar`.
    mutating func convertir(_ data: Data) -> [Float] {
        let combinado = sobrante + data
        let bytesCompletos = (combinado.count / 2) * 2
        guard bytesCompletos > 0 else {
            sobrante = combinado
            return []
        }
        sobrante = Data(combinado.suffix(combinado.count - bytesCompletos))
        let datos = Data(combinado.prefix(bytesCompletos))
        return Self.aFloats(datos)
    }

    /// Mirror of `ReproductorPCMStream.rellenar`: s16le (little-endian) →
    /// normalized Float32.
    static func aFloats(_ datos: Data) -> [Float] {
        let cantidad = datos.count / 2
        var salida = [Float]()
        salida.reserveCapacity(cantidad)
        datos.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            let bytes = raw.bindMemory(to: UInt8.self)
            for indice in 0..<cantidad {
                let bajo = UInt16(bytes[indice * 2])
                let alto = UInt16(bytes[indice * 2 + 1])
                let valor = Int16(bitPattern: bajo | (alto << 8))
                salida.append(Float(valor) / 32_768.0)
            }
        }
        return salida
    }
}

func probarConversion() {
    print("--- conversión s16le → Float32 ---")

    comprobarCerca("cero → 0.0", ConversorPCM16.aFloats(Data([0x00, 0x00]))[0], 0.0)
    comprobarCerca("menos uno → -1/32768", ConversorPCM16.aFloats(Data([0xFF, 0xFF]))[0], -1.0 / 32768.0)
    comprobarCerca("más uno → +1/32768", ConversorPCM16.aFloats(Data([0x01, 0x00]))[0], 1.0 / 32768.0)
    comprobarCerca("máximo 32767 → 32767/32768", ConversorPCM16.aFloats(Data([0xFF, 0x7F]))[0], 32_767.0 / 32768.0)
    comprobarCerca("mínimo -32768 → -1.0", ConversorPCM16.aFloats(Data([0x00, 0x80]))[0], -1.0)

    // Varias muestras a la vez, en orden.
    let multiples = ConversorPCM16.aFloats(Data([0x00, 0x00, 0xFF, 0x7F, 0x00, 0x80]))
    comprobar("múltiples: cantidad", multiples.count == 3, "\(multiples.count) != 3")
    if multiples.count == 3 {
        comprobarCerca("múltiples[0]", multiples[0], 0.0)
        comprobarCerca("múltiples[1]", multiples[1], 32_767.0 / 32768.0)
        comprobarCerca("múltiples[2]", multiples[2], -1.0)
    }

    // Límite de byte impar: el byte suelto espera al próximo chunk.
    var c = ConversorPCM16()
    let primera = c.convertir(Data([0x01, 0x00, 0x02])) // muestra 1 + byte suelto 0x02
    comprobar("byte impar: primera entrega 1 muestra", primera.count == 1, "\(primera.count) != 1")
    if primera.count == 1 {
        comprobarCerca("byte impar: primera muestra", primera[0], 1.0 / 32768.0)
    }
    let segunda = c.convertir(Data([0x00])) // completa la muestra 2 (0x02 0x00)
    comprobar("byte impar: segunda entrega 1 muestra", segunda.count == 1, "\(segunda.count) != 1")
    if segunda.count == 1 {
        comprobarCerca("byte impar: segunda muestra", segunda[0], 2.0 / 32768.0)
    }

    // Byte suelto solo (chunk de 1 byte): no produce muestra y se acumula.
    var d = ConversorPCM16()
    let vacio = d.convertir(Data([0x00]))
    comprobar("chunk de 1 byte: no entrega muestras", vacio.isEmpty)
}

// MARK: - 2. Offline render de un buffer Float32 no intercalado

func probarRenderOffline() {
    print("--- render offline: buffer Float32 no intercalado ---")

    let engine = AVAudioEngine()
    let player = AVAudioPlayerNode()
    guard let fmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24_000, channels: 1, interleaved: false) else {
        fallos += 1
        print("FAIL  render: no se pudo crear el formato Float32 no intercalado")
        return
    }

    engine.attach(player)
    engine.connect(player, to: engine.mainMixerNode, format: fmt)
    let formatoSalida = engine.mainMixerNode.outputFormat(forBus: 0)

    do {
        try engine.enableManualRenderingMode(.offline, format: formatoSalida, maximumFrameCount: 4_096)
    } catch {
        saltos += 1
        print("SKIP  render: enableManualRenderingMode no disponible en este host — \(error.localizedDescription)")
        return
    }

    do {
        try engine.start()
    } catch {
        saltos += 1
        print("SKIP  render: engine.start() falló en modo offline — \(error.localizedDescription)")
        return
    }
    player.play()

    // Seno de 440 Hz, amplitud 0.5, 0.5 s (12 000 frames) en Float32 mono.
    let frames: AVAudioFrameCount = 12_000
    guard let buffer = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: frames) else {
        fallos += 1
        print("FAIL  render: no se pudo crear el buffer")
        return
    }
    buffer.frameLength = frames
    guard let canal = buffer.floatChannelData?[0] else {
        fallos += 1
        print("FAIL  render: floatChannelData nulo para formato no intercalado")
        return
    }
    for indice in 0..<Int(frames) {
        canal[indice] = Float(0.5 * sin(2.0 * Double.pi * 440.0 * Double(indice) / 24_000.0))
    }

    player.scheduleBuffer(buffer, completionCallbackType: .dataConsumed) { _ in
        // En render manual solo `.dataConsumed` está garantizado; no se usa
        // para medir el contenido, solo para confirmar el drenaje.
    }

    // Render offline de una cantidad ACOTADA de frames. La fuente son 12 000
    // frames a 24 kHz (0.5 s) que el mixer re-muestrea a la tasa de salida del
    // host (típicamente 44.1 kHz → ~22 050 frames). En render manual un
    // `AVAudioPlayerNode` ya drenado NO devuelve `.insufficientDataFromInputNode`
    // (sigue rindiendo silencio en `.success`), así que NO se puede iterar hasta
    // ese estado: se rinde un tope con margen y se mide el pico.
    var pico: Float = 0
    var framesRenderizados: AVAudioFrameCount = 0
    guard let renderBuffer = AVAudioPCMBuffer(pcmFormat: formatoSalida, frameCapacity: 4_096) else {
        fallos += 1
        print("FAIL  render: no se pudo crear el buffer de salida")
        return
    }

    let iteraciones = 16 // 16 × 4096 = 65 536 frames ≈ 1.5 s a 44.1 kHz
    do {
        for _ in 0..<iteraciones {
            renderBuffer.frameLength = 4_096
            let estado = try engine.renderOffline(4_096, to: renderBuffer)
            guard estado == .success else {
                break
            }
            framesRenderizados += renderBuffer.frameLength
            pico = max(pico, picoAbsoluto(renderBuffer))
        }
    } catch {
        fallos += 1
        print("FAIL  render: renderOffline lanzó — \(error.localizedDescription)")
        return
    }

    player.stop()
    engine.stop()

    // El mixer entrega estéreo (o el formato de salida del host); el seno mono
    // de amplitud 0.5 debe aparecer en la salida con amplitud no nula. El
    // umbral es deliberadamente laxo (>0.2) para absorber la ley de paneo del
    // mixer (mono→estéreo) sin debilitar la afirmación de "no es silencio".
    comprobar("render: drenó frames (≥ 12 000)", framesRenderizados >= frames, "\(framesRenderizados) < \(frames)")
    comprobar("render: salida NO es silencio (pico > 0.2)", pico > 0.2, "pico=\(pico)")
    comprobar("render: amplitud plausible (pico ≤ 1.0)", pico <= 1.0, "pico=\(pico)")
}

func picoAbsoluto(_ buffer: AVAudioPCMBuffer) -> Float {
    var pico: Float = 0
    let canales = Int(buffer.format.channelCount)
    guard canales > 0, let data = buffer.floatChannelData else { return 0 }
    let frames = Int(buffer.frameLength)
    for canal in 0..<canales {
        let ptr = data[canal]
        for indice in 0..<frames {
            pico = max(pico, abs(ptr[indice]))
        }
    }
    return pico
}

// MARK: - main

probarConversion()
probarRenderOffline()

print("")
print("RESUMEN: \(totales) checks, \(fallos) fallos, \(saltos) saltados")
if fallos > 0 {
    exit(1)
}
print("Todos los checks ejecutados pasaron.")
exit(0)