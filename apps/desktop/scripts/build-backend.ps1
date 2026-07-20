#Requires -Version 5.1
<#
.SYNOPSIS
    apps/desktop/scripts/build-backend.ps1 (Windows) — equivalente de
    build-backend.sh. Arma el "backend local" que el sidecar de Tauri lanza
    (contrato en ARCHITECTURE.md §12.f, docs/desktop.md): construye la web
    estática de apps/web, congela `edecan_local` (apps/local, WP-V3-05) con
    PyInstaller, y deja el binario donde tauri.conf.json (bundle.externalBin)
    lo espera. Lo llama scripts/build-app.ps1 antes de `cargo tauri build`;
    también se puede correr suelto para iterar solo sobre el backend.

.DESCRIPTION
    Requisitos: Node 22 + npm 10 (apps/web), Python 3.12 + uv (workspace del repo),
    Rust (`rustc` en PATH, solo para leer el target triple de esta máquina).
    Ver docs/desktop.md para el detalle de cada uno.
#>

$ErrorActionPreference = "Stop"

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopDir   = Split-Path -Parent $ScriptDir
$RepoRoot     = Split-Path -Parent (Split-Path -Parent $DesktopDir)
$WebDir       = Join-Path $RepoRoot "apps\web"
$PackagingDir = Join-Path $DesktopDir "packaging"
$WebDestDir   = Join-Path $PackagingDir "web"
$DistDir      = Join-Path $PackagingDir "dist"
$WorkDir      = Join-Path $PackagingDir "build"
$BinariesDir  = Join-Path $DesktopDir "src-tauri\binaries"

foreach ($bin in @("node", "npm", "uv", "rustc")) {
    if (-not (Get-Command $bin -ErrorAction SilentlyContinue)) {
        Write-Error "falta '$bin' en el PATH. Ver requisitos en docs/desktop.md."
        exit 1
    }
}

if ($env:OS -ne "Windows_NT") {
    throw "build-backend.ps1 debe ejecutarse en Windows x64. En macOS usa build-backend.sh."
}

$NodeVersion = (& node --version).Trim()
$NpmVersion = (& npm --version).Trim()
$NodeMajor = [int]($NodeVersion.TrimStart("v").Split(".")[0])
$NpmMajor = [int]($NpmVersion.Split(".")[0])
if ($NodeMajor -ne 22 -or $NpmMajor -ne 10) {
    throw "apps/web requiere Node 22 y npm 10 (detectados: Node $NodeVersion, npm $NpmVersion)."
}

$RustcOutput = & rustc -Vv
$HostLine = $RustcOutput | Select-String -Pattern "^host:\s*(.+)$"
if (-not $HostLine) {
    throw "no se pudo determinar el target triple ('rustc -Vv' no imprimio 'host:')."
}
$TargetTriple = $HostLine.Matches[0].Groups[1].Value.Trim()
if ($TargetTriple -ne "x86_64-pc-windows-msvc") {
    throw "el bundle de Windows solo soporta x86_64-pc-windows-msvc; target detectado: $TargetTriple."
}

# Integracion OPCIONAL: si EDECAN_BUNDLE_OLLAMA=1, descarga Ollama antes de
# armar el sidecar de edecan-local (ver docs/desktop.md, "Ollama embebido
# (opcional)"). Sin esta variable (el default), este script no cambia en
# nada respecto de antes.
if ($env:EDECAN_BUNDLE_OLLAMA -eq "1") {
    Write-Host "==> [0/4] EDECAN_BUNDLE_OLLAMA=1: descargando Ollama (scripts/download-ollama.ps1)..."
    & (Join-Path $ScriptDir "download-ollama.ps1")
    if ($LASTEXITCODE -ne 0) { throw "download-ollama.ps1 fallo (codigo $LASTEXITCODE)." }
}

Write-Host "==> [1/4] Construyendo la web estatica (apps/web, export estatico)..."
Push-Location $WebDir
try {
    if (-not (Test-Path (Join-Path $WebDir "node_modules"))) {
        Write-Host "    (node_modules no existe todavia, instalando dependencias declaradas...)"
        if (Test-Path (Join-Path $WebDir "package-lock.json")) {
            npm ci
        } else {
            npm install
        }
        if ($LASTEXITCODE -ne 0) { throw "npm install/ci fallo (codigo $LASTEXITCODE)." }
    }

    # NEXT_OUTPUT=export activa `output: "export"` en next.config.mjs (HTML/CSS/JS
    # estatico en out/, sin servidor Next corriendo). NEXT_PUBLIC_API_URL vacio
    # (NO omitido -- un vacio explicito) hace que el frontend llame a la API con
    # rutas relativas (same-origin): en la app de escritorio, el backend local
    # sirve la API y esta web estatica desde el mismo origen
    # (http://127.0.0.1:<puerto>/). Ver docs/primeros-pasos.md 4 para el
    # detalle completo (incluye una advertencia ya documentada ahi sobre
    # lib/api.ts).
    $env:NEXT_OUTPUT = "export"
    $env:NEXT_PUBLIC_API_URL = ""
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "'npm run build' fallo (codigo $LASTEXITCODE)." }
    } finally {
        Remove-Item Env:\NEXT_OUTPUT -ErrorAction SilentlyContinue
        Remove-Item Env:\NEXT_PUBLIC_API_URL -ErrorAction SilentlyContinue
    }
} finally {
    Pop-Location
}

$WebOutDir = Join-Path $WebDir "out"
if (-not (Test-Path $WebOutDir)) {
    Write-Error "'npm run build' no genero $WebOutDir (revisa next.config.mjs)."
    exit 1
}

Write-Host "==> [2/4] Copiando apps/web/out/ -> packaging/web/..."
if (Test-Path $WebDestDir) { Remove-Item $WebDestDir -Recurse -Force }
New-Item -ItemType Directory -Path $WebDestDir -Force | Out-Null
Copy-Item (Join-Path $WebOutDir "*") $WebDestDir -Recurse -Force

Write-Host "==> [3/4] Congelando edecan_local con PyInstaller (uv run, workspace completo)..."
# PyInstaller vive fijado en el grupo `release` de la raiz y en `uv.lock`.
# `uv run --frozen --group release --all-packages` desde cualquier carpeta
# del workspace resuelve el entorno COMPARTIDO de todos los
# miembros (ver comentario largo al principio de packaging/edecan_local.spec)
# -- necesario para que collect_all() encuentre los paquetes de
# `edecan.tools` (EDECAN_TOOL_PACKAGES en ese .spec, 16 a la fecha de v7)
# ademas de edecan_api/edecan_worker/edecan_db/edecan_core.
# pgserver es dependencia directa de edecan-local: el mismo lock que usa
# desarrollo alimenta tambien a PyInstaller, sin flags ocultos.
Push-Location $DesktopDir
try {
    uv run --frozen --all-packages --group release pyinstaller packaging/edecan_local.spec `
        --noconfirm `
        --distpath $DistDir `
        --workpath $WorkDir
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller fallo (codigo $LASTEXITCODE)." }
} finally {
    Pop-Location
}

# packaging/edecan_local.spec corre en modo onefile (corregido 2026-07-09,
# ver el docstring del propio .spec): en Windows, PyInstaller deja un solo
# $DistDir/edecan-local.exe -- no una carpeta. El mecanismo de sidecar de
# Tauri (bundle.externalBin) solo sabe copiar un archivo por sidecar, asi
# que esto es justo lo que necesita (antes, en modo onedir, cargo build/
# cargo run copiaban solo el ejecutable y dejaban las DLLs/datas hermanas
# atras -- el sidecar reventaba al arrancar; ver HOTFIXES_PENDIENTES.md).
$FrozenExe = Join-Path $DistDir "edecan-local.exe"
if (-not (Test-Path $FrozenExe)) {
    Write-Error "no se encontro $FrozenExe (fallo pyinstaller arriba?)."
    exit 1
}

Write-Host "==> [4/4] Instalando el sidecar en src-tauri/binaries/..."
$SidecarName = "edecan-local-$TargetTriple.exe"

if (-not (Test-Path $BinariesDir)) { New-Item -ItemType Directory -Path $BinariesDir -Force | Out-Null }

# Limpia sidecars viejos de corridas anteriores antes de copiar -- nunca
# dejar binarios stale mezclados con los nuevos.
Get-ChildItem -Path $BinariesDir -Filter "edecan-local-*" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force

Copy-Item $FrozenExe (Join-Path $BinariesDir $SidecarName) -Force

Write-Host "==> Listo. Sidecar de un solo archivo: $(Join-Path $BinariesDir $SidecarName)"
