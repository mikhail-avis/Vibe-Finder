@echo off
setlocal
cd /d "%~dp0"
REM This launcher is intentionally stable and is never overwritten by updates.
set "PY_CMD=python"
where py >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3"
if exist ".venv\Scripts\python.exe" set "PY_CMD=.venv\Scripts\python.exe"

REM Apply a previously validated update while Vibe Finder is not running.
%PY_CMD% updater.py --apply-pending
if errorlevel 1 (
    echo An update could not be applied. Read the message above.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Setting up Vibe Finder for first use...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo Python 3.11 or 3.12 must be installed first. Visit https://www.python.org/downloads/windows/
        pause
        exit /b 1
    )
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
)
if exist "updates\needs_dependencies.txt" (
    echo Updating Vibe Finder dependencies...
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency update failed. Check your connection.
        pause
        exit /b 1
    )
    del "updates\needs_dependencies.txt"
)
echo Opening Vibe Finder in your browser...
.venv\Scripts\python.exe -m streamlit run app.py
pause
