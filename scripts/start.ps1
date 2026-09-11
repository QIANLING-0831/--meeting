param([switch]$NoBrowser)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$appPath = Join-Path $projectRoot "app.py"
$url = "http://127.0.0.1:8765"

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw '找不到 .venv。请先双击“安装依赖.bat”。'
}

try {
    $status = Invoke-RestMethod -Uri "$url/api/status" -TimeoutSec 2
    if ($null -ne $status) {
        Write-Host "Interview Copilot 已在运行。"
        if (-not $NoBrowser) { Start-Process $url }
        return
    }
} catch {
    # No healthy Interview Copilot service responded; check the port below.
}

$occupied = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($occupied) {
    throw "端口 8765 已被进程 $($occupied.OwningProcess) 占用，但它不是可用的 Interview Copilot 服务。"
}

Set-Location -LiteralPath $projectRoot
Write-Host "正在启动 Interview Copilot..."
$arguments = @($appPath)
if ($NoBrowser) { $arguments += "--no-browser" }
& $venvPython @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Interview Copilot 已异常退出，退出码：$LASTEXITCODE"
}
