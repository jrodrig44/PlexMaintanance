$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $project 'Run-Dashboard.ps1'
$tokens = $null
$parseErrors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Launcher syntax validation failed.' }

$source = [IO.File]::ReadAllText($scriptPath)
$portSelection = $source.IndexOf('$Port = Get-LaunchPort')
$runtimeSetup = $source.IndexOf('$RuntimeRoot =')
$dependencyRepair = $source.IndexOf('dependency_validation.py')
if ($portSelection -lt 0 -or $runtimeSetup -lt 0 -or $dependencyRepair -lt 0 -or
    $portSelection -ge $runtimeSetup -or $runtimeSetup -ge $dependencyRepair) {
    throw 'Port selection must precede runtime creation and dependency repair.'
}
if ($source -notmatch '\$env:LOCALAPPDATA' -or $source.Contains("Join-Path `$ScriptDir '.venv")) {
    throw 'The launcher must keep its managed environment outside the project directory.'
}

$portFunction = $tree.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-LaunchPort'
}, $true)
if ($null -eq $portFunction -or $portFunction.Extent.Text -match 'Stop-Process') {
    throw 'Port selection must not terminate an existing listener.'
}
. ([scriptblock]::Create($portFunction.Extent.Text))

$script:OccupiedPorts = @(8503)
function Test-PortInUse {
    param([int]$Port)
    return $script:OccupiedPorts -contains $Port
}
$selectedPort = Get-LaunchPort -PreferredPort 8503 -MaxAttempts 3
if ($selectedPort -ne 8504) { throw 'Occupied preferred port did not select the next free port.' }

$script:OccupiedPorts = @(8503, 8504, 8505)
$allOccupiedFailed = $false
try {
    Get-LaunchPort -PreferredPort 8503 -MaxAttempts 3 | Out-Null
}
catch {
    $allOccupiedFailed = $true
}
if (-not $allOccupiedFailed) { throw 'Port selection should fail when every candidate is occupied.' }

Write-Output 'Launcher syntax, ordering, fallback, and occupied-port handling passed without terminating listeners.'
