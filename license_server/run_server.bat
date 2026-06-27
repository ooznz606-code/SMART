@echo off
echo SmartTrader License Server
echo ==========================
echo.
pip install fastapi "uvicorn[standard]" requests python-multipart --quiet
echo.
echo Server: http://0.0.0.0:8000
echo Admin:  http://localhost:8000/admin
echo.
cd /d "%~dp0.."
python -m uvicorn license_server.server:app --host 0.0.0.0 --port 8000 --reload
pause
