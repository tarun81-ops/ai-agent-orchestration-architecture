@echo off
cd /d "C:\Users\tarun verma\ai agent kernal"
set /p TASK=Type your task: 
".venv\Scripts\python.exe" main.py "%TASK%"
pause