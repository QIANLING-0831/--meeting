@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo 正在准备 Interview Copilot 本地环境...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
if errorlevel 1 (
    echo.
    echo 安装失败，请根据上方提示处理后重试。
    pause
    exit /b 1
)

echo.
echo 安装完成。现在可以双击“启动转写.bat”。
pause

