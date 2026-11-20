@echo off
setlocal
if not exist "%~dp0ejecutar.bat" (
    echo ERROR: Copia este lanzador junto al ejecutar.bat del bot.
    exit /b 2
)
set "BACKEND_ACTIVO=false"
call "%~dp0ejecutar.bat"
exit /b %ERRORLEVEL%
