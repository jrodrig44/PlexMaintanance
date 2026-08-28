$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

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

$VenvPython = Join-Path $ScriptDir '.venv\Scripts\python.exe'

if (-not (Test-Path $VenvPython)) {
    Write-Host 'Project virtual environment not found. Creating .venv...' -ForegroundColor Yellow
    $PyLauncher = Get-Command py -ErrorAction SilentlyContinue

    if ($PyLauncher) {
        Invoke-CheckedCommand -FilePath $PyLauncher.Source -Arguments @('-3.12', '-m', 'venv', '.venv')
    }
    else {
        $PythonCmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $PythonCmd) {
            Write-Host 'Could not find a Python launcher (py/python) to create .venv.' -ForegroundColor Red
            Read-Host 'Press Enter to exit'
            exit 1
        }

        Invoke-CheckedCommand -FilePath $PythonCmd.Source -Arguments @('-m', 'venv', '.venv')
    }
}

$PythonExe = $VenvPython

if (-not (Test-Path $PythonExe)) {
    Write-Host "Virtual environment Python not found at: $PythonExe" -ForegroundColor Red
    Read-Host 'Press Enter to exit'
    exit 1
}

Write-Host "Using Python: $PythonExe" -ForegroundColor DarkGray

Write-Host 'Checking dependencies...' -ForegroundColor Cyan
Invoke-CheckedCommand -FilePath $PythonExe -Arguments @('-m', 'pip', 'install', '-r', 'requirements.txt')

$PreferredPort = 8503
$Port = Get-LaunchPort -PreferredPort $PreferredPort
if ($Port -ne $PreferredPort) {
    Write-Host "Preferred port $PreferredPort is in use. Falling back to $Port." -ForegroundColor Yellow
}

Write-Host 'Starting Plex Media Dashboard...' -ForegroundColor Green
Write-Host "Open http://localhost:$Port" -ForegroundColor DarkGray

# Run Streamlit as a tracked child process. Streamlit normally handles Ctrl+C
# itself, but its script thread can be blocked in a network request or while
# walking the media drive. In that case PowerShell's interrupted Wait-Process
# enters the finally block and prevents the dashboard from hanging forever.
$DashboardProcess = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList @('-m', 'streamlit', 'run', 'app.py', '--server.port', "$Port") `
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
