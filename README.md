# Wind Turbine Surveillance Dashboard

This Python app turns the current 1 Hz sample dataset into weekly turbine modal KPIs and an interactive dashboard. It follows the updated MATLAB monitoring flow in Python: load accelerometer and SCADA data, estimate RPM when SCADA RPM is unavailable, compute sliding PSD/f0/RMS indicators, compute the baseline from the loaded data, then estimate FDD damping coefficient zeta.

The default dataset is `Echantillon 1Hz/data5` with SCADA from `Echantillon 1Hz/SCADA`, configured as turbine `w005`.

## Run The Dashboard

```powershell
python -m pip install -r requirements.txt
python -m streamlit run dashboard.py
```

The dashboard exposes input folders, turbine, window length, overlap, visible UTC time range, and PSD-axis controls. It shows weekly baseline frequency, zeta, f0 drift, wind/RPM/generated power, PSD, and modal diagrams with P10/P50/P90 envelopes. It also lets users preview and download a compact weekly English text report named like `report_w005_2026-05-04.txt`. Alert and confidence views are intentionally removed from the dashboard.

## Run The Weekly Batch

```powershell
python run_weekly.py
```

By default this writes `outputs/latest_week/weekly_kpis.csv`, `outputs/latest_week/weekly_summary.csv`, `outputs/latest_week/summary.json`, `outputs/latest_week/psd_arrays.npz`, and weekly text reports under `outputs/latest_week/reports`. A future weekly scheduler can call the same command with a different input folder once the production data landing path is known.

## Current Assumptions

The accelerometer timestamps are interpreted as `Europe/Brussels`, matching the MATLAB script. SCADA `pointTime` is interpreted as UTC. The current 1 Hz sample does not include direct rotor speed, so RPM is estimated from power and wind in zone 2 using the MATLAB formula. The dashboard does not load fixed `REF_*.mat` files by default; it computes the monitoring baseline from the loaded dataset.
