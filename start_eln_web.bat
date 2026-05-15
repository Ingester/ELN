@echo off
setlocal
cd /d "%~dp0"
set ELN_WEB_HOST=0.0.0.0
set ELN_WEB_PORT=8550
set ELN_WEB_OPEN=0
"C:\Users\Ingester\mambaforge\envs\py310\python.exe" run_web.py
