# ART + PCD Integrated Monitor

## Folder layout inside `code`

- `main.py`
  - Integrated GUI, ART acquisition, and PCD analysis entry point
- `requirements.txt`
  - Python dependencies
- `setup_env.bat`
  - Create or refresh the virtual environment
- `start_gui.bat`
  - Start the GUI
- `runtime_settings.json`
  - Last-used settings, now defaulting to portable in-folder paths
- `vendor/ACTS1000_64.dll`
  - Bundled ART DLL used by hardware mode
- `data/reference/no_cavitation/*.csv`
  - Bundled no-cavitation reference library
- `data/reference/cavitation/*.csv`
  - Bundled cavitation reference library
- `data/playback/8-1.csv`
  - Bundled playback sample for offline testing
- `output/captures`
  - Capture output folder created by the app when needed

## What the GUI now shows

- Real-time PCD scatter
  - Each frame is plotted in the `log10(SCDultra)` vs `log10(ICD)` plane
  - The background includes the bundled reference groups
- Real-time spectrum
  - The displayed spectrum is the segment-averaged FFT spectrum
  - This matches the current analysis pipeline where 25000 points are split and then averaged after FFT
  - The current `f0` is marked on the spectrum view
- Latest metrics panel
  - `f0`
  - `SCDhar`
  - `SCDultra`
  - `ICD`
  - `Ultra/ICD`
  - `Cavitation Score`
  - `Risk Score`
  - Chinese conclusion text

## Modes

- `Playback 回放`
  - Uses `data/playback/*.csv` by default
  - Lets you validate the analysis and GUI without hardware
- `Hardware 真机`
  - Uses `vendor/ACTS1000_64.dll` by default
  - Acquires one triggered frame at a time from the ART board
  - Optionally saves each capture to `output/captures`

## Portability

You asked for the project to be movable by taking only the `code` folder.

At the software layer, this is now basically true because:

- the reference CSV library is inside `code/data`
- the default playback sample is inside `code/data`
- the ART DLL is inside `code/vendor`
- the runtime settings default to these in-folder paths

### Important note

For `Hardware 真机` mode, the target Windows PC still needs the ART board driver installed correctly. Bundling the DLL is enough for the Python app itself, but not for replacing the low-level device driver.

## How to move to the lab computer

1. Copy the whole `code` folder to the lab PC
2. Open the copied `code` folder
3. Run `setup_env.bat`
4. Run `start_gui.bat`
5. First test `Playback 回放`
6. Then switch to `Hardware 真机` and verify the board can open successfully

## Current defaults

- DLL path: `vendor/ACTS1000_64.dll`
- Playback source: `data/playback/*.csv`
- No-cavitation reference: `data/reference/no_cavitation/*.csv`
- Cavitation reference: `data/reference/cavitation/*.csv`
- Requested sample rate: `25 MHz`
- Points: `25000`
- Segment count: `2`
- Spectrum mode: `amplitude`
