import SwiftUI
import UIKit
import EdecanKit

/// Mini estudio humano: una idea entra, un borrador editable y compartible
/// sale. La creación usa la misma herramienta privada del chat, pero esta
/// pantalla se encarga del progreso y de la entrega manual de punta a punta.
struct ContentStudioView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(SessionStore.self) private var session

    @State private var plataforma: SocialContentPlatform = .linkedin
    @State private var tema = ""
    @State private var objetivo = "Enseñar algo útil"
    @State private var tono = "Claro y humano"
    @State private var incluirImagen = true
    @State private var creando = false
    @State private var etapa = ""
    @State private var errorMensaje: String?
    @State private var avisoMensaje: String?
    @State private var resultado: SocialContentDraft?
    @State private var partesEditadas: [String] = []
    @State private var imagenData: Data?
    @State private var elementosCompartir: [Any] = []
    @State private var mostrarCompartir = false
    @State private var archivoTemporal: URL?
    @State private var tareaCreacion: Task<Void, Never>?

    private let objetivos = [
        "Enseñar algo útil", "Contar una historia", "Lanzar un producto", "Generar conversación",
    ]
    private let tonos = ["Claro y humano", "Profesional", "Directo", "Inspirador", "Divertido"]

    private var textoParaCompartir: String {
        partesEditadas.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: "\n\n")
    }

    private var resultadoValido: Bool {
        !partesEditadas.isEmpty && partesEditadas.allSatisfy {
            let clean = $0.trimmingCharacters(in: .whitespacesAndNewlines)
            return !clean.isEmpty && clean.count <= plataforma.characterLimit
        }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    if resultado == nil {
                        formulario
                    } else {
                        entrega
                    }
                }
                .padding()
            }
            .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
            .navigationTitle("Crear")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cerrar") { dismiss() }
                }
            }
            .sheet(isPresented: $mostrarCompartir, onDismiss: {
                limpiarTemporal()
                elementosCompartir = []
            }) {
                HojaCompartirContenido(items: elementosCompartir)
            }
            .onDisappear {
                tareaCreacion?.cancel()
                limpiarTemporal()
            }
        }
    }

    private var formulario: some View {
        Group {
            VStack(alignment: .leading, spacing: 6) {
                Text("De una idea a una publicación completa")
                    .font(.title2.bold())
                Text("Edecan prepara el texto y, si quieres, una imagen. Tú revisas y compartes.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            TextField("¿Sobre qué quieres publicar?", text: $tema, axis: .vertical)
                .lineLimit(3...6)
                .padding(14)
                .tarjetaVidrio(esquina: 16)
                .disabled(creando)

            VStack(alignment: .leading, spacing: 8) {
                Text("¿Dónde?").font(.headline)
                Picker("Plataforma", selection: $plataforma) {
                    ForEach(SocialContentPlatform.allCases) { option in
                        Text(option.label).tag(option)
                    }
                }
                .pickerStyle(.segmented)
            }

            selector("Objetivo", seleccion: $objetivo, opciones: objetivos)
            selector("Tono", seleccion: $tono, opciones: tonos)

            Toggle("Crear también una imagen original", isOn: $incluirImagen)
                .tint(EdecanTheme.morado)
                .padding(14)
                .tarjetaVidrio(esquina: 16)
                .disabled(creando)

            if creando {
                HStack(spacing: 12) {
                    ProgressView()
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Creando tu borrador…").font(.headline)
                        Text(etapa).font(.caption).foregroundStyle(.secondary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(14)
                .tarjetaVidrio(esquina: 16)
                .accessibilityElement(children: .combine)
            }

            if let errorMensaje {
                Text(errorMensaje)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
            }

            Button(action: crear) {
                Label(creando ? "Creando…" : "Crear borrador", systemImage: "sparkles")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(EdecanTheme.morado)
            .disabled(creando || tema.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

            ideasRapidas
        }
    }

    private var entrega: some View {
        Group {
            VStack(alignment: .leading, spacing: 6) {
                Label("Borrador listo", systemImage: "checkmark.circle.fill")
                    .font(.title2.bold())
                    .foregroundStyle(.green)
                Text("Revísalo, ajusta lo que quieras y compártelo cuando esté a tu gusto.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            if !etapa.isEmpty, imagenData == nil {
                HStack(spacing: 12) {
                    ProgressView()
                    Text(etapa).font(.callout).foregroundStyle(.secondary)
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .tarjetaVidrio(esquina: 16)
            }

            ForEach(Array(partesEditadas.indices), id: \.self) { (index: Int) in
                VStack(alignment: .leading, spacing: 8) {
                    if partesEditadas.count > 1 {
                        Text("Parte \(index + 1) de \(partesEditadas.count)").font(.headline)
                    } else {
                        Text("Texto para \(plataforma.label)").font(.headline)
                    }
                    TextEditor(text: $partesEditadas[index])
                        .frame(minHeight: partesEditadas.count > 1 ? 120 : 220)
                        .padding(8)
                        .scrollContentBackground(.hidden)
                        .background(.background.opacity(0.72), in: RoundedRectangle(cornerRadius: 12))
                    let count = partesEditadas[index].count
                    Text("\(count)/\(plataforma.characterLimit) caracteres")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(
                            count <= plataforma.characterLimit ? Color.secondary : Color.red
                        )
                        .frame(maxWidth: .infinity, alignment: .trailing)
                }
                .padding(14)
                .tarjetaVidrio(esquina: 16)
            }

            if let image = imagenData.flatMap(UIImage.init(data:)) {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Imagen").font(.headline)
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                    if let alt = resultado?.altText, !alt.isEmpty {
                        Text("Texto alternativo: \(alt)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(14)
                .tarjetaVidrio(esquina: 16)
            }

            if let avisoMensaje {
                Text(avisoMensaje)
                    .font(.callout)
                    .foregroundStyle(.orange)
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
            }

            HStack(spacing: 12) {
                Button(action: copiar) {
                    Label("Copiar", systemImage: "doc.on.doc")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(!resultadoValido)

                Button(action: compartir) {
                    Label("Compartir", systemImage: "square.and.arrow.up")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(EdecanTheme.morado)
                .disabled(!resultadoValido)
            }

            Text("Nada se publica automáticamente.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .center)

            Button("Crear otra publicación") { reiniciar() }
                .frame(maxWidth: .infinity)
        }
    }

    private func selector(_ title: String, seleccion: Binding<String>, opciones: [String]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            Picker(title, selection: seleccion) {
                ForEach(opciones, id: \.self) { Text($0).tag($0) }
            }
            .pickerStyle(.menu)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(14)
        .tarjetaVidrio(esquina: 16)
        .disabled(creando)
    }

    private var ideasRapidas: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Ideas rápidas").font(.headline)
            ForEach([
                "Explica una idea difícil de forma sencilla",
                "Convierte una experiencia en una lección útil",
                "Presenta un producto sin sonar a publicidad",
            ], id: \.self) { idea in
                Button(idea) { tema = idea }
                    .buttonStyle(.bordered)
                    .disabled(creando)
            }
        }
        .padding(.top, 8)
    }

    private func crear() {
        guard let client = session.client else {
            errorMensaje = "No hay una sesión lista. Vuelve a conectar Edecan e inténtalo de nuevo."
            return
        }
        tareaCreacion?.cancel()
        creando = true
        errorMensaje = nil
        avisoMensaje = nil
        etapa = incluirImagen ? "Preparando el texto y la imagen" : "Preparando el texto"
        let request = SocialContentRequest(
            platform: plataforma,
            topic: tema.trimmingCharacters(in: .whitespacesAndNewlines),
            objective: objetivo,
            tone: tono,
            withImage: incluirImagen
        )
        tareaCreacion = Task { @MainActor in
            do {
                let draft = try await ContentStudioService(client: client).create(request)
                try Task.checkCancellation()
                resultado = draft
                partesEditadas = draft.parts
                await LocalNotificationScheduler.completed(
                    kind: "content",
                    id: String(draft.copy.hashValue),
                    title: "Contenido listo",
                    body: "Tu borrador ya está listo para revisar.",
                    route: .create
                )
                imagenData = nil
                creando = false
                etapa = ""
                if let artifact = draft.imageArtifact {
                    etapa = "Cargando la imagen"
                    do {
                        imagenData = try await client.descargarArtefacto(artifact).data
                        etapa = ""
                    } catch is CancellationError {
                        throw CancellationError()
                    } catch {
                        etapa = ""
                        avisoMensaje = "El texto está listo, pero la imagen no se pudo cargar. Puedes compartir el texto ahora."
                    }
                }
            } catch is CancellationError {
                return
            } catch {
                creando = false
                errorMensaje = error.localizedDescription
            }
        }
    }

    private func copiar() {
        UIPasteboard.general.string = textoParaCompartir
        avisoMensaje = "Texto copiado."
    }

    private func compartir() {
        limpiarTemporal()
        var items: [Any] = [textoParaCompartir]
        if let imageData = imagenData, let artifact = resultado?.imageArtifact {
            let safeName = artifact.filename
                .replacingOccurrences(of: "/", with: "-")
                .replacingOccurrences(of: "\\", with: "-")
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("edecan-\(UUID().uuidString)-\(safeName)")
            if (try? imageData.write(to: url, options: .atomic)) != nil {
                archivoTemporal = url
                items.append(url)
            }
        }
        elementosCompartir = items
        mostrarCompartir = true
    }

    private func reiniciar() {
        limpiarTemporal()
        resultado = nil
        partesEditadas = []
        imagenData = nil
        avisoMensaje = nil
        errorMensaje = nil
        etapa = ""
        tema = ""
    }

    private func limpiarTemporal() {
        if let archivoTemporal {
            try? FileManager.default.removeItem(at: archivoTemporal)
        }
        archivoTemporal = nil
    }
}

private struct HojaCompartirContenido: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}
