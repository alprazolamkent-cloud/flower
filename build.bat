@echo off
rem Сборка "Доставки цветов.exe" на Windows (нужен установленный Python 3.10+)
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name "FlowerDeliveries" main.py
echo.
echo Готово: dist\FlowerDeliveries.exe
pause
