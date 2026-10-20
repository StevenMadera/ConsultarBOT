@echo off
title Instalar BOTs
where py >nul 2>&1 || (
    echo Falta Python: instala Python 3 desde python.org y vuelve a abrir este archivo.
    pause
    exit /b 1
)
rem instalar.py usa pywin32 para crear los accesos directos.
py -m pip install --disable-pip-version-check --quiet pywin32 || (
    echo No se pudo instalar pywin32: revisa la red y vuelve a abrir este archivo.
    pause
    exit /b 1
)
py "%~dp0instalar.py"
set "RESULTADO=%ERRORLEVEL%"
pause
exit /b %RESULTADO%
