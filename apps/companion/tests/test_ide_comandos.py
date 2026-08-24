"""Pruebas del registro de comandos "/" del IDE (`ide_comandos.py`).

Cubre: parseo con y sin argumentos, resolución de alias al mismo comando,
comando desconocido con sugerencia (y sin ella cuando no aplica),
autocompletado por prefijo, y que `/help` liste todo el registro sin
duplicar alias.
"""

from __future__ import annotations

import pytest
from edecan_companion.ide_comandos import (
    IDEComandoError,
    autocompletar,
    listar_comandos,
    resolver_comando,
    texto_ayuda,
)

# ---------------------------------------------------------------------------
# Parseo básico: con y sin argumentos
# ---------------------------------------------------------------------------


def test_comando_sin_argumentos_se_resuelve():
    resuelto = resolver_comando("/help")
    assert resuelto.comando.nombre == "help"
    assert resuelto.alias_usado == "help"
    assert resuelto.argumentos is None


def test_comando_con_argumentos_conserva_el_texto_completo():
    resuelto = resolver_comando("/rename   Mi nueva conversación de prueba")
    assert resuelto.comando.nombre == "rename"
    assert resuelto.argumentos == "Mi nueva conversación de prueba"


def test_espacios_extra_alrededor_no_afectan_el_parseo():
    resuelto = resolver_comando("   /model   claude-sonnet-5   ")
    assert resuelto.comando.nombre == "model"
    assert resuelto.argumentos == "claude-sonnet-5"


def test_comando_opcional_puede_ir_sin_argumentos():
    resuelto = resolver_comando("/resume")
    assert resuelto.comando.nombre == "resume"
    assert resuelto.argumentos is None


def test_comando_obligatorio_sin_argumentos_falla_con_mensaje_claro():
    with pytest.raises(IDEComandoError, match="necesita argumentos"):
        resolver_comando("/rename")


def test_comando_obligatorio_con_solo_espacios_falla_igual():
    with pytest.raises(IDEComandoError, match="necesita argumentos"):
        resolver_comando("/rewind    ")


# ---------------------------------------------------------------------------
# Entradas mal formadas
# ---------------------------------------------------------------------------


def test_entrada_sin_barra_inicial_falla():
    with pytest.raises(IDEComandoError):
        resolver_comando("help")


def test_entrada_vacia_tras_la_barra_falla():
    with pytest.raises(IDEComandoError):
        resolver_comando("/")


def test_entrada_en_blanco_falla():
    with pytest.raises(IDEComandoError):
        resolver_comando("   ")


# ---------------------------------------------------------------------------
# Alias: de verdad el mismo comando
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("escrito", ["/clear", "/new", "/reset"])
def test_alias_de_clear_resuelven_al_mismo_objeto_comando(escrito):
    resuelto = resolver_comando(escrito)
    assert resuelto.comando is resolver_comando("/clear").comando
    assert resuelto.comando.destructivo is True


@pytest.mark.parametrize("escrito", ["/rc", "/remote-control"])
def test_alias_de_remote_control_resuelven_al_mismo_objeto_comando(escrito):
    resuelto = resolver_comando(escrito)
    assert resuelto.comando is resolver_comando("/remote-control").comando


@pytest.mark.parametrize("escrito", ["/settings", "/config"])
def test_alias_de_settings_resuelven_al_mismo_objeto_comando(escrito):
    resuelto = resolver_comando(escrito)
    assert resuelto.comando is resolver_comando("/config").comando


def test_alias_no_distingue_mayusculas():
    resuelto = resolver_comando("/CLEAR")
    assert resuelto.comando.nombre == "clear"


# ---------------------------------------------------------------------------
# Comando desconocido: con sugerencia y sin ella
# ---------------------------------------------------------------------------


def test_comando_desconocido_parecido_sugiere_el_mas_cercano():
    with pytest.raises(IDEComandoError) as excinfo:
        resolver_comando("/cler")
    assert excinfo.value.sugerencia == "/clear"
    assert "clear" in str(excinfo.value)


def test_comando_totalmente_distinto_no_sugiere_nada():
    with pytest.raises(IDEComandoError) as excinfo:
        resolver_comando("/qqzxjklw")
    assert excinfo.value.sugerencia is None


# ---------------------------------------------------------------------------
# Autocompletado por prefijo
# ---------------------------------------------------------------------------


def test_autocompletado_prefijo_re_devuelve_exactamente_estos_seis():
    esperado = [
        "/remote-control",
        "/rename",
        "/reset",
        "/resume",
        "/review",
        "/rewind",
    ]
    assert autocompletar("/re") == esperado


def test_autocompletado_acepta_prefijo_sin_barra_y_sin_distinguir_mayusculas():
    assert autocompletar("RE") == autocompletar("/re")


def test_autocompletado_incluye_alias_como_entrada_propia():
    # "/rc" es un alias corto de remote-control; debe aparecer para su propio
    # prefijo aunque el nombre canónico sea distinto.
    assert "/rc" in autocompletar("/r")


def test_autocompletado_sin_coincidencias_devuelve_lista_vacia():
    assert autocompletar("/zzzzz") == []


# ---------------------------------------------------------------------------
# /help generado desde el registro
# ---------------------------------------------------------------------------


def test_help_tiene_una_linea_por_comando_sin_duplicar_alias():
    lineas = texto_ayuda().splitlines()
    assert len(lineas) == len(listar_comandos())


def test_help_muestra_los_alias_juntos_en_una_sola_linea():
    lineas = texto_ayuda().splitlines()
    linea_clear = next(linea for linea in lineas if linea.startswith("/clear"))
    assert "/new" in linea_clear
    assert "/reset" in linea_clear
    # y no deben aparecer como líneas propias además de la de /clear:
    assert not any(linea.startswith("/new ") for linea in lineas)
    assert not any(linea.startswith("/reset ") for linea in lineas)


def test_help_marca_los_comandos_destructivos():
    lineas = texto_ayuda().splitlines()
    linea_clear = next(linea for linea in lineas if linea.startswith("/clear"))
    linea_help = next(linea for linea in lineas if linea.startswith("/help"))
    assert "destructivo" in linea_clear
    assert "destructivo" not in linea_help


def test_registro_no_tiene_nombres_duplicados_entre_comandos():
    todos_los_nombres = [nombre for comando in listar_comandos() for nombre in comando.nombres]
    assert len(todos_los_nombres) == len(set(todos_los_nombres))


def test_todos_los_comandos_pedidos_estan_registrados():
    esperados = {
        "help", "clear", "new", "reset", "rename", "branch", "resume", "model",
        "effort", "plan", "context", "cost", "usage", "memory", "agents",
        "batch", "diff", "rewind", "review", "security-review", "mcp",
        "doctor", "debug", "export", "copy", "init", "compact", "btw", "goal",
        "simplify", "background", "tasks", "permissions", "config",
        "settings", "voice", "remote-control", "rc", "workflows",
    }
    registrados = {nombre for comando in listar_comandos() for nombre in comando.nombres}
    assert registrados == esperados
