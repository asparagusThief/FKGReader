@echo off
setlocal
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\bootstrap.ps1" Check %*
set "readerExit=%errorlevel%"
pause
exit /b %readerExit%
