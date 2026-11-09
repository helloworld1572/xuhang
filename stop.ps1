$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'data\server.pid'
if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host 'No recorded Xuhang process.'
    exit 0
}
$serviceProcessId = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
$serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $serviceProcessId"
if (-not $serviceProcess) {
    Write-Host 'Xuhang is already stopped.'
    exit 0
}
$expectedScript = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'start.py'))
if (-not $serviceProcess.CommandLine -or -not $serviceProcess.CommandLine.Contains($expectedScript)) {
    throw 'Process does not match this project. Nothing was stopped. Use Ctrl+C in its original terminal.'
}
Stop-Process -Id $serviceProcessId
Write-Host 'Xuhang stopped. All saved data is retained.'
