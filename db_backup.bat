@echo off
set BACKUP_DIR=%USERPROFILE%\Desktop\IQC_DB백업
set DB_SRC=%~dp0iqc.db
set TODAY=%date:~0,4%%date:~5,2%%date:~8,2%
set FILENAME=iqc_backup_%TODAY%.db

if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
copy /Y "%DB_SRC%" "%BACKUP_DIR%\%FILENAME%" >nul

:: 30일 이상 된 백업 자동 삭제
forfiles /p "%BACKUP_DIR%" /s /m *.db /d -30 /c "cmd /c del @path" 2>nul
