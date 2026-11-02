@echo off
REM Kills anything left holding port 8000, plus stray arq workers.
REM Use this if a window got closed badly and the API will not start again
REM with "address already in use".

echo Looking for processes on port 8000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do (
    echo   killing pid %%a
    taskkill /PID %%a /F >nul 2>&1
)
echo Done.
