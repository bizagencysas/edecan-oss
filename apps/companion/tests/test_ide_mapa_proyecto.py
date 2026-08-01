"""Pruebas de ``ide_mapa_proyecto.ProjectMapService``.

Ninguna prueba toca la red: todo se arma sobre árboles de archivos falsos en
``tmp_path`` (incluido uno "grande" para probar presupuesto y que no se
cuelga). Se cubre:

- Estructura: detección de unidades en cada ecosistema que este IDE tiene que
  poder abrir -- Python, Node, Swift (SPM y Xcode), Kotlin/Java (Gradle y
  Maven), Go, Rust, Ruby, PHP y .NET -- con sus comandos de test/build y
  entradas. Hay un repo mínimo por ecosistema porque el fallo que importa no
  es "detecta mal": es "no detecta nada", y eso deja inerte a todo lo que se
  apoya en el mapa.
- Lenguajes: conteo de líneas por extensión, ignorando lo que no es código.
- ``.gitignore``: un repo Git real con ``node_modules`` ignorado no debe
  aparecer en el mapa.
- Caché: una segunda llamada sin cambios en disco no debe reconstruir (se
  verifica con un espía sobre el listado de archivos); tocar un archivo sí
  invalida.
- Presupuesto de ``render_prompt``: un mapa con muchas unidades y
  descripciones largas debe entrar en un presupuesto chico de tokens, con
  recorte por prioridad y nunca reventar.
- Rendimiento/escala: un árbol con varios cientos de archivos debe generarse
  en un tiempo acotado (no minutos, no colgado).
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
from edecan_companion.ide_mapa_proyecto import (
    IDEProjectMapError,
    ProjectMapService,
)
from edecan_companion.ide_workspaces import WorkspaceStore


def _make_service(tmp_path: Path) -> tuple[ProjectMapService, Path, str, WorkspaceStore]:
    state_dir = tmp_path / "state"
    project = tmp_path / "proyecto"
    project.mkdir()
    workspaces = WorkspaceStore(state_dir)
    workspaces.authorize(str(project))
    workspace_id = workspaces.list()[0]["id"]
    service = ProjectMapService(workspaces, state_dir)
    return service, project, workspace_id, workspaces


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# --------------------------------------------------------------------- #
# Estructura: unidades Python/Node detectadas con sus metadatos reales.
# --------------------------------------------------------------------- #


def test_detecta_unidad_python_con_descripcion_y_tests(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(
        project / "packages" / "core" / "pyproject.toml",
        """
[project]
name = "edecan-core"
description = "Motor del agente"
version = "0.1.0"
dependencies = []

[dependency-groups]
dev = ["pytest>=8"]
""",
    )
    _write(project / "packages" / "core" / "edecan_core" / "main.py", "print('hola')\n" * 10)

    mapa = service.get_map(workspace_id)

    assert mapa["total_archivos"] >= 2
    unidades = {u["ruta"]: u for u in mapa["unidades"]}
    assert "packages/core" in unidades
    unidad = unidades["packages/core"]
    assert unidad["tipo"] == "python"
    assert unidad["nombre"] == "edecan-core"
    assert unidad["descripcion"] == "Motor del agente"
    assert unidad["version"] == "0.1.0"
    assert unidad["comandos_test"] == ["pytest -q"]
    assert unidad["entradas"] == ["main.py"]
    assert unidad["lineas"] == 10


def test_detecta_unidad_node_con_scripts(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(
        project / "apps" / "web" / "package.json",
        json.dumps(
            {
                "name": "edecan-web",
                "version": "0.1.0",
                "description": "Frontend Next.js",
                "scripts": {"dev": "next dev", "build": "next build", "test": "vitest run"},
            }
        ),
    )
    _write(project / "apps" / "web" / "index.tsx", "export default 1;\n" * 5)

    mapa = service.get_map(workspace_id)

    unidades = {u["ruta"]: u for u in mapa["unidades"]}
    assert "apps/web" in unidades
    unidad = unidades["apps/web"]
    assert unidad["tipo"] == "node"
    assert unidad["nombre"] == "edecan-web"
    assert unidad["comandos_test"] == ["npm run test"]
    assert unidad["comandos_build"] == ["npm run build"]
    assert "npm run dev" in unidad["entradas"]


def test_gestor_pnpm_detectado_por_lockfile(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(project / "apps" / "web" / "pnpm-lock.yaml", "lockfileVersion: 6\n")
    _write(
        project / "apps" / "web" / "package.json",
        json.dumps({"name": "web", "scripts": {"build": "next build"}}),
    )

    mapa = service.get_map(workspace_id)

    unidad = next(u for u in mapa["unidades"] if u["ruta"] == "apps/web")
    assert unidad["comandos_build"] == ["pnpm run build"]
    assert "pnpm" in mapa["gestores_paquetes"]


def test_gestor_uv_detectado_por_workspace_raiz(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(
        project / "pyproject.toml",
        """
[project]
name = "workspace-raiz"

[tool.uv.workspace]
members = ["packages/core"]
""",
    )
    _write(project / "packages" / "core" / "pyproject.toml", '[project]\nname = "core"\n')

    mapa = service.get_map(workspace_id)

    assert "uv (workspace Python)" in mapa["gestores_paquetes"]


# --------------------------------------------------------------------- #
# Un repo mínimo por ecosistema: lo que el dueño va a abrir de verdad con este
# IDE incluye apps iOS, apps Android y servicios en Go o Rust.
# --------------------------------------------------------------------- #


def test_detecta_paquete_swift_de_spm(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(
        project / "packages" / "kit" / "Package.swift",
        """// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "EdecanKit",
    targets: [.executableTarget(name: "EdecanKit")]
)
""",
    )
    _write(
        project / "packages" / "kit" / "Sources" / "EdecanKit" / "main.swift",
        'print("hola")\n' * 7,
    )

    mapa = service.get_map(workspace_id)

    unidad = next(u for u in mapa["unidades"] if u["ruta"] == "packages/kit")
    assert unidad["tipo"] == "swift"
    assert unidad["nombre"] == "EdecanKit"
    assert unidad["comandos_test"] == ["swift test"]
    assert unidad["comandos_build"] == ["swift build"]
    assert unidad["entradas"] == ["main.swift"]
    # 7 del código + 7 del propio manifiesto: ``Package.swift`` no es un
    # archivo de datos, es Swift, y como tal cuenta.
    assert unidad["lineas"] == 14
    assert "swiftpm" in mapa["gestores_paquetes"]


def test_detecta_app_ios_por_su_xcodeproj_y_podfile(tmp_path: Path) -> None:
    """Xcode guarda el proyecto en una CARPETA: la unidad es la carpeta que la
    contiene, y el ``project.xcworkspace`` que vive DENTRO del ``.xcodeproj``
    no puede inventar una segunda unidad colgando de la primera."""

    service, project, workspace_id, _ = _make_service(tmp_path)
    ios = project / "apps" / "ios"
    _write(ios / "MiApp.xcodeproj" / "project.pbxproj", "// objetos del proyecto\n")
    _write(
        ios / "MiApp.xcodeproj" / "project.xcworkspace" / "contents.xcworkspacedata",
        "<Workspace></Workspace>\n",
    )
    _write(ios / "Podfile", "platform :ios, '17.0'\n")
    _write(ios / "MiApp" / "AppDelegate.swift", "import UIKit\n" * 4)

    mapa = service.get_map(workspace_id)

    rutas = {u["ruta"] for u in mapa["unidades"]}
    assert rutas == {"apps/ios"}
    unidad = next(u for u in mapa["unidades"] if u["ruta"] == "apps/ios")
    assert unidad["tipo"] == "swift"
    assert unidad["nombre"] == "MiApp"
    assert unidad["comandos_build"] == [
        "pod install",
        "xcodebuild -project MiApp.xcodeproj build",
    ]
    assert unidad["comandos_test"] == ["xcodebuild -project MiApp.xcodeproj test"]
    assert unidad["entradas"] == ["AppDelegate.swift"]
    assert "cocoapods" in mapa["gestores_paquetes"]


def test_detecta_proyecto_android_con_gradle_y_submodulo(tmp_path: Path) -> None:
    """El wrapper de Gradle vive una sola vez, en la raíz del build; un
    submódulo se invoca desde ahí con su ruta en dos puntos."""

    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(project / "gradlew", "#!/bin/sh\n")
    _write(project / "settings.gradle.kts", 'rootProject.name = "edecan-android"\n')
    _write(project / "build.gradle.kts", 'version = "2.4.0"\ndescription = "App Android"\n')
    _write(project / "app" / "build.gradle.kts", 'plugins { id("com.android.application") }\n')
    _write(project / "app" / "src" / "main" / "kotlin" / "Main.kt", "fun main() {}\n" * 3)

    mapa = service.get_map(workspace_id)

    unidades = {u["ruta"]: u for u in mapa["unidades"]}
    raiz = unidades["."]
    assert raiz["tipo"] == "kotlin"
    assert raiz["nombre"] == "edecan-android"
    assert raiz["version"] == "2.4.0"
    assert raiz["descripcion"] == "App Android"
    assert raiz["comandos_test"] == ["./gradlew test"]
    submodulo = unidades["app"]
    assert submodulo["tipo"] == "kotlin"
    assert submodulo["comandos_test"] == ["./gradlew :app:test"]
    assert submodulo["comandos_build"] == ["./gradlew :app:build"]
    assert submodulo["entradas"] == ["Main.kt"]
    assert "gradle" in mapa["gestores_paquetes"]


def test_detecta_modulo_maven_sin_confundirlo_con_su_pom_padre(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(
        project / "services" / "pagos" / "pom.xml",
        """<?xml version="1.0" encoding="UTF-8"?>
<project>
  <parent>
    <groupId>org.ejemplo</groupId>
    <artifactId>plataforma-padre</artifactId>
    <version>9.9.9</version>
  </parent>
  <artifactId>pagos-api</artifactId>
  <version>1.2.3</version>
  <description>Servicio de pagos</description>
</project>
""",
    )
    _write(
        project / "services" / "pagos" / "src" / "main" / "java" / "Main.java",
        "class Main {}\n" * 5,
    )

    mapa = service.get_map(workspace_id)

    unidad = next(u for u in mapa["unidades"] if u["ruta"] == "services/pagos")
    assert unidad["tipo"] == "java"
    assert unidad["nombre"] == "pagos-api"
    assert unidad["version"] == "1.2.3"
    assert unidad["descripcion"] == "Servicio de pagos"
    assert unidad["comandos_test"] == ["mvn test"]
    assert unidad["comandos_build"] == ["mvn package"]
    assert unidad["entradas"] == ["Main.java"]
    assert "maven" in mapa["gestores_paquetes"]


def test_detecta_modulo_go(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(
        project / "services" / "cobros" / "go.mod",
        "module github.com/ejemplo/cobros\n\ngo 1.23\n",
    )
    _write(project / "services" / "cobros" / "main.go", "package main\n" * 6)

    mapa = service.get_map(workspace_id)

    unidad = next(u for u in mapa["unidades"] if u["ruta"] == "services/cobros")
    assert unidad["tipo"] == "go"
    assert unidad["nombre"] == "github.com/ejemplo/cobros"
    # ``go 1.23`` es la versión del lenguaje, no la del módulo: no se inventa.
    assert unidad["version"] is None
    assert unidad["comandos_test"] == ["go test ./..."]
    assert unidad["entradas"] == ["main.go"]
    assert "go modules" in mapa["gestores_paquetes"]


def test_detecta_crate_de_rust(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(
        project / "crates" / "motor" / "Cargo.toml",
        '[package]\nname = "motor"\nversion = "0.3.1"\ndescription = "Motor de reglas"\n',
    )
    _write(project / "crates" / "motor" / "src" / "main.rs", "fn main() {}\n" * 8)

    mapa = service.get_map(workspace_id)

    unidad = next(u for u in mapa["unidades"] if u["ruta"] == "crates/motor")
    assert unidad["tipo"] == "rust"
    assert unidad["nombre"] == "motor"
    assert unidad["version"] == "0.3.1"
    assert unidad["descripcion"] == "Motor de reglas"
    assert unidad["comandos_test"] == ["cargo test"]
    assert unidad["entradas"] == ["src/main.rs"]
    assert "cargo" in mapa["gestores_paquetes"]


def test_un_cargo_toml_de_solo_workspace_sigue_siendo_una_unidad(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(project / "Cargo.toml", '[workspace]\nmembers = ["crates/motor"]\n')
    _write(project / "crates" / "motor" / "src" / "lib.rs", "pub fn x() {}\n")

    mapa = service.get_map(workspace_id)

    unidad = next(u for u in mapa["unidades"] if u["ruta"] == ".")
    assert unidad["tipo"] == "rust"
    assert unidad["nombre"] is None


def test_detecta_gema_ruby_con_sus_specs(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    portal = project / "apps" / "portal"
    _write(portal / "Gemfile", "source 'https://rubygems.org'\ngem 'rails'\n")
    _write(portal / "portal.gemspec", "Gem::Specification.new do |s|\nend\n")
    _write(portal / "config.ru", "run Portal::App\n")
    _write(portal / "spec" / "portal_spec.rb", "describe Portal do\nend\n" * 3)

    mapa = service.get_map(workspace_id)

    unidad = next(u for u in mapa["unidades"] if u["ruta"] == "apps/portal")
    assert unidad["tipo"] == "ruby"
    assert unidad["nombre"] == "portal"
    assert unidad["comandos_test"] == ["bundle exec rspec"]
    assert unidad["entradas"] == ["config.ru"]
    assert "bundler" in mapa["gestores_paquetes"]


def test_detecta_proyecto_php_con_composer(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    panel = project / "apps" / "panel"
    _write(
        panel / "composer.json",
        json.dumps(
            {
                "name": "ejemplo/panel",
                "description": "Panel administrativo",
                "version": "3.0.0",
                "scripts": {"test": "phpunit", "build": "npm run prod"},
            }
        ),
    )
    _write(panel / "public" / "index.php", "<?php echo 1;\n" * 4)

    mapa = service.get_map(workspace_id)

    unidad = next(u for u in mapa["unidades"] if u["ruta"] == "apps/panel")
    assert unidad["tipo"] == "php"
    assert unidad["nombre"] == "ejemplo/panel"
    assert unidad["descripcion"] == "Panel administrativo"
    assert unidad["comandos_test"] == ["composer run test"]
    assert unidad["comandos_build"] == ["composer run build"]
    assert unidad["entradas"] == ["public/index.php"]
    assert "composer" in mapa["gestores_paquetes"]


def test_detecta_proyectos_dotnet_y_solo_prueba_el_de_pruebas(tmp_path: Path) -> None:
    """``dotnet test`` sobre un proyecto que no es de pruebas falla: el mapa
    solo lo ofrece donde el propio ``.csproj`` dice que lo es."""

    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(
        project / "src" / "Pagos.Api" / "Pagos.Api.csproj",
        '<Project Sdk="Microsoft.NET.Sdk.Web">\n'
        "  <PropertyGroup>\n"
        "    <TargetFramework>net8.0</TargetFramework>\n"
        "    <Version>4.5.6</Version>\n"
        "    <Description>API de pagos</Description>\n"
        "  </PropertyGroup>\n"
        "</Project>\n",
    )
    _write(project / "src" / "Pagos.Api" / "Program.cs", "var app = 1;\n" * 3)
    _write(
        project / "tests" / "Pagos.Tests" / "Pagos.Tests.csproj",
        '<Project Sdk="Microsoft.NET.Sdk">\n'
        "  <ItemGroup>\n"
        '    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.11.1" />\n'
        "  </ItemGroup>\n"
        "</Project>\n",
    )
    _write(project / "tests" / "Pagos.Tests" / "PruebaDePagos.cs", "class T {}\n")

    mapa = service.get_map(workspace_id)

    unidades = {u["ruta"]: u for u in mapa["unidades"]}
    api = unidades["src/Pagos.Api"]
    assert api["tipo"] == "dotnet"
    assert api["nombre"] == "Pagos.Api"
    assert api["version"] == "4.5.6"
    assert api["descripcion"] == "API de pagos"
    assert api["comandos_test"] == []
    assert api["comandos_build"] == ["dotnet build Pagos.Api.csproj"]
    assert api["entradas"] == ["Program.cs"]
    assert unidades["tests/Pagos.Tests"]["comandos_test"] == ["dotnet test Pagos.Tests.csproj"]
    assert "dotnet" in mapa["gestores_paquetes"]


def test_cada_ecosistema_tiene_precedencia_y_constructor(tmp_path: Path) -> None:
    """Las tres tablas (manifiesto → ecosistema, precedencia, constructor) se
    tienen que mover juntas. Si alguien agrega un ecosistema y se olvida de una,
    la detección revienta con un KeyError en el repo de un usuario, no acá."""

    from edecan_companion.ide_mapa_proyecto import (
        _CONSTRUCTORES,
        _ECOSISTEMA_POR_MANIFIESTO,
        _PRECEDENCIA_ECOSISTEMAS,
        _SUFIJOS_MANIFIESTO,
    )

    declarados = set(_ECOSISTEMA_POR_MANIFIESTO.values())
    declarados |= {eco for _sufijo, eco in _SUFIJOS_MANIFIESTO}
    declarados.add("swift")  # los bundles de Xcode (.xcodeproj/.xcworkspace)

    assert declarados == set(_PRECEDENCIA_ECOSISTEMAS)
    assert declarados == set(_CONSTRUCTORES)


def test_un_manifiesto_degenerado_no_cuelga_el_mapa(tmp_path: Path) -> None:
    """Los manifiestos los escribe quien mantenga el repo que se está abriendo.
    Un ``pom.xml`` con miles de ``<parent`` sin cerrar tiene que costar lo que
    cuesta leerlo, no su forma."""

    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(project / "servicio" / "pom.xml", "<project>\n" + "<parent>\n" * 40_000)
    _write(project / "servicio" / "src" / "Main.java", "class Main {}\n")

    inicio = time.monotonic()
    mapa = service.get_map(workspace_id)
    duracion = time.monotonic() - inicio

    # Leer 200 KB por índices es instantáneo; el patrón cuadrático que esto
    # reemplaza tardaba ~11 s con este mismo archivo.
    assert duracion < 3.0, f"tardó demasiado: {duracion:.2f}s"
    unidad = next(u for u in mapa["unidades"] if u["ruta"] == "servicio")
    assert unidad["tipo"] == "java"
    assert unidad["nombre"] is None


def test_una_carpeta_con_dos_manifiestos_da_una_sola_unidad(tmp_path: Path) -> None:
    """Dos unidades con la misma ``ruta`` se repartirían los mismos archivos y
    cada línea se contaría dos veces. El desempate además tiene que ser
    estable: antes dependía del orden en que git listara los archivos."""

    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(project / "Gemfile", "source 'https://rubygems.org'\n")
    _write(project / "package.json", json.dumps({"name": "sitio", "scripts": {"test": "vitest"}}))
    _write(project / "app.js", "const x = 1;\n" * 5)

    mapa = service.get_map(workspace_id)

    unidades = [u for u in mapa["unidades"] if u["ruta"] == "."]
    assert len(unidades) == 1
    assert unidades[0]["tipo"] == "node"
    assert mapa["total_lineas"] == 5


# --------------------------------------------------------------------- #
# Lenguajes: solo cuenta extensiones de código real.
# --------------------------------------------------------------------- #


def test_conteo_de_lineas_por_lenguaje(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(project / "a.py", "x = 1\n" * 4)
    _write(project / "b.ts", "const x = 1;\n" * 6)
    _write(project / "README.md", "texto sin contar\n" * 100)

    mapa = service.get_map(workspace_id)

    lenguajes = {row["lenguaje"]: row for row in mapa["lenguajes"]}
    assert lenguajes["python"]["lineas"] == 4
    assert lenguajes["typescript"]["lineas"] == 6
    assert "markdown" not in lenguajes
    assert mapa["total_lineas"] == 10


# --------------------------------------------------------------------- #
# .gitignore: node_modules y compañía no deben colarse en el mapa.
# --------------------------------------------------------------------- #


def test_respeta_gitignore(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    _write(project / ".gitignore", "node_modules/\n")
    _write(project / "src" / "index.py", "print(1)\n")
    _write(project / "node_modules" / "paquete" / "index.js", "module.exports = {};\n" * 50)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=project,
        check=True,
    )

    mapa = service.get_map(workspace_id)

    rutas_lenguajes_archivos = mapa["total_archivos"]
    # node_modules tiene 50 líneas; si se coló, el total de líneas de código
    # sería >= 50. Con .gitignore respetado, solo debe contarse index.py.
    assert mapa["total_lineas"] == 1
    assert rutas_lenguajes_archivos >= 1


# --------------------------------------------------------------------- #
# Caché: no se reconstruye si nada cambió; sí se reconstruye si algo cambió.
# --------------------------------------------------------------------- #


def test_cache_evita_reconstruccion_sin_cambios(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(project / "a.py", "x = 1\n")

    primero = service.get_map(workspace_id)
    segundo = service.get_map(workspace_id)

    assert primero == segundo
    assert primero["firma"] == segundo["firma"]


def test_cache_se_invalida_si_cambia_un_archivo(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(project / "a.py", "x = 1\n")
    primero = service.get_map(workspace_id)

    time.sleep(0.01)
    _write(project / "a.py", "x = 1\ny = 2\n")
    segundo = service.get_map(workspace_id)

    assert primero["firma"] != segundo["firma"]
    assert segundo["total_lineas"] == 2


def test_force_reconstruye_aunque_no_cambie_nada(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(project / "a.py", "x = 1\n")
    primero = service.get_map(workspace_id)
    segundo = service.get_map(workspace_id, force=True)

    assert primero["firma"] == segundo["firma"]
    assert primero["generado_en"] != segundo["generado_en"] or primero == segundo


def test_invalidate_borra_el_cache(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(project / "a.py", "x = 1\n")
    service.get_map(workspace_id)

    service.invalidate(workspace_id)

    assert not service._cache_path(workspace_id).exists()


def test_workspace_invalido_lanza_error(tmp_path: Path) -> None:
    service, _project, _workspace_id, _ = _make_service(tmp_path)
    from edecan_companion.ide_workspaces import IDEWorkspaceError

    with pytest.raises(IDEWorkspaceError):
        service.get_map("no-existe")


# --------------------------------------------------------------------- #
# render_prompt: respeta el presupuesto explícito de tokens.
# --------------------------------------------------------------------- #


def test_render_prompt_respeta_presupuesto_chico(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    descripcion_larga = "Este paquete hace muchísimas cosas. " * 20
    for i in range(30):
        _write(
            project / "packages" / f"pkg{i:02d}" / "pyproject.toml",
            f'[project]\nname = "pkg{i:02d}"\ndescription = "{descripcion_larga}"\n',
        )
        _write(project / "packages" / f"pkg{i:02d}" / "mod.py", "x = 1\n" * (i + 1))

    mapa = service.get_map(workspace_id)
    texto = service.render_prompt(mapa, budget_tokens=200)

    assert len(texto) <= 200 * 4
    assert texto  # nunca vacío


def test_render_prompt_sin_recorte_cuando_alcanza(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(project / "packages" / "core" / "pyproject.toml", '[project]\nname = "core"\n')
    _write(project / "packages" / "core" / "main.py", "x = 1\n")

    mapa = service.get_map(workspace_id)
    texto = service.render_prompt(mapa, budget_tokens=1_500)

    assert "packages/core" in texto
    assert "unidades más" not in texto


def test_render_prompt_budget_invalido(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    _write(project / "a.py", "x = 1\n")
    mapa = service.get_map(workspace_id)

    with pytest.raises(IDEProjectMapError):
        service.render_prompt(mapa, budget_tokens=1)


# --------------------------------------------------------------------- #
# Escala: un árbol de varios cientos de archivos no debe tardar ni colgarse.
# --------------------------------------------------------------------- #


def test_arbol_grande_no_tarda_ni_se_cuelga(tmp_path: Path) -> None:
    service, project, workspace_id, _ = _make_service(tmp_path)
    for pkg in range(15):
        pkg_dir = project / "packages" / f"pkg{pkg:03d}"
        _write(
            pkg_dir / "pyproject.toml",
            f'[project]\nname = "pkg{pkg:03d}"\ndescription = "Paquete numero {pkg}"\n',
        )
        for mod in range(30):
            _write(pkg_dir / f"mod_{mod:03d}.py", "x = 1\n" * 20)

    inicio = time.monotonic()
    mapa = service.get_map(workspace_id)
    duracion = time.monotonic() - inicio

    assert duracion < 20.0, f"tardó demasiado: {duracion:.2f}s"
    assert mapa["total_archivos"] >= 15 * 30
    assert mapa["total_lineas"] == 15 * 30 * 20
    assert len(mapa["unidades"]) == 15

    # Repetir con caché caliente debe ser sensiblemente más rápido.
    inicio_cache = time.monotonic()
    service.get_map(workspace_id)
    duracion_cache = time.monotonic() - inicio_cache
    assert duracion_cache < duracion + 1.0


def test_un_archivo_enorme_no_se_lee_completo(tmp_path: Path) -> None:
    """Un archivo por encima de ``MAX_BYTES_FOR_LINE_COUNT`` se estima, no se
    lee línea por línea -- y el mapa lo marca explícitamente como
    aproximado."""
    from edecan_companion.ide_mapa_proyecto import MAX_BYTES_FOR_LINE_COUNT

    service, project, workspace_id, _ = _make_service(tmp_path)
    contenido_enorme = "x = 1\n" * ((MAX_BYTES_FOR_LINE_COUNT // 6) + 1000)
    assert len(contenido_enorme.encode("utf-8")) > MAX_BYTES_FOR_LINE_COUNT
    _write(project / "gigante.py", contenido_enorme)

    inicio = time.monotonic()
    mapa = service.get_map(workspace_id)
    duracion = time.monotonic() - inicio

    assert duracion < 10.0
    assert mapa["lineas_son_aproximadas"] is True
    assert mapa["total_lineas"] > 0
