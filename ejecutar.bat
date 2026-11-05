@echo off
setlocal

rem Ejecutar siempre desde la carpeta del proyecto, incluso desde otra unidad.
pushd "%~dp0"
if errorlevel 1 exit /b 1

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo ERROR: No se encuentra el Python del entorno virtual .venv.
    echo Cree el entorno e instale requirements.txt antes de ejecutar el robot.
    popd
    exit /b 1
)

"%~dp0.venv\Scripts\python.exe" "%~dp0main.py" %*
set "CODIGO_SALIDA=%ERRORLEVEL%"
popd
exit /b %CODIGO_SALIDA%
