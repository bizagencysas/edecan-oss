"""Tests de `edecan_meetings.audio` — offline, ffmpeg SIEMPRE mockeado
(`shutil.which`/`asyncio.create_subprocess_exec`), nunca se ejecuta un
binario real."""

from __future__ import annotations

import asyncio
import io
import wave
from pathlib import Path
from typing import Any

import pytest
from edecan_meetings import audio


def _wav_bytes(*, seconds: float = 1.0, rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    n_frames = int(rate * seconds)
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(b"\x00\x00" * n_frames)
    return buffer.getvalue()


class _FakeProcess:
    def __init__(self, *, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> None:
        return None


# ---------------------------------------------------------------------------
# ffmpeg_disponible
# ---------------------------------------------------------------------------


def test_ffmpeg_disponible_respeta_ffmpeg_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FFMPEG_PATH", "/opt/mi-ffmpeg")
    assert audio.ffmpeg_disponible() == "/opt/mi-ffmpeg"


def test_ffmpeg_disponible_none_si_no_esta_instalado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    monkeypatch.setattr(audio.shutil, "which", lambda _name: None)
    assert audio.ffmpeg_disponible() is None


# ---------------------------------------------------------------------------
# extraer_audio_wav — guardas
# ---------------------------------------------------------------------------


async def test_extraer_audio_wav_rechaza_vacio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio, "ffmpeg_disponible", lambda: "/usr/bin/ffmpeg")
    with pytest.raises(audio.AudioExtractionError, match="vacío"):
        await audio.extraer_audio_wav(b"", mime="audio/mpeg")


async def test_extraer_audio_wav_rechaza_demasiado_grande(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio, "ffmpeg_disponible", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(audio, "_TAMANO_MAXIMO_BYTES", 10)
    with pytest.raises(audio.AudioExtractionError, match="MB"):
        await audio.extraer_audio_wav(b"x" * 100, mime="audio/mpeg")


async def test_extraer_audio_wav_sin_ffmpeg_da_mensaje_instructivo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audio, "ffmpeg_disponible", lambda: None)
    with pytest.raises(audio.AudioExtractionError, match="ffmpeg"):
        await audio.extraer_audio_wav(b"contenido", mime="audio/mpeg")


# ---------------------------------------------------------------------------
# extraer_audio_wav — camino feliz / errores de subproceso
# ---------------------------------------------------------------------------


async def test_extraer_audio_wav_camino_feliz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio, "ffmpeg_disponible", lambda: "/usr/bin/ffmpeg")
    wav_falso = _wav_bytes(seconds=0.5)

    llamadas: list[list[str]] = []

    async def _fake_exec(*args: str, **kwargs: Any) -> _FakeProcess:
        llamadas.append(list(args))
        # El último argumento de la lista de `ffmpeg` es el archivo de salida.
        Path(args[-1]).write_bytes(wav_falso)
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    resultado = await audio.extraer_audio_wav(b"video-de-mentira", mime="video/mp4")

    assert resultado == wav_falso
    assert len(llamadas) == 1
    args = llamadas[0]
    assert args[0] == "/usr/bin/ffmpeg"
    assert "-ac" in args and args[args.index("-ac") + 1] == "1"
    assert "-ar" in args and args[args.index("-ar") + 1] == "16000"
    assert "-vn" in args
    # Nunca shell=True: el módulo llama SIEMPRE `asyncio.create_subprocess_exec`
    # (monkeypatcheado arriba) con una lista de argumentos, jamás
    # `create_subprocess_shell` con una cadena — ver el docstring del módulo.


async def test_extraer_audio_wav_sin_frames_lanza(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio, "ffmpeg_disponible", lambda: "/usr/bin/ffmpeg")

    async def _fake_exec(*args: str, **kwargs: Any) -> _FakeProcess:
        # No escribe ningún archivo de salida.
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    with pytest.raises(audio.AudioExtractionError, match="no generó ningún audio"):
        await audio.extraer_audio_wav(b"contenido", mime="audio/mpeg")


async def test_extraer_audio_wav_ffmpeg_binario_desaparecido(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audio, "ffmpeg_disponible", lambda: "/usr/bin/ffmpeg")

    async def _fake_exec(*args: str, **kwargs: Any) -> _FakeProcess:
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    with pytest.raises(audio.AudioExtractionError, match="ffmpeg"):
        await audio.extraer_audio_wav(b"contenido", mime="audio/mpeg")


async def test_extraer_audio_wav_codigo_de_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio, "ffmpeg_disponible", lambda: "/usr/bin/ffmpeg")

    async def _fake_exec(*args: str, **kwargs: Any) -> _FakeProcess:
        return _FakeProcess(returncode=1, stderr=b"codec no soportado")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    with pytest.raises(audio.AudioExtractionError, match="codec no soportado"):
        await audio.extraer_audio_wav(b"contenido", mime="audio/mpeg")


async def test_extraer_audio_wav_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio, "ffmpeg_disponible", lambda: "/usr/bin/ffmpeg")

    class _NuncaTermina(_FakeProcess):
        async def communicate(self) -> tuple[bytes, bytes]:
            raise TimeoutError

    async def _fake_exec(*args: str, **kwargs: Any) -> _NuncaTermina:
        return _NuncaTermina()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    with pytest.raises(audio.AudioExtractionError, match="no terminó"):
        await audio.extraer_audio_wav(b"contenido", mime="audio/mpeg", timeout_seconds=0.01)


# ---------------------------------------------------------------------------
# duracion_wav_segundos
# ---------------------------------------------------------------------------


def test_duracion_wav_segundos_wav_valido() -> None:
    assert audio.duracion_wav_segundos(_wav_bytes(seconds=2.0)) == pytest.approx(2.0, abs=0.01)


def test_duracion_wav_segundos_bytes_invalidos_devuelve_none() -> None:
    assert audio.duracion_wav_segundos(b"no-es-un-wav") is None
