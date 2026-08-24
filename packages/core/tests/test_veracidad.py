"""Contrato de veracidad (`edecan_core.veracidad`) — que un proveedor
simulado no pueda pasar por real.

Ver el docstring del módulo bajo prueba para el contexto completo (medido en
la instalación viva: `StubTTS` devolviendo 0.5s de silencio con HTTP 200 y
"puedes escucharlo desde tus archivos").
"""

from __future__ import annotations

import pytest
from edecan_core.tools.base import ToolResult
from edecan_core.veracidad import Fidelidad, InfoFidelidad, ProveedorDeclarado

# ---------------------------------------------------------------------------
# `__init_subclass__` — la puerta que hace que declarar sea obligatorio, no
# opcional. Revienta AL DEFINIR LA CLASE (import time), no en un test que
# alguien podría no correr.
# ---------------------------------------------------------------------------


def test_un_proveedor_que_no_declara_nada_revienta_al_definirse():
    with pytest.raises(TypeError, match=r"no declara.*familia.*fidelidad.*fuente"):

        class ProveedorMudo(ProveedorDeclarado):
            pass


def test_un_proveedor_que_solo_declara_familia_tambien_revienta():
    with pytest.raises(TypeError, match=r"no declara"):

        class ProveedorIncompleto(ProveedorDeclarado):
            familia = "tts"


def test_un_proveedor_simulado_sin_motivo_revienta():
    """Declarar SIMULADO sin decir qué falta es el mismo patrón que este
    contrato existe para prohibir: un stub que se sabe falso y no lo dice."""
    with pytest.raises(TypeError, match=r"no dice qué falta"):

        class StubMudo(ProveedorDeclarado):
            familia = "tts"
            fidelidad = Fidelidad.SIMULADO
            fuente = "silencio"
            # sin motivo_simulado


def test_un_proveedor_simulado_con_motivo_no_revienta():
    class StubHonesto(ProveedorDeclarado):
        familia = "tts"
        fidelidad = Fidelidad.SIMULADO
        fuente = "silencio offline"
        motivo_simulado = "falta ELEVENLABS_API_KEY"

    assert StubHonesto().info_fidelidad().motivo_simulado == "falta ELEVENLABS_API_KEY"


def test_un_proveedor_real_no_necesita_motivo_simulado():
    class ProveedorReal(ProveedorDeclarado):
        familia = "tts"
        fidelidad = Fidelidad.REAL
        fuente = "ElevenLabs"

    assert ProveedorReal().info_fidelidad().motivo_simulado is None


# ---------------------------------------------------------------------------
# `InfoFidelidad.aviso_para_el_modelo` — el texto que `Agent.run_turn`
# antepone al turno `role="tool"` para que el modelo no pueda decir "ya lo
# puedes escuchar" sobre algo simulado.
# ---------------------------------------------------------------------------


def test_aviso_para_el_modelo_vacio_cuando_es_real():
    info = InfoFidelidad(familia="tts", fidelidad=Fidelidad.REAL, fuente="ElevenLabs")
    assert info.aviso_para_el_modelo() == ""


def test_aviso_para_el_modelo_es_accionable_cuando_es_simulado():
    info = InfoFidelidad(
        familia="tts",
        fidelidad=Fidelidad.SIMULADO,
        fuente="silencio offline (0.5s)",
        motivo_simulado="falta ELEVENLABS_API_KEY",
    )
    aviso = info.aviso_para_el_modelo()
    assert "SIMULADA" in aviso
    assert "tts=silencio offline (0.5s)" in aviso
    assert "falta ELEVENLABS_API_KEY" in aviso
    assert "no afirmes" in aviso.lower()


# ---------------------------------------------------------------------------
# `fidelidad_efectiva` — override por instancia (caso real: proveedor cuya
# fidelidad depende de config de runtime, no es fija por clase).
# ---------------------------------------------------------------------------


def test_fidelidad_efectiva_es_overrideable_por_instancia():
    class ProveedorSandboxOProduccion(ProveedorDeclarado):
        familia = "cotizaciones"
        fidelidad = Fidelidad.REAL  # el ClassVar es solo el default
        fuente = "Broker XYZ"

        def __init__(self, *, es_sandbox: bool) -> None:
            self._es_sandbox = es_sandbox

        def fidelidad_efectiva(self) -> Fidelidad:
            return Fidelidad.SIMULADO if self._es_sandbox else Fidelidad.REAL

    sandbox = ProveedorSandboxOProduccion(es_sandbox=True)
    produccion = ProveedorSandboxOProduccion(es_sandbox=False)
    assert sandbox.info_fidelidad().fidelidad is Fidelidad.SIMULADO
    assert produccion.info_fidelidad().fidelidad is Fidelidad.REAL


# ---------------------------------------------------------------------------
# `ToolResult.fidelidad` — el campo que conecta el contrato con
# `edecan_core.agent.Agent.run_turn` (ver su docstring).
# ---------------------------------------------------------------------------


def test_toolresult_fidelidad_es_none_por_defecto():
    """La mayoría de las tools de Edecán no dependen de un proveedor externo
    real/simulado — no deben tener que declarar nada."""
    resultado = ToolResult(content="listo")
    assert resultado.fidelidad is None


def test_toolresult_puede_llevar_info_fidelidad():
    info = InfoFidelidad(familia="tts", fidelidad=Fidelidad.SIMULADO, fuente="silencio")
    resultado = ToolResult(content="listo", fidelidad=info)
    assert resultado.fidelidad is info
