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
    /// Único descanso entre series activo a la vez: índice del ejercicio,
    /// segundos que quedan y total. Un solo `Task` lo cuenta; si la vista o la
    /// sesión muere, morir con ella es aceptable.
    @State private var descansoActivo: (indice: Int, restantes: Int, total: Int)?
    /// Readiness de HealthKit (sueño/HRV): cómo está el cuerpo hoy.
    @State private var readiness: String?
    /// Cambiar un ejercicio por otro: tap en el nombre abre la hoja; la IA
    /// interpreta el nombre (aunque no sea técnico) y propone reemplazo.
    @State private var swapIndice: Int?

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
                } else if let completada = vm.sesionCompletadaHoy {
                    yaEntrenasteHoy(completada, vm)
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
        .background(EdecanTheme.degradado.opacity(0.12).ignoresSafeArea())
        .navigationTitle("Entrenamiento")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: Binding(
            get: { swapIndice.map { SwapTarget(indice: $0) } },
            set: { swapIndice = $0?.indice }
        )) { destino in
            GymSwapEjercicioView(vm: viewModel, indice: destino.indice, client: session.client)
        }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                NavigationLink {
                    GymFormCoachView()
                } label: {
                    Image(systemName: "figure.strengthtraining.traditional")
                        .accessibilityLabel("Coach de técnica")
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink {
                    GymHistorialView()
                } label: {
                    Image(systemName: "chart.bar.fill")
                        .accessibilityLabel("Historial de entrenamiento")
                }
            }
        }
        .task {
            await viewModel.health.solicitarAutorizacion()
            await viewModel.cargar(client: session.client)
            readiness = await viewModel.health.readinessResumen()
            viewModel.enviarEstadoAlWatch()
        }
        .refreshable {
            await viewModel.cargar(client: session.client)
        }
        .onChange(of: viewModel.health.frecuenciaCardiaca) { _, _ in
            viewModel.enviarMetricasAlWatch()
        }
        .onChange(of: viewModel.health.caloriasActivas) { _, _ in
            viewModel.enviarMetricasAlWatch()
        }
    }

    // MARK: - Sesión terminada

    @ViewBuilder
    /// Pantalla de cierre cuando el entrenamiento de HOY ya está completado:
    /// en vez de ofrecer "Iniciar entrenamiento" otra vez, celebra lo hecho y
    /// muestra el resumen real (series, ejercicios, racha) sin inventar datos.
    private func yaEntrenasteHoy(_ sesion: GymSession, _ vm: GymViewModel) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 42))
                    .foregroundStyle(.green)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Ya entrenaste hoy")
                        .font(.title3.weight(.bold))
                    Text(sesion.plan.title)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }

            HStack(spacing: 10) {
                chipResumen(
                    icono: "repeat",
                    texto: "\(sesion.series.count) series"
                )
                chipResumen(
                    icono: "figure.strengthtraining.traditional",
                    texto: "\(sesion.progress.exercises.count) ejercicios"
                )
                if vm.rachaDias > 0 {
                    chipResumen(icono: "flame.fill", texto: "Racha: \(vm.rachaDias) día\(vm.rachaDias == 1 ? "" : "s")")
                }
            }

            Text("Buen trabajo. El próximo plan llega mañana a tu chat.")
                .font(.footnote)
                .foregroundStyle(.secondary)

            NavigationLink {
                GymHistorialView()
            } label: {
                Label("Ver historial", systemImage: "chart.bar.fill")
            }
            .buttonStyle(.bordered)
            .tint(EdecanTheme.morado)
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 18)
    }

    private func chipResumen(icono: String, texto: String) -> some View {
        HStack(spacing: 5) {
            Image(systemName: icono)
                .font(.caption2)
            Text(texto)
                .font(.caption.weight(.medium))
        }
        .foregroundStyle(EdecanTheme.morado)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(EdecanTheme.morado.opacity(0.10), in: Capsule())
    }

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
            if let resumen = vm.resumenIA, !resumen.isEmpty {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "sparkles")
                        .font(.subheadline)
                        .foregroundStyle(EdecanTheme.morado)
                    Text(resumen)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .tarjetaVidrio(esquina: 14)
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
        VStack(alignment: .leading, spacing: 10) {
            if !vm.tituloPlan.isEmpty {
                Text(vm.tituloPlan)
                    .font(.title3.weight(.bold))
            }
            if let objetivo = vm.sesion?.plan.objective, !objetivo.isEmpty {
                Text(objetivo)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            HStack(alignment: .center, spacing: 14) {
                if let inicio = vm.fechaInicio {
                    HStack(spacing: 8) {
                        Image(systemName: "timer")
                            .foregroundStyle(EdecanTheme.morado)
                        Text(inicio, style: .timer)
                            .font(.system(.title, design: .rounded).monospacedDigit().weight(.bold))
                    }
                }
                Spacer(minLength: 0)
                if vm.health.siguiendo {
                    if let fc = vm.health.frecuenciaCardiaca {
                        metrica(sistema: "heart.fill", texto: "\(Int(fc.rounded()))", unidad: "bpm", color: .red)
                    }
                    if let kcal = vm.health.caloriasActivas {
                        metrica(sistema: "flame.fill", texto: "\(Int(kcal.rounded()))", unidad: "kcal", color: .orange)
                    }
                }
            }
            if vm.pausada {
                Label("En pausa", systemImage: "pause.circle.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.orange)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 18)
    }

    @ViewBuilder
    private func metrica(sistema: String, texto: String, unidad: String, color: Color) -> some View {
        HStack(spacing: 5) {
            Image(systemName: sistema)
                .foregroundStyle(color)
            Text(texto)
                .font(.subheadline.weight(.bold))
                .monospacedDigit()
            Text(unidad)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(.ultraThinMaterial, in: Capsule())
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
                Label("Iniciar entrenamiento", systemImage: "play.fill")
                    .font(.footnote.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 13)
                    .background(EdecanTheme.degradado, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .foregroundStyle(.white)
            }
            .buttonStyle(.plain)
        } else if vm.pausada {
            Button {
                Task { await vm.reanudar(client: session.client) }
            } label: {
                Label("Reanudar", systemImage: "play.fill")
                    .font(.footnote.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 13)
                    .background(Color.green.gradient, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .foregroundStyle(.white)
            }
            .buttonStyle(.plain)
        } else {
            Button {
                Task { await vm.pausar(client: session.client) }
            } label: {
                Label("Pausar", systemImage: "pause.fill")
                    .font(.footnote.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 13)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            .buttonStyle(.plain)
        }
        if !vm.esPlaneada {
            Button(role: .destructive) {
                Task { await vm.terminar(client: session.client) }
            } label: {
                Label("Terminar", systemImage: "stop.fill")
                    .font(.footnote.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 13)
                    .background(Color.red.gradient, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .foregroundStyle(.white)
            }
            .buttonStyle(.plain)
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
                    .onTapGesture { vm.swapIndicePedir(indice) }
                Image(systemName: "arrow.triangle.2.circlepath")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .onTapGesture { vm.swapIndicePedir(indice) }
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
            }
            if !ejercicio.notes.isEmpty {
                Text(ejercicio.notes)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let previo = vm.sesion?.previo?.first(where: { $0.idx == indice }) {
                memoriaLine(vm, previo: previo, indice: indice)
            }
            let metaHoy = textoMeta(vm, indice: indice)
            if !metaHoy.isEmpty {
                metaPildora(metaHoy)
            }

            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) {
                    TextField("Reps", text: bindingReps(indice))
                        .keyboardType(.numberPad)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 72)
                    pesoControl(indice)
                    Spacer(minLength: 0)
                    if ejercicio.restSeconds > 0 {
                        botonDescanso(indice, total: ejercicio.restSeconds)
                    }
                    Button {
                        Task { await vm.registrarSerie(ejercicio: ejercicio, indice: indice, client: session.client) }
                    } label: {
                        Label("+1 serie", systemImage: "plus.circle.fill")
                            .font(.footnote.weight(.semibold))
                    }
                    .buttonStyle(.borderedProminent)
                }
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 8) {
                        TextField("Reps", text: bindingReps(indice))
                            .keyboardType(.numberPad)
                            .textFieldStyle(.roundedBorder)
                            .frame(maxWidth: 72)
                        pesoControl(indice)
                    }
                    HStack(spacing: 8) {
                        if ejercicio.restSeconds > 0 {
                            botonDescanso(indice, total: ejercicio.restSeconds)
                        }
                        Button {
                            Task { await vm.registrarSerie(ejercicio: ejercicio, indice: indice, client: session.client) }
                        } label: {
                            Label("+1 serie", systemImage: "plus.circle.fill")
                                .font(.footnote.weight(.semibold))
                        }
                        .buttonStyle(.borderedProminent)
                    }
                }
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
            if let readiness, !readiness.isEmpty {
                HStack(spacing: 8) {
                    Image(systemName: "moon.zzz.fill")
                        .foregroundStyle(EdecanTheme.morado)
                    Text(readiness)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .tarjetaVidrio(esquina: 14)
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

    /// Memoria de progresión (sobrecarga progresiva): "La semana pasada: 45kg ×
    /// 10" más una pista verde cuando el usuario ya marcó un valor que supera
    /// el registro previo, o cuando todavía no registró ninguna serie.
    @ViewBuilder
    private func memoriaLine(_ vm: GymViewModel, previo: GymPrevioEjercicio, indice: Int) -> some View {
        let texto = memoriaTexto(previo)
        VStack(alignment: .leading, spacing: 3) {
            if !texto.isEmpty {
                HStack(spacing: 4) {
                    Image(systemName: "clock.arrow.circlepath")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Text(texto)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            if mostrarPistaSuperar(vm, previo, indice: indice) {
                Text("+2.5kg o +2 reps para superar")
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.green)
            }
        }
    }

    private func memoriaTexto(_ previo: GymPrevioEjercicio) -> String {
        if let peso = previo.weightKg, let reps = previo.repetitions {
            return "La semana pasada: \(formatearPeso(peso))kg × \(reps)"
        }
        if let reps = previo.repetitions {
            return "La semana pasada: × \(reps) reps"
        }
        if let peso = previo.weightKg {
            return "La semana pasada: \(formatearPeso(peso))kg"
        }
        return ""
    }

    /// Texto de la meta de sobrecarga de hoy para un ejercicio ("Hoy: intenta
    /// 42.5kg × 9"), según qué traiga el backend en `sesion.meta`. Vacío cuando
    /// no hay meta (o la entrada no trae peso ni reps): en ese caso no se pinta.
    private func textoMeta(_ vm: GymViewModel, indice: Int) -> String {
        guard let meta = vm.sesion?.meta?.first(where: { $0.idx == indice }) else { return "" }
        switch (meta.pesoObjetivo, meta.repeticionesObjetivo) {
        case let (peso?, reps?):
            return "Hoy: intenta \(formatearPeso(peso))kg × \(reps)"
        case let (peso?, nil):
            return "Hoy: intenta \(formatearPeso(peso))kg"
        case let (nil, reps?):
            return "Hoy: intenta \(reps) reps"
        case (nil, nil):
            return ""
        }
    }

    /// Píldora verde suave con la meta del día, al pie de la memoria de
    /// progresión. Mismo tono que la pista "+2.5kg o +2 reps para superar".
    @ViewBuilder
    private func metaPildora(_ texto: String) -> some View {
        HStack(spacing: 5) {
            Image(systemName: "scope")
                .font(.caption2)
            Text(texto)
                .font(.caption2.weight(.medium))
        }
        .foregroundStyle(.green)
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color.green.opacity(0.12), in: Capsule())
    }

    private func mostrarPistaSuperar(_ vm: GymViewModel, _ previo: GymPrevioEjercicio, indice: Int) -> Bool {
        let setsHechas = vm.progreso(para: indice)?.setsDone ?? 0
        if setsHechas == 0 { return true }
        if let texto = viewModel.pesos[indice],
            let peso = Double(texto.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: ",", with: ".")),
            let previoPeso = previo.weightKg,
            peso > previoPeso {
            return true
        }
        if let texto = viewModel.repeticiones[indice],
            let reps = Int(texto.trimmingCharacters(in: .whitespaces)),
            let previoReps = previo.repetitions,
            reps > previoReps {
            return true
        }
        return false
    }

    /// Botones "−"/"+" que suben o bajan el peso escrito en pasos de 2.5.
    @ViewBuilder
    private func pesoControl(_ indice: Int) -> some View {
        HStack(spacing: 4) {
            Button {
                ajustarPeso(indice, delta: -2.5)
            } label: {
                Text("−")
                    .font(.caption.weight(.bold))
                    .frame(width: 30, height: 30)
            }
            .buttonStyle(.bordered)

            TextField("Kg (opcional)", text: bindingPeso(indice))
                .keyboardType(.decimalPad)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 110)
                .multilineTextAlignment(.center)

            Button {
                ajustarPeso(indice, delta: 2.5)
            } label: {
                Text("+")
                    .font(.caption.weight(.bold))
                    .frame(width: 30, height: 30)
            }
            .buttonStyle(.bordered)
        }
    }

    /// Botón de descanso entre series: inactivo muestra "Descanso Ns"; activo,
    /// la cuenta regresiva en vivo. Un solo descanso a la vez.
    @ViewBuilder
    private func botonDescanso(_ indice: Int, total: Int) -> some View {
        Button {
            Task { await iniciarDescanso(indice: indice, total: total) }
        } label: {
            Text(descansoActivo?.indice == indice ? "\(descansoActivo?.restantes ?? 0)s" : "Descanso \(total)s")
                .font(.footnote.weight(.semibold))
                .monospacedDigit()
        }
        .buttonStyle(.bordered)
        .tint(descansoActivo?.indice == indice ? EdecanTheme.morado : Color.accentColor)
        .disabled(descansoActivo?.indice == indice)
    }

    private func ajustarPeso(_ indice: Int, delta: Double) {
        let actual = viewModel.pesos[indice] ?? ""
        let limpio = actual.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: ",", with: ".")
        let base = Double(limpio) ?? 0
        let nuevo = base + delta
        guard nuevo >= 0 else { return }
        viewModel.pesos[indice] = formatearPeso(nuevo)
    }

    /// Punto decimal y sin ceros innecesarios: 42.50 → "42.5", 45.00 → "45".
    private func formatearPeso(_ valor: Double) -> String {
        let texto = String(format: "%.2f", valor).replacingOccurrences(of: ",", with: ".")
        var resultado = texto
        while resultado.hasSuffix("0") {
            resultado = String(resultado.dropLast())
        }
        if resultado.hasSuffix(".") {
            resultado = String(resultado.dropLast())
        }
        return resultado
    }

    private func iniciarDescanso(indice: Int, total: Int) async {
        guard total > 0 else { return }
        let nombre = viewModel.ejercicios.indices.contains(indice)
            ? viewModel.ejercicios[indice].name : nil
        descansoActivo = (indice: indice, restantes: total, total: total)
        viewModel.enviarDescansoAlWatch(restante: total, ejercicio: nombre)
        while descansoActivo?.indice == indice, (descansoActivo?.restantes ?? 0) > 0 {
            try? await Task.sleep(for: .seconds(1))
            guard !Task.isCancelled else { return }
            if descansoActivo?.indice == indice {
                descansoActivo?.restantes -= 1
                viewModel.enviarDescansoAlWatch(restante: descansoActivo?.restantes, ejercicio: nombre)
            }
        }
        guard descansoActivo?.indice == indice else { return }
        descansoActivo = nil
        viewModel.enviarDescansoAlWatch(restante: 0, ejercicio: nombre)
        UINotificationFeedbackGenerator().notificationOccurred(.success)
    }

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
// MARK: - Cambiar un ejercicio (IA interpreta el nombre)

struct SwapTarget: Identifiable {
    let indice: Int
    var id: Int { indice }
}

/// Hoja «¿Por cuál lo cambias?»: texto libre (aunque no sea técnico), la IA
/// propone el ejercicio y alternativas; el dueño aplica o escoge de la lista.
struct GymSwapEjercicioView: View {
    let vm: GymViewModel
    let indice: Int
    let client: APIClient?

    @Environment(\.dismiss) private var dismiss
    @State private var nombre = ""
    @State private var cargando = false
    @State private var propuesta: GymEjercicio?
    @State private var alternativas: [GymEjercicio] = []
    @State private var interpreto: String?
    @State private var elegido: GymEjercicio?
    @State private var errorSwap: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("¿Por cuál lo cambias?") {
                    TextField(
                        "Escribe el nombre o el músculo (p. ej. «pecho», «press banca»)",
                        text: $nombre,
                        axis: .vertical
                    )
                    .lineLimit(1...3)
                    .submitLabel(.done)
                    Button {
                        Task { await pedirOpciones() }
                    } label: {
                        if cargando {
                            ProgressView().controlSize(.small)
                        } else {
                            Label("Ver opciones de la IA", systemImage: "sparkles")
                        }
                    }
                    .disabled(nombre.trimmingCharacters(in: .whitespaces).count < 2 || cargando)
                }

                if let interpreto {
                    Section {
                        Text(interpreto).font(.caption).foregroundStyle(.secondary)
                    }
                }
                if let propuesta {
                    Section("Propuesta de la IA") {
                        filaEjercicio(propuesta, seleccionado: elegido?.name == propuesta.name)
                    }
                }
                if !alternativas.isEmpty {
                    Section("Alternativas") {
                        ForEach(alternativas, id: \.name) { alt in
                            filaEjercicio(alt, seleccionado: elegido?.name == alt.name)
                        }
                    }
                }
                if let errorSwap {
                    Section { Text(errorSwap).font(.footnote).foregroundStyle(.red) }
                }

                if propuesta != nil || !alternativas.isEmpty {
                    Section {
                        Button {
                            Task { await aplicar() }
                        } label: {
                            if cargando {
                                ProgressView().controlSize(.small)
                            } else {
                                Label("Aplicar cambio", systemImage: "checkmark.circle.fill")
                                    .font(.subheadline.weight(.semibold))
                            }
                        }
                        .disabled(elegido == nil || cargando)
                    }
                }
            }
            .navigationTitle("Cambiar ejercicio")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Cerrar") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func filaEjercicio(_ ejercicio: GymEjercicio, seleccionado: Bool) -> some View {
        Button {
            elegido = ejercicio
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(ejercicio.name).font(.subheadline.weight(.medium))
                    if !ejercicio.muscle.isEmpty {
                        Text(ejercicio.muscle).font(.caption).foregroundStyle(.secondary)
                    }
                    if !ejercicio.notes.isEmpty {
                        Text(ejercicio.notes).font(.caption2).foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                }
                Spacer()
                Text("\(ejercicio.sets)x\(ejercicio.repetitions)")
                    .font(.caption.weight(.semibold)).monospacedDigit()
                if seleccionado {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(EdecanTheme.morado)
                }
            }
        }
        .buttonStyle(.plain)
    }

    /// Pide opciones a la IA (sin aplicar): propuesta + alternativas.
    private func pedirOpciones() async {
        cargando = true
        errorSwap = nil
        defer { cargando = false }
        do {
            let out = try await client?.gymSwapEjercicio(
                indice: indice, nombre: nombre, soloOpciones: true
            )
            interpreto = out?.interpreto
            propuesta = out?.ejercicioPropuesto
            alternativas = out?.alternativas ?? []
            elegido = propuesta
        } catch {
            errorSwap = "No pude pedir opciones: \(error.localizedDescription)"
        }
    }

    /// Aplica el cambio con el ejercicio ESCOGIDO (IA re-resuelve el nombre).
    private func aplicar() async {
        guard let seleccionadoFinal = elegido else { return }
        cargando = true
        errorSwap = nil
        defer { cargando = false }
        do {
            let out = try await client?.gymSwapEjercicio(
                indice: indice, nombre: seleccionadoFinal.name, soloOpciones: false
            )
            if let planNuevo = out?.plan {
                vm.aplicarPlan(planNuevo)
            }
            dismiss()
        } catch {
            self.errorSwap = "No pude aplicar el cambio: \(error.localizedDescription)"
        }
    }
}
