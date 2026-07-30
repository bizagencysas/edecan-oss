"""Motor de rotación editorial: territorios temáticos, formatos argumentales y cooldown
geográfico/temático para contenido social recurrente (p.ej. un post diario de LinkedIn).

Este módulo NO llama a ningún modelo ni decide texto: es lógica pura de rotación y
composición de instrucciones. `editorial.py` / `social.py` son quienes escriben el post;
este módulo solo les dice QUÉ territorio y QUÉ forma tocan en este turno, y qué texto
inyectar en el prompt para que el feed no se sienta repetitivo.

Tres problemas que resuelve, cada uno reemplazable por el tenant:

1. TERRITORIOS: de qué habla la cuenta (tecnología, salud, negocios...). Cada territorio
   trae una instrucción editorial larga (el "pilar") y una consulta de búsqueda de
   noticias asociada. `TERRITORIOS_POR_DEFECTO` es un punto de partida genérico y
   editable; cualquier tenant puede sustituirlo por su propia lista.
2. FORMATOS: cómo se argumenta el post (nota directa, pregunta genuina, apunte breve...),
   independiente del tema. Rota en un reloj separado del de territorios para que el feed
   no caiga siempre en la misma combinación tema+forma. `FORMATOS_POR_DEFECTO` es el
   catálogo de partida.
3. COOLDOWN GEOGRÁFICO/TEMÁTICO: evita que un solo país o tema se vuelva el protagonista
   permanente del feed ("LatAm" no debería significar "siempre el mismo país"). Dado un
   historial reciente de temas usados, calcula cuáles están "saturados" y qué términos
   excluir de la próxima búsqueda.

El estado de rotación es un `dict` plano y JSON-serializable (índices + historial). Este
módulo nunca lo persiste: cada función pura recibe el estado actual y devuelve el estado
SIGUIENTE, y el llamador decide dónde guardarlo (fila de tenant, archivo, lo que sea).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict


class Formato(TypedDict):
    """Una forma argumental: cómo se construye el post, sin importar el tema."""

    id: str
    instruccion: str


class Territorio(TypedDict):
    """Un territorio editorial: de qué habla el post."""

    id: str
    etiqueta: str
    pilar: str
    query: str


class FocoGeografico(TypedDict):
    """Un foco de cobertura geográfica/temática por el que rota la búsqueda de noticias."""

    etiqueta: str
    busqueda: str


# ---------------------------------------------------------------------------
# 1. Formatos argumentales por defecto.
#
# La forma cambia; la voz no. Cada formato manda sobre cualquier ejemplo de
# estructura que traiga el perfil del tenant (ver `instruccion_formato`).
# ---------------------------------------------------------------------------

FORMATOS_POR_DEFECTO: list[Formato] = [
    {
        "id": "nota_directa",
        "instruccion": (
            "Empieza por el hecho o detalle más interesante y desarrolla una observación "
            "concreta. Sin listas, sin frases de revelación y sin obligar un gran cierre. "
            "Puede terminar en un dato seco."
        ),
    },
    {
        "id": "explicacion_clara",
        "instruccion": (
            "Explica cómo funciona el asunto sin anunciar el mecanismo ni posar de quien "
            "descubre una verdad oculta. Usa verbos simples, detalles concretos y "
            "transiciones discretas. Sin viñetas."
        ),
    },
    {
        "id": "detalle_concreto",
        "instruccion": (
            "Construye el texto alrededor de una decisión, acción, objeto o restricción "
            "verificable. Un número solo puede ser el gancho si el cooldown estructural lo "
            "permite. Puedes usar dos o cuatro viñetas si aclaran algo; no fuerces grupos "
            "de tres ni una moraleja."
        ),
    },
    {
        "id": "comparacion_natural",
        "instruccion": (
            "Compara dos hechos solo si la comparación aclara el tema. Evita la estructura "
            "simétrica de 'no es X, es Y', las oposiciones teatrales y las frases diseñadas "
            "para parecer una cita."
        ),
    },
    {
        "id": "pregunta_genuina",
        "instruccion": (
            "Solo abre con una pregunta si un lector real podría formularla así. No la "
            "sigas con 'la respuesta es/no es'. Contesta en prosa y permite que la "
            "explicación avance sin otra pregunta retórica."
        ),
    },
    {
        "id": "apunte_breve",
        "instruccion": (
            "Escribe entre 55 y 100 palabras. Una observación, un detalle útil y nada más. "
            "No agregues contexto para llegar a una longitud ni cierres con una sentencia "
            "grandilocuente."
        ),
    },
]


# ---------------------------------------------------------------------------
# 2. Territorios editoriales por defecto.
#
# Amplios a propósito: cualquier cuenta profesional puede reconocerse en
# alguno. Un tenant reemplaza esta lista completa por la suya en su perfil
# editorial (p.ej. el campo `content_pillars`) sin tocar este módulo.
# ---------------------------------------------------------------------------

TERRITORIOS_POR_DEFECTO: list[Territorio] = [
    {
        "id": "tecnologia",
        "etiqueta": "Tecnología",
        "pilar": (
            "TECNOLOGÍA: explica una herramienta, modelo o lanzamiento por la tarea concreta "
            "que ahora resuelve, su límite y quién puede aprovecharlo."
        ),
        "query": (
            '(tecnología OR "inteligencia artificial" OR software) '
            "(lanzamiento OR herramienta OR modelo OR actualización)"
        ),
    },
    {
        "id": "trabajo_y_negocio",
        "etiqueta": "Trabajo y negocio",
        "pilar": (
            "TRABAJO Y NEGOCIO: elige un cambio tecnológico o de mercado que altere una "
            "tarea, un puesto, un costo o una oportunidad accesible. Evita estudios "
            "genéricos de productividad."
        ),
        "query": (
            "(tecnología OR mercado OR industria) "
            "(empleo OR trabajo OR productividad OR negocio OR oportunidad)"
        ),
    },
    {
        "id": "riesgos_y_regulacion",
        "etiqueta": "Riesgos y regulación",
        "pilar": (
            "RIESGOS Y REGULACIÓN: examina privacidad, fraude, seguridad, errores, "
            "regulación o costos con un caso verificable y una decisión concreta."
        ),
        "query": (
            "(tecnología OR empresa OR mercado) "
            "(privacidad OR fraude OR seguridad OR error OR regulación OR demanda)"
        ),
    },
    {
        "id": "salud",
        "etiqueta": "Salud",
        "pilar": (
            "SALUD: explica un tratamiento, diagnóstico, tecnología, costo o problema de "
            "acceso que afecte a pacientes o profesionales. Cero promesas médicas y cero "
            "curas exageradas."
        ),
        "query": (
            '("salud pública" OR pacientes OR médicos OR hospital OR diagnóstico OR tratamiento) '
            "(innovación OR tecnología OR acceso OR costo) -veterinaria -mascotas"
        ),
    },
    {
        "id": "empresas_y_mercados",
        "etiqueta": "Empresas y mercados",
        "pilar": (
            "EMPRESAS Y MERCADOS: examina una decisión, estrategia, producto, adquisición o "
            "conflicto con consecuencias comprensibles para clientes, empleados o "
            "competidores."
        ),
        "query": (
            "(empresa OR mercado OR startup) "
            "(decisión OR estrategia OR lanzamiento OR controversia OR empleados OR clientes)"
        ),
    },
    {
        "id": "finanzas_y_dinero",
        "etiqueta": "Finanzas y dinero",
        "pilar": (
            "FINANZAS Y DINERO: explica pagos, crédito, comisiones, fraude, privacidad o "
            "acceso al dinero en lenguaje común. Una ronda de inversión no es tema por sí "
            "sola."
        ),
        "query": (
            "(bancos OR finanzas OR pagos OR crédito) "
            "(lanzamiento OR fraude OR privacidad OR comisiones OR consumidores OR regulación)"
        ),
    },
    {
        "id": "oportunidades",
        "etiqueta": "Oportunidades",
        "pilar": (
            "OPORTUNIDADES: muestra una necesidad, herramienta o cambio que permita crear, "
            "vender, aprender, ahorrar tiempo o competir. Específico y plausible, nunca "
            "dinero fácil."
        ),
        "query": (
            "(oportunidad OR convocatoria OR beca OR herramienta OR mercado) "
            "(emprendedores OR profesionales OR pymes OR creadores)"
        ),
    },
    {
        "id": "gobierno_y_politica_publica",
        "etiqueta": "Gobierno y política pública",
        "pilar": (
            "GOBIERNO Y POLÍTICA PÚBLICA: explica una medida, regla, inversión o "
            "controversia por lo que cambia para ciudadanos o empresas. Analiza la "
            "decisión; evita propaganda partidista."
        ),
        "query": (
            "(gobierno OR congreso OR regulador OR ministerio) "
            "(tecnología OR empresas OR empleo OR economía OR salud OR regulación)"
        ),
    },
    {
        "id": "empleo_y_carrera",
        "etiqueta": "Empleo y carrera",
        "pilar": (
            "EMPLEO Y CARRERA: explica una contratación, despido, salario, habilidad o "
            "cambio real en cómo se trabaja. Evita encuestas de intención y consejos de "
            "coach."
        ),
        "query": (
            '(empleo OR contrataciones OR despidos OR salarios OR "trabajo remoto") '
            "(empresa OR tecnología OR habilidades)"
        ),
    },
    {
        "id": "ciencia",
        "etiqueta": "Ciencia",
        "pilar": (
            "CIENCIA: traduce un descubrimiento, infraestructura o capacidad nueva a una "
            "consecuencia que una persona no especializada pueda entender."
        ),
        "query": (
            "(ciencia OR espacio OR energía OR robótica) "
            "(descubrimiento OR lanzamiento OR avance OR experimento)"
        ),
    },
    {
        "id": "deportes",
        "etiqueta": "Deportes",
        "pilar": (
            "DEPORTES: prioriza negocio, tecnología, dinero, derechos, carrera, salud o una "
            "decisión humana alrededor del deporte. Un marcador o rumor de fichaje sin "
            "consecuencia no basta."
        ),
        "query": (
            "(deporte OR fútbol OR baloncesto OR tenis OR atleta) "
            "(negocio OR tecnología OR salario OR patrocinio OR derechos OR salud)"
        ),
    },
]


# ---------------------------------------------------------------------------
# 3. Cooldown geográfico/temático.
#
# El catálogo por defecto usa países como ejemplo de "tema a vigilar", pero la
# clave puede ser cualquier etiqueta (un producto, un competidor, un ángulo).
# Editable/reemplazable por el tenant.
# ---------------------------------------------------------------------------

CATALOGO_TEMAS_POR_DEFECTO: dict[str, tuple[str, ...]] = {
    "mexico": ("méxico", "mexico", "mexicano", "mexicana", "cdmx"),
    "colombia": ("colombia", "colombiano", "colombiana", "bogotá", "bogota"),
    "argentina": ("argentina", "argentino", "argentina", "buenos aires", "peso argentino"),
    "brasil": ("brasil", "brazil", "brasileño", "brasileña", "são paulo", "sao paulo"),
    "chile": ("chile", "chileno", "chilena", "santiago de chile"),
    "peru": ("perú", "peru", "peruano", "peruana", "lima"),
    "espana": ("españa", "espana", "madrid", "español", "española"),
    "estados_unidos": ("estados unidos", "eeuu", "ee.uu", "washington", "wall street"),
}

FOCOS_GEOGRAFICOS_POR_DEFECTO: list[FocoGeografico] = [
    {"etiqueta": "cobertura global sin país obligatorio", "busqueda": ""},
    {
        "etiqueta": (
            "principales mercados de habla hispana (México, Colombia, Chile, Perú, "
            "Argentina, Brasil)"
        ),
        "busqueda": "(México OR Colombia OR Chile OR Perú OR Argentina OR Brasil)",
    },
    {
        "etiqueta": "Estados Unidos y Europa (Wall Street, Silicon Valley, banca)",
        "busqueda": '("Wall Street" OR "Silicon Valley" OR banca OR "Estados Unidos" OR Europa)',
    },
    {
        "etiqueta": "mercados emergentes y economías en desarrollo",
        "busqueda": "(mercado emergente OR economía en desarrollo)",
    },
]

_NOTA_DIVERSIDAD_POR_DEFECTO = (
    "No conviertas una sola región o mercado en el protagonista permanente del feed; "
    "alterna entre las coberturas configuradas."
)


# ---------------------------------------------------------------------------
# Estado de rotación: dict plano, JSON-serializable, sin asumir dónde se guarda.
# ---------------------------------------------------------------------------


def estado_inicial() -> dict[str, Any]:
    """Estado de rotación vacío, listo para el primer turno."""
    return {"territorio_idx": 0, "formato_idx": 0, "foco_idx": 0, "historial": []}


def registrar_historial(
    estado: Mapping[str, Any], entrada: Mapping[str, Any], *, limite: int = 300
) -> dict[str, Any]:
    """Devuelve un estado nuevo con `entrada` agregada al historial (recortado a `limite`,
    igual convención que el motor del que se portó esta lógica: 300 entradas). No muta
    `estado`. `entrada` es libre en forma; los campos que este módulo sabe leer son
    `temas` (lista de ids de `CATALOGO_TEMAS_POR_DEFECTO` o del catálogo que uses) para
    el cooldown, y `formato_id` / `territorio_id` si quieres poder auditar qué combinación
    salió en cada turno."""
    nuevo = dict(estado)
    historial = list(estado.get("historial") or [])
    historial.append(dict(entrada))
    nuevo["historial"] = historial[-limite:] if limite > 0 else historial
    return nuevo


# ---------------------------------------------------------------------------
# Rotación de formato y territorio (relojes independientes).
# ---------------------------------------------------------------------------


def formato_actual(estado: Mapping[str, Any], formatos: Sequence[Formato] | None = None) -> Formato:
    formatos = formatos if formatos else FORMATOS_POR_DEFECTO
    idx = int(estado.get("formato_idx", 0)) % len(formatos)
    return formatos[idx]


def avanzar_formato(
    estado: Mapping[str, Any], formatos: Sequence[Formato] | None = None
) -> dict[str, Any]:
    """Devuelve un estado nuevo con `formato_idx` avanzado una posición. No muta `estado`."""
    formatos = formatos if formatos else FORMATOS_POR_DEFECTO
    nuevo = dict(estado)
    nuevo["formato_idx"] = (int(estado.get("formato_idx", 0)) + 1) % len(formatos)
    return nuevo


def instruccion_formato(formato: Formato | None) -> str:
    """Texto listo para pegar en el prompt de escritura: la forma manda sobre cualquier
    ejemplo de estructura que traiga el perfil del tenant."""
    if not formato:
        return ""
    return (
        f"\n\nFORMA EDITORIAL OBLIGATORIA DE ESTE BORRADOR ({formato['id']}): "
        f"{formato['instruccion']} Esta forma manda sobre cualquier ejemplo de estructura "
        "del perfil."
    )


def territorio_actual(
    estado: Mapping[str, Any], territorios: Sequence[Territorio] | None = None
) -> Territorio:
    territorios = territorios if territorios else TERRITORIOS_POR_DEFECTO
    idx = int(estado.get("territorio_idx", 0)) % len(territorios)
    return territorios[idx]


def avanzar_territorio(
    estado: Mapping[str, Any], territorios: Sequence[Territorio] | None = None
) -> dict[str, Any]:
    """Devuelve un estado nuevo con `territorio_idx` avanzado una posición. No muta `estado`."""
    territorios = territorios if territorios else TERRITORIOS_POR_DEFECTO
    nuevo = dict(estado)
    nuevo["territorio_idx"] = (int(estado.get("territorio_idx", 0)) + 1) % len(territorios)
    return nuevo


@dataclass(frozen=True)
class PasoEditorial:
    """El resultado de pedir "qué toca ahora": territorio, formato, el texto de forma ya
    listo para el prompt, y el estado siguiente (con ambos índices avanzados) para que el
    llamador lo persista cuando el borrador se produzca de verdad."""

    territorio: Territorio
    formato: Formato
    instruccion_formato: str
    estado: dict[str, Any]


def siguiente_paso(
    estado: Mapping[str, Any],
    territorios: Sequence[Territorio] | None = None,
    formatos: Sequence[Formato] | None = None,
) -> PasoEditorial:
    """Calcula el territorio y formato de este turno a partir del estado de rotación, y
    devuelve también el estado ya avanzado para el próximo turno.

    Es una función pura: no muta `estado` ni nada global. Si un intento falla y no quieres
    avanzar el reloj de formato hasta que un borrador realmente se produzca (así se
    comporta el motor del que se portó esta lógica), simplemente no persistas
    `resultado.estado` en ese intento fallido y vuelve a llamar con el estado original;
    si solo quieres retener el avance de territorio pero no el de formato (o viceversa),
    compón el estado final a mano con `avanzar_territorio` / `avanzar_formato` en vez de
    usar este atajo.
    """
    territorio = territorio_actual(estado, territorios)
    formato = formato_actual(estado, formatos)
    nuevo_estado = avanzar_formato(avanzar_territorio(estado, territorios), formatos)
    return PasoEditorial(
        territorio=territorio,
        formato=formato,
        instruccion_formato=instruccion_formato(formato),
        estado=nuevo_estado,
    )


# ---------------------------------------------------------------------------
# Foco geográfico: un tercer reloj, independiente de territorio/formato, que
# decide qué región priorizar en la búsqueda de noticias de este turno.
# ---------------------------------------------------------------------------


def foco_actual(
    estado: Mapping[str, Any], focos: Sequence[FocoGeografico] | None = None
) -> FocoGeografico:
    focos = focos if focos else FOCOS_GEOGRAFICOS_POR_DEFECTO
    idx = int(estado.get("foco_idx", 0)) % len(focos)
    return focos[idx]


def avanzar_foco(
    estado: Mapping[str, Any], focos: Sequence[FocoGeografico] | None = None
) -> dict[str, Any]:
    """Devuelve un estado nuevo con `foco_idx` avanzado una posición. No muta `estado`."""
    focos = focos if focos else FOCOS_GEOGRAFICOS_POR_DEFECTO
    nuevo = dict(estado)
    nuevo["foco_idx"] = (int(estado.get("foco_idx", 0)) + 1) % len(focos)
    return nuevo


# ---------------------------------------------------------------------------
# Cooldown: detectar temas saturados en el historial reciente y excluirlos.
# ---------------------------------------------------------------------------


def detectar_temas(texto: str, catalogo: Mapping[str, Sequence[str]] | None = None) -> list[str]:
    """Busca en `texto` los términos de cada tema del catálogo (por defecto,
    `CATALOGO_TEMAS_POR_DEFECTO`) y devuelve los ids de tema que aparecen. Útil para
    etiquetar automáticamente un borrador ya escrito antes de guardarlo en el historial."""
    catalogo = catalogo if catalogo is not None else CATALOGO_TEMAS_POR_DEFECTO
    low = (texto or "").lower()
    return [tema for tema, terminos in catalogo.items() if any(t in low for t in terminos)]


def temas_en_cooldown(
    recientes: Sequence[str],
    *,
    umbral: int = 3,
    umbral_por_tema: Mapping[str, int] | None = None,
) -> list[str]:
    """Cuenta cuántas veces aparece cada tema en `recientes` (ids de tema ya aplanados de
    los últimos N borradores — el llamador decide la ventana) y devuelve los que igualan o
    superan su umbral: han salido demasiado y deben evitarse en el próximo borrador.

    `umbral_por_tema` permite bajar el umbral de un tema puntual que se sature más rápido
    que el resto; cualquier tema no listado ahí usa `umbral`.
    """
    conteo: dict[str, int] = {}
    for tema in recientes:
        conteo[tema] = conteo.get(tema, 0) + 1
    umbrales = umbral_por_tema or {}
    return [tema for tema, total in conteo.items() if total >= umbrales.get(tema, umbral)]


def temas_recientes_de_historial(
    historial: Sequence[Mapping[str, Any]], ventana: int = 20
) -> list[str]:
    """Aplana el campo `temas` de las últimas `ventana` entradas del historial de estado en
    una sola lista de ids (con repetición), lista para pasarle a `temas_en_cooldown`."""
    recientes: list[str] = []
    for entrada in list(historial)[-ventana:]:
        temas = entrada.get("temas") if isinstance(entrada, Mapping) else None
        if temas:
            recientes.extend(str(t) for t in temas)
    return recientes


def terminos_a_excluir(
    temas: Sequence[str], catalogo: Mapping[str, Sequence[str]] | None = None
) -> tuple[str, ...]:
    """Los términos de búsqueda (variantes de texto) a excluir por cada tema en cooldown."""
    catalogo = catalogo if catalogo is not None else CATALOGO_TEMAS_POR_DEFECTO
    terminos: list[str] = []
    for tema in temas:
        terminos.extend(catalogo.get(tema, ()))
    return tuple(dict.fromkeys(terminos))


def instruccion_cooldown(
    temas: Sequence[str],
    foco: str = "",
    *,
    nota_diversidad: str = _NOTA_DIVERSIDAD_POR_DEFECTO,
) -> str:
    """Texto listo para pegar en el prompt de escritura: el foco geográfico/temático a
    priorizar en este turno y, si hay temas en cooldown, la orden explícita de evitarlos."""
    partes: list[str] = []
    if foco:
        partes.append(f"\n\nDIVERSIDAD DEL FEED: para este borrador prioriza este foco: {foco}.")
        if nota_diversidad:
            partes.append(f" {nota_diversidad}")
    elif nota_diversidad:
        partes.append(f"\n\nDIVERSIDAD DEL FEED: {nota_diversidad}")
    if temas:
        bloqueados = ", ".join(t.replace("_", " ").capitalize() for t in temas)
        partes.append(
            f" Cooldown activo: {bloqueados} ya salió demasiado en los últimos borradores. "
            "No lo uses de nuevo en este post salvo que el usuario lo haya pedido explícitamente."
        )
    return "".join(partes)


def query_con_diversidad(base: str, foco_busqueda: str, excluir: Sequence[str]) -> str:
    """Compone la consulta de búsqueda final: la query base del territorio + el filtro de
    búsqueda del foco geográfico actual + los términos negativos de los temas en cooldown."""
    negativos = " ".join(f'-"{t}"' if " " in t else f"-{t}" for t in excluir)
    return " ".join(parte for parte in (base, foco_busqueda, negativos) if parte).strip()


__all__ = [
    "CATALOGO_TEMAS_POR_DEFECTO",
    "FOCOS_GEOGRAFICOS_POR_DEFECTO",
    "FORMATOS_POR_DEFECTO",
    "TERRITORIOS_POR_DEFECTO",
    "Formato",
    "FocoGeografico",
    "PasoEditorial",
    "Territorio",
    "avanzar_foco",
    "avanzar_formato",
    "avanzar_territorio",
    "detectar_temas",
    "estado_inicial",
    "foco_actual",
    "formato_actual",
    "instruccion_cooldown",
    "instruccion_formato",
    "query_con_diversidad",
    "registrar_historial",
    "siguiente_paso",
    "temas_en_cooldown",
    "temas_recientes_de_historial",
    "terminos_a_excluir",
    "territorio_actual",
]
