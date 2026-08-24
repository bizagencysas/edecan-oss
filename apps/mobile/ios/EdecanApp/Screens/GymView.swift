import EdecanKit
import SwiftUI
import UIKit

/// Pantalla de entrenamiento del gimnasio. Carga la sesión activa (o el plan
/// de hoy con un botón "Empezar") y deja registrar series, pausar/reanudar y
/// terminar. El cronómetro usa `Text(timerInterval:)`, así que avanza solo sin
/// depender de updates del backend.
struct GymView: View {
    @Environment(SessionStore.self) private var session
    @State private var viewModel = GymViewModel()

    var body: some View {
        let vm = viewModel
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if let error = vm.errorMensaje {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .padding(12)
                        .tarjetaVidrio(esquina: 14)
                }

                if vm.cargando && vm.ejercicios.isEmpty && !vm.tieneSesion {
                    ProgressView("Cargando entrenamiento…")
                        .frame(maxWidth: .infinity, minHeight: 160)
                } else if vm.terminada {
                    sesionTerminada(vm)
                } else if vm.tieneSesion {
                    sesionActiva(vm)
                } else if vm.plan != nil {
                    planDeHoy(vm)
                } else {
                    EmptyStateView(
                        icono: "figure.strengthtraining.traditional",
                        titulo: "Sin entrenamiento hoy",
                        descripcion: "No hay un plan para hoy. Vuelve mañana o responde el aviso en el chat."
                    )
                }
            }
            .padding()
        }
        .background(EdecanTheme.degradado.opacity(0.06).ignoresSafeArea())
        .navigationTitle("Entrenamiento")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await viewModel.health.solicitarAutorizacion()
            await viewModel.cargar(client: session.client)
        }
        .refreshable {
            await viewModel.cargar(client: session.client)
        }
    }

    // MARK: - Sesión terminada

    @ViewBuilder
    private func sesionTerminada(_ vm: GymViewModel) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Sesión terminada", systemImage: "checkmark.circle.fill")
                .font(.headline)
                .foregroundStyle(.green)
            if !vm.tituloPlan.isEmpty {
                Text(vm.tituloPlan).font(.subheadline)
            }
            if let mensaje = vm.mensajeBackend, !mensaje.isEmpty {
                Text(mensaje)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 16)
    }

    // MARK: - Sesión activa

    @ViewBuilder
    private func sesionActiva(_ vm: GymViewModel) -> some View {
        cabecera(vm)

        if vm.imageFileID != nil {
            CollageView(fileId: vm.imageFileID, client: session.client)
                .frame(maxWidth: .infinity)
                .aspectRatio(1, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        }

        controlesSesion(vm)

        if let mensaje = vm.mensajeBackend, !mensaje.isEmpty {
            Text(mensaje)
                .font(.footnote)
                .foregroundStyle(.secondary)
        }

        listaEjercicios(vm)
    }

    @ViewBuilder
    private func cabecera(_ vm: GymViewModel) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if !vm.tituloPlan.isEmpty {
                Text(vm.tituloPlan)
                    .font(.title3.weight(.bold))
            }
            if let objetivo = vm.sesion?.plan.objective, !objetivo.isEmpty {
                Text(objetivo)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            if let inicio = vm.fechaInicio {
                HStack(spacing: 6) {
                    Image(systemName: "timer")
                        .foregroundStyle(.secondary)
                    Text(inicio, style: .timer)
                        .font(.title2.monospacedDigit().weight(.semibold))
                }
            }
            if vm.pausada {
                Label("En pausa", systemImage: "pause.circle.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.orange)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func controlesSesion(_ vm: GymViewModel) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) { botonesControl(vm) }
            VStack(alignment: .leading, spacing: 8) { botonesControl(vm) }
        }
    }

    @ViewBuilder
    private func botonesControl(_ vm: GymViewModel) -> some View {
        if vm.esPlaneada {
            Button {
                Task { await vm.iniciar(client: session.client) }
            } label: {
                Label("Iniciar entrenamiento", systemImage: "play.fill").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.green)
        } else if vm.pausada {
            Button {
                Task { await vm.reanudar(client: session.client) }
            } label: {
                Label("Reanudar", systemImage: "play.fill").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.green)
        } else {
            Button {
                Task { await vm.pausar(client: session.client) }
            } label: {
                Label("Pausar", systemImage: "pause.fill").frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
        }
        if !vm.esPlaneada {
            Button(role: .destructive) {
                Task { await vm.terminar(client: session.client) }
            } label: {
                Label("Terminar", systemImage: "stop.fill").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.red)
        }
    }

    @ViewBuilder
    private func listaEjercicios(_ vm: GymViewModel) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Ejercicios")
                .font(.headline)
            ForEach(Array(vm.ejercicios.enumerated()), id: \.offset) { indice, ejercicio in
                filaEjercicio(vm, ejercicio: ejercicio, indice: indice)
            }
        }
    }

    @ViewBuilder
    private func filaEjercicio(_ vm: GymViewModel, ejercicio: GymEjercicio, indice: Int) -> some View {
        let progreso = vm.progreso(para: indice)
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(ejercicio.name)
                    .font(.subheadline.weight(.semibold))
                Spacer()
                if let progreso {
                    Text("\(progreso.setsDone)/\(progreso.setsTotal) series")
                        .font(.caption.weight(.semibold))
                        .monospacedDigit()
                }
            }
            if !ejercicio.muscle.isEmpty {
                Text(ejercicio.muscle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 8) {
                etiqueta("\(ejercicio.sets) series")
                etiqueta("\(ejercicio.repetitions) reps")
                if ejercicio.restSeconds > 0 {
                    etiqueta("Descanso \(ejercicio.restSeconds)s")
                }
            }
            if !ejercicio.notes.isEmpty {
                Text(ejercicio.notes)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            HStack(spacing: 8) {
                TextField("Reps", text: bindingReps(indice))
                    .keyboardType(.numberPad)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 72)
                TextField("Kg (opcional)", text: bindingPeso(indice))
                    .keyboardType(.decimalPad)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 120)
                Spacer(minLength: 0)
                Button {
                    Task { await vm.registrarSerie(ejercicio: ejercicio, indice: indice, client: session.client) }
                } label: {
                    Label("+1 serie", systemImage: "plus.circle.fill")
                        .font(.footnote.weight(.semibold))
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(14)
        .tarjetaVidrio(esquina: 16)
    }

    @ViewBuilder
    private func etiqueta(_ texto: String) -> some View {
        Text(texto)
            .font(.caption2.weight(.medium))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(.quaternary.opacity(0.4), in: Capsule())
    }

    // MARK: - Plan de hoy (sin sesión)

    @ViewBuilder
    private func planDeHoy(_ vm: GymViewModel) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            if !vm.tituloPlan.isEmpty {
                Text(vm.tituloPlan).font(.title3.weight(.bold))
            }
            if let objetivo = vm.plan?.objective, !objetivo.isEmpty {
                Text(objetivo).font(.subheadline).foregroundStyle(.secondary)
            }
            if vm.imageFileID != nil {
                CollageView(fileId: vm.imageFileID, client: session.client)
                    .frame(maxWidth: .infinity)
                    .aspectRatio(1, contentMode: .fit)
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            }
            Text("\(vm.ejercicios.count) ejercicios")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Button {
                Task { await vm.empezar(client: session.client) }
            } label: {
                Label("Empezar", systemImage: "figure.strengthtraining.traditional")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(EdecanTheme.degradado, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .foregroundStyle(.white)
            }
            .buttonStyle(.plain)

            ForEach(Array(vm.ejercicios.enumerated()), id: \.offset) { indice, ejercicio in
                VStack(alignment: .leading, spacing: 6) {
                    Text(ejercicio.name).font(.subheadline.weight(.semibold))
                    if !ejercicio.muscle.isEmpty {
                        Text(ejercicio.muscle).font(.caption).foregroundStyle(.secondary)
                    }
                    HStack(spacing: 8) {
                        etiqueta("\(ejercicio.sets) series")
                        etiqueta("\(ejercicio.repetitions) reps")
                    }
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .tarjetaVidrio(esquina: 14)
            }
        }
    }

    // MARK: - Helpers

    private func bindingReps(_ indice: Int) -> Binding<String> {
        Binding(
            get: { viewModel.repeticiones[indice] ?? "" },
            set: { viewModel.repeticiones[indice] = $0 }
        )
    }

    private func bindingPeso(_ indice: Int) -> Binding<String> {
        Binding(
            get: { viewModel.pesos[indice] ?? "" },
            set: { viewModel.pesos[indice] = $0 }
        )
    }
}

/// Descarga y muestra el collage del plan con el Bearer del tenant vía
/// `APIClient.descargarArtefacto` — el mismo camino autenticado que usa el
/// resto de la app para artefactos. Nunca usa una URL pública.
private struct CollageView: View {
    let fileId: String?
    let client: APIClient?

    @State private var imagen: UIImage?

    var body: some View {
        Group {
            if let imagen {
                Image(uiImage: imagen).resizable().scaledToFit()
            } else {
                placeholder
            }
        }
        .task(id: fileId) {
            await cargar()
        }
    }

    private var placeholder: some View {
        ZStack {
            Rectangle().fill(.quaternary.opacity(0.3))
            Image(systemName: "figure.strengthtraining.traditional")
                .font(.largeTitle)
                .foregroundStyle(.secondary)
        }
    }

    private func cargar() async {
        imagen = nil
        guard let fileId, let client else { return }
        do {
            let download = try await client.descargarArtefacto(
                ArtifactRef(fileId: fileId, filename: "gym-collage.png", mime: "image/png")
            )
            imagen = UIImage(data: download.data)
        } catch {
            // best-effort: si no descarga, queda el placeholder.
        }
    }
}