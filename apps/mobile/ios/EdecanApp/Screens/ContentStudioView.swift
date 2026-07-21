import SwiftUI

/// Estudio simple: no es otro producto, solo convierte opciones humanas en
/// una petición precisa para el mismo chat universal.
struct ContentStudioView: View {
    @Environment(TabRouter.self) private var router
    @State private var plataforma = "LinkedIn"
    @State private var tema = ""
    @State private var objetivo = "Enseñar algo útil"
    @State private var tono = "Claro y humano"
    @State private var incluirImagen = true

    private let plataformas = ["LinkedIn", "X", "Instagram", "Facebook", "Threads", "TikTok"]
    private let objetivos = ["Enseñar algo útil", "Contar una historia", "Lanzar un producto", "Generar conversación"]
    private let tonos = ["Claro y humano", "Profesional", "Directo", "Inspirador", "Divertido"]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    cabecera
                    TextField("¿Sobre qué quieres publicar?", text: $tema, axis: .vertical)
                        .lineLimit(3...6)
                        .padding(14)
                        .tarjetaVidrio(esquina: 16)

                    selector("Plataforma", seleccion: $plataforma, opciones: plataformas)
                    selector("Objetivo", seleccion: $objetivo, opciones: objetivos)
                    selector("Tono", seleccion: $tono, opciones: tonos)

                    Toggle("Crear también una imagen original", isOn: $incluirImagen)
                        .tint(EdecanTheme.morado)
                        .padding(14)
                        .tarjetaVidrio(esquina: 16)

                    Button(action: crear) {
                        Label("Crear en el chat", systemImage: "sparkles")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(EdecanTheme.morado)
                    .disabled(tema.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                    ideasRapidas
                }
                .padding()
            }
            .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
            .navigationTitle("Crear")
        }
    }

    private var cabecera: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("De una idea a una publicación completa")
                .font(.title2.bold())
            Text("Edecan prepara el copy, la imagen, el texto alternativo y los archivos para compartir.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    private func selector(_ titulo: String, seleccion: Binding<String>, opciones: [String]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(titulo).font(.headline)
            Picker(titulo, selection: seleccion) {
                ForEach(opciones, id: \.self) { Text($0).tag($0) }
            }
            .pickerStyle(.menu)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(14)
        .tarjetaVidrio(esquina: 16)
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
            }
        }
        .padding(.top, 8)
    }

    private func crear() {
        let imagen = incluirImagen ? "Incluye una imagen original y su texto alternativo." : "No generes imagen."
        router.pedir(
            "Crea un paquete de contenido para \(plataforma) sobre: \(tema). " +
            "Objetivo: \(objetivo). Tono: \(tono). \(imagen) " +
            "Usa la herramienta crear_contenido_social y entrégame todos los archivos aquí. " +
            "No publiques nada sin mi confirmación."
        )
        tema = ""
    }
}
