#Requires -Version 5.1
<#
.SYNOPSIS
    apps/desktop/scripts/download-opencode.ps1 (Windows) -- equivalente de
    download-opencode.sh. Descarga el binario oficial de opencode
    (https://opencode.ai, MIT, ver NOTICE) para un target triple de Windows y
    lo deja en src-tauri\binaries\ con la convencion de sidecar de Tauri
    (opencode-<target-triple>.exe, ver tauri.conf.json -> bundle.externalBin)
    -- el mismo lugar donde build-backend.ps1 instala el sidecar de
    edecan-local.

.DESCRIPTION
    A DIFERENCIA de download-ollama.ps1, este script NO es opcional: opencode
    es el MOTOR del IDE de Edecan (decision del 30-jul-2026, ver
    docs/opencode-motor.md). Sin este binario dentro del paquete, la app
    instalada no puede ejecutar ningun agente en ninguna maquina que no sea la
    de desarrollo: ide_opencode_binario.py no encontraria nada que lanzar y el
    IDE quedaria muerto. Por eso build-backend.ps1 lo llama siempre, sin
    variable de entorno que lo active (mismo criterio que download-opencode.sh
    en macOS/Linux).

    Version fijada a proposito (igual que download-opencode.sh y
    docs/opencode-empaquetado.md S2.2): el adaptador (ide_opencode.py) esta
    escrito contra la superficie /api/* de esta version exacta y comprueba el
    minimo en arranque. Subirla es una decision consciente, no un `latest`
    que cambie el motor del IDE sin que nadie lo note.

    Estructura adaptada de download-ollama.ps1 (a su vez adaptado de
    open-jarvis/OpenJarvis, Apache-2.0 -- ver NOTICE) y de download-opencode.sh
    (mismo script, version bash/macOS/Linux). Diferencias: la tabla de
    version/SHA-256 es la de opencode (docs/opencode-empaquetado.md S2.2), y
    no hace falta preservar DLLs adicionales -- el asset de opencode es un
    unico ejecutable autocontenido en las tres plataformas.

.PARAMETER Target
    Target triple de Windows a preparar. El pipeline de build de hoy
    (build-backend.ps1) solo admite x86_64-pc-windows-msvc -- aarch64 esta
    en la tabla por si algun dia se agrega un instalador Windows ARM64, pero
    todavia no hay build-backend.ps1/build-app.ps1 que lo invoque con ese
    valor.

.EXAMPLE
    .\download-opencode.ps1
    .\download-opencode.ps1 -Target aarch64-pc-windows-msvc
#>
param(
    [ValidateSet("x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc")]
    [string]$Target = "x86_64-pc-windows-msvc"
)

$ErrorActionPreference = "Stop"

# Version fijada a proposito -- ver docstring de arriba y el comentario
# equivalente en download-opencode.sh. Si se sube, hay que actualizar esta
# tabla Y la de docs/opencode-empaquetado.md S2.2 a conciencia.
$Version = "1.17.18"
$BaseUrl = "https://github.com/anomalyco/opencode/releases/download/v$Version"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BinariesDir = Join-Path (Split-Path -Parent $ScriptDir) "src-tauri\binaries"
New-Item -ItemType Directory -Path $BinariesDir -Force | Out-Null

# --- Asset y SHA-256 por target ---------------------------------------------
# Mismos valores que download-opencode.sh / docs/opencode-empaquetado.md S2.2,
# leidos de la API de GitHub para el release v1.17.18. Se verifican ANTES de
# mover nada a binaries\: un binario que se va a firmar y distribuir no se
# acepta por su nombre de archivo.
switch ($Target) {
    "x86_64-pc-windows-msvc" {
        $Asset = "opencode-windows-x64.zip"
        $ExpectedSha256 = "7d489fd9b314e25bccf9c5dd2f17ef2774902c7b7db9aa34f46b0aab4715c70c"
    }
    "aarch64-pc-windows-msvc" {
        $Asset = "opencode-windows-arm64.zip"
        $ExpectedSha256 = "fcfbd7f82242f47ec7e98bc8819eeebe716654e9bce1fb1bd7f364e887cb95ab"
    }
}
$Interno = "opencode.exe"
$Salida = "opencode-$Target.exe"
$Destino = Join-Path $BinariesDir $Salida

# --- Salida temprana si ya esta y es el correcto ----------------------------
# Se compara el SHA esperado para esta version/target contra una marca dejada
# por una descarga previa que termino bien. Sin esta marca, build-backend.ps1
# volveria a bajar ~40 MB en cada recompilacion.
$Marca = Join-Path $BinariesDir ".opencode-$Target.sha256"
if ((Test-Path $Destino) -and (Test-Path $Marca)) {
    $MarcaActual = (Get-Content -Path $Marca -Raw).Trim()
    if ($MarcaActual -eq $ExpectedSha256) {
        Write-Host "==> opencode $Version para $Target ya esta en binaries\ (SHA verificado)."
        exit 0
    }
    Write-Host "==> opencode en binaries\ es de otra version; se reemplaza."
}

$TmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("edecan-opencode-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null
try {
    $ArchiveFile = Join-Path $TmpDir $Asset
    $DownloadUrl = "$BaseUrl/$Asset"

    Write-Host "==> Descargando opencode $Version ($Asset) para $Target..."
    Invoke-WebRequest -Uri $DownloadUrl -OutFile $ArchiveFile -UseBasicParsing

    Write-Host "==> Verificando SHA-256..."
    $ActualSha256 = (Get-FileHash -Path $ArchiveFile -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ActualSha256 -ne $ExpectedSha256) {
        throw "SHA-256 no coincide para $Asset.`n  esperado: $ExpectedSha256`n  obtenido: $ActualSha256`n  No se instala nada. Si opencode publico un release nuevo con el mismo numero, actualiza la tabla de este script a conciencia -- no borres la comprobacion."
    }

    Write-Host "==> Extrayendo..."
    $ExtractDir = Join-Path $TmpDir "extraido"
    Expand-Archive -Path $ArchiveFile -DestinationPath $ExtractDir -Force

    $Origen = Join-Path $ExtractDir $Interno
    if (-not (Test-Path $Origen)) {
        # El asset se verifico por SHA, asi que si el ejecutable no esta donde
        # se esperaba es que el formato del release cambio: se dice, no se
        # adivina (mismo criterio que download-opencode.sh).
        Write-Error "el archivo descargado no trae '$Interno' en la raiz. Contenido de ${ExtractDir}:"
        Get-ChildItem -Path $ExtractDir -Recurse -File | Select-Object -First 20 | ForEach-Object { Write-Host $_.FullName }
        exit 1
    }

    Copy-Item $Origen $Destino -Force
    Set-Content -Path $Marca -Value $ExpectedSha256 -NoNewline
} finally {
    Remove-Item $TmpDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "==> Listo: $Destino"
Get-Item $Destino | Select-Object FullName, Length | Format-List

# Comprobacion best-effort (igual que download-opencode.sh): no es un chequeo
# de integridad -- el SHA-256 de arriba ya lo cubrio -- solo confirma que el
# .exe arranca cuando el target coincide con el de esta maquina.
try {
    & $Destino --version 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "==> Comprobado: responde a --version."
    } else {
        Write-Host "aviso: el binario no respondio a --version (normal si el target no es el de esta maquina)."
    }
} catch {
    Write-Host "aviso: el binario no respondio a --version (normal si el target no es el de esta maquina)."
}
