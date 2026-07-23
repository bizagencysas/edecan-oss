#Requires -Version 5.1
<#
.SYNOPSIS
    Empaqueta packages/fydesign-engine, Chromium y Node 22 para Tauri/Windows.

.DESCRIPTION
    Usa npm ci contra package-lock.json dentro de un directorio temporal. El
    node-runtime y Chromium resultantes no dependen de Node/Chrome del usuario.
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopDir = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent (Split-Path -Parent $DesktopDir)
$EngineSource = Join-Path $RepoRoot "packages\fydesign-engine"
$EngineResource = Join-Path $DesktopDir "packaging\studio-engine"
$BinariesDir = Join-Path $DesktopDir "src-tauri\binaries"
$NodeVersion = "22.17.0"
$NodeAsset = "node-v$NodeVersion-win-x64.zip"
$NodeSha256 = "721ab118a3aac8584348b132767eadf51379e0616f0db802cc1e66d7f0d98f85"
$NodeUrl = "https://nodejs.org/dist/v$NodeVersion/$NodeAsset"
$YtDlpVersion = "2026.06.09"
$YtDlpUrl = "https://github.com/yt-dlp/yt-dlp/releases/download/$YtDlpVersion/yt-dlp.exe"
$YtDlpSha256 = "3a48cb955d55c8821b60ccbdbbc6f61bc958f2f3d3b7ad5eaf3d83a543293a27"
$FfmpegVersion = "8.1.2-29-g703dcc25b9"
$FfmpegAsset = "ffmpeg-n8.1.2-29-g703dcc25b9-win64-gpl-8.1.zip"
$FfmpegRelease = "autobuild-2026-07-21-13-38"
$FfmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/$FfmpegRelease/$FfmpegAsset"
$FfmpegSha256 = "ebf57e8b1a10b176b88c3cbc66e68a4aed472cf47520b0fbf003e892fb3be642"
$FfmpegGplUrl = "https://raw.githubusercontent.com/FFmpeg/FFmpeg/n8.1.2/COPYING.GPLv3"
$FfmpegGplSha256 = "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"
$YtDlpLicenseUrl = "https://raw.githubusercontent.com/yt-dlp/yt-dlp/$YtDlpVersion/LICENSE"
$YtDlpLicenseSha256 = "7e12e5df4bae12cb21581ba157ced20e1986a0508dd10d0e8a4ab9a4cf94e85c"
$YtDlpThirdPartyUrl = "https://raw.githubusercontent.com/yt-dlp/yt-dlp/$YtDlpVersion/THIRD_PARTY_LICENSES.txt"
$YtDlpThirdPartySha256 = "b085c65586a953cdb4b13c6390d63ec984d66912e4b6a19e66ba3582f2ed104b"

if ($env:OS -ne "Windows_NT" -or -not [System.Environment]::Is64BitOperatingSystem) {
    throw "build-studio-engine.ps1 solo soporta Windows x64."
}
if (-not (Test-Path (Join-Path $EngineSource "package-lock.json"))) {
    throw "falta packages/fydesign-engine/package-lock.json; el bundle debe ser reproducible."
}

$RustcOutput = & rustc -Vv
if ($LASTEXITCODE -ne 0) { throw "falta rustc para resolver el target de Tauri." }
$HostLine = $RustcOutput | Select-String -Pattern "^host:\s*(.+)$"
if (-not $HostLine) { throw "rustc -Vv no imprimio el target host." }
$TargetTriple = $HostLine.Matches[0].Groups[1].Value.Trim()
if ($TargetTriple -ne "x86_64-pc-windows-msvc") {
    throw "FyDesign Studio para Windows requiere x86_64-pc-windows-msvc; detectado: $TargetTriple."
}

$BuildRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("edecan-studio-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null
try {
    $NodeArchive = Join-Path $BuildRoot $NodeAsset
    Write-Host "==> [Studio 1/5] Descargando Node v$NodeVersion oficial para $TargetTriple..."
    Invoke-WebRequest -Uri $NodeUrl -OutFile $NodeArchive -UseBasicParsing
    $ActualSha256 = (Get-FileHash -Algorithm SHA256 $NodeArchive).Hash.ToLowerInvariant()
    if ($ActualSha256 -ne $NodeSha256) { throw "checksum invalido para $NodeAsset." }
    Expand-Archive -Path $NodeArchive -DestinationPath $BuildRoot -Force

    $NodeHome = Join-Path $BuildRoot $NodeAsset.Substring(0, $NodeAsset.Length - 4)
    $NodeRuntime = Join-Path $NodeHome "node.exe"
    $NpmCli = Join-Path $NodeHome "node_modules\npm\bin\npm-cli.js"
    if ((& $NodeRuntime --version).Trim() -ne "v$NodeVersion" -or -not (Test-Path $NpmCli)) {
        throw "el archivo oficial de Node no contiene el runtime/npm esperados."
    }

    $StagedEngine = Join-Path $BuildRoot "fydesign-engine"
    New-Item -ItemType Directory -Path $StagedEngine -Force | Out-Null
    foreach ($file in @(
        "package.json", "package-lock.json", "tsconfig.json", "LICENSE", "NOTICE",
        "README.md", "CAPABILITIES.md", "PORTING_MANIFEST.json"
    )) {
        Copy-Item (Join-Path $EngineSource $file) $StagedEngine -Force
    }
    foreach ($directory in @("mcp", "scripts", "src")) {
        Copy-Item (Join-Path $EngineSource $directory) $StagedEngine -Recurse -Force
    }

    Write-Host "==> [Studio 2/5] Instalando packages/fydesign-engine con npm ci..."
    Push-Location $StagedEngine
    try {
        & $NodeRuntime $NpmCli ci --ignore-scripts
        if ($LASTEXITCODE -ne 0) { throw "npm ci fallo (codigo $LASTEXITCODE)." }
    } finally {
        Pop-Location
    }

    Write-Host "==> [Studio 3/5] Instalando ffmpeg/ffprobe redistribuibles y yt-dlp fijados..."
    $ToolsDir = Join-Path $StagedEngine "tools"
    New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null
    $LicensesDir = Join-Path $ToolsDir "licenses"
    New-Item -ItemType Directory -Path $LicensesDir -Force | Out-Null
    $FfmpegArchive = Join-Path $BuildRoot $FfmpegAsset
    $FfmpegExpanded = Join-Path $BuildRoot "ffmpeg"
    Invoke-WebRequest -Uri $FfmpegUrl -OutFile $FfmpegArchive -UseBasicParsing
    if ((Get-FileHash -Algorithm SHA256 $FfmpegArchive).Hash.ToLowerInvariant() -ne $FfmpegSha256) {
        throw "checksum invalido para $FfmpegAsset."
    }
    Expand-Archive -Path $FfmpegArchive -DestinationPath $FfmpegExpanded -Force
    $FfmpegSource = Get-ChildItem $FfmpegExpanded -Filter "ffmpeg.exe" -Recurse -File | Select-Object -First 1
    $FfprobeSource = Get-ChildItem $FfmpegExpanded -Filter "ffprobe.exe" -Recurse -File | Select-Object -First 1
    if (-not $FfmpegSource -or -not $FfprobeSource) {
        throw "el archivo fijado no contiene ffmpeg.exe y ffprobe.exe."
    }
    $FfmpegTarget = Join-Path $ToolsDir "ffmpeg.exe"
    $FfprobeTarget = Join-Path $ToolsDir "ffprobe.exe"
    $YtDlpTarget = Join-Path $ToolsDir "yt-dlp.exe"
    Copy-Item $FfmpegSource.FullName $FfmpegTarget -Force
    Copy-Item $FfprobeSource.FullName $FfprobeTarget -Force
    Invoke-WebRequest -Uri $YtDlpUrl -OutFile $YtDlpTarget -UseBasicParsing
    if ((Get-FileHash -Algorithm SHA256 $YtDlpTarget).Hash.ToLowerInvariant() -ne $YtDlpSha256) {
        throw "checksum invalido para yt-dlp.exe."
    }
    $FfmpegLicenseOutput = (& $FfmpegTarget -L 2>&1 | Out-String)
    if ($FfmpegLicenseOutput -match "enable-nonfree|not legally redistributable|unredistributable") {
        throw "la build de ffmpeg contiene componentes no redistribuibles."
    }
    if ($FfmpegLicenseOutput -notmatch "GNU General Public License") {
        throw "la build de ffmpeg no declaró su licencia GPL esperada."
    }
    $GplTarget = Join-Path $LicensesDir "GPL-3.0.txt"
    $YtDlpLicenseTarget = Join-Path $LicensesDir "YT-DLP-UNLICENSE.txt"
    $YtDlpThirdPartyTarget = Join-Path $LicensesDir "YT-DLP-THIRD-PARTY-LICENSES.txt"
    Invoke-WebRequest -Uri $FfmpegGplUrl -OutFile $GplTarget -UseBasicParsing
    Invoke-WebRequest -Uri $YtDlpLicenseUrl -OutFile $YtDlpLicenseTarget -UseBasicParsing
    Invoke-WebRequest -Uri $YtDlpThirdPartyUrl -OutFile $YtDlpThirdPartyTarget -UseBasicParsing
    if ((Get-FileHash -Algorithm SHA256 $GplTarget).Hash.ToLowerInvariant() -ne $FfmpegGplSha256 -or
        (Get-FileHash -Algorithm SHA256 $YtDlpLicenseTarget).Hash.ToLowerInvariant() -ne $YtDlpLicenseSha256 -or
        (Get-FileHash -Algorithm SHA256 $YtDlpThirdPartyTarget).Hash.ToLowerInvariant() -ne $YtDlpThirdPartySha256) {
        throw "checksum invalido para las licencias de herramientas multimedia."
    }
    & $FfmpegTarget -buildconf 2>&1 | Out-File -Encoding utf8 (Join-Path $LicensesDir "FFMPEG-BUILD-CONFIGURATION.txt")
    @(
        "FFmpeg/ffprobe $FfmpegVersion - separate GPL-3.0-or-later executables."
        "Binary source: $FfmpegUrl"
        "Corresponding source: https://github.com/FFmpeg/FFmpeg/tree/n8.1.2"
        "Build scripts: https://github.com/BtbN/FFmpeg-Builds/tree/$FfmpegRelease"
    ) | Out-File -Encoding utf8 (Join-Path $LicensesDir "FFMPEG-SOURCE.txt")
    @(
        "yt-dlp $YtDlpVersion - separate executable; the PyInstaller build is GPLv3+ because of bundled components."
        "Source: https://github.com/yt-dlp/yt-dlp/tree/$YtDlpVersion"
    ) | Out-File -Encoding utf8 (Join-Path $LicensesDir "YT-DLP-SOURCE.txt")
    & $FfprobeTarget -version | Out-Null
    & $YtDlpTarget --version | Out-Null

    Write-Host "==> [Studio 4/5] Descargando Chromium fijado por Playwright..."
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $StagedEngine "playwright-browsers"
    try {
        & $NodeRuntime (Join-Path $StagedEngine "node_modules\playwright\cli.js") install --only-shell chromium
        if ($LASTEXITCODE -ne 0) { throw "Playwright no pudo instalar Chromium." }
        Push-Location $StagedEngine
        try {
            & $NodeRuntime --input-type=module -e `
                "import { chromium } from 'playwright'; const browser = await chromium.launch({ headless: true }); await browser.close();"
            if ($LASTEXITCODE -ne 0) { throw "Chromium empaquetado no pudo arrancar." }
            & $NodeRuntime $NpmCli prune --omit=dev --ignore-scripts
            if ($LASTEXITCODE -ne 0) { throw "npm prune no pudo optimizar Studio." }
        } finally {
            Pop-Location
        }
    } finally {
        Remove-Item Env:\PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue
    }

    # Algunos SDKs marcan declaraciones @types como dependencias de producción,
    # pero tsx no hace type-check durante la ejecución del recurso empaquetado.
    $TypesDir = Join-Path $StagedEngine "node_modules\@types"
    if (Test-Path $TypesDir) { Remove-Item $TypesDir -Recurse -Force }
    if (
        (Test-Path (Join-Path $StagedEngine "node_modules\typescript")) -or
        (Test-Path (Join-Path $StagedEngine "node_modules\@sparticuz"))
    ) {
        throw "Studio conservó una dependencia de desarrollo o Chromium legado."
    }
    $StagedPackage = Join-Path $StagedEngine "package.json"
    & $NodeRuntime -e `
        'const fs=require("node:fs");const p=process.argv[1];const value=JSON.parse(fs.readFileSync(p,"utf8"));delete value.devDependencies;fs.writeFileSync(p,JSON.stringify(value,null,2)+"\n");' `
        $StagedPackage
    if ($LASTEXITCODE -ne 0) { throw "No se pudo limpiar la metadata de desarrollo." }
    Remove-Item (Join-Path $StagedEngine "package-lock.json") -Force
    Remove-Item (Join-Path $StagedEngine "node_modules\.package-lock.json") -Force -ErrorAction SilentlyContinue
    & $NodeRuntime -e `
        'const cp=require("node:child_process"),fs=require("node:fs"),path=require("node:path");const root=path.resolve(process.argv[1]),npm=process.argv[2];for(let round=0;round<8;round++){let out="";try{out=cp.execFileSync(process.execPath,[npm,"ls","--json","--omit=dev","--depth=0"],{cwd:root,encoding:"utf8",stdio:["ignore","pipe","pipe"]});}catch(error){out=String(error.stdout||"");}const tree=JSON.parse(out);const extras=Object.entries(tree.dependencies||{}).filter(([,meta])=>meta&&meta.extraneous).map(([name])=>name);if(!extras.length)process.exit(0);for(const name of extras){const target=path.resolve(root,"node_modules",name),modules=path.resolve(root,"node_modules")+path.sep;if(!target.startsWith(modules))throw new Error("unsafe dependency path");fs.rmSync(target,{recursive:true,force:true});}}process.exit(1);' `
        $StagedEngine $NpmCli
    if ($LASTEXITCODE -ne 0) { throw "No se pudieron retirar dependencias huérfanas." }
    Push-Location $StagedEngine
    try {
        & $NodeRuntime $NpmCli ls --omit=dev --depth=0 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "El árbol de producción de Studio no está limpio." }
    } finally {
        Pop-Location
    }

    Write-Host "==> [Studio 5/5] Instalando recurso y externalBin de Tauri..."
    if (Test-Path $EngineResource) { Remove-Item $EngineResource -Recurse -Force }
    New-Item -ItemType Directory -Path (Split-Path -Parent $EngineResource) -Force | Out-Null
    Move-Item $StagedEngine $EngineResource

    New-Item -ItemType Directory -Path $BinariesDir -Force | Out-Null
    Get-ChildItem $BinariesDir -Filter "fydesign-node-*" -ErrorAction SilentlyContinue |
        Remove-Item -Force
    $NodeSidecar = Join-Path $BinariesDir "fydesign-node-$TargetTriple.exe"
    Copy-Item $NodeRuntime $NodeSidecar -Force
    if ((& $NodeSidecar --version).Trim() -ne "v$NodeVersion") {
        throw "el runtime Node copiado para Studio no arranca."
    }

    Write-Host "==> Studio listo: recurso $EngineResource + Node runtime $NodeSidecar"
} finally {
    if (Test-Path $BuildRoot) { Remove-Item $BuildRoot -Recurse -Force }
}
