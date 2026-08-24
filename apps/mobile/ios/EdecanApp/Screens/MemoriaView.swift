import SwiftUI
import EdecanKit

/// Memoria de Edecán sobre la persona: un acceso claro a "¿qué recuerdas de
/// mí?" y a "olvida todo". No gestiona la memoria directamente — ambas
/// acciones son prompts que se prefillan en el chat universal (`TabRouter.
/// pedir`) y la persona tiene la última palabra antes de enviarlos. Así la
/// memoria sigue siendo responsabilidad del backend/agente, no de la UI.
struct MemoriaView: View {
    @Environment(TabRouter.self) private var router

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                introduccion
                botonPreguntar
                botonOlvidar
            }
            .padding()
        }
        .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
        .navigationTitle("Memoria")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var introduccion: some View {
        VStack(spacing: 14) {
            ZStack {
                Circle()
                    .fill(EdecanTheme.degradado)
                    .frame(width: 64, height: 64)
                Image(systemName: "brain.head.profile.fill")
                    .font(.system(size: 28, weight: .semibold))
                    .foregroundStyle(.white)
            }

            Text("Lo que Edecán sabe de ti")
                .font(.title3.bold())

            Text("Edecán recuerda cosas sobre ti a medida que conversan. Puedes preguntarle qué recuerda o pedirle que olvide algo.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity)
        .padding(24)
        .tarjetaVidrio(esquina: 22)
    }

    private var botonPreguntar: some View {
        Button {
            router.pedir("¿Qué recuerdas de mí?")
        } label: {
            filaAccion(
                icono: "text.bubble.fill",
                titulo: "Preguntar qué recuerdas",
                subtitulo: "Abre el chat con la pregunta lista para enviar",
                tintada: false
            )
        }
        .buttonStyle(.plain)
    }

    private var botonOlvidar: some View {
        Button {
            router.pedir("Olvida todo lo que recuerdas de mí")
        } label: {
            filaAccion(
                icono: "trash.fill",
                titulo: "Pedir que olvide todo",
                subtitulo: "Abre el chat con la orden lista para enviar",
                tintada: true
            )
        }
        .buttonStyle(.plain)
    }

    private func filaAccion(icono: String, titulo: String, subtitulo: String, tintada: Bool) -> some View {
        HStack(spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(tintada ? Color.red.opacity(0.12) : EdecanTheme.morado.opacity(0.13))
                    .frame(width: 48, height: 48)
                Image(systemName: icono)
                    .foregroundStyle(tintada ? Color.red : EdecanTheme.morado)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(titulo)
                    .font(.headline)
                    .foregroundStyle(tintada ? Color.red : .primary)
                Text(subtitulo)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 4)
            Image(systemName: "arrow.up.right")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.tertiary)
        }
        .padding(16)
        .tarjetaVidrio(esquina: 20)
        .contentShape(Rectangle())
    }
}
