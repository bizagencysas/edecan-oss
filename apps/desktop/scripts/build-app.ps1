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

$ExternalBin = @("binaries/edecan-local", "binaries/fydesign-node")
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
$BundleOverride = @{
    bundle = @{
        externalBin = $ExternalBin
        resources = $Resources
        createUpdaterArtifacts = (-not [string]::IsNullOrWhiteSpace($env:TAURI_SIGNING_PRIVATE_KEY) -or -not [string]::IsNullOrWhiteSpace($env:TAURI_SIGNING_PRIVATE_KEY_PATH))
    }
} | ConvertTo-Json -Depth 5 -Compress
if ($BundleOverride -match '"createUpdaterArtifacts":true') {
    Write-Host "    (firma de updater detectada: generando artefactos de actualizacion)"
}
$TauriArgs = @("tauri", "build", "--config", $BundleOverride)
$TauriArgs += @("--", "--locked")

Write-Host "==> [2/2] cargo tauri build..."
Push-Location (Join-Path $DesktopDir "src-tauri")
try {
    & cargo @TauriArgs
    if ($LASTEXITCODE -ne 0) { throw "cargo tauri build fallo (codigo $LASTEXITCODE)." }
} finally {
    Pop-Location
}

Write-Host "==> Listo. Instaladores en src-tauri\target\release\bundle\."
