@echo off

rem Modify the script to launch below
set batch=echo.bat

pushd "%~dp0"
python batch_on_files.py --script "%batch%" %*
IF %ERRORLEVEL% NEQ 0 pause
