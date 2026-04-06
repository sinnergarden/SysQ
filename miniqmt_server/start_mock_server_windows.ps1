param(
    [string]$Python = "",
    [string]$Config = "miniqmt_server/config.windows.mock.example.yaml",
    [string]$Host = "0.0.0.0",
    [int]$Port = 8811
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

if ($Python) {
    $PythonCommand = @($Python)
}
elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonCommand = @("py", "-3")
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonCommand = @("python")
}
else {
    throw "Could not find 'py' or 'python' on PATH."
}

Push-Location $RepoRoot
try {
    $Args = @()
    if ($PythonCommand.Length -gt 1) {
        $Args += $PythonCommand[1..($PythonCommand.Length - 1)]
    }
    $Args += @("-m", "miniqmt_server.app", "--config", $Config, "--host", $Host, "--port", "$Port")
    & $PythonCommand[0] @Args
}
finally {
    Pop-Location
}
