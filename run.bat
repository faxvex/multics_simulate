@echo off
rem Multics emulator. Usage: run.bat  or  run.bat --demo
chcp 65001 >nul
cd /d "%~dp0"
python -m multics_sim %*
pause
