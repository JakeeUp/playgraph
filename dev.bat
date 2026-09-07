@echo off
REM Starts the API and the sync worker in two labelled windows.
REM
REM Why not honcho (which the Procfile is written for): honcho starts both
REM fine on Windows, but its Ctrl+C shutdown path throws InterruptedError on
REM Python 3.14 and can leave orphaned processes holding port 8000. Two native
REM windows are less elegant but they stop cleanly and you can restart one
REM without touching the other, which matters because the worker needs a
REM manual restart after code changes while the API reloads itself.

echo Starting PlayGraph...
start "PlayGraph API" cmd /k python -m uvicorn app.main:app --reload
start "PlayGraph WORKER" cmd /k python -m arq app.worker.WorkerSettings
echo.
echo Two windows opened: PlayGraph API and PlayGraph WORKER.
echo Docs at http://localhost:8000/docs
echo Close each window, or Ctrl+C inside it, to stop that process.
