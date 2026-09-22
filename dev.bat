@echo off
REM Launch the API and Redis-backed worker in separate windows.
REM Run this file from the project folder after finishing any active sync.

pushd "%~dp0"
python -m app.migrations check
if errorlevel 1 (
    echo Database setup is required. Stop API and worker, then run: python -m app.migrations upgrade
    popd
    exit /b 1
)
popd

start "PlayGraph API" /D "%~dp0" cmd /k "python -m uvicorn app.main:app"
start "PlayGraph WORKER" /D "%~dp0" cmd /k "python -m arq app.worker.WorkerSettings"
