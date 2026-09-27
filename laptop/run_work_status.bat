@echo off
cd /d "%~dp0"
pythonw "Work Status.pyw"
if errorlevel 1 python "Work Status.pyw"
