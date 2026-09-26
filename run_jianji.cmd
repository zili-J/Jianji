@echo off
setlocal
cd /d "%~dp0"
"C:\Users\22910\AppData\Local\Programs\Python\Python312\pythonw.exe" "app\main.py"
if errorlevel 1 (
  echo JianJi failed to start. Trying console mode for details...
  "C:\Users\22910\AppData\Local\Programs\Python\Python312\python.exe" "app\main.py"
  pause
)
