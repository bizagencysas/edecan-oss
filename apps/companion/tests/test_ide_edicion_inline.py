"""Pruebas de ``ide_edicion_inline``.

Lo que de verdad hay que demostrar aquí no es que "funcione el camino feliz",
sino las tres garantías del módulo:

1. Pase lo que pase con la respuesta del modelo, las líneas de fuera del rango
   quedan byte a byte iguales.
2. Si el archivo cambió en disco durante la espera, se falla claro y NO se
   escribe.
3. Lo que se muestra (el diff) es exactamente lo que se aplica.

Sin red: el modelo entra siempre como un ``Completador`` de prueba.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from edecan_companion.ide_edicion_inline import (
    MAX_LINEAS_RANGO,
    PERFIL_EDICION_INLINE,
    EdicionDesincronizadaError,
    IDEEdicionInlineError,
    Rango,
    aplicar_propuesta,
    construir_peticion,
    construir_propuesta,
    editar_inline,
    leer_contenido_vigente,
    modelo_para_edicion_inline,
    preparar_edicion,
)
from edecan_companion.ide_files import FileService
from edecan_companion.ide_workspaces import WorkspaceStore
from edecan_llm.base import CompletionRequest, CompletionResponse, Usage

ARCHIVO = "\n".join(
    [
        "import os",
        "",
        "",
        "def saludar(nombre):",
        "    mensaje = 'hola ' + nombre",
        "    print(mensaje)",
        "    return mensaje",
        "",
        "",
        "def despedir(nombre):",
        "    return 'chao ' + nombre",
        "",
    ]
)
# 1-based: la función `saludar` ocupa de la 4 a la 7.
RANGO_SALUDAR = (4, 7)

#: Respuesta que incluye la línea `def saludar(...)`, que en algunos casos es
#: parte del rango y en otros es contexto de solo lectura.
RESPUESTA_CON_FIRMA = "\n".join(
    [
        "def saludar(nombre):",
        "    mensaje = f'hola {nombre}'",
        "    print(mensaje)",
        "    return mensaje",
    ]
)


def _entorno(tmp_path: Path, contenido: str = ARCHIVO) -> tuple[FileService, str, Path]:
    state_dir = tmp_path / "state"
    proyecto = tmp_path / "proyecto"
    proyecto.mkdir()
    (proyecto / "app.py").write_text(contenido, encoding="utf-8")
    workspaces = WorkspaceStore(state_dir)
    workspaces.authorize(str(proyecto))
    workspace_id = workspaces.list()[0]["id"]
    return FileService(workspaces), workspace_id, proyecto


def _respuesta(texto: str) -> CompletionResponse:
    return CompletionResponse(text=texto, usage=Usage(), stop_reason="end")


def _completador(texto: str, registro: list[CompletionRequest] | None = None):
    async def completar(peticion: CompletionRequest) -> CompletionResponse:
        if registro is not None:
            registro.append(peticion)
        return _respuesta(texto)

    return completar


# --------------------------------------------------------------------------- #
# Rango y validación de entrada
# --------------------------------------------------------------------------- #


def test_rango_invalido_no_se_puede_construir():
    with pytest.raises(IDEEdicionInlineError):
        Rango(inicio=0, fin=3)
    with pytest.raises(IDEEdicionInlineError):
        Rango(inicio=8, fin=4)


def test_instruccion_vacia_se_rechaza(tmp_path: Path):
    files, workspace_id, _ = _entorno(tmp_path)
    with pytest.raises(IDEEdicionInlineError):
        preparar_edicion(files, workspace_id, "app.py", 4, 7, "   ")


def test_rango_mas_alla_del_archivo_es_desincronizacion(tmp_path: Path):
    files, workspace_id, _ = _entorno(tmp_path)
    with pytest.raises(EdicionDesincronizadaError):
        preparar_edicion(files, workspace_id, "app.py", 4, 900, "arregla esto")


def test_seleccion_gigante_manda_al_agente_en_vez_de_a_la_edicion_inline(tmp_path: Path):
    grande = "\n".join(f"linea {i}" for i in range(MAX_LINEAS_RANGO + 50))
    files, workspace_id, _ = _entorno(tmp_path, grande)
    with pytest.raises(IDEEdicionInlineError, match="tope de una edición inline"):
        preparar_edicion(files, workspace_id, "app.py", 1, MAX_LINEAS_RANGO + 1, "ordena")


# --------------------------------------------------------------------------- #
# El modelo ve el contexto pero el rango es lo único editable
# --------------------------------------------------------------------------- #


def test_el_prompt_marca_el_rango_y_el_contexto_como_no_editable(tmp_path: Path):
    files, workspace_id, _ = _entorno(tmp_path)
    solicitud = preparar_edicion(files, workspace_id, "app.py", *RANGO_SALUDAR, "usa f-strings")
    prompt = solicitud.as_prompt()

    assert '<rango_editable lineas="4-7">' in prompt
    assert 'editable="no"' in prompt
    # El contexto de alrededor SÍ viaja (el modelo lo necesita para entender).
    assert "import os" in prompt
    assert "def despedir(nombre):" in prompt
    # Y el rango va completo.
    assert "    return mensaje" in prompt


def test_la_respuesta_del_modelo_nunca_toca_lineas_de_fuera_del_rango(tmp_path: Path):
    """La garantía central: aunque el modelo devuelva el archivo entero mal
    formado, el prefijo y el sufijo se copian del original."""
    files, workspace_id, _ = _entorno(tmp_path)
    solicitud = preparar_edicion(files, workspace_id, "app.py", *RANGO_SALUDAR, "usa f-strings")
    propuesta = construir_propuesta(
        solicitud,
        RESPUESTA_CON_FIRMA,
        ARCHIVO,
    )

    lineas = propuesta.contenido_nuevo.split("\n")
    assert lineas[:3] == ARCHIVO.split("\n")[:3]
    assert lineas[-5:] == ARCHIVO.split("\n")[-5:]
    assert "f'hola {nombre}'" in propuesta.contenido_nuevo
    assert "def despedir(nombre):" in propuesta.contenido_nuevo


def test_el_modelo_que_repite_la_firma_del_contexto_no_la_duplica(tmp_path: Path):
    """Fallo clásico: se pide cambiar el cuerpo y el modelo devuelve también la
    línea `def ...` que estaba en el contexto. Empalmarla tal cual dejaría la
    firma dos veces; recortarla es exacto (es idéntica a la que ya está fuera
    del rango), no una adivinanza."""
    files, workspace_id, _ = _entorno(tmp_path)
    solicitud = preparar_edicion(files, workspace_id, "app.py", 5, 7, "usa f-strings")
    propuesta = construir_propuesta(
        solicitud,
        RESPUESTA_CON_FIRMA,
        ARCHIVO,
    )

    assert propuesta.lineas_contexto_recortadas == 1
    assert propuesta.contenido_nuevo.count("def saludar(nombre):") == 1


ARCHIVO_LARGO = "\n".join(
    [
        "from dataclasses import dataclass",
        "import logging",
        "",
        "REINTENTOS_MAXIMOS = 5",
        "TIEMPO_DE_ESPERA_SEGUNDOS = 30",
        "registrador = logging.getLogger(__name__)",
        "",
        "@dataclass(frozen=True)",
        "class Configuracion:",
        "    servidor: str",
        "    puerto_de_escucha: int",
        "",
        "def construir(configuracion):",
        "    return configuracion.servidor",
        "",
        "def cerrar_todas_las_conexiones(pool):",
        "    for conexion in pool.conexiones_abiertas:",
        "        conexion.cerrar_con_gracia()",
        "",
    ]
)


def test_el_modelo_que_devuelve_el_archivo_entero_tal_cual_se_recorta_solo(tmp_path: Path):
    """Si el modelo contesta con el archivo completo SIN cambios fuera del
    rango, lo que sobra es texto idéntico al contexto: se recorta y queda
    exactamente el rango. No hay nada que adivinar, así que no se falla."""
    files, workspace_id, _ = _entorno(tmp_path, ARCHIVO_LARGO)
    solicitud = preparar_edicion(files, workspace_id, "app.py", 13, 14, "devuelve el puerto")

    propuesta = construir_propuesta(solicitud, ARCHIVO_LARGO, ARCHIVO_LARGO)

    assert propuesta.contenido_nuevo == ARCHIVO_LARGO
    assert propuesta.sin_cambios
    assert propuesta.lineas_contexto_recortadas > 0


def test_el_modelo_que_reescribe_fuera_del_rango_falla_sin_escribir(tmp_path: Path):
    """El caso que el plan nombra: se pide "arregla esta función" y el modelo
    devuelve el archivo entero, con cambios propios fuera de la selección. Esos
    cambios NO se aplican y no se inventa cuál era la parte buena: se falla."""
    files, workspace_id, proyecto = _entorno(tmp_path, ARCHIVO_LARGO)
    solicitud = preparar_edicion(files, workspace_id, "app.py", 13, 14, "devuelve el puerto")
    entrometido = ARCHIVO_LARGO.replace("REINTENTOS_MAXIMOS = 5", "REINTENTOS_MAXIMOS = 7").replace(
        "    return configuracion.servidor", "    return configuracion.puerto_de_escucha"
    )

    with pytest.raises(IDEEdicionInlineError, match="mucho más que la selección"):
        construir_propuesta(solicitud, entrometido, ARCHIVO_LARGO)
    assert (proyecto / "app.py").read_text(encoding="utf-8") == ARCHIVO_LARGO


def test_respuesta_envuelta_en_cerca_de_codigo_se_desenvuelve(tmp_path: Path):
    files, workspace_id, _ = _entorno(tmp_path)
    solicitud = preparar_edicion(files, workspace_id, "app.py", 11, 11, "usa f-strings")
    propuesta = construir_propuesta(
        solicitud, "```python\n    return f'chao {nombre}'\n```", ARCHIVO
    )

    assert "```" not in propuesta.contenido_nuevo
    assert propuesta.texto_rango_nuevo == "    return f'chao {nombre}'"


def test_markdown_con_su_propio_bloque_de_codigo_no_se_desarma(tmp_path: Path):
    doc = "\n".join(["# Guía", "", "texto viejo", "", "fin"])
    files, workspace_id, _ = _entorno(tmp_path, doc)
    solicitud = preparar_edicion(files, workspace_id, "app.py", 3, 3, "pon un ejemplo")
    propuesta = construir_propuesta(solicitud, "Ejemplo:\n\n```py\nprint(1)\n```", doc)

    # Cuatro líneas de cerca no habría; con dos, la heurística solo desenvuelve
    # cuando la cerca ABRE en la primera línea. Aquí no, así que se conserva.
    assert "```py" in propuesta.contenido_nuevo
    assert "print(1)" in propuesta.contenido_nuevo


def test_respuesta_vacia_no_produce_propuesta(tmp_path: Path):
    files, workspace_id, _ = _entorno(tmp_path)
    solicitud = preparar_edicion(files, workspace_id, "app.py", *RANGO_SALUDAR, "borra")
    with pytest.raises(IDEEdicionInlineError, match="vacía"):
        construir_propuesta(solicitud, "   \n\n", ARCHIVO)


# --------------------------------------------------------------------------- #
# Diff, no archivo entero
# --------------------------------------------------------------------------- #


def test_la_propuesta_trae_diff_unificado_y_conteo_de_lineas(tmp_path: Path):
    files, workspace_id, _ = _entorno(tmp_path)
    solicitud = preparar_edicion(files, workspace_id, "app.py", 5, 5, "usa f-strings")
    propuesta = construir_propuesta(solicitud, "    mensaje = f'hola {nombre}'", ARCHIVO)

    assert propuesta.diff.startswith("--- a/app.py")
    assert "-    mensaje = 'hola ' + nombre" in propuesta.diff
    assert "+    mensaje = f'hola {nombre}'" in propuesta.diff
    assert (propuesta.lineas_agregadas, propuesta.lineas_eliminadas) == (1, 1)
    assert not propuesta.sin_cambios


def test_respuesta_identica_al_rango_se_reporta_como_sin_cambios(tmp_path: Path):
    files, workspace_id, _ = _entorno(tmp_path)
    solicitud = preparar_edicion(files, workspace_id, "app.py", 5, 5, "déjalo igual")
    propuesta = construir_propuesta(solicitud, "    mensaje = 'hola ' + nombre", ARCHIVO)

    assert propuesta.sin_cambios
    assert propuesta.diff == ""
    assert "no propuso ningún cambio" in propuesta.resumen()


def test_el_salto_de_linea_final_del_archivo_se_respeta(tmp_path: Path):
    sin_salto = "uno\ndos\ntres"
    files, workspace_id, _ = _entorno(tmp_path, sin_salto)
    solicitud = preparar_edicion(files, workspace_id, "app.py", 3, 3, "pon TRES")
    propuesta = construir_propuesta(solicitud, "TRES", sin_salto)

    assert propuesta.contenido_nuevo == "uno\ndos\nTRES"
    assert not propuesta.contenido_nuevo.endswith("\n")


def test_seleccionar_hasta_el_final_no_le_quita_el_salto_de_linea_al_archivo(tmp_path: Path):
    """Seleccionar "de aquí al final" es de lo más común, y el rango incluye
    entonces el renglón vacío que deja el salto de línea final.

    Es el fallo silencioso de los dos: ``difflib`` no emite "\\ No newline at
    end of file", así que el diff se ve como un cambio limpio de una línea
    mientras el archivo aplicado pierde su salto final (ruff W292,
    ``eol-last`` de eslint, y en git una línea tocada de más)."""
    contenido = "import os\n\n\ndef saludar(n):\n    return n\n"
    files, workspace_id, _ = _entorno(tmp_path, contenido)
    # `split("\n")` da 6 renglones: el 6 es el vacío del salto final.
    solicitud = preparar_edicion(files, workspace_id, "app.py", 4, 6, "usa f-strings")

    propuesta = construir_propuesta(solicitud, "def saludar(n):\n    return f'{n}'\n", contenido)

    assert propuesta.contenido_nuevo == "import os\n\n\ndef saludar(n):\n    return f'{n}'\n"
    assert propuesta.contenido_nuevo.endswith("\n")


ARCHIVO_CON_SEPARADORES = "\n".join(
    ["def uno():", "    return 1", "", "", "def dos():", "    return 2", ""]
)


def test_los_renglones_en_blanco_del_borde_de_la_seleccion_no_se_borran(tmp_path: Path):
    """Seleccionar una función entera arrastra los renglones en blanco que la
    separan de la siguiente. Borrarlos no lo pidió nadie y rompe el estilo del
    archivo (E302), así que el borde del rango se conserva."""
    files, workspace_id, _ = _entorno(tmp_path, ARCHIVO_CON_SEPARADORES)
    solicitud = preparar_edicion(files, workspace_id, "app.py", 1, 4, "renombra a uno_mejor")

    propuesta = construir_propuesta(
        solicitud, "def uno_mejor():\n    return 1\n\n\n", ARCHIVO_CON_SEPARADORES
    )

    assert propuesta.contenido_nuevo == ARCHIVO_CON_SEPARADORES.replace(
        "def uno()", "def uno_mejor()"
    )
    assert (propuesta.lineas_agregadas, propuesta.lineas_eliminadas) == (1, 1)


def test_devolver_el_rango_tal_cual_es_un_no_op_aunque_termine_en_blanco(tmp_path: Path):
    """ "Déjalo igual" tiene que dar cero cambios también cuando la selección
    incluye renglones en blanco: si no, la interfaz ofrece Aplicar para un
    cambio que nadie pidió."""
    files, workspace_id, _ = _entorno(tmp_path, ARCHIVO_CON_SEPARADORES)
    solicitud = preparar_edicion(files, workspace_id, "app.py", 1, 4, "déjalo igual")

    propuesta = construir_propuesta(
        solicitud, "def uno():\n    return 1\n\n", ARCHIVO_CON_SEPARADORES
    )

    assert propuesta.sin_cambios
    assert propuesta.diff == ""
    assert propuesta.contenido_nuevo == ARCHIVO_CON_SEPARADORES


# --------------------------------------------------------------------------- #
# El archivo cambió en disco
# --------------------------------------------------------------------------- #


def test_archivo_cambiado_durante_la_espera_falla_y_no_escribe(tmp_path: Path):
    files, workspace_id, proyecto = _entorno(tmp_path)
    solicitud = preparar_edicion(files, workspace_id, "app.py", *RANGO_SALUDAR, "usa f-strings")

    # Alguien más (el agente, un `git checkout`, la propia persona) mete dos
    # líneas ARRIBA del rango: el texto del rango no cambió, pero su NÚMERO de
    # línea sí. Es justo el caso en el que se escribiría en el lugar equivocado.
    movido = "# nuevo\n# nuevo\n" + ARCHIVO
    (proyecto / "app.py").write_text(movido, encoding="utf-8")

    with pytest.raises(EdicionDesincronizadaError):
        construir_propuesta(solicitud, "def saludar(nombre):\n    return nombre", movido)
    with pytest.raises(EdicionDesincronizadaError):
        leer_contenido_vigente(files, solicitud)
    assert (proyecto / "app.py").read_text(encoding="utf-8") == movido


def test_editar_inline_relee_el_archivo_despues_de_la_respuesta(tmp_path: Path):
    files, workspace_id, proyecto = _entorno(tmp_path)

    async def completar_y_ensuciar(peticion: CompletionRequest) -> CompletionResponse:
        # El archivo se mueve MIENTRAS el modelo piensa.
        (proyecto / "app.py").write_text("otra cosa\n", encoding="utf-8")
        return _respuesta("    return nombre")

    with pytest.raises(EdicionDesincronizadaError):
        asyncio.run(
            editar_inline(
                files,
                workspace_id,
                "app.py",
                5,
                5,
                "simplifica",
                completar=completar_y_ensuciar,
            )
        )


def test_aplicar_vuelve_a_comprobar_la_huella_antes_de_escribir(tmp_path: Path):
    files, workspace_id, proyecto = _entorno(tmp_path)
    solicitud = preparar_edicion(files, workspace_id, "app.py", 5, 5, "usa f-strings")
    propuesta = construir_propuesta(solicitud, "    mensaje = f'hola {nombre}'", ARCHIVO)

    # La persona se toma su tiempo en decidir y el archivo cambia mientras tanto.
    (proyecto / "app.py").write_text("distinto\n", encoding="utf-8")

    with pytest.raises(EdicionDesincronizadaError):
        aplicar_propuesta(files, propuesta)
    assert (proyecto / "app.py").read_text(encoding="utf-8") == "distinto\n"


# --------------------------------------------------------------------------- #
# Flujo completo y aplicación
# --------------------------------------------------------------------------- #


def test_flujo_completo_propone_y_aplica_lo_mismo_que_mostro(tmp_path: Path):
    files, workspace_id, proyecto = _entorno(tmp_path)
    propuesta = asyncio.run(
        editar_inline(
            files,
            workspace_id,
            "app.py",
            5,
            5,
            "usa f-strings",
            completar=_completador("    mensaje = f'hola {nombre}'"),
        )
    )

    # Proponer NO escribe.
    assert (proyecto / "app.py").read_text(encoding="utf-8") == ARCHIVO

    resultado = aplicar_propuesta(files, propuesta)
    assert resultado["aplicado"] is True
    assert (proyecto / "app.py").read_text(encoding="utf-8") == propuesta.contenido_nuevo
    assert "f'hola {nombre}'" in (proyecto / "app.py").read_text(encoding="utf-8")


def test_aplicar_una_propuesta_sin_cambios_no_escribe(tmp_path: Path):
    files, workspace_id, proyecto = _entorno(tmp_path)
    antes = (proyecto / "app.py").stat().st_mtime_ns
    solicitud = preparar_edicion(files, workspace_id, "app.py", 5, 5, "déjalo igual")
    propuesta = construir_propuesta(solicitud, "    mensaje = 'hola ' + nombre", ARCHIVO)

    resultado = aplicar_propuesta(files, propuesta)

    assert resultado["aplicado"] is False
    assert (proyecto / "app.py").stat().st_mtime_ns == antes


# --------------------------------------------------------------------------- #
# Latencia: modelo del perfil rápido, leído de config/modelos.yml
# --------------------------------------------------------------------------- #


def test_el_modelo_sale_del_perfil_rapido_del_yaml(tmp_path: Path):
    yaml_falso = tmp_path / "modelos.yml"
    yaml_falso.write_text(
        "perfiles:\n"
        f"  {PERFIL_EDICION_INLINE}:\n"
        "    modelo: '@cf/proveedor/veloz'\n"
        "    contexto_max_tokens: 16000\n"
        "  ingenieria_software:\n"
        "    modelo: '@cf/proveedor/pesado'\n",
        encoding="utf-8",
    )

    elegido = modelo_para_edicion_inline(ruta_yaml=yaml_falso)

    assert elegido == "@cf/proveedor/veloz"
    assert elegido != "@cf/proveedor/pesado"


def test_un_modelo_no_declarado_en_el_yaml_se_rechaza():
    with pytest.raises(IDEEdicionInlineError, match="no está declarado"):
        modelo_para_edicion_inline("@cf/inventado/modelo-que-no-existe")


def test_la_peticion_va_sin_herramientas_con_deadline_corto_y_reserva_de_razonamiento(
    tmp_path: Path,
):
    files, workspace_id, _ = _entorno(tmp_path)
    solicitud = preparar_edicion(files, workspace_id, "app.py", *RANGO_SALUDAR, "usa f-strings")
    peticion = construir_peticion(solicitud)

    assert peticion.tools == []
    assert peticion.reasoning_effort == "low"
    assert peticion.metadata["deadline_s"] <= 60
    # Regla `presupuesto_de_razonamiento` de config/modelos.yml: siempre por
    # encima del contenido esperado, o la respuesta llega vacía y se cobra igual.
    assert peticion.max_tokens >= 512
    assert peticion.temperature <= 0.2


def test_editar_inline_usa_el_modelo_resuelto_por_perfil(tmp_path: Path):
    files, workspace_id, _ = _entorno(tmp_path)
    registro: list[CompletionRequest] = []
    asyncio.run(
        editar_inline(
            files,
            workspace_id,
            "app.py",
            5,
            5,
            "usa f-strings",
            completar=_completador("    mensaje = f'hola {nombre}'", registro),
        )
    )

    assert len(registro) == 1
    assert registro[0].model
    assert registro[0].model.startswith("@cf/")
