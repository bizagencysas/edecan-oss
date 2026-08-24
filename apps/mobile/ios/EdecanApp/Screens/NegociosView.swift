import SwiftUI
import Charts
import EdecanKit

/// Pestaña "Negocios" (pantalla 4 del mockup, `docs/negocios.md`): tarjetas
/// de KPIs del mes (`GET /v1/negocios/kpis`), dona de ventas por canal con
/// Swift Charts (nativo, sin dependencias), actividad reciente y la lista de
/// facturas (`GET /v1/negocios/facturas`) con su estado.
struct NegociosView: View {
    @Environment(SessionStore.self) private var session
    @State private var viewModel = NegociosViewModel()

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.cargando && viewModel.sinDatosTodavia {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let kpis = viewModel.kpis {
                    contenido(kpis: kpis)
                } else {
                    EmptyStateView(
                        icono: "chart.pie.fill",
                        titulo: "No se pudieron cargar tus datos",
                        descripcion: viewModel.errorMensaje ?? "Desliza hacia abajo para reintentar.",
                        etiquetaRoadmap: nil
                    )
                }
            }
            .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
            .navigationTitle("Negocios")
            .task { await viewModel.cargar(client: session.client) }
            .refreshable { await viewModel.cargar(client: session.client) }
        }
    }

    private func contenido(kpis: NegocioKPIs) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                if let error = viewModel.errorMensaje {
                    Text(error).font(.footnote).foregroundStyle(.red)
                }

                Text(kpis.mes)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.secondary)

                tarjetasKPI(kpis: kpis)
                donaDeCanales(kpis.porCanal)

                if !kpis.actividad.isEmpty {
                    seccion(titulo: "Actividad reciente") {
                        VStack(spacing: 10) {
                            ForEach(kpis.actividad) { item in
                                FilaActividad(item: item)
                            }
                        }
                    }
                }

                seccion(titulo: "Facturas") {
                    if viewModel.facturas.isEmpty {
                        Text("Todavía no has creado ninguna factura.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    } else {
                        VStack(spacing: 10) {
                            ForEach(viewModel.facturas) { factura in
                                FilaFactura(factura: factura)
                            }
                        }
                    }
                }
            }
            .padding()
        }
    }

    private func tarjetasKPI(kpis: NegocioKPIs) -> some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 14)], spacing: 14) {
            TarjetaKPI(titulo: "Ingresos", valor: formatoNumero(kpis.ingresos), icono: "arrow.down.circle.fill", color: .green)
            TarjetaKPI(titulo: "Gastos", valor: formatoNumero(kpis.gastos), icono: "arrow.up.circle.fill", color: .red)
            TarjetaKPI(titulo: "Beneficio", valor: formatoNumero(kpis.beneficio), icono: "chart.line.uptrend.xyaxis", color: EdecanTheme.morado)
            TarjetaKPI(titulo: "Clientes nuevos", valor: "\(kpis.nuevosClientes)", icono: "person.badge.plus.fill", color: EdecanTheme.azul)
        }
    }

    private func donaDeCanales(_ porCanal: [CanalKPI]) -> some View {
        seccion(titulo: "Ventas por canal") {
            if porCanal.isEmpty {
                Text("Sin ventas registradas este mes.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 80)
            } else {
                Chart(porCanal) { canal in
                    SectorMark(
                        angle: .value("Total", canal.total),
                        innerRadius: .ratio(0.62),
                        angularInset: 1.5
                    )
                    .foregroundStyle(by: .value("Canal", canal.canal))
                    .cornerRadius(4)
                }
                .chartLegend(position: .bottom, alignment: .center, spacing: 10)
                .frame(height: 220)
            }
        }
    }

    private func seccion<Contenido: View>(titulo: String, @ViewBuilder contenido: () -> Contenido) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(titulo).font(.headline)
            contenido()
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 18)
    }

    private func formatoNumero(_ valor: Double) -> String {
        valor.formatted(.number.precision(.fractionLength(0...2)).grouping(.automatic))
    }
}

private struct TarjetaKPI: View {
    let titulo: String
    let valor: String
    let icono: String
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Image(systemName: icono)
                .font(.title3)
                .foregroundStyle(color)
            Text(valor)
                .font(.title2.weight(.bold))
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            Text(titulo)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .tarjetaVidrio(esquina: 18)
    }
}

private struct FilaActividad: View {
    let item: ActividadKPI

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: item.tipo == "factura" ? "doc.text.fill" : "arrow.left.arrow.right.circle.fill")
                .foregroundStyle(EdecanTheme.degradado)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.descripcion).font(.subheadline).lineLimit(1)
                Text(item.fecha.prefix(10)).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Text(item.monto.formatted(.currency(code: item.moneda)))
                .font(.subheadline.weight(.medium))
        }
    }
}

private struct FilaFactura: View {
    let factura: Factura

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(factura.numero).font(.subheadline.weight(.semibold))
                Text(factura.clienteNombre).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 4) {
                Text(factura.total.formatted(.currency(code: factura.moneda)))
                    .font(.subheadline.weight(.medium))
                EtiquetaEstado(status: factura.status)
            }
        }
    }
}

private struct EtiquetaEstado: View {
    let status: String

    var body: some View {
        Text(etiqueta)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color.opacity(0.18), in: Capsule())
            .foregroundStyle(color)
    }

    private var etiqueta: String {
        switch status {
        case "draft": return "Borrador"
        case "sent": return "Enviada"
        case "paid": return "Pagada"
        case "void": return "Anulada"
        default: return status.capitalized
        }
    }

    private var color: Color {
        switch status {
        case "draft": return .gray
        case "sent": return EdecanTheme.azul
        case "paid": return .green
        case "void": return .red
        default: return .secondary
        }
    }
}
