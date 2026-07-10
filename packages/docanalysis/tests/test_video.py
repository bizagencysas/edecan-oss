"""Tests de `edecan_docanalysis.video` (`extraer_frames`, `construir_bloques_video`,
`analizar_video`, WP-V3-14).

Ningún test invoca al binario `ffmpeg`/`ffprobe` real: `fake_ffmpeg` escribe un
script `#!/bin/sh` ejecutable en `tmp_path` que imita el contrato que
`extraer_frames` espera de `ffmpeg` (crea `out_NNN.jpg` a partir del patrón de
salida que recibe como último argumento, y respeta `exit_code`/`stderr`/
`sleep_seconds` de prueba) y registra cada argv recibido en un archivo sidecar
para que los tests puedan inspeccionar exactamente qué le pidió `extraer_frames`.
`sin_ffmpeg` neutraliza `ffmpeg_disponible()` a `None` sin importar si el host
que corre la suite tiene ffmpeg/ffprobe reales instalados. Ninguna llamada de
red ni al filesystem fuera de `tmp_path` (ARCHITECTURE.md §10.15).
"""

from __future__ import annotations

import base64
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from edecan_docanalysis import video as video_module
from edecan_docanalysis.video import (
    FFMPEG_INSTALL_HINT,
    AnalizarVideoTool,
    VideoAnalysisError,
    construir_bloques_video,
    extraer_frames,
    ffmpeg_disponible,
)

# Bytes JPEG mínimos (SOI + APP0 + EOI) que escribe el ffmpeg fake en cada frame.
_JPEG_MINIMO = b"\xff\xd8\xff\xe0\xff\xd9"
_VIDEO_BYTES = b"contenido-binario-de-un-video-fake"

_SCRIPT_TEMPLATE = r"""#!/bin/sh
# Fake ffmpeg para tests de edecan_docanalysis.video (WP-V3-14): nunca invoca
# al binario real. Registra cada argv recibido y, si exit_code es 0, crea
# n_frames archivos JPEG minimos a partir del patron de salida (ultimo argv).
for a in "$@"; do
  printf '%s\n' "$a" >> "{argv_log}"
done
sleep {sleep_seconds}
if [ {exit_code} -ne 0 ]; then
  printf '%s' '{stderr}' 1>&2
  exit {exit_code}
fi
for ultimo in "$@"; do :; done
i=1
while [ "$i" -le {n_frames} ]; do
  n=$(printf '%03d' "$i")
  destino=$(printf '%s' "$ultimo" | sed "s/%03d/$n/")
  printf '\xff\xd8\xff\xe0\xff\xd9' > "$destino"
  i=$((i + 1))
done
exit 0
"""


@pytest.fixture
def fake_ffmpeg(tmp_path, monkeypatch):
    """Factory: `fake_ffmpeg(n_frames=2, exit_code=0, stderr="", sleep_seconds=0.0)`
    escribe un ffmpeg fake NUEVO en `tmp_path` (se puede llamar varias veces
    por test) y apunta `FFMPEG_PATH` a él, así `ffmpeg_disponible()` lo
    resuelve sin tocar el PATH real del host. También neutraliza
    `shutil.which("ffprobe")` (siempre `None`), para que `_calcular_fps`/
    `estimar_duracion_segundos` caigan siempre al fallback determinista sin
    importar si el host de test tiene ffprobe instalado.

    Devuelve `(script_path, argv_log_path)` — `argv_log_path` solo existe en
    disco después de invocar el script al menos una vez (`extraer_frames`
    hace exactamente una invocación por llamada).
    """
    contador = {"n": 0}
    monkeypatch.setattr(shutil, "which", lambda nombre: None)

    def _make(
        *,
        n_frames: int = 2,
        exit_code: int = 0,
        stderr: str = "",
        sleep_seconds: float = 0.0,
    ) -> tuple[Path, Path]:
        contador["n"] += 1
        script = tmp_path / f"ffmpeg_fake_{contador['n']}"
        argv_log = tmp_path / f"ffmpeg_fake_{contador['n']}.argv.log"
        contenido = _SCRIPT_TEMPLATE.format(
            argv_log=str(argv_log),
            sleep_seconds=sleep_seconds,
            exit_code=exit_code,
            stderr=stderr,
            n_frames=n_frames,
        )
        script.write_text(contenido, encoding="utf-8")
        script.chmod(0o755)
        monkeypatch.setenv("FFMPEG_PATH", str(script))
        return script, argv_log

    return _make


@pytest.fixture
def sin_ffmpeg(monkeypatch):
    """Fuerza `ffmpeg_disponible()` a `None` sin importar si el host que corre
    la suite tiene ffmpeg real instalado o `FFMPEG_PATH` seteado en su
    entorno de proceso."""
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    monkeypatch.setattr(shutil, "which", lambda nombre: None)


# ---------------------------------------------------------------------------
# ffmpeg_disponible
# ---------------------------------------------------------------------------


def test_ffmpeg_disponible_respeta_ffmpeg_path_y_gana_sobre_which(monkeypatch):
    # FFMPEG_PATH del entorno gana sobre la autodetección (mismo criterio que
    # CLAUDE_CLI_PATH/CODEX_CLI_PATH en edecan_llm.detect, ver docstring del módulo).
    monkeypatch.setattr(shutil, "which", lambda nombre: "/usr/local/bin/otro-ffmpeg")
    monkeypatch.setenv("FFMPEG_PATH", "/ruta/elegida/a/mano/ffmpeg")
    assert ffmpeg_disponible() == "/ruta/elegida/a/mano/ffmpeg"


def test_ffmpeg_disponible_none_si_no_esta_instalado(sin_ffmpeg):
    assert ffmpeg_disponible() is None


# ---------------------------------------------------------------------------
# extraer_frames
# ---------------------------------------------------------------------------


async def test_extraer_frames_feliz(fake_ffmpeg):
    fake_ffmpeg(n_frames=3)

    frames = await extraer_frames(_VIDEO_BYTES, max_frames=3)

    assert len(frames) == 3
    assert all(frame == _JPEG_MINIMO for frame in frames)


async def test_extraer_frames_arma_el_argv_de_ffmpeg_correctamente(fake_ffmpeg):
    _script, argv_log = fake_ffmpeg(n_frames=2)

    await extraer_frames(_VIDEO_BYTES, max_frames=2, escala_px=480)

    argv = argv_log.read_text(encoding="utf-8").splitlines()
    assert argv[0] == "-i"
    assert any(a.startswith("fps=") and a.endswith("scale=480:-2") for a in argv)
    assert argv[argv.index("-frames:v") + 1] == "2"
    assert argv[argv.index("-q:v") + 1] == "4"
    assert argv[-1].endswith(".jpg")  # patrón de salida out_%03d.jpg


async def test_extraer_frames_acota_max_frames_fuera_de_rango(fake_ffmpeg):
    _script, argv_log = fake_ffmpeg(n_frames=1)

    await extraer_frames(_VIDEO_BYTES, max_frames=999)

    argv = argv_log.read_text(encoding="utf-8").splitlines()
    assert argv[argv.index("-frames:v") + 1] == "16"  # acotado a _MAX_FRAMES


async def test_extraer_frames_error_de_ffmpeg(fake_ffmpeg):
    fake_ffmpeg(exit_code=1, stderr="input invalido")

    with pytest.raises(VideoAnalysisError, match="input invalido"):
        await extraer_frames(_VIDEO_BYTES, max_frames=2)


async def test_extraer_frames_timeout(fake_ffmpeg):
    fake_ffmpeg(sleep_seconds=5)

    with pytest.raises(VideoAnalysisError, match="no terminó"):
        await extraer_frames(_VIDEO_BYTES, max_frames=2, timeout_seconds=0.2)


async def test_extraer_frames_sin_frames_generados(fake_ffmpeg):
    fake_ffmpeg(n_frames=0)  # ffmpeg "termina bien" pero no produce nada

    with pytest.raises(VideoAnalysisError, match="no generó ningún frame"):
        await extraer_frames(_VIDEO_BYTES, max_frames=2)


async def test_extraer_frames_sin_ffmpeg_instalado(sin_ffmpeg):
    with pytest.raises(VideoAnalysisError) as excinfo:
        await extraer_frames(_VIDEO_BYTES, max_frames=2)
    assert str(excinfo.value) == FFMPEG_INSTALL_HINT


async def test_extraer_frames_ffmpeg_path_apunta_a_binario_inexistente(monkeypatch):
    # `ffmpeg_disponible()` no valida que FFMPEG_PATH exista en disco (solo lo
    # antepone a la autodetección) — este caso ejercita el `FileNotFoundError`
    # que atrapa `_ejecutar_ffmpeg` al intentar exec-earlo de verdad.
    monkeypatch.setattr(shutil, "which", lambda nombre: None)
    monkeypatch.setenv("FFMPEG_PATH", "/ruta/que/no/existe/ffmpeg-fake-inexistente")

    with pytest.raises(VideoAnalysisError) as excinfo:
        await extraer_frames(_VIDEO_BYTES, max_frames=2)
    assert str(excinfo.value) == FFMPEG_INSTALL_HINT


async def test_extraer_frames_rechaza_video_grande_sin_tocar_ffmpeg(monkeypatch, fake_ffmpeg):
    # ffmpeg fake configurado pero que NUNCA debe invocarse: si `extraer_frames`
    # lo llamara igual, `argv_log` existiría en disco. Que no exista confirma
    # que el chequeo de tamaño corta ANTES de escribir el tmpfile / correr ffmpeg.
    monkeypatch.setattr(video_module, "_TAMANO_MAXIMO_BYTES", 10)
    _script, argv_log = fake_ffmpeg(n_frames=1)

    with pytest.raises(VideoAnalysisError, match="MB"):
        await extraer_frames(b"x" * 11, max_frames=1)

    assert not argv_log.exists()


# ---------------------------------------------------------------------------
# construir_bloques_video
# ---------------------------------------------------------------------------


def test_construir_bloques_video_sin_duracion_conocida():
    bloques = construir_bloques_video([b"f1", b"f2"], "¿Qué pasa?")

    etiquetas = [b["text"] for b in bloques if b.get("type") == "text" and b["text"][:5] == "Frame"]
    assert etiquetas == ["Frame 1 de 2", "Frame 2 de 2"]
    assert bloques[-1] == {"type": "text", "text": "¿Qué pasa?"}
    imagenes = [b for b in bloques if b.get("type") == "image"]
    assert len(imagenes) == 2
    assert imagenes[0]["source"]["media_type"] == "image/jpeg"


def test_construir_bloques_video_con_duracion_agrega_timestamps_aproximados():
    bloques = construir_bloques_video(
        [b"f1", b"f2", b"f3", b"f4"], "¿Qué pasa?", duracion_estimada_s=40.0
    )

    etiquetas = [b["text"] for b in bloques if b.get("type") == "text" and b["text"][:5] == "Frame"]
    assert etiquetas == [
        "Frame 1 de 4, ~00:00",
        "Frame 2 de 4, ~00:10",
        "Frame 3 de 4, ~00:20",
        "Frame 4 de 4, ~00:30",
    ]


# ---------------------------------------------------------------------------
# AnalizarVideoTool
# ---------------------------------------------------------------------------


async def test_archivo_invalido(make_ctx, fake_s3):
    resultado = await AnalizarVideoTool().run(make_ctx(), {"archivo": "no-es-uuid"})
    assert "identificador válido" in resultado.content


async def test_archivo_no_encontrado(make_ctx, fake_s3):
    fake_s3.archivo = None
    resultado = await AnalizarVideoTool().run(make_ctx(), {"archivo": str(uuid4())})
    assert "No encontré ese archivo" in resultado.content


async def test_rechaza_mime_que_no_es_video(make_ctx, fake_s3, make_archivo):
    fake_s3.archivo = make_archivo(
        contenido=b"no soy un video", filename="foto.png", mime="image/png"
    )

    resultado = await AnalizarVideoTool().run(make_ctx(), {"archivo": str(uuid4())})

    assert "no parece un video" in resultado.content


async def test_proveedor_no_anthropic_nunca_toca_ffmpeg_ni_llama_al_llm(
    make_ctx, fake_s3, make_archivo, make_llm
):
    fake_s3.archivo = make_archivo(contenido=_VIDEO_BYTES, filename="clip.mp4", mime="video/mp4")
    llm = make_llm(proveedor_nombre="openai_compat")
    ctx = make_ctx(llm=llm)
    # A propósito, sin configurar `fake_ffmpeg`/`sin_ffmpeg`: si la tool
    # llegara a revisar/usar ffmpeg antes que el proveedor, este test fallaría
    # de forma flaky (depende de si el host tiene ffmpeg real) en vez de
    # fallar limpio — confirma que el chequeo de proveedor corta primero.

    resultado = await AnalizarVideoTool().run(ctx, {"archivo": str(uuid4())})

    assert "proveedor LLM con soporte de visión" in resultado.content
    assert "openai_compat" in resultado.content
    assert llm.llamadas == []


async def test_ffmpeg_ausente_devuelve_mensaje_instructivo_sin_traceback(
    make_ctx, fake_s3, make_archivo, sin_ffmpeg
):
    fake_s3.archivo = make_archivo(contenido=_VIDEO_BYTES, filename="clip.mp4", mime="video/mp4")

    resultado = await AnalizarVideoTool().run(make_ctx(), {"archivo": str(uuid4())})

    assert resultado.content == FFMPEG_INSTALL_HINT
    assert "brew install ffmpeg" in resultado.content
    assert "apt install ffmpeg" in resultado.content


async def test_error_de_ffmpeg_se_devuelve_como_tool_result_sin_excepcion(
    make_ctx, fake_s3, make_archivo, fake_ffmpeg
):
    fake_s3.archivo = make_archivo(contenido=_VIDEO_BYTES, filename="clip.mp4", mime="video/mp4")
    fake_ffmpeg(exit_code=1, stderr="codec no soportado")

    resultado = await AnalizarVideoTool().run(make_ctx(), {"archivo": str(uuid4())})

    assert "codec no soportado" in resultado.content


async def test_flujo_feliz_manda_n_frames_y_respeta_flags_del_tenant(
    make_ctx, fake_s3, make_archivo, make_llm, fake_ffmpeg
):
    fake_s3.archivo = make_archivo(contenido=_VIDEO_BYTES, filename="clip.mp4", mime="video/mp4")
    fake_ffmpeg(n_frames=3)
    llm = make_llm(texto="Se ve una persona caminando y luego se sienta.")
    ctx = make_ctx(llm=llm, extras={"flags": {"models.premium": True}})

    resultado = await AnalizarVideoTool().run(ctx, {"archivo": str(uuid4()), "max_frames": 3})

    assert resultado.content == "Se ve una persona caminando y luego se sienta."
    assert resultado.data == {"frames_analizados": 3, "duracion_estimada_s": None}

    assert len(llm.llamadas) == 1
    alias, flags, req = llm.llamadas[0]
    assert alias == "principal"
    assert flags == {"models.premium": True}

    bloques = req.messages[0].content
    bloques_imagen = [b for b in bloques if b.get("type") == "image"]
    assert len(bloques_imagen) == 3
    for bloque in bloques_imagen:
        assert bloque["source"]["type"] == "base64"
        assert bloque["source"]["media_type"] == "image/jpeg"
        assert base64.b64decode(bloque["source"]["data"]) == _JPEG_MINIMO

    # última pregunta por defecto, al final de los bloques
    assert bloques[-1] == {
        "type": "text",
        "text": "¿Qué ocurre en este video? Resume los eventos clave.",
    }
    etiquetas = [b["text"] for b in bloques if b.get("type") == "text" and b["text"][:5] == "Frame"]
    assert etiquetas == ["Frame 1 de 3", "Frame 2 de 3", "Frame 3 de 3"]


async def test_flujo_con_pregunta_explicita_y_max_frames_por_defecto(
    make_ctx, fake_s3, make_archivo, make_llm, fake_ffmpeg
):
    fake_s3.archivo = make_archivo(
        contenido=_VIDEO_BYTES, filename="clip.mov", mime="video/quicktime"
    )
    _script, argv_log = fake_ffmpeg(n_frames=8)
    llm = make_llm(texto="Aparece un auto rojo.")
    ctx = make_ctx(llm=llm)

    resultado = await AnalizarVideoTool().run(
        ctx, {"archivo": str(uuid4()), "pregunta": "¿Qué marca de auto aparece?"}
    )

    assert resultado.data["frames_analizados"] == 8
    argv = argv_log.read_text(encoding="utf-8").splitlines()
    assert argv[argv.index("-frames:v") + 1] == "8"  # _DEFAULT_MAX_FRAMES

    _alias, _flags, req = llm.llamadas[0]
    assert req.messages[0].content[-1] == {
        "type": "text",
        "text": "¿Qué marca de auto aparece?",
    }


async def test_resuelve_mime_por_extension_si_el_declarado_es_generico(
    make_ctx, fake_s3, make_archivo, make_llm, fake_ffmpeg
):
    fake_s3.archivo = make_archivo(
        contenido=_VIDEO_BYTES, filename="clip.webm", mime="application/octet-stream"
    )
    fake_ffmpeg(n_frames=1)
    llm = make_llm(texto="ok")
    ctx = make_ctx(llm=llm)

    resultado = await AnalizarVideoTool().run(ctx, {"archivo": str(uuid4()), "max_frames": 1})

    assert resultado.content == "ok"  # no lo rechazó por mime genérico


async def test_respuesta_vacia_del_llm_cae_a_mensaje_claro(
    make_ctx, fake_s3, make_archivo, make_llm, fake_ffmpeg
):
    fake_s3.archivo = make_archivo(contenido=_VIDEO_BYTES, filename="clip.mp4", mime="video/mp4")
    fake_ffmpeg(n_frames=2)
    llm = make_llm(texto="   ")
    ctx = make_ctx(llm=llm)

    resultado = await AnalizarVideoTool().run(ctx, {"archivo": str(uuid4())})

    assert "No logré analizar ese video" in resultado.content
