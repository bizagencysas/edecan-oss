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

    El flujo canonico es fijar EDECAN_BUNDLE_OLLAMA=1 y ejecutar
    build-app.ps1, que llama este script y agrega tanto ollama.exe como su
    arbol lib\ollama al instalador. Invocarlo aislado solo prepara archivos.

.EXAMPLE
    .\download-ollama.ps1
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BinariesDir = Join-Path (Split-Path -Parent $ScriptDir) "src-tauri\binaries"
New-Item -ItemType Directory -Path $BinariesDir -Force | Out-Null

# Unico target de Windows que soporta hoy este bundle opcional.
$Target = "x86_64-pc-windows-msvc"
$OutFile = Join-Path $BinariesDir "ollama-$Target.exe"
$OllamaLibDest = Join-Path $BinariesDir "ollama-lib"
$OllamaVersion = "v0.32.1"
$ExpectedSha256 = "d5abdc21b64ee928d3c92880ac22da5e5b0a46b8b07179791dd8c711b35f8397"

# Version y digest fijados desde la metadata oficial del release de GitHub.
# Nunca usamos `latest`: el build aborta si los bytes no son exactamente los
# revisados para esta version.
$AssetUrl = "https://github.com/ollama/ollama/releases/download/$OllamaVersion/ollama-windows-amd64.zip"

$TmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("edecan-ollama-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null
try {
    $ArchiveFile = Join-Path $TmpDir "ollama-windows-amd64.zip"

    Write-Host "==> Descargando: $AssetUrl"
    Invoke-WebRequest -Uri $AssetUrl -OutFile $ArchiveFile -UseBasicParsing

    Write-Host "==> Verificando SHA-256 oficial de Ollama $OllamaVersion..."
    $ActualSha256 = (Get-FileHash -Path $ArchiveFile -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ActualSha256 -ne $ExpectedSha256) {
        throw "digest SHA-256 invalido para $AssetUrl. Esperado: $ExpectedSha256; recibido: $ActualSha256"
    }

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

    # El ZIP standalone no es un unico exe: Ollama busca los helpers y DLLs
    # en .\lib\ollama relativo a ollama.exe. Validamos el layout antes de
    # reemplazar la salida de una build anterior.
    $OllamaLibSource = Join-Path $TmpDir "lib\ollama"
    foreach ($required in @("ggml.dll", "libllama.dll")) {
        if (-not (Test-Path (Join-Path $OllamaLibSource $required))) {
            throw "el asset verificado no contiene lib\ollama\$required."
        }
    }

    Copy-Item $OllamaBin $OutFile -Force
    if (Test-Path $OllamaLibDest) {
        Remove-Item $OllamaLibDest -Recurse -Force
    }
    New-Item -ItemType Directory -Path $OllamaLibDest -Force | Out-Null
    Copy-Item (Join-Path $OllamaLibSource "*") $OllamaLibDest -Recurse -Force
    Write-Host "==> Runtime nativo de Windows: $OllamaLibDest"
} finally {
    Remove-Item $TmpDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "==> Listo: $OutFile"
Get-Item $OutFile | Select-Object FullName, Length | Format-List
