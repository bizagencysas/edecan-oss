"""El gate del banco privado: un post no puede nombrar a su dueño.

Nace de una corrida real (una de tres): con el banco cargado, el escritor devolvió
"La máquina de Alex incluye un agente de voz...", nombrando al dueño y usando su
contexto interno como material publicable. El banco mismo lo prohíbe en su
cabecera ("no son material publicable ni siquiera cuando el tema se parece"),
pero confiar en que un modelo respete un párrafo del prompt no basta. Este
archivo fija la garantía objetiva: si un nombre propio aparece 2+ veces en el
banco, no puede aparecer en el post — con la misma dureza que la primera persona.
"""

from __future__ import annotations

from edecan_creative.editorial import revisar

_BANCO_DE_EXAMPLE = (
    "IDENTIDAD: Alex, venezolano viviendo en Colombia. Fundador de Acme,\n"
    "una fintech de crédito alternativo.\n"
    "LA MÁQUINA: Alex opera todo con agentes de IA. Acme es una de sus\n"
    "empresas activas.\n"
)


def test_un_post_no_puede_nombrar_al_dueno_que_el_banco_menciona() -> None:
    filtrado = revisar(
        "La máquina de Alex incluye un agente de voz con IA para llamadas de ventas.",
        "linkedin",
        banco_privado=_BANCO_DE_EXAMPLE,
    )
    assert any("Alex" in mensaje for mensaje in filtrado)


def test_incluso_en_modo_permisivo_la_fuga_del_banco_no_pasa() -> None:
    """El nombre del banco es clase DURA a propósito: sobrevive al permisivo,
    igual que la primera persona. Un post pedido explícitamente por el usuario
    tampoco puede filtrar su identidad interna."""
    filtrado = revisar(
        "Según Alex, la reputación financiera alternativa importa cada día más.",
        "linkedin",
        permisivo=True,
        banco_privado=_BANCO_DE_EXAMPLE,
    )
    assert any("Alex" in mensaje for mensaje in filtrado)


def test_una_marca_personal_del_banco_tambien_se_bloquea() -> None:
    """Acme aparece dos veces en el banco: es una entidad del dueño y
    tampoco puede colarse al post como si fuera una fuente pública."""
    filtrado = revisar(
        "Acme está construyendo una alternativa al buró tradicional.",
        "linkedin",
        banco_privado=_BANCO_DE_EXAMPLE,
    )
    assert any("Acme" in mensaje for mensaje in filtrado)


def test_un_texto_sano_no_se_rechaza_por_el_gate_del_banco() -> None:
    """Un post que habla del mundo sin nombrar al dueño ni a sus proyectos pasa
    limpio. El gate solo mira nombres propios del banco, no cualquier mayúscula."""
    filtrado = revisar(
        "Operar sistemas de IA en producción exige más plomería que modelo.",
        "linkedin",
        banco_privado=_BANCO_DE_EXAMPLE,
    )
    assert not any("banco de contexto" in mensaje for mensaje in filtrado)


def test_un_tenant_sin_banco_no_activa_el_gate() -> None:
    """El gate depende del banco: si el tenant no lo configuró, no se aplica."""
    filtrado = revisar(
        "La máquina de Alex incluye un agente de voz.",
        "linkedin",
        banco_privado="",
    )
    # No se puede prohibir un nombre que el sistema no conoce, así que este texto
    # pasa el gate del banco (fallará por otras razones, como la primera persona).
    assert not any("banco de contexto" in mensaje for mensaje in filtrado)


def test_el_banco_auditable_cabe_entero_en_el_presupuesto_del_auditor():
    """La cadena de presupuestos que ya se rompió DOS veces por descuido: el banco que ve
    el auditor (`redaccion._MAX_BANCO_AUDITABLE_CHARS`) más el artículo leído
    (`investigacion._MAX_CUERPO_ARTICULO_CHARS`) tienen que caber juntos en el
    presupuesto total del auditor (`auditoria._MAX_CONTEXTO_AUDITOR_CHARS`) -- si no, la
    cola del banco se corta en silencio y las escenas de los ejes finales se vetan como
    invención. Quien suba un tope tiene que subir el de arriba."""
    from edecan_creative.auditoria import _MAX_CONTEXTO_AUDITOR_CHARS
    from edecan_creative.investigacion import _MAX_CUERPO_ARTICULO_CHARS
    from edecan_creative.redaccion import _MAX_BANCO_AUDITABLE_CHARS

    # Margen de 1.000 chars para el encabezado de fuentes, la regla del banco y la línea
    # del citable: lo que el auditor ve además del banco y el cuerpo.
    assert (
        _MAX_BANCO_AUDITABLE_CHARS + _MAX_CUERPO_ARTICULO_CHARS + 1000
        <= _MAX_CONTEXTO_AUDITOR_CHARS
    )
