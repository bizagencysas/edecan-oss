import SwiftUI
import EdecanKit

/// Autocompletado del composer del chat: `@` muestra menciones (compañeros,
/// equipos, conectores y workspaces) y `/` muestra comandos de skill. La lista
/// se carga de forma perezosa la primera vez que aparece un token; no hay nada
/// que "gane" aquí, solo inserta texto en el campo.
struct SugerenciaComposer: Identifiable, Equatable {
    let id: String
    let titulo: String
    let subtitulo: String?
    let icono: String
    let insercion: String
}

/// Datos fuente del autocompletado, cargados una sola vez por sesión de vista.
struct DatosMenciones {
    var cargados = false
    var agentes: [PersistentWorker] = []
    var equipos: [Team] = []
    var workspaces: [Workspace] = []
    var conectores: [MCPServerSummary] = []
    var skills: [SkillSummary] = []
}

enum AutocompletadoComposer {
    /// El segmento activo (sin espacios) al final del texto, si empieza con
    /// `@` o `/`. Devuelve el prefijo y la consulta (lo que sigue al prefijo).
    static func tokenActivo(en texto: String) -> (prefijo: String, query: String)? {
        let segmento = String(texto[rangoUltimoSegmento(en: texto)])
        if segmento.hasPrefix("@") { return ("@", String(segmento.dropFirst())) }
        if segmento.hasPrefix("/") { return ("/", String(segmento.dropFirst())) }
        return nil
    }

    /// Rango del último "segmento" (la cola del texto sin espacios) para
    /// reemplazarlo al elegir una sugerencia.
    static func rangoUltimoSegmento(en texto: String) -> Range<String.Index> {
        if let indice = texto.lastIndex(where: { $0.isWhitespace }) {
            return texto.index(after: indice)..<texto.endIndex
        }
        return texto.startIndex..<texto.endIndex
    }

    static let comandosLocales: [SugerenciaComposer] = [
        SugerenciaComposer(
            id: "cmd-clear", titulo: "/clear", subtitulo: "Reinicia el contexto de este chat",
            icono: "broom", insercion: "/clear "
        ),
        SugerenciaComposer(
            id: "cmd-branch", titulo: "/branch", subtitulo: "Abre una rama nueva del chat",
            icono: "arrow.triangle.branch", insercion: "/branch "
        ),
        SugerenciaComposer(
            id: "cmd-rewind", titulo: "/rewind", subtitulo: "Rebobina el último turno",
            icono: "backward.end.fill", insercion: "/rewind "
        ),
    ]

    static func sugerencias(
        prefijo: String, query: String, datos: DatosMenciones
    ) -> [SugerenciaComposer] {
        let q = query.lowercased()
        func coincide(_ s: String) -> Bool {
            q.isEmpty || s.lowercased().contains(q)
        }
        if prefijo == "/" {
            var comandos = comandosLocales.filter { coincide($0.titulo) }
            for skill in datos.skills where coincide(skill.nombre) {
                comandos.append(SugerenciaComposer(
                    id: "skill-\(skill.id)", titulo: "/\(skill.nombre)",
                    subtitulo: skill.descripcion, icono: "sparkles",
                    insercion: "/\(skill.nombre) "
                ))
            }
            return comandos
        }

        var menciones: [SugerenciaComposer] = []
        for agente in datos.agentes where coincide(agente.nombreVisible) {
            menciones.append(SugerenciaComposer(
                id: "agent-\(agente.id)", titulo: agente.nombreVisible,
                subtitulo: "Compañero · \(agente.cargoVisible)",
                icono: "person.fill", insercion: "@\(agente.nombreVisible) "
            ))
        }
        for equipo in datos.equipos where coincide(equipo.name) {
            menciones.append(SugerenciaComposer(
                id: "team-\(equipo.id)", titulo: equipo.name,
                subtitulo: "Equipo", icono: "bubble.left.and.bubble.right.fill",
                insercion: "@\(equipo.name) "
            ))
        }
        for workspace in datos.workspaces where coincide(workspace.name) {
            menciones.append(SugerenciaComposer(
                id: "workspace-\(workspace.id)", titulo: workspace.name,
                subtitulo: "Workspace", icono: "square.stack.3d.up.fill",
                insercion: "@\(workspace.name) "
            ))
        }
        for conector in datos.conectores where coincide(conector.nombre) {
            menciones.append(SugerenciaComposer(
                id: "connector-\(conector.nombre)", titulo: conector.nombre,
                subtitulo: "Conector", icono: "cable.connector",
                insercion: "@\(conector.nombre) "
            ))
        }
        return menciones
    }
}

/// Panel flotante del autocompletado, dibujado justo encima del composer.
struct PanelMenciones: View {
    let sugerencias: [SugerenciaComposer]
    let onSeleccionar: (SugerenciaComposer) -> Void

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(sugerencias) { sugerencia in
                    Button {
                        onSeleccionar(sugerencia)
                    } label: {
                        HStack(spacing: 10) {
                            Image(systemName: sugerencia.icono)
                                .font(.subheadline)
                                .foregroundStyle(EdecanTheme.morado)
                                .frame(width: 22)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(sugerencia.titulo)
                                    .font(.subheadline.weight(.medium))
                                    .foregroundStyle(.primary)
                                if let subtitulo = sugerencia.subtitulo, !subtitulo.isEmpty {
                                    Text(subtitulo)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                }
                            }
                            Spacer(minLength: 0)
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    if sugerencia.id != sugerencias.last?.id {
                        Divider().padding(.leading, 44)
                    }
                }
            }
        }
        .frame(maxHeight: 220)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .padding(.horizontal, 4)
    }
}