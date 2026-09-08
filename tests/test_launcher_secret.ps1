# Deterministic tests: extract only the loader function; never start dependencies/app.
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$tokens = $null
$parseErrors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $project 'Run-Dashboard.ps1'), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Launcher syntax validation failed.' }
$functionAst = $tree.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Import-TautulliSecret' }, $true)
# Trusted tracked function, never secret-file content.
. ([scriptblock]::Create($functionAst.Extent.Text))
$tempDirectory = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
[void][IO.Directory]::CreateDirectory($tempDirectory)
$secretPath = Join-Path $tempDirectory '.env.local'
$originalKey = $env:TAUTULLI_API_KEY
try {
    $env:TAUTULLI_API_KEY = ''
    $output = @(Import-TautulliSecret $tempDirectory *>&1)
    if ($env:TAUTULLI_API_KEY -or $output.Count) { throw 'Absent-file test failed.' }
    $cases = @(
        @{Text='TAUTULLI_API_KEY=synthetic-only'; Expected='synthetic-only'},
        @{Text='TAUTULLI_API_KEY=   '; Expected=''},
        @{Text="# comment`n`nunrelated garbage`nOTHER=value`n TAUTULLI_API_KEY = synthetic-only  "; Expected='synthetic-only'},
        @{Text='Write-Output unsafe'; Expected=''},
        @{Text='TAUTULLI_API_KEY=$(throw "must not execute")'; Expected='$(throw "must not execute")'}
    )
    foreach ($case in $cases) {
        [IO.File]::WriteAllText($secretPath, $case.Text)
        $env:TAUTULLI_API_KEY = ''
        $output = @(Import-TautulliSecret $tempDirectory *>&1)
        if ([string]$env:TAUTULLI_API_KEY -ne $case.Expected -or $output.Count) { throw 'Secret parsing/no-output test failed.' }
    }
    $env:TAUTULLI_API_KEY = 'environment-only'
    $output = @(Import-TautulliSecret $tempDirectory *>&1)
    if ($env:TAUTULLI_API_KEY -ne 'environment-only' -or $output.Count) { throw 'Environment precedence test failed.' }
    Write-Output 'Launcher syntax and 7 secret-loading scenarios passed; loader emitted no output.'
}
finally {
    $env:TAUTULLI_API_KEY = $originalKey
    if ([IO.File]::Exists($secretPath)) { [IO.File]::Delete($secretPath) }
    [IO.Directory]::Delete($tempDirectory)
}
