[CmdletBinding()]
param(
    [switch]$SkipPythonInstall,
    [switch]$SkipNpmInstall,
    [switch]$SkipBackendSidecar,
    [switch]$RunChecks,
    [switch]$Smoke
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot
$script:TauriConfigArgs = @()

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found in PATH."
    }
}

function Invoke-Native {
    param(
        [string]$Command,
        [string[]]$Arguments
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command $($Arguments -join ' ')"
    }
}

function Get-RustHostTriple {
    $rustVersion = & rustc -vV
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: rustc -vV"
    }
    $hostLine = $rustVersion | Where-Object { $_ -like "host:*" } | Select-Object -First 1
    if (-not $hostLine) {
        throw "Could not determine Rust host target triple."
    }
    return $hostLine.Substring(5).Trim()
}

function Stop-AITradeBackendProcesses {
    $processes = Get-Process -Name "aitrade-backend" -ErrorAction SilentlyContinue
    if (-not $processes) {
        return
    }

    Write-Step "Stopping existing AITrade backend processes"
    foreach ($process in $processes) {
        Write-Host "Stopping aitrade-backend.exe (PID $($process.Id))"
        Stop-Process -Id $process.Id -Force -ErrorAction Stop
    }
}

function Set-PackagedAuthServerUrl {
    if ($env:AITRADE_AUTH_SERVER_URL) {
        return
    }

    $deployConfig = Join-Path $repoRoot "deploy.local.json"
    if (-not (Test-Path $deployConfig)) {
        return
    }

    try {
        $rawConfig = Get-Content -LiteralPath $deployConfig -Raw
    }
    catch {
        Write-Warning "Could not read deploy.local.json; packaged auth server URL was not set."
        return
    }

    $match = [regex]::Match($rawConfig, '"auth_server_url"\s*:\s*"([^"]+)"')
    if ($match.Success) {
        $env:AITRADE_AUTH_SERVER_URL = $match.Groups[1].Value
        Write-Host "Using packaged auth server URL from deploy.local.json."
    }
}

function Build-BackendSidecar {
    Write-Step "Building Python backend sidecar"
    Invoke-Native "python" @("-m", "PyInstaller", "--version")

    $targetTriple = Get-RustHostTriple
    $entryScript = Join-Path $repoRoot "scripts\desktop_backend_sidecar.py"
    $sidecarDir = Join-Path $repoRoot "src-tauri\binaries"
    $distDir = Join-Path $repoRoot "build\aitrade-sidecar-dist"
    $workDir = Join-Path $repoRoot "build\aitrade-sidecar-work"
    $specDir = Join-Path $repoRoot "build\aitrade-sidecar-spec"

    New-Item -ItemType Directory -Force -Path $sidecarDir | Out-Null
    Invoke-Native "python" @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--collect-data", "akshare",
        "--collect-data", "adata",
        "--collect-binaries", "py_mini_racer",
        "--hidden-import", "baostock",
        "--hidden-import", "finshare",
        "--hidden-import", "adata",
        "--name", "aitrade-backend",
        "--distpath", $distDir,
        "--workpath", $workDir,
        "--specpath", $specDir,
        $entryScript
    )

    $builtDir = Join-Path $distDir "aitrade-backend"
    $builtExe = Join-Path $builtDir "aitrade-backend.exe"
    if (-not (Test-Path $builtExe)) {
        throw "PyInstaller did not produce expected backend executable: $builtExe"
    }
    $sidecarTarget = Join-Path $sidecarDir "aitrade-backend-$targetTriple"
    if (Test-Path $sidecarTarget) {
        Remove-Item -LiteralPath $sidecarTarget -Recurse -Force
    }
    Copy-Item -LiteralPath $builtDir -Destination $sidecarTarget -Recurse -Force
    Write-Host "Backend sidecar:"
    Write-Host "  $sidecarTarget"
}

function Set-TauriSidecarConfig {
    $targetTriple = Get-RustHostTriple
    $sidecarSource = "binaries/aitrade-backend-$targetTriple"
    $sidecarConfigPath = Join-Path $repoRoot "build\tauri-sidecar-config.json"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $sidecarConfigPath) | Out-Null
    $sidecarConfig = @"
{
    "bundle": {
    "resources": {
      "$sidecarSource": "aitrade-backend"
    }
  }
}
"@
    Set-Content -LiteralPath $sidecarConfigPath -Value $sidecarConfig -Encoding UTF8
    $script:TauriConfigArgs = @("--config", $sidecarConfigPath)
}

function Invoke-TauriBuild {
    param([switch]$NoBundle)

    $arguments = @("run", "tauri", "--", "build")
    if ($NoBundle) {
        $arguments += "--no-bundle"
    }
    $arguments += $script:TauriConfigArgs
    Invoke-Native "npm" $arguments
}

if ($env:OS -ne "Windows_NT") {
    throw "This script packages the Windows desktop app and must be run on Windows."
}

Write-Step "Checking local tools"
Require-Command "python"
Require-Command "npm"
Require-Command "cargo"
Stop-AITradeBackendProcesses

if (-not $SkipPythonInstall) {
    Write-Step "Installing Python package in editable mode"
    Invoke-Native "python" @("-m", "pip", "install", "-e", ".")
}

if (-not $SkipNpmInstall) {
    if (-not (Test-Path "node_modules")) {
        Write-Step "Installing frontend dependencies"
        if (Test-Path "package-lock.json") {
            Invoke-Native "npm" @("ci")
        }
        else {
            Invoke-Native "npm" @("install")
        }
    }
    else {
        Write-Step "Using existing node_modules"
    }
}

if ($RunChecks) {
    Write-Step "Running quick verification"
    Invoke-Native "python" @("-m", "pytest", "tests/test_package_identity.py", "tests/test_windows_packaging_script.py", "-q")
    Invoke-Native "npm" @("run", "test:frontend")
    Invoke-Native "cargo" @("test", "--manifest-path", "src-tauri/Cargo.toml")
}

Set-PackagedAuthServerUrl
if (-not $SkipBackendSidecar) {
    Build-BackendSidecar
    Set-TauriSidecarConfig
}

if ($Smoke) {
    Write-Step "Building release desktop app without installer bundle"
    Invoke-TauriBuild -NoBundle
    $releaseExe = Join-Path $repoRoot "src-tauri\target\release\aitrade-cn.exe"
    Write-Host ""
    Write-Host "Smoke build finished:"
    Write-Host "  $releaseExe"
}
else {
    Write-Step "Packaging Windows desktop app"
    Invoke-TauriBuild
    $bundleDir = Join-Path $repoRoot "src-tauri\target\release\bundle"
    Write-Host ""
    Write-Host "Windows package output:"
    Write-Host "  $bundleDir"
    if (Test-Path $bundleDir) {
        Get-ChildItem $bundleDir -Recurse -File |
            Where-Object { $_.Extension -in ".exe", ".msi" } |
            Sort-Object LastWriteTime -Descending |
            Select-Object FullName, Length, LastWriteTime |
            Format-Table -AutoSize
    }
}
