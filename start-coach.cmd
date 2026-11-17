@echo off
setlocal
cd /d "%~dp0"
py -m pro_replay school
if errorlevel 1 pause
