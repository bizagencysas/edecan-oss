"""Pruebas de conocimiento verificado del IDE -- ``ide_conocimiento.ConocimientoStore``.

Cubre lo que pidió el encargo explícitamente: guardar con fuente, rechazar
sin fuente, recuperar fresco, recuperar caducado con su marca, y actualizar
un hecho cuando se reverifica. Suma además las piezas de diseño que el
módulo documenta como decisiones propias: TTL distinto por tipo, que leer
nunca cuenta como reverificar, y aislamiento entre workspaces.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from edecan_companion.ide_conocimiento import (
    MAX_CONTENT_CHARS,
    ConocimientoStore,
    IDEConocimientoError,
)
from edecan_companion.ide_workspaces import WorkspaceStore

FUENTE_VALIDA = "https://nodejs.org/en/about/previous-releases"


def _make_store(tmp_path: Path, **kwargs) -> tuple[ConocimientoStore, str]:
    state_dir = tmp_path / "state"
    project = tmp_path / "proyecto"
    project.mkdir()
    workspaces = WorkspaceStore(state_dir)
    registro = workspaces.authorize(str(project))
    conocimiento = ConocimientoStore(state_dir, workspaces, **kwargs)
    return conocimiento, registro["id"]


def _hace_dias(dias: float) -> str:
    """Fecha ISO ``dias`` en el pasado, en el mismo formato que usa el
    módulo -- para simular en tests un hecho verificado hace tiempo sin
    tener que esperar días reales."""

    epoch = time.time() - dias * 86400
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


# --------------------------------------------------------------------- #
# Guardar con fuente, y recuperar fresco.
# --------------------------------------------------------------------- #


def test_verificar_y_obtener_hecho_fresco(tmp_path: Path):
    conocimiento, workspace_id = _make_store(tmp_path)
    conocimiento.verificar(
        workspace_id,
        "version_vigente",
        "versión estable de Node.js",
        "La versión estable (LTS) de Node.js es la 22.x.",
        FUENTE_VALIDA,
    )

    hecho = conocimiento.obtener(workspace_id, "version_vigente", "versión estable de Node.js")

    assert hecho is not None
    assert hecho["vigente"] is True
    assert hecho["fuente_url"] == FUENTE_VALIDA
    assert hecho["veces_reverificado"] == 0
    assert hecho["edad_dias"] < 1


def test_obtener_hecho_nunca_verificado_devuelve_none(tmp_path: Path):
    conocimiento, workspace_id = _make_store(tmp_path)

    assert conocimiento.obtener(workspace_id, "version_vigente", "versión de Rust") is None


# --------------------------------------------------------------------- #
# Rechazar sin fuente.
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("fuente", [None, "", "   ", "me acuerdo que era así", "ftp://x.com/a"])
def test_verificar_sin_fuente_valida_se_rechaza(tmp_path: Path, fuente):
    conocimiento, workspace_id = _make_store(tmp_path)
    with pytest.raises(IDEConocimientoError):
        conocimiento.verificar(
            workspace_id,
            "practica_recomendada",
            "forma de autenticar contra la API de Stripe",
            "Se recomienda usar claves restringidas y rotarlas cada 90 días.",
            fuente,
        )


def test_contenido_demasiado_largo_se_rechaza(tmp_path: Path):
    conocimiento, workspace_id = _make_store(tmp_path)
    with pytest.raises(IDEConocimientoError):
        conocimiento.verificar(
            workspace_id,
            "version_vigente",
            "versión estable de algo",
            "x" * (MAX_CONTENT_CHARS + 1),
            FUENTE_VALIDA,
        )


def test_kind_invalido_se_rechaza(tmp_path: Path):
    conocimiento, workspace_id = _make_store(tmp_path)
    with pytest.raises(IDEConocimientoError):
        conocimiento.verificar(
            workspace_id, "opinion", "algo", "un contenido cualquiera de prueba", FUENTE_VALIDA
        )


def test_workspace_inexistente_se_rechaza(tmp_path: Path):
    conocimiento, _workspace_id = _make_store(tmp_path)
    with pytest.raises(IDEConocimientoError):
        conocimiento.verificar(
            "no-existe", "version_vigente", "tema", "un contenido cualquiera", FUENTE_VALIDA
        )
    with pytest.raises(IDEConocimientoError):
        conocimiento.obtener("no-existe", "version_vigente", "tema")


# --------------------------------------------------------------------- #
# Recuperar caducado, marcado -- nunca en silencio.
# --------------------------------------------------------------------- #


def test_hecho_vencido_se_entrega_marcado_no_se_calla(tmp_path: Path):
    conocimiento, workspace_id = _make_store(tmp_path)
    conocimiento.verificar(
        workspace_id,
        "version_vigente",
        "versión estable de Node.js",
        "La versión estable (LTS) de Node.js es la 20.x.",
        FUENTE_VALIDA,
        verified_at=_hace_dias(45),  # TTL de version_vigente es 30 días
    )

    hecho = conocimiento.obtener(workspace_id, "version_vigente", "versión estable de Node.js")

    assert hecho is not None  # se entrega igual, no se borra en silencio
    assert hecho["vigente"] is False
    assert hecho["edad_dias"] > hecho["ttl_dias"]


def test_ttl_distinto_por_tipo_de_hecho(tmp_path: Path):
    """El mismo tiempo transcurrido (40 días) deja vencido un
    ``version_vigente`` (TTL 30) pero deja vigente un
    ``practica_recomendada`` (TTL 120) -- justo el punto de tener TTL
    distinto por tipo en vez de uno solo para todo el módulo."""

    conocimiento, workspace_id = _make_store(tmp_path)
    hace_40_dias = _hace_dias(40)
    conocimiento.verificar(
        workspace_id,
        "version_vigente",
        "versión estable de Deno",
        "La versión estable de Deno es la 2.x.",
        FUENTE_VALIDA,
        verified_at=hace_40_dias,
    )
    conocimiento.verificar(
        workspace_id,
        "practica_recomendada",
        "forma de autenticar contra la API de GitHub",
        "Se recomienda usar OAuth device flow en vez de tokens de acceso personal.",
        FUENTE_VALIDA,
        verified_at=hace_40_dias,
    )

    version = conocimiento.obtener(workspace_id, "version_vigente", "versión estable de Deno")
    practica = conocimiento.obtener(
        workspace_id, "practica_recomendada", "forma de autenticar contra la API de GitHub"
    )

    assert version["vigente"] is False
    assert practica["vigente"] is True


# --------------------------------------------------------------------- #
# Actualizar un hecho al reverificarlo.
# --------------------------------------------------------------------- #


def test_reverificar_el_mismo_tema_actualiza_no_duplica(tmp_path: Path):
    conocimiento, workspace_id = _make_store(tmp_path)
    conocimiento.verificar(
        workspace_id,
        "version_vigente",
        "versión estable de Node.js",
        "La versión estable (LTS) de Node.js es la 20.x.",
        FUENTE_VALIDA,
        verified_at=_hace_dias(50),
    )

    actualizado = conocimiento.verificar(
        workspace_id,
        "version_vigente",
        "  Versión estable de Node.js  ",
        "La versión estable (LTS) de Node.js es la 22.x.",
        "https://nodejs.org/en/download",
    )

    notas = conocimiento.listar(workspace_id)
    assert len(notas) == 1  # no se duplicó la fila
    assert actualizado["contenido"] == "La versión estable (LTS) de Node.js es la 22.x."
    assert actualizado["fuente_url"] == "https://nodejs.org/en/download"
    assert actualizado["veces_reverificado"] == 1
    assert actualizado["vigente"] is True  # se refrescó verified_at


def test_leer_no_cuenta_como_reverificar(tmp_path: Path):
    """A diferencia de ``MemoriaStore.recall``, leer un hecho de
    conocimiento NUNCA debe tocar su fecha de verificación ni su contador --
    ver el docstring del módulo."""

    conocimiento, workspace_id = _make_store(tmp_path)
    conocimiento.verificar(
        workspace_id,
        "version_vigente",
        "versión estable de Node.js",
        "La versión estable (LTS) de Node.js es la 22.x.",
        FUENTE_VALIDA,
    )

    conocimiento.obtener(workspace_id, "version_vigente", "versión estable de Node.js")
    conocimiento.buscar(workspace_id, "versión de Node.js")

    hecho = conocimiento.listar(workspace_id)[0]
    assert hecho["veces_reverificado"] == 0


# --------------------------------------------------------------------- #
# Búsqueda por relevancia.
# --------------------------------------------------------------------- #


def test_buscar_encuentra_por_palabras_en_comun(tmp_path: Path):
    conocimiento, workspace_id = _make_store(tmp_path)
    conocimiento.verificar(
        workspace_id,
        "cambio_deprecado",
        "parámetro legacy de la API de pagos",
        "El parámetro `legacy_token` quedó deprecado a favor de `api_key`.",
        FUENTE_VALIDA,
    )

    resultados = conocimiento.buscar(workspace_id, "¿sigue vigente el parámetro legacy_token?")

    assert len(resultados) == 1
    assert "legacy_token" in resultados[0]["contenido"]


def test_buscar_no_devuelve_nada_sin_coincidencia_lexica(tmp_path: Path):
    conocimiento, workspace_id = _make_store(tmp_path)
    conocimiento.verificar(
        workspace_id,
        "version_vigente",
        "versión estable de Node.js",
        "La versión estable (LTS) de Node.js es la 22.x.",
        FUENTE_VALIDA,
    )

    assert conocimiento.buscar(workspace_id, "cuota gratuita de un servicio de correo") == []


# --------------------------------------------------------------------- #
# Listado, borrado, y aislamiento entre workspaces.
# --------------------------------------------------------------------- #


def test_listar_no_filtra_por_vigencia(tmp_path: Path):
    conocimiento, workspace_id = _make_store(tmp_path)
    conocimiento.verificar(
        workspace_id, "version_vigente", "versión de Python", "Python 3.13 es la última.",
        FUENTE_VALIDA, verified_at=_hace_dias(200),
    )
    conocimiento.verificar(
        workspace_id, "limite_o_cuota", "cuota gratuita de Resend", "1000 correos/mes gratis.",
        FUENTE_VALIDA,
    )

    assert len(conocimiento.listar(workspace_id)) == 2


def test_olvidar_borra_un_hecho_puntual(tmp_path: Path):
    conocimiento, workspace_id = _make_store(tmp_path)
    creado = conocimiento.verificar(
        workspace_id, "version_vigente", "versión de un paquete de prueba",
        "La versión de prueba es la 1.0.", FUENTE_VALIDA,
    )

    conocimiento.olvidar(workspace_id, creado["id"])

    assert conocimiento.listar(workspace_id) == []
    with pytest.raises(IDEConocimientoError):
        conocimiento.olvidar(workspace_id, creado["id"])


def test_lo_verificado_en_un_proyecto_sirve_en_los_demas(tmp_path: Path):
    """Este almacén es GLOBAL a propósito, al revés que `ide_memoria.py`.

    La distinción es la razón de que sean dos módulos y no uno:
      - `ide_memoria`  guarda lo cierto de UN repo ("aquí los tests necesitan
        Postgres real") y NO debe cruzar de proyecto: contaminaría los demás.
      - `ide_conocimiento` guarda lo cierto del MUNDO ("la versión estable de
        esa librería es N.N"), y eso no cambia al abrir otro repo.

    Aislar esto por proyecto obligaba a reverificar en cada uno lo mismo que ya
    se comprobó ayer en el de al lado -- exactamente el trabajo que este módulo
    existe para evitar. Y es seguro compartirlo por CÓMO se guarda, no por
    confianza: `verificar` exige una URL de fuente y cada hecho caduca por tipo.
    """
    state_dir = tmp_path / "state"
    proyecto_a = tmp_path / "proyecto_a"
    proyecto_b = tmp_path / "proyecto_b"
    proyecto_a.mkdir()
    proyecto_b.mkdir()
    workspaces = WorkspaceStore(state_dir)
    workspace_a = workspaces.authorize(str(proyecto_a))["id"]
    workspace_b = workspaces.authorize(str(proyecto_b))["id"]
    conocimiento = ConocimientoStore(state_dir, workspaces)

    conocimiento.verificar(
        workspace_a, "version_vigente", "versión estable de una librería",
        "La versión estable de esa librería es la 1.0.", FUENTE_VALIDA,
    )

    # Comprobado en A, disponible en B sin volver a buscarlo en Internet.
    assert len(conocimiento.listar(workspace_a)) == 1
    assert len(conocimiento.listar(workspace_b)) == 1

    # Y la procedencia se conserva: se sabe dónde se comprobó, aunque ya no
    # restrinja quién lo ve.
    assert conocimiento.listar(workspace_b)[0]["workspace_id"] == workspace_a


def test_un_hecho_erroneo_se_puede_corregir_desde_cualquier_proyecto(tmp_path: Path):
    """Si un hecho equivocado se ve desde todos lados, tiene que poder borrarse
    desde todos lados. Obligar a volver al repo donde se comprobó dejaría el
    dato malo circulando por el resto."""
    state_dir = tmp_path / "state"
    proyecto_a = tmp_path / "proyecto_a"
    proyecto_b = tmp_path / "proyecto_b"
    proyecto_a.mkdir()
    proyecto_b.mkdir()
    workspaces = WorkspaceStore(state_dir)
    workspace_a = workspaces.authorize(str(proyecto_a))["id"]
    workspace_b = workspaces.authorize(str(proyecto_b))["id"]
    conocimiento = ConocimientoStore(state_dir, workspaces)

    hecho = conocimiento.verificar(
        workspace_a, "version_vigente", "un dato que resultó estar mal",
        "Este dato resultó ser incorrecto.", FUENTE_VALIDA,
    )

    conocimiento.olvidar(workspace_b, hecho["id"])

    assert conocimiento.listar(workspace_a) == []


def test_persiste_entre_instancias_del_store(tmp_path: Path):
    state_dir = tmp_path / "state"
    project = tmp_path / "proyecto"
    project.mkdir()
    workspaces = WorkspaceStore(state_dir)
    workspace_id = workspaces.authorize(str(project))["id"]

    primero = ConocimientoStore(state_dir, workspaces)
    primero.verificar(
        workspace_id, "version_vigente", "versión que debe sobrevivir a un reinicio",
        "Esta versión debe seguir ahí tras reiniciar el companion.", FUENTE_VALIDA,
    )

    segundo = ConocimientoStore(state_dir, workspaces)
    assert len(segundo.listar(workspace_id)) == 1
