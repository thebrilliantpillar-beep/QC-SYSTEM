@echo off
cd /d "%~dp0"
echo IQC 검사 시스템 서버를 시작합니다...
echo.
echo 브라우저에서 http://localhost:5000 으로 접속하세요.
echo 이 창을 닫으면 서버가 종료됩니다.
echo.
python app.py
pause
