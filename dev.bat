@echo off
REM Launch the API and Redis-backed worker in separate windows.
REM Run this file from the project folder after finishing any active sync.

start "PlayGraph API" /D "%~dp0" cmd /k "python -m uvicorn app.main:app --reload"
start "PlayGraph WORKER" /D "%~dp0" cmd /k "python -m arq app.worker.WorkerSettings"
