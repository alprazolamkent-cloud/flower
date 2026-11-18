@echo off
rem Сборка AloFlorist.exe на Windows (нужен установленный Python 3.10+)
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm AloFlorist.spec
start /wait "" dist\AloFlorist.exe --selftest selftest.txt
type selftest.txt
echo.
echo Готово: dist\AloFlorist.exe
pause
