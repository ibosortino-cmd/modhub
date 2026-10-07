@echo off
rem Crea dist\ModHub.exe (un unico file, senza console) con PyInstaller.
rem Una volta sola:  python -m pip install --user pyinstaller
cd /d "%~dp0"
python -m PyInstaller --noconfirm --onefile --noconsole --name ModHub --icon "%~dp0modhub.ico" ^
  --distpath dist --workpath build --specpath build ^
  --add-data "%~dp0ui;ui" --add-data "%~dp0catalog;catalog" --add-data "%~dp0public-catalog;public-catalog" ^
  --add-data "%~dp0emu_settings;emu_settings" --add-data "%~dp0emulators.json;." ^
  --hidden-import overlay_ui --hidden-import selfrun --hidden-import tkinter.filedialog ^
  modhub_main.py
