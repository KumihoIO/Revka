@echo off
setlocal enabledelayedexpansion

:: ===========================================================================
:: install-sidecars.bat — install Construct's Python MCP sidecars on Windows
::
:: Idempotent. Creates scaffolding under %USERPROFILE%\.construct\ only.
:: Mirrors scripts/install-sidecars.sh on POSIX.
::
:: Usage:
::   scripts\install-sidecars.bat [--skip-kumiho] [--skip-operator]
:: ===========================================================================

set "SKIP_KUMIHO=false"
set "SKIP_OPERATOR=false"
set "PYTHON_BIN=python"

:parse_args
if "%~1"=="" goto :after_args
if /I "%~1"=="--skip-kumiho"   set "SKIP_KUMIHO=true"   & shift & goto :parse_args
if /I "%~1"=="--skip-operator" set "SKIP_OPERATOR=true" & shift & goto :parse_args
if /I "%~1"=="--python"        set "PYTHON_BIN=%~2"     & shift & shift & goto :parse_args
if /I "%~1"=="-h"              goto :show_help
if /I "%~1"=="--help"          goto :show_help
echo unknown flag: %~1
exit /b 2

:after_args

:: ── Paths ────────────────────────────────────────────────────────
set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
for %%I in ("%REPO_ROOT%") do set "REPO_ROOT=%%~fI"
set "OPERATOR_SRC=%REPO_ROOT%\operator-mcp"

set "CONSTRUCT_DIR=%USERPROFILE%\.construct"
set "KUMIHO_DIR=%CONSTRUCT_DIR%\kumiho"
set "KUMIHO_VENV=%KUMIHO_DIR%\venv"
set "KUMIHO_LAUNCHER=%KUMIHO_DIR%\run_kumiho_mcp.py"
set "OPERATOR_DIR=%CONSTRUCT_DIR%\operator_mcp"
set "OPERATOR_LAUNCHER=%OPERATOR_DIR%\run_operator_mcp.py"

:: ── Preflight ────────────────────────────────────────────────────
where %PYTHON_BIN% >nul 2>&1
if errorlevel 1 (
    echo error: %PYTHON_BIN% not found on PATH. Install Python 3.11+ and retry.
    exit /b 1
)

if not exist "%CONSTRUCT_DIR%" mkdir "%CONSTRUCT_DIR%"

:: ── Operator sidecar ─────────────────────────────────────────────
if "%SKIP_OPERATOR%"=="true" (
    echo    [skip] Operator install skipped --skip-operator
    goto :kumiho_step
)

echo ==^> Installing Operator MCP -^> %OPERATOR_DIR%

if not exist "%OPERATOR_SRC%" (
    echo    [warn] operator-mcp\ not found at %OPERATOR_SRC% — skipping
    goto :kumiho_step
)

if not exist "%OPERATOR_DIR%" mkdir "%OPERATOR_DIR%"

if not exist "%OPERATOR_DIR%\venv\Scripts\python.exe" (
    %PYTHON_BIN% -m venv "%OPERATOR_DIR%\venv"
)
set "OP_PY=%OPERATOR_DIR%\venv\Scripts\python.exe"

"%OP_PY%" -m pip install --quiet --upgrade pip
"%OP_PY%" -m pip install --quiet "%OPERATOR_SRC%[all]" 2>nul || "%OP_PY%" -m pip install --quiet "%OPERATOR_SRC%"

:: Mirror package tree with robocopy (Windows-native)
robocopy "%OPERATOR_SRC%\operator_mcp" "%OPERATOR_DIR%" /E /XD __pycache__ venv session-manager /XF *.pyc >nul
copy /Y "%OPERATOR_SRC%\operator_mcp\run_operator_mcp.py" "%OPERATOR_LAUNCHER%" >nul
if exist "%OPERATOR_SRC%\requirements.txt" copy /Y "%OPERATOR_SRC%\requirements.txt" "%OPERATOR_DIR%\requirements.txt" >nul

if exist "%OPERATOR_LAUNCHER%" (
    echo    [ok] Operator launcher present: %OPERATOR_LAUNCHER%
) else (
    echo    [warn] Operator launcher missing at %OPERATOR_LAUNCHER%
)

:: ── Kumiho sidecar ───────────────────────────────────────────────
:kumiho_step
if "%SKIP_KUMIHO%"=="true" (
    echo    [skip] Kumiho install skipped --skip-kumiho
    goto :done
)

echo ==^> Installing Kumiho MCP -^> %KUMIHO_DIR%

if not exist "%KUMIHO_DIR%" mkdir "%KUMIHO_DIR%"

if not exist "%KUMIHO_VENV%\Scripts\python.exe" (
    %PYTHON_BIN% -m venv "%KUMIHO_VENV%"
    echo    [ok] venv created
) else (
    echo    [skip] venv already exists
)

set "K_PY=%KUMIHO_VENV%\Scripts\python.exe"
"%K_PY%" -m pip install --quiet --upgrade pip
:: [mcp] extra pulls in mcp>=1.0.0 + httpx>=0.27.0, required by kumiho.mcp_server.
:: kumiho_memory (separate package) provides the high-level memory tools
:: (engage/reflect/recall/consolidate/dream_state) that the Construct
:: session-bootstrap prompt mandates. kumiho.mcp_server auto-merges them
:: when the package is importable.
"%K_PY%" -m pip install --quiet "kumiho[mcp]>=0.9.20" "kumiho_memory>=0.5.0"
echo    [ok] kumiho[mcp] + kumiho_memory installed

:: Write launcher if absent — do NOT overwrite user-authored launcher.
if exist "%KUMIHO_LAUNCHER%" (
    echo    [skip] launcher already present: %KUMIHO_LAUNCHER%
    goto :done
)

> "%KUMIHO_LAUNCHER%" echo #!/usr/bin/env python3
>>"%KUMIHO_LAUNCHER%" echo """Kumiho MCP launcher installed by Construct's install-sidecars script."""
>>"%KUMIHO_LAUNCHER%" echo import os, pathlib, sys
>>"%KUMIHO_LAUNCHER%" echo HERE = pathlib.Path(__file__).resolve().parent
>>"%KUMIHO_LAUNCHER%" echo VENV_PY = HERE / "venv" / "bin" / "python3"
>>"%KUMIHO_LAUNCHER%" echo if not VENV_PY.exists():
>>"%KUMIHO_LAUNCHER%" echo ^    VENV_PY = HERE / "venv" / "Scripts" / "python.exe"
>>"%KUMIHO_LAUNCHER%" echo if not VENV_PY.exists():
>>"%KUMIHO_LAUNCHER%" echo ^    sys.stderr.write(f"[kumiho-launcher] venv python not found at {VENV_PY}.\n")
>>"%KUMIHO_LAUNCHER%" echo ^    sys.exit(127)
>>"%KUMIHO_LAUNCHER%" echo os.execv(str(VENV_PY), [str(VENV_PY), "-m", "kumiho.mcp_server", *sys.argv[1:]])
echo    [ok] launcher written: %KUMIHO_LAUNCHER%

:done
echo.
echo ==^> Done.
echo Verify with:
echo   construct doctor
echo   dir "%KUMIHO_LAUNCHER%" "%OPERATOR_LAUNCHER%"
exit /b 0

:show_help
echo install-sidecars.bat — install Construct Kumiho + Operator MCP sidecars
echo.
echo Usage: scripts\install-sidecars.bat [--skip-kumiho] [--skip-operator] [--python python3]
exit /b 0
