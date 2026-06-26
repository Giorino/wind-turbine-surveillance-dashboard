# Wind Turbine Surveillance Dashboard

This project turns 1 Hz wind-turbine accelerometer data and SCADA data into an interactive Streamlit dashboard for tracking modal frequency, damping, PSD, and weekly reports.

## Quick Start On Windows

Install Python 3.11 or newer from [python.org](https://www.python.org/downloads/). During installation, enable **Add python.exe to PATH**.

Download or clone this GitHub repository, then double-click:

```text
run_app.bat
```

The first run creates a local `.venv` environment and installs the required Python packages. After that, it starts the dashboard at:

```text
http://localhost:8502
```

If the browser does not open automatically, copy that address into Chrome, Edge, or Firefox.

## Manual Install

Use this option if you are not on Windows or prefer terminal commands.

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m streamlit run dashboard.py --server.port 8502
```

On macOS or Linux, activate the environment with:

```bash
source .venv/bin/activate
```

then run the same `pip install` and `streamlit` commands.

## Dataset Structure

Put all data under one dataset folder. By default the app uses:

```text
dataset/
  data5/
    2026-02-25.csv
    2026-02-26.csv
  data7/
    2026-02-24.csv
    2026-02-25.csv
  SCADA/
    2026-02-25.csv
    2026-02-26.csv
```

Turbine folders are discovered automatically from folder names:

```text
data5  -> W005
data7  -> W007
data12 -> W012
```

Only date-named CSV files are loaded from turbine and SCADA folders, for example `2026-03-16.csv`. Other files are ignored by the loader.

The accelerometer CSV files should contain:

```text
datetime, ax, ay
```

Additional accelerometer columns such as `az`, `gx`, `gy`, or `gz` can be present. The dashboard currently uses `ax` and `ay`.

The SCADA CSV files should contain `pointTime` plus turbine-specific columns such as:

```text
w005Speed
w005Power
w005RotorSpeed
```

Rotor speed is optional. If it is missing, the app estimates RPM from wind speed and generated power. The SCADA loader supports both the older compact SCADA format and the newer wider SCADA format.

## Using Your Own Data Path

Open the dashboard and set **Dataset folder** in the sidebar.

Examples:

```text
C:\Users\YourName\Documents\my-wind-data
D:\projects\turbine-dataset
```

The selected folder must contain turbine folders such as `data5` and a `SCADA` folder. The turbine dropdown is created from those folders.

## Dashboard Graphs

**Frequency Drift** shows detected modal frequencies for AX and AY over time, together with trend lines and the current reference baseline.

**Weekly Baselines** shows weekly AX/AY baseline frequency. The summary cards also show weekly frequency shift in `Hz/week`.

**PSD** shows the power spectral density over time for AX or AY.

**Modal Diagram** shows FFT scatter points with P10, P50, and P90 envelopes for AX and AY. It also overlays FDD frequency and damping information when available.

**Weekly Report** lets users preview and download a compact English text report for each weekly period.

## Weekly Batch Export

To generate output files without opening the dashboard, run:

```powershell
python run_weekly.py
```

Outputs are written to:

```text
outputs/latest_week/
  weekly_kpis.csv
  weekly_summary.csv
  summary.json
  psd_arrays.npz
  reports/
```

You can choose a turbine or dataset folder:

```powershell
python run_weekly.py --turbine w007 --dataset-dir "D:\projects\turbine-dataset"
```

## Notes

Accelerometer timestamps are interpreted as `Europe/Brussels`. SCADA `pointTime` is interpreted as UTC.

## Disclaimer

This dashboard is a monitoring and engineering review tool, not an alarm system or a certified diagnostic system. The calculated frequencies, damping values, shifts, PSD views, and weekly reports are estimates based on the supplied data and assumptions in the code. They should not be used alone to decide turbine condition, safety, maintenance actions, or operational limits. Any important conclusion should be checked by qualified engineers against the raw data, turbine context, SCADA history, inspection records, and the operator's normal procedures.
