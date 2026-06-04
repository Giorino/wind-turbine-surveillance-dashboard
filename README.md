# Wind Turbine Surveillance Dashboard

This Python app turns the current 1 Hz sample dataset into weekly turbine-health KPIs and an interactive dashboard. It follows the main flow of `Surveillance_eolienne_V5_100Hz.m`: load accelerometer and SCADA data, estimate RPM when SCADA RPM is unavailable, compute sliding PSD/RMS/f0 indicators, build operating and stable masks, then classify f0 and RMS alerts.

The default dataset is `Echantillon 1Hz/data5` with SCADA from `Echantillon 1Hz/SCADA`, configured as turbine `w005`.

## Run The Dashboard

```powershell
python -m pip install -r requirements.txt
python -m streamlit run dashboard.py
```

The dashboard exposes turbine, input folder, window length, overlap, date, state, stability, metric, and PSD-axis filters.

## Run The Weekly Batch

```powershell
python run_weekly.py
```

By default this writes `outputs/latest_week/weekly_kpis.csv`, `outputs/latest_week/summary.json`, and `outputs/latest_week/psd_arrays.npz`. A future weekly scheduler can call the same command with a different input folder once the production data landing path is known.

## Current Assumptions

The accelerometer timestamps are interpreted as `Europe/Brussels`, matching the MATLAB script. SCADA `pointTime` is interpreted as UTC. The current 1 Hz sample does not include direct rotor speed, so RPM is estimated from power and wind in zone 2 using the MATLAB formula. The root `WT3`, `WT5`, and `WT7` ZIP folders are larger 100 Hz-style inputs; the loader can read them, but the dashboard defaults to the 1 Hz sample requested for this phase.
