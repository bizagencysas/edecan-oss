#Requires -Version 5.1
<#
.SYNOPSIS
    Build de produccion completo de Edecan Desktop para Windows x64.

.DESCRIPTION
    Ejecuta build-backend.ps1 y luego cargo tauri build con la version fijada
    del CLI. Si EDECAN_BUNDLE_OLLAMA=1, descarga/verifica el asset fijado de
    Ollama y lo agrega realmente a bundle.externalBin para esta build.
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopDir = Split-Path -Parent $ScriptDir
$TauriCliVersion = "2.11.4"

if (-not [System.Environment]::Is64BitOperatingSystem) {
    throw "Edecan Desktop para Windows solo publica instaladores x64."
}

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "falta cargo en PATH (instala Rust desde https://rustup.rs)."
}

$ActualTauriVersion = (& cargo tauri --version 2>$null)
if ($LASTEXITCODE -ne 0) {
    throw "falta cargo-tauri. Instala: cargo install tauri-cli --version '$TauriCliVersion' --locked"
}
if ($ActualTauriVersion.Trim() -ne "tauri-cli $TauriCliVersion") {
    throw "version de cargo-tauri no reproducible: $ActualTauriVersion. Instala la fijada con --version '$TauriCliVersion' --locked --force."
}

Write-Host "==> [1/2] Empaquetando backend y web..."
& (Join-Path $ScriptDir "build-backend.ps1")
if ($LASTEXITCODE -ne 0) { throw "build-backend.ps1 fallo (codigo $LASTEXITCODE)." }

# `binaries/opencode` es el motor del IDE (docs/opencode-motor.md), no una
# integracion opcional: va siempre, sin variable que lo active, igual que
# `download-opencode.ps1` corre siempre en build-backend.ps1 y que
# `binaries/opencode` va siempre en TAURI_EXTERNAL_BIN_JSON de build-app.sh
# (macOS/Linux).
#
# OJO -- esta es EXACTAMENTE la trampa que ya mordio en macOS (ver el
# comentario largo en build-app.sh): el `--config` de `cargo tauri build`
# REEMPLAZA `bundle.externalBin` entero en vez de extenderlo. Si esta lista
# se arma sin `binaries/opencode`, el override de mas abajo lo saca del build
# aunque `tauri.conf.json` SI lo liste en su `externalBin` base -- el binario
# esta descargado y verificado, `cargo tauri build` termina "ok", y el
# instalador sale sin el motor del IDE. Cualquier sidecar nuevo que se sume
# tiene que ir AQUI ademas de en tauri.conf.json.
$ExternalBin = @("binaries/edecan-local", "binaries/fydesign-node", "binaries/opencode")
$Resources = @{ "../packaging/studio-engine" = "studio-engine" }
if ($env:EDECAN_BUNDLE_OLLAMA -eq "1") {
    Write-Host "    (EDECAN_BUNDLE_OLLAMA=1: agregando Ollama verificado al instalador)"
    $OllamaLibDir = Join-Path $DesktopDir "src-tauri\binaries\ollama-lib"
    foreach ($required in @("ggml.dll", "libllama.dll")) {
        if (-not (Test-Path (Join-Path $OllamaLibDir $required))) {
            throw "bundle Ollama incompleto: falta $required en $OllamaLibDir."
        }
    }
    $ExternalBin += "binaries/ollama"
    $Resources["binaries/ollama-lib"] = "lib/ollama"
}

# Tauri espera el contenido de la clave privada, no una ruta. El alias de ruta
# evita que el mantenedor tenga que copiar el secreto al historial de
# PowerShell y solo lo carga en el entorno de este proceso.
if (
    [string]::IsNullOrWhiteSpace($env:TAURI_SIGNING_PRIVATE_KEY) -and
    -not [string]::IsNullOrWhiteSpace($env:TAURI_SIGNING_PRIVATE_KEY_PATH)
) {
    if (-not (Test-Path -LiteralPath $env:TAURI_SIGNING_PRIVATE_KEY_PATH -PathType Leaf)) {
        throw "TAURI_SIGNING_PRIVATE_KEY_PATH no apunta a un archivo legible."
    }
    $env:TAURI_SIGNING_PRIVATE_KEY = [System.IO.File]::ReadAllText(
        $env:TAURI_SIGNING_PRIVATE_KEY_PATH
    ).TrimEnd("`r", "`n")
    if ([string]::IsNullOrWhiteSpace($env:TAURI_SIGNING_PRIVATE_KEY)) {
        throw "el archivo de firma del updater esta vacio."
    }
}

# --- Firma Authenticode opcional (equivalente Windows de una leccion de macOS) ---
# En macOS, Tauri firma la app y sus sidecars pero NUNCA toca lo que viaja
# dentro de Contents/Resources/ -- ahi viven los binarios nativos de
# studio-engine (Chromium, ffmpeg, yt-dlp, esbuild) y quedaban sin firmar,
# rompiendo la notarizacion EN SILENCIO (ver la firma manual que
# build-backend.sh agrega para compensarlo, con su propio `find` sobre
# Contents/Resources/).
#
# En Windows el bundler de Tauri se comporta AL REVES para bundle.resources:
# leido el codigo fuente de tauri-bundler 2.11.4 (la version fijada de este
# repo, $TauriCliVersion arriba) -- crates/tauri-bundler/src/bundle/windows/
# {nsis,msi}/mod.rs recorren settings.resource_files() (exactamente
# bundle.resources, es decir studio-engine/ completo) y llaman a
# should_sign()/try_sign() (sign.rs) sobre cada archivo .exe/.dll que
# encuentran ahi, ademas del exe principal, los sidecars y el instalador --
# pero SOLO si bundle.windows trae una firma configurada. El plan de
# docs/edecan-windows.md S5 describe en cambio un paso manual de signtool
# aparte que enumera 5 archivos a mano (edecan-desktop.exe, edecan-local.exe,
# fydesign-node.exe, el NSIS, el MSI): esa ruta SI reproduce el bug de
# macOS, porque deja fuera a ffmpeg.exe, ffprobe.exe, yt-dlp.exe,
# esbuild.exe (dependencia transitiva de tsx, ver
# packages/fydesign-engine/package-lock.json -> @esbuild/win32-x64) y los
# .exe/.dll de Chromium bajo studio-engine/ -- nadie se entera hasta que
# AppLocker/WDAC ("solo binarios firmados") o un antivirus los bloqueen.
#
# Por eso la firma se conecta AQUI, en bundle.windows.signCommand, para que
# sea el propio recorrido de Tauri sobre bundle.resources el que firme todo
# el arbol de studio-engine -- no un post-proceso que hay que mantener
# sincronizado a mano con cada binario nuevo que empaquete Studio.
# EDECAN_WINDOWS_SIGN_COMMAND/EDECAN_WINDOWS_SIGN_ARGS son OPCIONALES: sin
# ellos (el estado de hoy) esta build sigue sin firmar nada, igual que
# antes. Cuando la Fase 3 conecte Azure Trusted Signing, el comando exacto
# (signtool + /dlib + /dmdf, o un wrapper) se pasa por ahi -- Tauri sustituye
# el marcador "%1" por la ruta de cada archivo (sign_command_custom en
# sign.rs), asi que EDECAN_WINDOWS_SIGN_ARGS debe incluirlo.
#
# NOTA (pendiente de verificar, no se puede probar sin Windows): un addon
# nativo `.node` (DLL de Windows con otra extension) NO lo firmaria de
# todos modos should_sign(), que solo reconoce .exe/.dll -- build-studio-
# engine.ps1 ahora falla la build si aparece uno, igual que macOS ya vigila
# `.node`/`.dylib` en su propio `find`.
$BundleWindows = @{}
if (-not [string]::IsNullOrWhiteSpace($env:EDECAN_WINDOWS_SIGN_COMMAND)) {
    if ([string]::IsNullOrWhiteSpace($env:EDECAN_WINDOWS_SIGN_ARGS) -or
        $env:EDECAN_WINDOWS_SIGN_ARGS -notmatch '%1') {
        throw "EDECAN_WINDOWS_SIGN_COMMAND esta definido pero EDECAN_WINDOWS_SIGN_ARGS falta o no incluye el marcador %1 (la ruta de cada archivo a firmar; ver sign_command_custom en tauri-bundler)."
    }
    $BundleWindows["signCommand"] = @{
        cmd = $env:EDECAN_WINDOWS_SIGN_COMMAND
        args = @($env:EDECAN_WINDOWS_SIGN_ARGS -split '\s+' | Where-Object { $_ -ne "" })
    }
    Write-Host "    (EDECAN_WINDOWS_SIGN_COMMAND detectado: Tauri firmara el exe principal, los sidecars, NSIS/MSI Y todo .exe/.dll bajo bundle.resources -- incluye studio-engine)"
} else {
    Write-Host "    (sin EDECAN_WINDOWS_SIGN_COMMAND: build sin firma Authenticode, igual que antes -- ver docs/edecan-windows.md S5, pendiente de Fase 3)"
}

$BundleOverride = @{
    bundle = @{
        externalBin = $ExternalBin
        resources = $Resources
        createUpdaterArtifacts = (-not [string]::IsNullOrWhiteSpace($env:TAURI_SIGNING_PRIVATE_KEY))
        windows = $BundleWindows
    }
} | ConvertTo-Json -Depth 6 -Compress
if ($BundleOverride -match '"createUpdaterArtifacts":true') {
    Write-Host "    (firma de updater detectada: generando artefactos de actualizacion)"
}
# El JSON va por ARCHIVO, no por la linea de comandos.
#
# Pasarlo inline (`--config $BundleOverride`) NO funciona desde PowerShell: al
# invocar un ejecutable nativo, PowerShell se come las comillas del JSON y
# cargo recibe algo como
#
#   {bundle:{windows:{},externalBin:[binaries/edecan-local,...]}}
#
# sin una sola comilla, y falla con "key must be a string at line 1 column 2".
# Medido en la VM de Windows Server 2022 el 01-ago-2026 -- era el ULTIMO
# obstaculo del instalador, con todo lo demas (motor de estudio, web,
# PyInstaller, sidecars) ya construido.
#
# `--config` acepta una ruta a un archivo JSON, que es inmune al quoting. Es la
# misma leccion que `Invoke-NodeScript` en build-studio-engine.ps1: en Windows,
# los datos estructurados no viajan por argv.
$BundleConfigFile = Join-Path ([System.IO.Path]::GetTempPath()) ("edecan-tauri-" + [guid]::NewGuid().ToString("N") + ".json")
[System.IO.File]::WriteAllText($BundleConfigFile, $BundleOverride, (New-Object System.Text.UTF8Encoding($false)))

$TauriArgs = @("tauri", "build", "--config", $BundleConfigFile)
$TauriArgs += @("--", "--locked")

Write-Host "==> [2/2] cargo tauri build..."
Push-Location (Join-Path $DesktopDir "src-tauri")
try {
    & cargo @TauriArgs
    if ($LASTEXITCODE -ne 0) { throw "cargo tauri build fallo (codigo $LASTEXITCODE)." }
} finally {
    Remove-Item $BundleConfigFile -Force -ErrorAction SilentlyContinue
    Pop-Location
}

# ---------------------------------------------------------------------------
# Comprobacion de que lo que se pidio empaquetar de verdad quedo dentro.
#
# Existe por la MISMA trampa que ya mordio en macOS (ver el comentario largo
# junto a $ExternalBin, arriba, y el bloque equivalente al final de
# build-app.sh): el `--config` de `cargo tauri build` REEMPLAZA
# `bundle.externalBin` entero en vez de extenderlo, asi que un sidecar que
# solo estuviera en la lista base de tauri.conf.json pero no en $ExternalBin
# de este script quedaria fuera del paquete SIN QUE `cargo tauri build`
# fallara ni avisara nada.
#
# Donde se verifica: antes de invocar al bundler de NSIS/MSI, `cargo tauri
# build` copia cada sidecar de `externalBin` a
# `src-tauri\target\release\<nombre-sin-target-triple>.exe`, junto al
# ejecutable principal -- ese es justo el archivo que el instalador empaqueta
# despues. Confirmado contra un caso real reportado en el propio issue
# tracker de Tauri (tauri-apps/tauri#15134: "Tauri copies the sidecar to
# src-tauri/target/release/<name>.exe (stripping the target triple)"), no
# asumido de memoria -- esta sesion no tiene una maquina Windows para
# comprobarlo por si misma, asi que quien corra esto en la VM real debe leer
# el resultado con atencion.
$TargetReleaseDir = Join-Path $DesktopDir "src-tauri\target\release"
$Faltantes = @()
foreach ($entry in $ExternalBin) {
    $nombre = ($entry -split "/")[-1]
    $esperado = Join-Path $TargetReleaseDir "$nombre.exe"
    if (-not (Test-Path $esperado)) {
        $Faltantes += "$nombre.exe"
    }
}
if ($Faltantes.Count -gt 0) {
    # Un solo `throw` con el mensaje completo: con $ErrorActionPreference =
    # "Stop" ya activo en todo el script, un `Write-Error` se vuelve
    # terminante y corta en la PRIMERA linea -- las siguientes nunca se
    # imprimirian.
    throw "el paquete se construyo SIN estos sidecars: $($Faltantes -join ', ')." + `
        " Esperados en: $TargetReleaseDir." + `
        " Revisa `$ExternalBin en este mismo script: el --config de 'cargo tauri build' REEMPLAZA bundle.externalBin, no lo extiende."
}
Write-Host "==> Sidecars verificados en ${TargetReleaseDir}: $(($ExternalBin | ForEach-Object { (($_ -split '/')[-1]) + '.exe' }) -join ' ')"

Write-Host "==> Listo. Instaladores en src-tauri\target\release\bundle\."
