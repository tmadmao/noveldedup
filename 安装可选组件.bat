@echo off
cd /d "%~dp0"

echo ==========================================
echo   小说查重 - 安装可选组件
echo ==========================================
echo.
echo 核心功能不需要装任何东西。
echo txt / epub / html / docx 查重与回收站删除都是纯标准库。
echo.
echo 下面三个是可选组件：
echo   pypdf                        支持 PDF，仅带文本层的
echo   opencc-python-reimplemented  繁简视为同一本
echo   pyinstaller                  打包成 exe
echo.

set "PY="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto INSTALL

python -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto INSTALL

echo 没找到 Python。
echo 请先到 python.org 安装 Python 3.9 以上版本。
pause
exit /b 1

:INSTALL
echo 正在安装，网络慢时会等一会儿...
echo.
%PY% -m pip install --upgrade pypdf opencc-python-reimplemented pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 goto RETRY
goto DONE

:RETRY
echo.
echo 镜像失败，改用官方源重试...
%PY% -m pip install --upgrade pypdf opencc-python-reimplemented pyinstaller
if errorlevel 1 goto FAIL

:DONE
echo.
echo 安装完成。重新打开 运行工具.bat 即可用上：
echo   PDF 文件会被识别
echo   界面上的 繁简视为同一本 开关会可勾选
echo.
pause
exit /b 0

:FAIL
echo.
echo 安装失败，通常是网络原因，不影响核心功能。
pause
exit /b 1
