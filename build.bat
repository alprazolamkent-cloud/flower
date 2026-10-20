@echo off
rem Сборка AloFlorist.exe на Windows (нужен установленный Python 3.10+)
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name "AloFlorist" main.py
echo.
echo Готово: dist\AloFlorist.exe
pause
