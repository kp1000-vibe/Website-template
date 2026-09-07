@echo off
REM Double click this, or run `setup.cmd` from any Windows shell.
REM It calls setup.ps1 with the execution policy bypassed for this one run,
REM which is what Windows blocks by default on a freshly downloaded script.
REM Nothing about the machine's policy is changed.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
  echo.
  echo Setup did not finish. The error is above.
  pause
  exit /b 1
)
pause
