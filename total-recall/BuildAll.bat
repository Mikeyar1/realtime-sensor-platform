@echo off
echo ========================================
echo Building Total Recall Application
echo ========================================
echo.

REM Check if Poetry is available
where poetry >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Poetry is not installed or not in PATH
    echo Please install Poetry: https://python-poetry.org/docs/#installation
    pause
    exit /b 1
)

REM Check if Git is available
where git >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Git is not installed or not in PATH
    pause
    exit /b 1
)

REM Get version from git tag
for /f "tokens=*" %%i in ('git describe --tags --always') do set GIT_VERSION=%%i
echo Build Version: %GIT_VERSION%
echo.

echo [1/5] Cleaning dist folder...
if exist dist (
    echo Removing existing dist folder...
    rmdir /s /q dist
)

echo Clean complete.
echo.

echo [2/5] Installing/updating dependencies...
poetry install
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo.
echo [3/5] Building executables with PyInstaller...
poetry build
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to build executables
    pause
    exit /b 1
)

echo.
echo [4/5] Renaming executables with version...
set DIST_DIR=dist\pyinstaller\win_amd64
if exist %DIST_DIR%\total_recall.exe (
    move %DIST_DIR%\total_recall.exe %DIST_DIR%\total_recall-%GIT_VERSION%.exe
)

echo.
echo [5/5] Creating distribution package...
set ZIP_NAME=total-recall-%GIT_VERSION%-win64.zip
set ZIP_PATH=%DIST_DIR%\%ZIP_NAME%
if exist %ZIP_PATH% del /f /q %ZIP_PATH%
powershell -Command "Compress-Archive -Path '%DIST_DIR%\*' -DestinationPath '%ZIP_PATH%' -CompressionLevel Optimal"
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to create zip package
    pause
    exit /b 1
)

echo.
echo ========================================
echo Build completed successfully!
echo ========================================
echo.
echo Version: %GIT_VERSION%
echo Package: %ZIP_PATH%
echo.
echo Distribution contents:
echo   - total_recall-%GIT_VERSION%.exe
echo   - config.toml
echo.

pause
