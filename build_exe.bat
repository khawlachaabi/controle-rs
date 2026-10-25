@echo off
REM Construit l'executable Windows dist\ControleRS.exe avec PyInstaller.
REM Utilise le venv du projet (cree s'il n'existe pas).
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
    python -m venv venv
)

venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if errorlevel 1 goto :erreur

REM Les tests doivent passer avant de construire un exe livrable
venv\Scripts\python.exe -m pytest tests -q
if errorlevel 1 goto :erreur

venv\Scripts\python.exe -m PyInstaller --noconfirm --clean ControleRS.spec
if errorlevel 1 goto :erreur

echo.
echo Build termine : dist\ControleRS.exe
pause
exit /b 0

:erreur
echo.
echo ECHEC du build - voir les messages ci-dessus.
pause
exit /b 1
