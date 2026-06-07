# Wind Turbine Surveillance Dashboard

This Python app turns the current 1 Hz sample dataset into weekly turbine modal KPIs and an interactive dashboard. It follows the updated MATLAB reference flow in Python: load accelerometer and SCADA data, estimate RPM when SCADA RPM is unavailable, compute sliding PSD/f0/RMS indicators, load fixed `REF_<turbine>_*.mat` baselines when available, then estimate FDD damping coefficient zeta.

The default dataset is `Echantillon 1Hz/data5` with SCADA from `Echantillon 1Hz/SCADA`, configured as turbine `w005`.

## Run The Dashboard

```powershell
python -m pip install -r requirements.txt
python -m streamlit run dashboard.py
```

The dashboard exposes turbine, input folders, baseline folder, window length, overlap, light/dark theme, visible UTC time range, and PSD-axis controls. It shows weekly baseline frequency, zeta, f0 drift, wind/RPM/generated power, and PSD. Alert and confidence views are intentionally removed from the dashboard.

## Run The Weekly Batch

```powershell
python run_weekly.py
```

By default this writes `outputs/latest_week/weekly_kpis.csv`, `outputs/latest_week/weekly_summary.csv`, `outputs/latest_week/summary.json`, and `outputs/latest_week/psd_arrays.npz`. A future weekly scheduler can call the same command with a different input folder once the production data landing path is known.

## Current Assumptions

The accelerometer timestamps are interpreted as `Europe/Brussels`, matching the MATLAB script. SCADA `pointTime` is interpreted as UTC. The current 1 Hz sample does not include direct rotor speed, so RPM is estimated from power and wind in zone 2 using the MATLAB formula. W005 and W007 references are loaded from `reference-matlab-files` by default; turbines without a matching reference file fall back to an internal baseline from the loaded data.

## Baseline Reference Files

The MATLAB workflow calls these fixed healthy baselines "references": `Calcul_Reference_Eolienne_2KC.m` creates `REF_<turbine>_*.mat`, and `Surveillance_Eolienne_2KC.m` loads that file before monitoring new data. In the dashboard, this is labeled as the baseline source. The best practice is to build this baseline from a longer healthy period and keep it fixed for routine weekly monitoring, then recalculate it only after a deliberate engineering review.
