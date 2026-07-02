@echo off
chcp 65001 >nul
echo.
echo ===  SmartTrader - Build EXE v1.6.2  ===
echo.

pip install requests --quiet

echo [1/3] Building assets...
python create_assets.py

echo [2/3] Building EXE...
pyinstaller SmartTrader.spec --noconfirm --clean

echo [3/3] Copying output...
if exist "dist\SmartTrader.exe" (
    copy /Y "dist\SmartTrader.exe" "%USERPROFILE%\Desktop\SmartTrader.exe" >nul
    echo.
    echo BUILD SUCCESS: %USERPROFILE%\Desktop\SmartTrader.exe
) else (
    echo BUILD FAILED - check errors above
)

echo.
pause

