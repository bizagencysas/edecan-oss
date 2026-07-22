#Requires -Version 5.1
<#
.SYNOPSIS
    Verifica los instaladores Windows x64 de Edecan Desktop.

.DESCRIPTION
    Inspecciona NSIS y MSI, extrae el MSI de forma administrativa, instala
    NSIS en una carpeta efimera y arranca la aplicacion instalada. Espera el
    backend local real, cierra la ventana por su protocolo normal y comprueba
    que no queden procesos del shell, PyInstaller o PostgreSQL.

    Este script debe ejecutarse en Windows x64. No sustituye la firma
    Authenticode ni una prueba manual de WebView2/SmartScreen en un equipo de
    usuario; valida el contenido y el ciclo de vida del artefacto CI.
#>

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if ($env:OS -ne "Windows_NT" -or -not [System.Environment]::Is64BitOperatingSystem) {
    throw "verify-windows-bundles.ps1 debe ejecutarse en Windows x64."
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopDir = Split-Path -Parent $ScriptDir
$BundleDir = if ($args.Count -gt 0) {
    [System.IO.Path]::GetFullPath($args[0])
} else {
    Join-Path $DesktopDir "src-tauri\target\release\bundle"
}

function Get-OneArtifact {
    param(
        [Parameter(Mandatory = $true)][string] $Directory,
        [Parameter(Mandatory = $true)][string] $Filter,
        [Parameter(Mandatory = $true)][string] $Label
    )
    $Items = @(Get-ChildItem -Path $Directory -Filter $Filter -File -ErrorAction SilentlyContinue)
    if ($Items.Count -ne 1) {
        throw "se esperaba exactamente un $Label en $Directory; encontrados: $($Items.Count)."
    }
    return $Items[0].FullName
}

function Assert-InstalledPayload {
    param([Parameter(Mandatory = $true)][string] $Root)

    foreach ($Name in @("edecan-local.exe", "fydesign-node.exe")) {
        if (@(Get-ChildItem $Root -Recurse -File -Filter $Name -ErrorAction SilentlyContinue).Count -ne 1) {
            throw "el paquete no contiene exactamente un $Name."
        }
    }
    $Backend = @(Get-ChildItem $Root -Recurse -File -Filter "edecan-local.exe" -ErrorAction SilentlyContinue)[0]
    $DesktopExe = @(Get-ChildItem $Backend.DirectoryName -File -Filter "*.exe" -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -notin @("edecan-local.exe", "fydesign-node.exe", "ollama.exe") -and
        $_.Name -notmatch "^uninstall"
    })
    if ($DesktopExe.Count -ne 1) {
        throw "el paquete no contiene un unico ejecutable principal junto al sidecar en $($Backend.DirectoryName)."
    }

    $NodeRuntime = @(Get-ChildItem $Root -Recurse -File -Filter "fydesign-node.exe" -ErrorAction SilentlyContinue)[0]
    $NodeVersion = (& $NodeRuntime.FullName --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $NodeVersion -notmatch "^v22\.") {
        throw "el runtime Node de FyDesign no arranca como Node 22 (detectado: $NodeVersion)."
    }

    $StudioMcp = @(Get-ChildItem $Root -Recurse -File -Filter "fydesign-mcp.mjs" -ErrorAction SilentlyContinue)
    if ($StudioMcp.Count -ne 1 -or $StudioMcp[0].FullName -notmatch "studio-engine") {
        throw "el paquete no contiene el MCP de FyDesign Studio."
    }
    foreach ($Name in @("ffmpeg.exe", "ffprobe.exe", "yt-dlp.exe")) {
        $Matches = @(Get-ChildItem $Root -Recurse -File -Filter $Name -ErrorAction SilentlyContinue)
        $StudioMatches = @($Matches | Where-Object { $_.FullName -match "studio-engine" })
        if ($StudioMatches.Count -eq 0) {
            throw "el paquete no contiene la herramienta de Studio $Name."
        }
    }
    if (@(Get-ChildItem $Root -Recurse -Directory -Filter "playwright-browsers" -ErrorAction SilentlyContinue).Count -eq 0) {
        throw "el paquete no contiene el Chromium fijado de FyDesign Studio."
    }
    return $DesktopExe[0].FullName
}

function Get-SmokeProcesses {
    param([Parameter(Mandatory = $true)][string] $Needle)
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and $_.CommandLine.IndexOf($Needle, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
}

$Nsis = Get-OneArtifact (Join-Path $BundleDir "nsis") "*.exe" "instalador NSIS"
$Msi = Get-OneArtifact (Join-Path $BundleDir "msi") "*.msi" "instalador MSI"

$SmokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("edecan-windows-smoke-" + [guid]::NewGuid().ToString("N"))
$MsiExtract = Join-Path $SmokeRoot "msi"
$NsisInstall = Join-Path $SmokeRoot "nsis"
$OldAppData = $env:APPDATA
$OldLocalAppData = $env:LOCALAPPDATA
$AppProcess = $null

try {
    New-Item -ItemType Directory -Path $MsiExtract, $NsisInstall -Force | Out-Null

    Write-Host "==> Extrayendo e inspeccionando MSI..."
    $MsiProcess = Start-Process msiexec.exe -ArgumentList @(
        "/a", "`"$Msi`"", "/qn", "TARGETDIR=`"$MsiExtract`""
    ) -Wait -PassThru
    if ($MsiProcess.ExitCode -ne 0) {
        throw "la extraccion administrativa del MSI fallo (codigo $($MsiProcess.ExitCode))."
    }
    Assert-InstalledPayload $MsiExtract | Out-Null

    Write-Host "==> Instalando NSIS en un perfil efimero..."
    # NSIS exige que /D sea el ultimo argumento. Las comillas embebidas
    # conservan rutas temporales con espacios en perfiles Windows reales.
    $NsisProcess = Start-Process $Nsis -ArgumentList @("/S", "/D=`"$NsisInstall`"") -Wait -PassThru
    if ($NsisProcess.ExitCode -ne 0) {
        throw "la instalacion silenciosa NSIS fallo (codigo $($NsisProcess.ExitCode))."
    }
    $InstalledExe = Assert-InstalledPayload $NsisInstall

    # Aisla datos, cache y preferencias. El default de autoinicio se desactiva
    # solo para este primer arranque automatizado; el producto conserva el
    # default normal cuando la variable no existe.
    $env:APPDATA = Join-Path $SmokeRoot "roaming"
    $env:LOCALAPPDATA = Join-Path $SmokeRoot "local"
    $env:EDECAN_AUTOSTART_DEFAULT = "disabled"
    $env:EDECAN_DESKTOP_DIAGNOSTICS = "1"
    New-Item -ItemType Directory -Path $env:APPDATA, $env:LOCALAPPDATA -Force | Out-Null

    Write-Host "==> Arrancando la aplicacion NSIS y su backend real..."
    $AppProcess = Start-Process $InstalledExe -ArgumentList "--exit-on-close" -PassThru
    $Port = $null
    $Deadline = [DateTime]::UtcNow.AddMinutes(3)
    do {
        if ($AppProcess.HasExited) {
            throw "Edecan termino antes de dejar listo el backend."
        }
        foreach ($Process in (Get-SmokeProcesses $SmokeRoot)) {
            if ($Process.Name -ieq "edecan-local.exe" -and $Process.CommandLine -match "--port\s+(\d+)") {
                $Port = [int]$Matches[1]
                break
            }
        }
        if ($Port) {
            try {
                Invoke-RestMethod -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 2 | Out-Null
                break
            } catch {
                $Port = $null
            }
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $Deadline)
    if (-not $Port) {
        throw "el backend empaquetado no respondio /healthz dentro de 180 segundos."
    }

    $WindowDeadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        $AppProcess.Refresh()
        if ($AppProcess.MainWindowHandle -ne 0) { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $WindowDeadline)
    if ($AppProcess.MainWindowHandle -eq 0) {
        throw "el backend quedo sano, pero Edecan no mostro su ventana principal."
    }

    Write-Host "==> Cerrando la ventana y verificando apagado limpio..."
    if (-not $AppProcess.CloseMainWindow()) {
        throw "Windows no pudo enviar el cierre normal a la ventana de Edecan."
    }
    if (-not $AppProcess.WaitForExit(30000)) {
        throw "Edecan no termino dentro de 30 segundos despues de cerrar su ventana."
    }
    $AppProcess = $null

    $OrphanDeadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $Orphans = Get-SmokeProcesses $SmokeRoot
        if ($Orphans.Count -eq 0) { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $OrphanDeadline)
    if ($Orphans.Count -ne 0) {
        $Descriptions = $Orphans | ForEach-Object { "$($_.Name)[$($_.ProcessId)]" }
        throw "quedaron procesos huerfanos: $($Descriptions -join ', ')."
    }

    Write-Host "==> Windows verificado: MSI + NSIS, FyDesign, health real y cero procesos huerfanos."
} finally {
    if ($AppProcess -and -not $AppProcess.HasExited) {
        Stop-Process -Id $AppProcess.Id -Force -ErrorAction SilentlyContinue
    }
    foreach ($Process in (Get-SmokeProcesses $SmokeRoot)) {
        Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Remove-Item Env:\EDECAN_AUTOSTART_DEFAULT -ErrorAction SilentlyContinue
    Remove-Item Env:\EDECAN_DESKTOP_DIAGNOSTICS -ErrorAction SilentlyContinue
    $env:APPDATA = $OldAppData
    $env:LOCALAPPDATA = $OldLocalAppData
    if (Test-Path $NsisInstall) {
        $Uninstaller = Get-ChildItem $NsisInstall -File -Filter "uninstall*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($Uninstaller) {
            Start-Process $Uninstaller.FullName -ArgumentList "/S" -Wait -ErrorAction SilentlyContinue
        }
    }
    Remove-Item $SmokeRoot -Recurse -Force -ErrorAction SilentlyContinue
}
