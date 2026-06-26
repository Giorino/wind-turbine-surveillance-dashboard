from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .analysis import AnalysisResult


@dataclass(frozen=True)
class WeeklyTextReport:
    filename: str
    label: str
    content: str


def build_weekly_text_report(result: AnalysisResult, week_index: int) -> WeeklyTextReport:
    if result.weekly.empty:
        raise ValueError("No weekly report is available for the current dataset.")
    if week_index < 0 or week_index >= len(result.weekly):
        raise IndexError(f"Week index {week_index} is outside the available weekly reports.")

    week = result.weekly.iloc[week_index]
    kpis = _kpis_for_week(result.kpis, week)
    summary = result.summary
    turbine_id = str(summary.get("turbine_id", "TURBINE")).upper()
    window_minutes = int(summary.get("window_minutes") or 0)
    overlap = float(summary.get("overlap") or 0.0)
    window_count = int(week.get("window_count") or len(kpis))
    stable_count = int(week.get("stable_count") or 0)
    on_count = int(kpis["is_on"].fillna(False).sum()) if "is_on" in kpis else 0
    duration_h = window_count * window_minutes * (1 - overlap) / 60.0

    start_utc = pd.Timestamp(kpis["time_utc"].min()) if not kpis.empty else pd.Timestamp(week["period_start_utc"])
    end_utc = _week_end_for_report(kpis, week, window_minutes)
    week_start = pd.Timestamp(week["period_start_utc"])

    lines = [
        f"=== WEEKLY REPORT - {turbine_id} ===",
        f"Range : {_format_utc(start_utc)} -> {_format_utc(end_utc)}",
        f"Duration : {duration_h:.1f} h | {window_count} windows of {window_minutes} min",
        f"Operating : {on_count}/{window_count} | Stable : {stable_count}",
        (
            "f0 reference : "
            f"AX={_format_number(week.get('f0_baseline_ax_hz'), 4)} Hz | "
            f"AY={_format_number(week.get('f0_baseline_ay_hz'), 4)} Hz"
        ),
        (
            "Drift : "
            f"AX={_format_number(week.get('f0_drift_ax_hz_per_day'), 5)} Hz/day | "
            f"AY={_format_number(week.get('f0_drift_ay_hz_per_day'), 5)} Hz/day"
        ),
        (
            "OMA FDD : "
            f"f0={_format_number(week.get('fdd_f0_hz'), 4)} Hz | "
            f"Damping={_format_number(week.get('zeta_fdd_pct'), 2)}%"
        ),
    ]

    date_token = week_start.strftime("%Y-%m-%d")
    return WeeklyTextReport(
        filename=f"report_{turbine_id.lower()}_{date_token}.txt",
        label=f"{date_token} to {pd.Timestamp(week['period_end_utc']).strftime('%Y-%m-%d')}",
        content="\n".join(lines) + "\n",
    )


def build_all_weekly_text_reports(result: AnalysisResult) -> list[WeeklyTextReport]:
    return [build_weekly_text_report(result, index) for index in range(len(result.weekly))]


def _kpis_for_week(kpis: pd.DataFrame, week: pd.Series) -> pd.DataFrame:
    if kpis.empty:
        return kpis.copy()
    start = pd.Timestamp(week["period_start_utc"])
    end = pd.Timestamp(week["period_end_utc"])
    times = pd.to_datetime(kpis["time_utc"], utc=True)
    return kpis[(times >= start) & (times < end)].copy()


def _week_end_for_report(kpis: pd.DataFrame, week: pd.Series, window_minutes: int) -> pd.Timestamp:
    if kpis.empty:
        return pd.Timestamp(week["period_end_utc"])
    end = pd.Timestamp(kpis["time_utc"].max()) + pd.Timedelta(minutes=window_minutes)
    return min(end, pd.Timestamp(week["period_end_utc"]))


def _format_utc(value: pd.Timestamp) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.strftime("%Y-%m-%d %H:%M:%S %z")


def _format_number(value: object, digits: int) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    if not np.isfinite(number):
        return "n/a"
    return f"{number:.{digits}f}"
