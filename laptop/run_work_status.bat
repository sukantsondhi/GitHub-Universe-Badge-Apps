@echo off
cd /d "%~dp0"
pythonw work_status_controller.py
if errorlevel 1 python work_status_controller.py
