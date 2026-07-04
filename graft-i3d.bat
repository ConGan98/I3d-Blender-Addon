@echo off
setlocal enabledelayedexpansion

REM ====================================================================
REM  graft-i3d.bat -- drag-and-drop wrapper for graft_skeleton.py.
REM
REM  What it does (mesh-only edits, to keep the stock animation working):
REM    Takes your Blender-EXPORTED .i3d and grafts a clean skeleton whose
REM    REST POSE comes from the stock MODEL (the pose your mesh was skinned
REM    to) but whose NODE IDS come from the ANIMATION i3d (the ids the
REM    .i3d.anim references). Re-points the mesh's skin by bone name and adds
REM    the <Animation> reference so GIANTS Editor plays the clip.
REM    Result: <exported>_GE.i3d next to the input.
REM
REM  Why two files: some animals (e.g. Highland) ship an animation i3d whose
REM  skeleton is in a DIFFERENT rest pose than the model. Grafting the anim
REM  skeleton would deform the mesh; taking rest from the model and ids from
REM  the anim is correct for every animal.
REM
REM  Usage:
REM    Drag the EXPORTED .i3d onto this file. Picker 1 asks for the stock
REM    MODEL .i3d (the one you imported from); picker 2 asks for the
REM    ANIMATION .i3d (the one paired with the .i3d.anim).
REM
REM  Or from a console:
REM    graft-i3d.bat <exported.i3d> [model.i3d] [animation.i3d] [output.i3d]
REM ====================================================================

REM --- Model scale -----------------------------------------------------
REM  --scale-from-export : if you scaled the whole rig bigger (e.g. a bigger
REM  bull), scale the grafted skeleton ROOT to match. The stock-sized .i3d.anim
REM  then plays at your model's scale instead of snapping it back to stock size
REM  during playback. No-op for an unscaled model, so it's safe to leave on.
REM  Set to empty to disable.
set "SCALE_FROM_EXPORT=--scale-from-export"
REM --------------------------------------------------------------------

if "%~1"=="" (
    echo Drag the exported .i3d file onto this script.
    echo Or run: graft-i3d.bat ^<exported.i3d^> [model.i3d] [animation.i3d] [output.i3d]
    pause
    exit /b 1
)

set "EXPORTED=%~1"
set "PICKDIR=%~dp1"

REM --- Model i3d (skeleton REST source) -------------------------------------
if "%~2"=="" (
    for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.OpenFileDialog; $d.Title = 'Select the stock MODEL .i3d (what you imported from - for the correct rest pose)'; $d.Filter = 'i3d files (*.i3d)^|*.i3d^|All files (*.*)^|*.*'; $d.InitialDirectory = '%PICKDIR%'; if ($d.ShowDialog() -eq 'OK') { $d.FileName }"`) do set "MODELI3D=%%I"
    if "!MODELI3D!"=="" (
        echo Cancelled -- no model .i3d picked.
        pause
        exit /b 1
    )
) else (
    set "MODELI3D=%~2"
)

REM --- Animation i3d (node-ID source + anim reference) ----------------------
if "%~3"=="" (
    for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.OpenFileDialog; $d.Title = 'Select the ANIMATION .i3d (paired with the .i3d.anim - for the node ids)'; $d.Filter = 'i3d files (*.i3d)^|*.i3d^|All files (*.*)^|*.*'; $d.InitialDirectory = '%PICKDIR%'; if ($d.ShowDialog() -eq 'OK') { $d.FileName }"`) do set "ANIMI3D=%%I"
    if "!ANIMI3D!"=="" (
        echo Cancelled -- no animation .i3d picked.
        pause
        exit /b 1
    )
) else (
    set "ANIMI3D=%~3"
)

if "%~4"=="" (
    set "OUTPUT=%~dpn1_GE.i3d"
) else (
    set "OUTPUT=%~4"
)

REM Derive the .anim binary name/path from the animation i3d.
for %%A in ("!ANIMI3D!") do set "ANIMNAME=%%~nxA"
set "ANIMSRC=!ANIMI3D!.anim"
set "ANIMDEST=%~dp1!ANIMNAME!.anim"

set "SCRIPT=%~dp0io_import_i3d\tools\graft_skeleton.py"
if not exist "%SCRIPT%" (
    echo Could not find graft_skeleton.py at:
    echo   %SCRIPT%
    echo Make sure graft-i3d.bat sits next to the io_import_i3d folder.
    pause
    exit /b 1
)

REM Make sure the .anim sits next to the output so GIANTS Editor can load it.
if exist "!ANIMSRC!" (
    if not exist "!ANIMDEST!" copy /Y "!ANIMSRC!" "!ANIMDEST!" >nul
) else (
    echo   NOTE: no .anim found next to the animation i3d:
    echo     !ANIMSRC!
    echo   GE needs !ANIMNAME!.anim next to the output to play the clip.
)

echo.
echo Model     : !MODELI3D!
echo Animation : !ANIMI3D!
echo Exported  : %EXPORTED%
echo Output    : %OUTPUT%
echo.

python "%SCRIPT%" "!MODELI3D!" "%EXPORTED%" "%OUTPUT%" --id-source "!ANIMI3D!" --anim-ref "!ANIMNAME!.anim" %SCALE_FROM_EXPORT%
set RC=%errorlevel%

echo.
if %RC% neq 0 (
    echo Graft failed with exit code %RC%.
) else (
    echo Done. Open the _GE.i3d in GIANTS Editor and play the animation.
)
pause
exit /b %RC%
