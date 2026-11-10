@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: falta el entorno .venv del bot.
    popd
    exit /b 1
)
".venv\Scripts\python.exe" "probar_flujo_web.py"
set "CODIGO=%ERRORLEVEL%"
popd
exit /b %CODIGO%
