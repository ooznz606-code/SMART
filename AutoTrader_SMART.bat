@echo off
title SmartTrader X1
cd /d "C:\Users\USER\OneDrive\Desktop\SMART"

echo ======================================
echo       SmartTrader X1 - Starting
echo ======================================
echo Project: C:\Users\USER\OneDrive\Desktop\SMART
echo.

if not exist "trading_app.py" (
    echo [ERROR] trading_app.py not found in SMART folder.
    echo تأكد أن الملف موجود داخل:
    echo C:\Users\USER\OneDrive\Desktop\SMART
    echo.
    pause
    exit /b 1
)

if exist "__pycache__" rd /s /q "__pycache__"

python trading_app.py

echo.
echo ======================================
echo Program closed.
echo ======================================
pause
