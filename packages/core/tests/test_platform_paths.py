"""`edecan_core.platform_paths` — rutas de datos/config/caché/temporales por plataforma.

Prueba de contrato (ver docstring del módulo y el de `edecan_companion.
platform_paths`): estos mismos casos, con los mismos nombres, corren también
contra la implementación autocontenida de `apps/companion` en
`apps/companion/tests/test_platform_paths.py`. Si agregas un caso aquí,
agrégalo también allá -- no hay import cruzado entre los dos porque
`edecan_companion` está deliberadamente aislado de `edecan_core` (ver
`apps/companion/pyproject.toml`).

`platform.system()`/`os.environ` se simulan con `monkeypatch`: nunca se
confía en el sistema operativo real donde corre la suite (macOS aquí) para
probar ramas de Windows/Linux -- exactamente lo que exige
`docs/edecan-windows.md` ("nada se afirma sin haberlo corrido"; aquí lo que
se corre es la RAMA de código, no el sistema operativo real).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from edecan_core import platform_paths as pp


class TestResolverDataDir:
    def test_default_es_edecan_data_bajo_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(pp.DATA_DIR_ENV_VAR, raising=False)
        assert pp.resolver_data_dir() == Path.home() / ".edecan" / "data"

    def test_configurado_gana_sobre_default_y_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(pp.DATA_DIR_ENV_VAR, "/deberia/ignorarse")
        assert pp.resolver_data_dir("/explicito") == Path("/explicito")

    def test_env_var_gana_sobre_default_cuando_no_hay_configurado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(pp.DATA_DIR_ENV_VAR, "/desde/env")
        assert pp.resolver_data_dir() == Path("/desde/env")

    def test_expande_virgulilla(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(pp.DATA_DIR_ENV_VAR, raising=False)
        assert pp.resolver_data_dir("~/x") == Path.home() / "x"


class TestCacheDir:
    def test_windows_usa_localappdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pp.platform, "system", lambda: "Windows")
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\ana\AppData\Local")
        assert pp.cache_dir("edecan") == Path(r"C:\Users\ana\AppData\Local") / "edecan" / "Cache"

    def test_windows_sin_localappdata_cae_a_appdata_local(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pp.platform, "system", lambda: "Windows")
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        assert pp.cache_dir("edecan") == Path.home() / "AppData" / "Local" / "edecan" / "Cache"

    def test_macos_usa_library_caches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pp.platform, "system", lambda: "Darwin")
        assert pp.cache_dir("edecan") == Path.home() / "Library" / "Caches" / "edecan"

    def test_linux_usa_xdg_cache_home_si_esta_fijada(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pp.platform, "system", lambda: "Linux")
        monkeypatch.setenv("XDG_CACHE_HOME", "/xdg/cache")
        assert pp.cache_dir("edecan") == Path("/xdg/cache") / "edecan"

    def test_linux_sin_xdg_cae_a_punto_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pp.platform, "system", lambda: "Linux")
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        assert pp.cache_dir("edecan") == Path.home() / ".cache" / "edecan"


class TestTempDir:
    def test_vive_bajo_gettempdir_no_bajo_tmp_literal(self) -> None:
        destino = pp.temp_dir("edecan-test", crear=False)
        assert destino == Path(tempfile.gettempdir()) / "edecan-test"

    def test_crea_la_carpeta_por_defecto(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(pp.tempfile, "gettempdir", lambda: str(tmp_path))
        destino = pp.temp_dir("edecan-test")
        assert destino.is_dir()

    def test_no_crea_si_crear_es_falso(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(pp.tempfile, "gettempdir", lambda: str(tmp_path))
        destino = pp.temp_dir("edecan-test-no-creado", crear=False)
        assert not destino.exists()


class TestValidarNombreMultiplataforma:
    def test_nombre_normal_no_lanza(self) -> None:
        pp.validar_nombre_multiplataforma("informe.docx")

    def test_nombre_vacio_lanza(self) -> None:
        with pytest.raises(pp.NombreInvalidoError, match="vacío"):
            pp.validar_nombre_multiplataforma("")

    @pytest.mark.parametrize("caracter", list('<>:"/\\|?*') + ["\x00", "\x1f"])
    def test_caracter_prohibido_lanza_y_lo_nombra(self, caracter: str) -> None:
        nombre = f"archivo{caracter}malo.txt"
        with pytest.raises(pp.NombreInvalidoError) as exc_info:
            pp.validar_nombre_multiplataforma(nombre)
        assert repr(caracter) in str(exc_info.value)

    def test_termina_en_espacio_lanza(self) -> None:
        with pytest.raises(pp.NombreInvalidoError, match="espacio o punto"):
            pp.validar_nombre_multiplataforma("nombre ")

    def test_termina_en_punto_lanza(self) -> None:
        with pytest.raises(pp.NombreInvalidoError, match="espacio o punto"):
            pp.validar_nombre_multiplataforma("nombre.")

    @pytest.mark.parametrize(
        "nombre",
        ["NUL", "nul.txt", "CON", "con.log", "PRN", "AUX", "COM1", "com3.txt", "LPT1", "lpt9.ini"],
    )
    def test_nombre_reservado_windows_lanza_con_o_sin_extension(self, nombre: str) -> None:
        with pytest.raises(pp.NombreInvalidoError, match="reservado"):
            pp.validar_nombre_multiplataforma(nombre)

    def test_nombre_parecido_a_reservado_no_lanza(self) -> None:
        # "CONSOLA" no es "CON": el prefijo no basta, debe ser el nombre base exacto.
        pp.validar_nombre_multiplataforma("CONSOLA.txt")


class TestAdvertirSiRutaLarga:
    def test_ruta_corta_no_advierte(self) -> None:
        assert pp.advertir_si_ruta_larga("/tmp/corta") is None

    def test_ruta_larga_advierte_con_longitud_y_limite(self) -> None:
        ruta = "C:\\" + "a" * 300
        mensaje = pp.advertir_si_ruta_larga(ruta)
        assert mensaje is not None
        assert str(len(ruta)) in mensaje
        assert str(pp.MAX_PATH_WINDOWS) in mensaje

    def test_limite_personalizado(self) -> None:
        assert pp.advertir_si_ruta_larga("12345", limite=5) is not None
        assert pp.advertir_si_ruta_larga("1234", limite=5) is None
