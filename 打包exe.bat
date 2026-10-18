@echo off
cd /d "%~dp0"

echo ==========================================
echo   小说查重 - 打包成 exe
echo ==========================================
echo.

set "PY="
py -3 -c "import PyInstaller" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto BUILD

python -c "import PyInstaller" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto BUILD

echo 缺少 PyInstaller，打包工具本身没装。
echo.
echo 两种办法：
echo   一、双击 安装可选组件.bat
echo   二、手动执行：
echo         py -3 -m pip install pyinstaller
echo.
echo 不打包也能用，直接双击 小说查重.pyw 即可。
pause
exit /b 1

:BUILD
echo 正在打包，首次约 1 到 3 分钟...
echo.

%PY% -m PyInstaller --noconfirm --clean --windowed --onedir ^
  --name "小说查重" ^
  --add-data "noveldedup;noveldedup" ^
  --collect-submodules "noveldedup" ^
  --collect-submodules "noveldedup" ^
  --hidden-import "noveldedup.gui" ^
  --hidden-import "noveldedup.cli" ^
  --hidden-import "noveldedup.formats.epub" ^
  --hidden-import "noveldedup.formats.docx" ^
  --hidden-import "noveldedup.formats.html" ^
  --hidden-import "noveldedup.formats.pdf" ^
  "小说查重.pyw"
if errorlevel 1 goto FAIL

echo.
copy /y "README.md" "dist\小说查重\" >nul 2>nul
copy /y "LICENSE" "dist\小说查重\" >nul 2>nul
copy /y "CHANGELOG.md" "dist\小说查重\" >nul 2>nul

echo.
echo ==========================================
echo   完成 dist\小说查重\小说查重.exe
echo   整个 dist\小说查重 文件夹可发给别人
echo ==========================================
pause
exit /b 0

:FAIL
echo.
echo 打包失败，上面是 PyInstaller 的输出。
echo 常见原因：
echo   杀毒软件拦截，把项目目录加入白名单再试
echo   路径里有特殊字符
echo 不打包也能用，双击 小说查重.pyw 即可。
pause
exit /b 1
