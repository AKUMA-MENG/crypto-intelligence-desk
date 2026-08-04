@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start.ps1"
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" if not defined CID_NO_PAUSE pause
exit /b %APP_EXIT%
