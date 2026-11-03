@echo off
rem Installs Champ Studio for this Windows user from this repo (no administrator rights needed).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 pause
