#Requires -Version 5.1
<#
.SYNOPSIS
    apps/desktop/scripts/download-ollama.ps1 (Windows) — equivalente de
    download-ollama.sh. Descarga el binario oficial de Ollama
    (https://ollama.com) para x86_64-pc-windows-msvc y lo deja en
    src-tauri\binaries\ con la convencion de sidecar de Tauri
    (ollama-x86_64-pc-windows-msvc.exe, ver tauri.conf.json ->
    bundle.externalBin) -- mismo lugar donde build-backend.ps1 instala el
    sidecar de edecan-local. Empaquetar Ollama es 100% OPCIONAL (ver
    docs/desktop.md, "Ollama embebido (opcional)").

.DESCRIPTION
    Adaptacion propia (bring-your-own binary, cero llave/servicio
    compartido de la plataforma) del script equivalente de
    open-jarvis/OpenJarvis (Apache-2.0,
    frontend/src-tauri/scripts/download-ollama.sh) -- ver NOTICE para la
    atribucion completa.

    Este script NO se corre para descargar de verdad como parte de
    WP-V4-09 -- queda escrito y documentado; lo corre quien empaqueta un
    release real de Windows.

.EXAMPLE
    .\download-ollama.ps1
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BinariesDir = Join-Path (Split-Path -Parent $ScriptDir) "src-tauri\binaries"
New-Item -ItemType Directory -Path $BinariesDir -Force | Out-Null

# Unico target de Windows que soporta hoy la app de escritorio (ver
# DIRECCION_ACTUAL.md y tauri.conf.json -> bundle.targets: msi/nsis solo se
# generan para x86_64).
$Target = "x86_64-pc-windows-msvc"
$OutFile = Join-Path $BinariesDir "ollama-$Target.exe"

if (Test-Path $OutFile) {
    Write-Host "Ya existe: $OutFile"
    Write-Host "Borralo primero si queres forzar una descarga nueva."
    exit 0
}

# Asset verificado contra la ultima release de github.com/ollama/ollama:
# "ollama-windows-amd64.zip".
$AssetUrl = "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip"

$TmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("edecan-ollama-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null
try {
    $ArchiveFile = Join-Path $TmpDir "ollama-windows-amd64.zip"

    Write-Host "==> Descargando: $AssetUrl"
    Invoke-WebRequest -Uri $AssetUrl -OutFile $ArchiveFile -UseBasicParsing

    Write-Host "==> Extrayendo..."
    Expand-Archive -Path $ArchiveFile -DestinationPath $TmpDir -Force

    # Busca el binario ollama.exe dentro de lo extraido -- la estructura
    # interna del archivo puede variar segun el release, asi que se prueban
    # varias ubicaciones conocidas en vez de asumir una sola (mismo criterio
    # defensivo que download-ollama.sh).
    $Candidates = @(
        (Join-Path $TmpDir "ollama.exe"),
        (Join-Path $TmpDir "bin\ollama.exe")
    )
    $OllamaBin = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-not $OllamaBin) {
        Write-Error "no se encontro 'ollama.exe' dentro del archivo descargado. Contenido de $TmpDir :"
        Get-ChildItem -Path $TmpDir -Recurse -File | Select-Object -First 20 | ForEach-Object { Write-Host $_.FullName }
        exit 1
    }

    Copy-Item $OllamaBin $OutFile -Force
} finally {
    Remove-Item $TmpDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "==> Listo: $OutFile"
Get-Item $OutFile | Select-Object FullName, Length | Format-List
