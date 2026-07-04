@echo off
setlocal enabledelayedexpansion

REM ====================================================================
REM  fix-i3d.bat -- drag-and-drop wrapper for the node-id remap tool.
REM
REM  Usage:
REM    Drag the EXPORTED .i3d onto this file. A picker will ask for the
REM    ORIGINAL .i3d. Result is written to <exported>_fixed.i3d next to
REM    the input.
REM
REM  Or run from a console:
REM    fix-i3d.bat <exported.i3d> [original.i3d] [output.i3d]
REM ====================================================================

REM --- Mesh orientation ------------------------------------------------
REM  none : native GIANTS export (I3D exporter Forward -Z, Up Y). The mesh
REM         is already in the Y-up bone frame, so no rotation is applied.
REM  x180 : legacy -- only if your export axis setting leaves the mesh
REM         upside down / facing backward relative to the skeleton.
set "VERTEX_ROTATE=none"
REM --------------------------------------------------------------------

if "%~1"=="" (
    echo Drag the exported .i3d file onto this script.
    echo Or run: fix-i3d.bat ^<exported.i3d^> [original.i3d] [output.i3d]
    pause
    exit /b 1
)

set "EXPORTED=%~1"

if "%~2"=="" (
    REM No original supplied -- open a file picker.
    for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.OpenFileDialog; $d.Title = 'Select the ORIGINAL .i3d (the one you imported into Blender)'; $d.Filter = 'i3d files (*.i3d)^|*.i3d^|All files (*.*)^|*.*'; $d.InitialDirectory = [System.IO.Path]::GetDirectoryName('%EXPORTED%'); if ($d.ShowDialog() -eq 'OK') { $d.FileName }"`) do set "ORIGINAL=%%I"
    if "!ORIGINAL!"=="" (
        echo Cancelled -- no original .i3d picked.
        pause
        exit /b 1
    )
) else (
    set "ORIGINAL=%~2"
)

if "%~3"=="" (
    REM Default output: <exported>_fixed.i3d in the same folder.
    set "OUTPUT=%~dpn1_fixed.i3d"
) else (
    set "OUTPUT=%~3"
)

set "SCRIPT=%~dp0io_import_i3d\tools\remap_node_ids.py"

if not exist "%SCRIPT%" (
    echo Could not find remap_node_ids.py at:
    echo   %SCRIPT%
    echo Make sure fix-i3d.bat sits next to the io_import_i3d folder.
    pause
    exit /b 1
)

echo.
echo Original : %ORIGINAL%
echo Exported : %EXPORTED%
echo Output   : %OUTPUT%
echo.

python "%SCRIPT%" "%ORIGINAL%" "%EXPORTED%" "%OUTPUT%" --vertex-rotate %VERTEX_ROTATE%
set RC=%errorlevel%

echo.
if %RC% neq 0 (
    echo Remap failed with exit code %RC%.
) else (
    echo Done. Open the _fixed.i3d in GIANTS Editor.
)
pause
exit /b %RC%
