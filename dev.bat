@echo off
rem ============================================================================
rem dev.bat — launch OpenShorts dev stack from CMD / PowerShell / Explorer.
rem   dev.bat                 backend + frontend + renderer
rem   dev.bat --no-renderer   skip the Remotion renderer
rem   dev.bat stop --yes      kill services from a previous run
rem   dev.bat --check         preflight + port report only
rem All real logic lives in dev.sh, run through Git Bash (never WSL bash).
rem ============================================================================
setlocal
set "BASH_EXE="
if exist "%ProgramFiles%\Git\bin\bash.exe" set "BASH_EXE=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH_EXE if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "BASH_EXE=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined BASH_EXE if exist "%LOCALAPPDATA%\Programs\Git\bin\bash.exe" set "BASH_EXE=%LOCALAPPDATA%\Programs\Git\bin\bash.exe"
if not defined BASH_EXE (
  echo [dev] Git Bash not found.  Install Git for Windows, or run ^"bash dev.sh^"
  echo        from a Git Bash terminal.
  pause
  exit /b 1
)
cd /d "%~dp0"
"%BASH_EXE%" dev.sh %*