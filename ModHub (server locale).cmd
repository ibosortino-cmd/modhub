@echo off
rem Serve il catalogo via HTTP (come farebbe un vero sito) e apri ModHub collegato a quello.
cd /d "%~dp0"
start "Server catalogo ModHub" /min python -m http.server 8765 --bind 127.0.0.1 --directory catalog
timeout /t 1 >nul
start "" pythonw server.py --catalog http://127.0.0.1:8765
