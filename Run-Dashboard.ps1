$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

function Test-PortInUse {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    $getNetTcp = Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue
    if ($getNetTcp) {
        $existing = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
        return $null -ne $existing
    }

    $netstatOutput = netstat -ano | Select-String -Pattern ":$Port\s"
    return $null -ne $netstatOutput
}

function Get-LaunchPort {
    param(
        [Parameter(Mandatory = $true)]
        [int]$PreferredPort,
        [int]$MaxAttempts = 25
    )

    for ($offset = 0; $offset -lt $MaxAttempts; $offset++) {
        $candidatePort = $PreferredPort + $offset
        if (-not (Test-PortInUse -Port $candidatePort)) {
            return $candidatePort
        }

        Write-Host "Port $candidatePort is in use. Trying next port..." -ForegroundColor Yellow
    }

    throw "No free port found from $PreferredPort to $($PreferredPort + $MaxAttempts - 1)."
}

function Import-TautulliSecret {
    param([Parameter(Mandatory = $true)][string]$ProjectDirectory)

    # Inherited process environment includes Windows user/system values.
    if (-not [string]::IsNullOrWhiteSpace($env:TAUTULLI_API_KEY)) { return }
    $secretPath = Join-Path $ProjectDirectory '.env.local'
    if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) { return }
    try {
        $lines = [System.IO.File]::ReadAllLines($secretPath)
    }
    catch {
        throw 'Could not read local Tautulli secret file. Check file permissions.'
    }
    foreach ($line in $lines) {
        if ($line -match '^\s*TAUTULLI_API_KEY\s*=(.*)$') {
            $value = $Matches[1].Trim()
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                $env:TAUTULLI_API_KEY = $value
                return
            }
        }
    }
}

Import-TautulliSecret -ProjectDirectory $ScriptDir

$PreferredPort = 8503
$Port = Get-LaunchPort -PreferredPort $PreferredPort
if ($Port -ne $PreferredPort) {
    Write-Host "Preferred port $PreferredPort is in use. Falling back to $Port." -ForegroundColor Yellow
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw 'LOCALAPPDATA is not available; cannot create a machine-local dashboard environment.'
}

$RuntimeRoot = Join-Path $env:LOCALAPPDATA 'PlexMaintanance'
$RuntimeEnvironment = Join-Path $RuntimeRoot 'venv'
$PythonExe = Join-Path $RuntimeEnvironment 'Scripts\python.exe'
$PipCache = Join-Path $RuntimeRoot 'pip-cache'
$DependencyFingerprint = Join-Path $RuntimeRoot 'dependency-fingerprint.txt'

$RuntimeUsable = $false
if (Test-Path -LiteralPath $PythonExe -PathType Leaf) {
    $ExistingPythonVersion = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    $RuntimeUsable = ($LASTEXITCODE -eq 0) -and ($ExistingPythonVersion -match '^\d+\.\d+$') -and ([version]$ExistingPythonVersion -ge [version]'3.12')
    if (-not $RuntimeUsable) {
        Write-Host 'The machine-local Python environment is incomplete or incompatible; recreating it...' -ForegroundColor Yellow
        Remove-Item -LiteralPath $RuntimeEnvironment -Recurse -Force
    }
}

if (-not $RuntimeUsable) {
    [void](New-Item -ItemType Directory -Path $RuntimeRoot -Force)
    if (Test-Path -LiteralPath $RuntimeEnvironment) {
        Remove-Item -LiteralPath $RuntimeEnvironment -Recurse -Force
    }

    Write-Host "Creating the machine-local Python environment at $RuntimeEnvironment..." -ForegroundColor Yellow
    $PyLauncher = Get-Command py -ErrorAction SilentlyContinue

    if ($PyLauncher) {
        Invoke-CheckedCommand -FilePath $PyLauncher.Source -Arguments @('-3.12', '-m', 'venv', $RuntimeEnvironment)
    }
    else {
        $PythonCmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $PythonCmd) {
            Write-Host 'Could not find a Python launcher (py/python) to create .venv.' -ForegroundColor Red
            Read-Host 'Press Enter to exit'
            exit 1
        }

        $BasePythonVersion = & $PythonCmd.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($LASTEXITCODE -ne 0 -or $BasePythonVersion -notmatch '^\d+\.\d+$' -or [version]$BasePythonVersion -lt [version]'3.12') {
            throw 'Python 3.12 or later is required to create the dashboard environment.'
        }
        Invoke-CheckedCommand -FilePath $PythonCmd.Source -Arguments @('-m', 'venv', $RuntimeEnvironment)
    }

    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        throw "Virtual environment Python was not created at: $PythonExe"
    }
}

Write-Host "Using Python: $PythonExe" -ForegroundColor DarkGray

Write-Host 'Validating and repairing dependencies if needed...' -ForegroundColor Cyan
Invoke-CheckedCommand -FilePath $PythonExe -Arguments @(
    (Join-Path $ScriptDir 'dependency_validation.py'),
    '--requirements', (Join-Path $ScriptDir 'requirements.txt'),
    '--lock', (Join-Path $ScriptDir 'requirements-lock.txt'),
    '--fingerprint', $DependencyFingerprint,
    '--cache-dir', $PipCache
)

Write-Host 'Starting Plex Media Dashboard...' -ForegroundColor Green
Write-Host "Open http://localhost:$Port" -ForegroundColor DarkGray

# Run Streamlit as a tracked child process. Streamlit normally handles Ctrl+C
# itself, but its script thread can be blocked in a network request or while
# walking the media drive. In that case PowerShell's interrupted Wait-Process
# enters the finally block and prevents the dashboard from hanging forever.
$DashboardProcess = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList @('-m', 'streamlit', 'run', 'app.py', '--server.port', "$Port", '--server.headless', 'true') `
    -WorkingDirectory $ScriptDir `
    -NoNewWindow `
    -PassThru

try {
    Wait-Process -Id $DashboardProcess.Id
    $DashboardProcess.Refresh()

    if ($DashboardProcess.ExitCode -ne 0) {
        throw "Dashboard exited with code $($DashboardProcess.ExitCode)."
    }
}
finally {
    $DashboardProcess.Refresh()
    if (-not $DashboardProcess.HasExited) {
        Write-Host 'Waiting up to 3 seconds for the dashboard to stop...' -ForegroundColor Yellow
        if (-not $DashboardProcess.WaitForExit(3000)) {
            Write-Host 'Dashboard did not stop cleanly; forcing it to close.' -ForegroundColor Yellow
            Stop-Process -Id $DashboardProcess.Id -Force -ErrorAction SilentlyContinue
            $DashboardProcess.WaitForExit()
        }
    }
}
