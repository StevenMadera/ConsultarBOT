@echo off
setlocal
title Instalar PaginasBOT
echo.
echo   Instalando PaginasBOT. No cierres esta ventana: puede tardar unos minutos.
echo.
set "CARPETA=%USERPROFILE%\BOTs"
set "TEMPORAL=%TEMP%\BOTs-instalador"
set "PROGRAMA=https://github.com/StevenMadera/ConsultarBOT/archive/refs/heads/main.zip"
set "PYTHON=https://www.python.org/ftp/python/3.14.8/python-3.14.8-amd64.exe"
set "BAJAR=$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing"
if exist "%TEMPORAL%" rmdir /s /q "%TEMPORAL%"
mkdir "%TEMPORAL%"

rem Sin Python 3.12 o superior, se instala solo para este usuario: sin ventanas ni permisos de administrador.
set "PY=py"
py -3 -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>&1 || (
    echo [1/3] Instalando Python...
    powershell -NoProfile -Command "%BAJAR% '%PYTHON%' -OutFile '%TEMPORAL%\python.exe'; if ((Get-AuthenticodeSignature '%TEMPORAL%\python.exe').Status -ne 'Valid') { exit 1 }" 2>nul || (
        echo No se pudo descargar Python: revisa que haya internet y vuelve a abrir este archivo.
        pause
        exit /b 1
    )
    start "" /wait "%TEMPORAL%\python.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 InstallLauncherAllUsers=0 Include_test=0
    if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
)
"%PY%" -3 -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>&1 || (
    echo No se pudo instalar Python. Pide ayuda a soporte.
    pause
    exit /b 1
)

echo [2/3] Descargando PaginasBOT...
powershell -NoProfile -Command "%BAJAR% '%PROGRAMA%' -OutFile '%TEMPORAL%\programa.zip'; Expand-Archive '%TEMPORAL%\programa.zip' '%TEMPORAL%\programa'" 2>nul || (
    echo No se pudo descargar PaginasBOT: revisa que haya internet y vuelve a abrir este archivo.
    pause
    exit /b 1
)
rem Copia encima sin borrar nada: salidas, capturas y log.txt de una instalacion anterior se conservan.
for /d %%d in ("%TEMPORAL%\programa\*") do robocopy "%%d" "%CARPETA%" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo No se pudo copiar PaginasBOT a la carpeta BOTs. Pide ayuda a soporte.
    pause
    exit /b 1
)

echo [3/3] Instalando lo necesario y creando el acceso directo...
"%PY%" -3 -m pip install --disable-pip-version-check --quiet pywin32 || (
    echo No se pudo instalar pywin32: revisa que haya internet y vuelve a abrir este archivo.
    pause
    exit /b 1
)
"%PY%" -3 "%CARPETA%\instalar.py"
rmdir /s /q "%TEMPORAL%" 2>nul
pause
