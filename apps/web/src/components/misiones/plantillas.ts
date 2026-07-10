/**
 * Catálogo estático de plantillas de misión (WP-V6-10, principio de
 * configuración de pocos clicks — `DIRECCION_ACTUAL.md`): cada una arma un
 * `objetivo` para `POST /v1/missions` (contrato existente, sin tocar) a
 * partir de un mini-formulario de placeholders que rellena
 * `PlantillasMisiones.tsx`.
 *
 * Filosofía — HONESTAS con las tools reales del repo: cada plantilla
 * describe algo que los perfiles de `packages/agents/edecan_agents/
 * profiles.py` pueden hacer de verdad hoy con sus `allowed_tools` reales
 * (investigación web, análisis de tablas, contenido, finanzas/estado del
 * negocio, legal informativo). Ninguna promete algo que el repo no hace:
 * nunca LinkedIn, nunca "publica esto por mí" sin aprobación explícita
 * (`publicar_social` es `dangerous`, siempre pausa para confirmación humana,
 * `ARCHITECTURE.md` §10.7), nunca conectar una cuenta/plataforma que Edecán
 * no soporta. El `Orchestrator.plan()` decide él solo qué perfil(es) usar
 * para cada paso — estas plantillas no fuerzan un agente, solo redactan un
 * objetivo claro y concreto para que ese planificador lo divida bien.
 */

export interface MissionTemplateField {
  /** Debe coincidir con el placeholder `{{key}}` dentro de `objetivo_template`. */
  key: string;
  label: string;
  placeholder: string;
}

export interface MissionTemplate {
  id: string;
  titulo: string;
  descripcion: string;
  objetivo_template: string;
  campos: MissionTemplateField[];
}

export const MISSION_TEMPLATES: MissionTemplate[] = [
  {
    id: "investigacion-mercado",
    titulo: "Investigación de mercado",
    descripcion:
      "Busca y resume el panorama de un mercado o producto: tamaño, tendencias y jugadores principales.",
    objetivo_template:
      "Investiga el mercado de {{tema}} enfocado en {{region}}: tamaño y tendencias recientes, " +
      "principales jugadores y qué está cambiando en los últimos meses. Cita las fuentes que uses.",
    campos: [
      { key: "tema", label: "Tema o producto", placeholder: "ej. software de facturación electrónica" },
      { key: "region", label: "Región o mercado", placeholder: "ej. Colombia" },
    ],
  },
  {
    id: "informe-competencia",
    titulo: "Informe de competencia",
    descripcion:
      "Investiga a tus competidores directos y arma un resumen comparativo con fortalezas y debilidades.",
    objetivo_template:
      "Investiga a estos competidores de {{negocio}}: {{competidores}}. Para cada uno resume su " +
      "propuesta de valor, precios públicos si los encuentras, y 2-3 fortalezas/debilidades frente " +
      "a nosotros. Cierra con una tabla comparativa en texto.",
    campos: [
      {
        key: "negocio",
        label: "Tu negocio o producto",
        placeholder: "ej. mi app de mensajería para pymes",
      },
      {
        key: "competidores",
        label: "Competidores (separados por coma)",
        placeholder: "ej. Acme, Beta Corp, Contoso",
      },
    ],
  },
  {
    id: "plan-contenido-semanal",
    titulo: "Plan de contenido semanal",
    descripcion:
      "Arma un plan de contenido para varias redes con formatos y mensajes clave por día. Queda como " +
      "borrador para revisar — nunca publica nada por su cuenta.",
    objetivo_template:
      "Arma un plan de contenido semanal (lunes a viernes) sobre {{tema}} para {{redes}}. Para cada " +
      "día propone el formato (texto, imagen, video corto), un titular/gancho y el mensaje clave. " +
      "Esto es un borrador para que yo revise — no publiques nada todavía.",
    campos: [
      {
        key: "tema",
        label: "Tema central de la semana",
        placeholder: "ej. lanzamiento de nuestra nueva funcionalidad",
      },
      { key: "redes", label: "Redes objetivo", placeholder: "ej. Instagram, X y el blog" },
    ],
  },
  {
    id: "analisis-archivo-datos",
    titulo: "Análisis de un archivo de datos",
    descripcion:
      "Analiza una tabla o archivo ya subido a Archivos: tendencias, variación entre periodos y una " +
      "proyección simple.",
    objetivo_template:
      "Analiza el archivo «{{archivo}}» que ya subí a Archivos: identifica las tendencias " +
      "principales de {{columna_o_metrica}}, calcula la variación entre periodos y proyecta cómo " +
      "podría comportarse en los próximos periodos si la tendencia se mantiene. Resume con números " +
      "concretos, no vaguedades.",
    campos: [
      { key: "archivo", label: "Nombre del archivo (ya subido)", placeholder: "ej. ventas_2026.xlsx" },
      {
        key: "columna_o_metrica",
        label: "Qué columna o métrica analizar",
        placeholder: "ej. ventas mensuales",
      },
    ],
  },
  {
    id: "comparativa-precios",
    titulo: "Comparativa de precios de un producto",
    descripcion:
      "Investiga en la web cuánto cuesta un producto o servicio en distintos proveedores y arma una " +
      "tabla comparativa.",
    objetivo_template:
      "Busca en la web cuánto cuesta {{producto}} en al menos 3 proveedores distintos ({{proveedores}}). " +
      "Arma una tabla comparativa con precio, qué incluye cada uno y la fuente de cada precio.",
    campos: [
      {
        key: "producto",
        label: "Producto o servicio",
        placeholder: "ej. plan de hosting para una tienda online",
      },
      {
        key: "proveedores",
        label: "Proveedores a comparar (o escribe 'los que encuentres')",
        placeholder: "ej. proveedor A, proveedor B — o 'los que encuentres'",
      },
    ],
  },
  {
    id: "borrador-contrato-riesgos",
    titulo: "Borrador de contrato + análisis de riesgos",
    descripcion:
      "Redacta un borrador de contrato y señala cláusulas de riesgo a revisar. Siempre informativo — " +
      "nunca sustituye asesoría legal profesional.",
    objetivo_template:
      "Redacta un borrador de contrato de {{tipo_contrato}} entre {{partes}}, con las cláusulas " +
      "usuales para ese tipo de acuerdo, y después analiza el borrador señalando qué cláusulas " +
      "conviene revisar con un abogado antes de firmar. Recuerda que esto es informativo, no " +
      "asesoría legal vinculante.",
    campos: [
      { key: "tipo_contrato", label: "Tipo de contrato", placeholder: "ej. prestación de servicios" },
      {
        key: "partes",
        label: "Partes involucradas",
        placeholder: "ej. mi empresa y un proveedor freelance",
      },
    ],
  },
  {
    id: "salud-negocio",
    titulo: "Resumen de salud del negocio",
    descripcion: "Sintetiza el estado financiero y operativo del negocio con sus KPIs más relevantes.",
    objetivo_template:
      "Arma un resumen ejecutivo del estado del negocio para el periodo {{periodo}}: resume las " +
      "finanzas (ingresos, gastos, balance) y el estado operativo general, destaca los 3-5 KPIs más " +
      "relevantes y qué está funcionando bien o mal.",
    campos: [
      { key: "periodo", label: "Periodo a resumir", placeholder: "ej. este mes, o el último trimestre" },
    ],
  },
  {
    id: "plan-aprendizaje",
    titulo: "Plan de aprendizaje de un tema",
    descripcion:
      "Investiga los fundamentos de un tema y arma un plan de estudio estructurado por semanas, con " +
      "recursos y ejercicios.",
    objetivo_template:
      "Investiga los conceptos fundamentales de {{tema}} para alguien con nivel {{nivel}}, y arma un " +
      "plan de aprendizaje estructurado por semanas ({{duracion}}) con los temas de cada semana, " +
      "recursos recomendados y una práctica o ejercicio sugerido por semana.",
    campos: [
      { key: "tema", label: "Tema a aprender", placeholder: "ej. SQL para análisis de datos" },
      { key: "nivel", label: "Nivel de partida", placeholder: "ej. principiante" },
      { key: "duracion", label: "Duración deseada", placeholder: "ej. 4 semanas" },
    ],
  },
];

/** Sustituye cada `{{key}}` de `objetivo_template` por el valor (recortado)
 * que el usuario escribió en el mini-formulario; si un campo quedó vacío
 * (no debería ocurrir — `plantillaCompleta` gatea el botón de uso, ver
 * abajo), cae a `[Label]` en vez de dejar el placeholder crudo o inventar
 * texto. */
export function renderMissionTemplate(
  template: MissionTemplate,
  valores: Record<string, string>,
): string {
  return template.campos.reduce((texto, campo) => {
    const valor = (valores[campo.key] ?? "").trim();
    return texto.split(`{{${campo.key}}}`).join(valor || `[${campo.label}]`);
  }, template.objetivo_template);
}

/** `true` solo cuando TODOS los campos de la plantilla tienen un valor no
 * vacío — usado para deshabilitar el botón de "usar plantilla" hasta que el
 * mini-formulario esté completo. */
export function plantillaCompleta(
  template: MissionTemplate,
  valores: Record<string, string>,
): boolean {
  return template.campos.every((campo) => (valores[campo.key] ?? "").trim().length > 0);
}
