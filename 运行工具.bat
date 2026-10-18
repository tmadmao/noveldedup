@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY="

rem 优先用带 tkinter 的 Python 打开图形界面
py -3 -c "import tkinter" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto RUN

python -c "import tkinter" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto RUN

python --version >nul 2>nul
if errorlevel 1 goto NOPY
set "PY=python"
echo [提示] 当前 Python 没有 tkinter，改用命令行模式。
echo.

:RUN
%PY% novel_dedup.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto FAIL
exit /b 0

:NOPY
echo [错误] 没找到 Python。
echo        请到 python.org 安装 Python 3.9 以上版本，
echo        安装时勾选 Add python.exe to PATH 和 tcl/tk and IDLE。
pause
exit /b 1

:FAIL
echo.
echo [退出码 %RC%] 上面是错误信息，可截图反馈。
pause
exit /b %RC%
