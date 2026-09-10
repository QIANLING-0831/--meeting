param([switch]$CheckOnly)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

function Resolve-PythonLauncher {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & $pyLauncher.Source -3.11 -c "import sys; assert sys.version_info >= (3, 11)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @{ File = $pyLauncher.Source; Prefix = @("-3.11") }
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source -c "import sys; assert sys.version_info >= (3, 11)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @{ File = $python.Source; Prefix = @() }
        }
    }

    throw "未找到 Python 3.11 或更高版本。请先从 https://www.python.org/downloads/windows/ 安装。"
}

$launcher = Resolve-PythonLauncher
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if ($CheckOnly) {
    Write-Host "Python 3.11+：可用"
    Write-Host "虚拟环境：$(if (Test-Path -LiteralPath $venvPython) { '已存在' } else { '尚未创建' })"
    $codexCheck = Get-Command codex -ErrorAction SilentlyContinue
    Write-Host "Codex CLI：$(if ($codexCheck) { '可用' } else { '未检测到' })"
    return
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "[1/3] 创建 Python 虚拟环境..."
    & $launcher.File @($launcher.Prefix) -m venv (Join-Path $projectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败。" }
} else {
    Write-Host "[1/3] 已存在 Python 虚拟环境。"
}

Write-Host "[2/3] 安装项目依赖..."
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "升级 pip 失败。" }
& $venvPython -m pip install -r (Join-Path $projectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "安装 Python 依赖失败。" }

Write-Host "[3/3] 检查 Codex..."
$codex = Get-Command codex -ErrorAction SilentlyContinue
if ($codex) {
    $version = & $codex.Source --version 2>$null
    Write-Host "已检测到 Codex：$version"
} else {
    Write-Warning "没有检测到 codex 命令。转写可以启动，但生成回答前必须安装并登录 Codex。"
    Write-Host "官方说明：https://developers.openai.com/codex/cli"
}

Write-Host "本机配置、API Key、简历和面试记录均不会进入 Git。"
