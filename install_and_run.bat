@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY=python"
    ) else (
        echo [ERROR] Python was not found.
        pause
        exit /b 1
    )
)

%PY% -c "import PySide6, PIL" >nul 2>nul
if errorlevel 1 (
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
)

%PY% app.py
if errorlevel 1 pause
endlocal
