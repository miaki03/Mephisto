@echo off
REM Mephisto ??????? PowerShell ????????????
REM   tasks.cmd setup ^| test ^| doctor ^| version
setlocal
set "PWSH=powershell"
where pwsh >nul 2>nul
if %ERRORLEVEL% EQU 0 set "PWSH=pwsh"
"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0tasks.ps1" %*
exit /b %ERRORLEVEL%
