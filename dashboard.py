from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from wind_dashboard import AnalysisConfig, TURBINES, analyze_dataset
from wind_dashboard.analysis import discover_accelerometer_files, discover_scada_files


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ACCEL_DIR = BASE_DIR / "Echantillon 1Hz" / "data5"
DEFAULT_SCADA_DIR = BASE_DIR / "Echantillon 1Hz" / "SCADA"


st.set_page_config(
    page_title="Wind Turbine Surveillance",
    layout="wide",
    initial_sidebar_state="expanded",
)


st.markdown(
    """
    <style>
    .block-container { padding-top: 1.1rem; padding-bottom: 2rem; }
    [data-testid="stMetric"] { border: 1px solid #d8dee8; border-radius: 6px; padding: 0.7rem 0.8rem; background: #fbfcfe; }
    [data-testid="stMetricLabel"] p { color: #475569 !important; }
    [data-testid="stMetricValue"] { color: #0f172a !important; }
    [data-testid="stMetricDelta"] { color: #334155 !important; }
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 0.65rem; }
    h1 { font-size: 2.25rem !important; line-height: 1.15 !important; margin-bottom: 0.7rem !important; }
    h1, h2, h3 { letter-spacing: 0; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner="Computing weekly turbine KPIs...")
def load_weekly_result(
    accel_dir: str,
    scada_dir: str,
    turbine_id: str,
    window_minutes: int,
    overlap: float,
):
    accel_files = discover_accelerometer_files(accel_dir)
    scada_files = discover_scada_files(scada_dir)
    if not accel_files:
        raise ValueError(f"No accelerometer CSV or ZIP files found in {accel_dir}")

    config = AnalysisConfig(
        turbine_id=turbine_id,
        window_minutes=window_minutes,
        overlap=overlap,
    )
    return analyze_dataset(accel_files, scada_files, config)


def utc_date_bounds(series: pd.Series) -> tuple[pd.Timestamp, pd.Timestamp]:
    return pd.Timestamp(series.min()).tz_convert("UTC"), pd.Timestamp(series.max()).tz_convert("UTC")


def apply_filters(kpis: pd.DataFrame) -> pd.DataFrame:
    kpis = kpis.copy()
    start, end = utc_date_bounds(kpis["time_utc"])
    date_value = st.sidebar.date_input(
        "Date range",
        value=(start.date(), end.date()),
        min_value=start.date(),
        max_value=end.date(),
    )
    if isinstance(date_value, tuple) and len(date_value) == 2:
        start_dt = pd.Timestamp(date_value[0], tz="UTC")
        end_dt = pd.Timestamp(date_value[1], tz="UTC") + pd.Timedelta(days=1)
        kpis = kpis[(kpis["time_utc"] >= start_dt) & (kpis["time_utc"] < end_dt)]

    states = st.sidebar.multiselect(
        "Window state",
        ["Alert", "Warning", "OK", "Stopped"],
        default=["Alert", "Warning", "OK", "Stopped"],
    )
    if states:
        kpis = kpis[kpis["severity"].isin(states)]

    if st.sidebar.checkbox("Stable windows only", value=False):
        kpis = kpis[kpis["is_stable"]]

    return kpis


def render_summary(result, filtered: pd.DataFrame) -> None:
    summary = result.summary
    cols = st.columns(4)
    cols[0].metric("Turbine", summary["turbine_id"])
    cols[1].metric("Sample rate", f"{summary['sample_rate_hz']} Hz")
    cols[2].metric("Windows", f"{len(filtered):,}")
    cols[3].metric("Warnings", f"{int(filtered['alert_warning'].sum()):,}")

    cols = st.columns(4)
    cols[0].metric("f0 AX ref", _fmt(summary.get("f0_ref_ax_hz"), " Hz", 4))
    cols[1].metric("f0 AY ref", _fmt(summary.get("f0_ref_ay_hz"), " Hz", 4))
    cols[2].metric("AX drift", _fmt(summary.get("drift_ax_hz_per_day"), " Hz/day", 5))
    cols[3].metric("AY drift", _fmt(summary.get("drift_ay_hz_per_day"), " Hz/day", 5))


def render_main_plot(df: pd.DataFrame, show_thresholds: bool) -> None:
    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.045,
        row_heights=[0.28, 0.27, 0.24, 0.21],
        specs=[[{}], [{}], [{"secondary_y": True}], [{}]],
        subplot_titles=(
            "Natural frequency",
            "RMS and moving mean",
            "Operating context",
            "Alert score",
        ),
    )

    fig.add_trace(_markers(df, "f0_ax_hz", "f0 AX", "#2563eb", size=5), row=1, col=1)
    fig.add_trace(_markers(df, "f0_ay_hz", "f0 AY", "#15803d", size=5), row=1, col=1)
    fig.add_trace(_line(df, "f0_ax_trend_hz", "AX trend", "#1d4ed8"), row=1, col=1)
    fig.add_trace(_line(df, "f0_ay_trend_hz", "AY trend", "#166534"), row=1, col=1)

    if show_thresholds:
        for column, name, color in (
            ("f0_ax_low_hz", "AX low", "#93c5fd"),
            ("f0_ax_high_hz", "AX high", "#93c5fd"),
            ("f0_ay_low_hz", "AY low", "#86efac"),
            ("f0_ay_high_hz", "AY high", "#86efac"),
        ):
            fig.add_trace(_line(df, column, name, color, dash="dot"), row=1, col=1)

    alert_f0 = df[df["alert_f0"]]
    if not alert_f0.empty:
        fig.add_trace(_markers(alert_f0, "f0_ax_hz", "f0 alert", "#dc2626", size=10), row=1, col=1)

    fig.add_trace(_markers(df, "rms_ax", "RMS AX", "#2563eb", size=5), row=2, col=1)
    fig.add_trace(_markers(df, "rms_ay", "RMS AY", "#15803d", size=5), row=2, col=1)
    fig.add_trace(_line(df, "ewma_ax", "AX moving mean", "#1d4ed8"), row=2, col=1)
    fig.add_trace(_line(df, "ewma_ay", "AY moving mean", "#166534"), row=2, col=1)
    if show_thresholds:
        fig.add_trace(_line(df, "p95_ax", "AX P95", "#f59e0b", dash="dash"), row=2, col=1)
        fig.add_trace(_line(df, "p99_ax", "AX P99.5", "#ef4444", dash="dash"), row=2, col=1)
        fig.add_trace(_line(df, "p95_ay", "AY P95", "#fbbf24", dash="dot"), row=2, col=1)
        fig.add_trace(_line(df, "p99_ay", "AY P99.5", "#f87171", dash="dot"), row=2, col=1)

    alert_rms = df[df["alert_rms"]]
    if not alert_rms.empty:
        fig.add_trace(_markers(alert_rms, "rms_ax", "RMS alert", "#dc2626", size=10), row=2, col=1)

    fig.add_trace(_line(df, "power_kw", "Power kW", "#0f172a"), row=3, col=1, secondary_y=False)
    fig.add_trace(_line(df, "wind_ms", "Wind m/s", "#0891b2"), row=3, col=1, secondary_y=True)
    fig.add_trace(_line(df, "rpm", "RPM", "#7c3aed"), row=3, col=1, secondary_y=True)

    fig.add_trace(
        go.Scatter(
            x=df["time_utc"],
            y=df["score"],
            mode="markers+lines",
            name="Score",
            marker={
                "size": 7,
                "color": df["score"],
                "colorscale": [[0, "#16a34a"], [0.35, "#f59e0b"], [1, "#dc2626"]],
                "cmin": 0,
                "cmax": 3,
            },
            line={"color": "#64748b", "width": 1},
        ),
        row=4,
        col=1,
    )

    _add_stopped_regions(fig, df)

    fig.update_yaxes(title_text="Hz", row=1, col=1)
    fig.update_yaxes(title_text="m/s²", row=2, col=1)
    fig.update_yaxes(title_text="kW", row=3, col=1, secondary_y=False)
    fig.update_yaxes(title_text="m/s, rpm", row=3, col=1, secondary_y=True)
    fig.update_yaxes(title_text="0-3", row=4, col=1, range=[-0.2, 3.2])
    fig.update_layout(
        height=920,
        margin={"l": 32, "r": 28, "t": 48, "b": 32},
        hovermode="x unified",
        legend={"orientation": "h", "y": -0.08},
    )
    st.plotly_chart(fig, width="stretch")


def render_overlay(df: pd.DataFrame) -> None:
    metric_options = {
        "RPM": "rpm",
        "Power": "power_kw",
        "Wind": "wind_ms",
        "f0 AX": "f0_ax_hz",
        "f0 AY": "f0_ay_hz",
        "RMS AX": "rms_ax",
        "RMS AY": "rms_ay",
        "1P RMS": "rms_1p_ax",
        "3P RMS": "rms_3p_ax",
    }
    selected = st.multiselect(
        "Overlay metrics",
        list(metric_options),
        default=["RPM", "Power", "Wind", "f0 AX", "RMS AX"],
    )
    fig = go.Figure()
    palette = ["#2563eb", "#0f172a", "#0891b2", "#15803d", "#7c3aed", "#f97316", "#dc2626"]
    for idx, label in enumerate(selected):
        column = metric_options[label]
        y_raw = df[column].astype(float)
        y_norm = _normalize(y_raw)
        fig.add_trace(
            go.Scatter(
                x=df["time_utc"],
                y=y_norm,
                mode="lines",
                name=label,
                customdata=np.round(y_raw.to_numpy(dtype=float), 5),
                hovertemplate=f"{label}: %{{customdata}}<extra></extra>",
                line={"color": palette[idx % len(palette)], "width": 1.8},
            )
        )
    fig.update_layout(
        height=330,
        yaxis_title="Normalized value",
        margin={"l": 30, "r": 18, "t": 10, "b": 28},
        hovermode="x unified",
        legend={"orientation": "h", "y": -0.18},
    )
    st.plotly_chart(fig, width="stretch")


def render_psd(result, df: pd.DataFrame) -> None:
    if df.empty:
        return
    axis = st.radio("PSD axis", ["AX", "AY"], horizontal=True)
    indices = df.index.to_numpy()
    psd = result.psd_ax_db if axis == "AX" else result.psd_ay_db
    psd = psd[indices]

    fig = go.Figure(
        data=go.Heatmap(
            x=df["time_utc"],
            y=result.psd_frequencies_hz,
            z=psd.T,
            colorscale="Viridis",
            colorbar={"title": "dB"},
        )
    )
    fig.update_layout(
        height=430,
        yaxis_title="Frequency (Hz)",
        margin={"l": 32, "r": 28, "t": 10, "b": 28},
    )
    st.plotly_chart(fig, width="stretch")


def render_alert_table(df: pd.DataFrame) -> None:
    problem_df = df[df["severity"].isin(["Alert", "Warning"])].copy()
    columns = [
        "time_utc",
        "severity",
        "score",
        "reasons",
        "f0_ax_hz",
        "f0_ay_hz",
        "rms_ax",
        "rms_ay",
        "power_kw",
        "wind_ms",
        "rpm",
    ]
    table = problem_df[columns].sort_values(["time_utc", "score"], ascending=[True, False])
    st.dataframe(table, width="stretch", height=360)
    st.download_button(
        "Download alert CSV",
        table.to_csv(index=False).encode("utf-8"),
        file_name="wind_turbine_alerts.csv",
        mime="text/csv",
    )


def _markers(df: pd.DataFrame, column: str, name: str, color: str, size: int) -> go.Scatter:
    return go.Scatter(
        x=df["time_utc"],
        y=df[column],
        mode="markers",
        name=name,
        marker={"color": color, "size": size, "opacity": 0.75},
    )


def _line(df: pd.DataFrame, column: str, name: str, color: str, dash: str = "solid") -> go.Scatter:
    visible = "legendonly" if name in {"AX P95", "AX P99.5", "AY P95", "AY P99.5"} else True
    return go.Scatter(
        x=df["time_utc"],
        y=df[column],
        mode="lines",
        name=name,
        visible=visible,
        line={"color": color, "width": 1.7, "dash": dash},
    )


def _add_stopped_regions(fig: go.Figure, df: pd.DataFrame) -> None:
    if df.empty or "Stopped" not in set(df["severity"]):
        return
    fill_color, opacity = _stopped_region_style()
    stopped = (df["severity"] == "Stopped").to_numpy()
    transitions = np.diff(np.r_[False, stopped, False].astype(int))
    starts = np.where(transitions == 1)[0]
    stops = np.where(transitions == -1)[0] - 1
    for start, stop in list(zip(starts, stops, strict=True))[:60]:
        fig.add_vrect(
            x0=df["time_utc"].iloc[start],
            x1=df["time_utc"].iloc[min(stop + 1, len(df) - 1)],
            fillcolor=fill_color,
            opacity=opacity,
            layer="below",
            line_width=0,
        )


def _stopped_region_style() -> tuple[str, float]:
    base_theme = st.get_option("theme.base") or "light"
    if str(base_theme).lower() == "light":
        return "#334155", 0.28
    return "#f8fafc", 0.42


def _normalize(values: pd.Series) -> np.ndarray:
    arr = values.to_numpy(dtype=float)
    ok = np.isfinite(arr)
    out = np.full_like(arr, np.nan, dtype=float)
    if ok.sum() < 2:
        return out
    lo, hi = np.nanpercentile(arr[ok], [5, 95])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = np.nanmin(arr[ok]), np.nanmax(arr[ok])
    if hi <= lo:
        out[ok] = 0.5
    else:
        out[ok] = np.clip((arr[ok] - lo) / (hi - lo), 0, 1)
    return out


def _fmt(value, suffix: str, digits: int) -> str:
    if value is None or not np.isfinite(float(value)):
        return "n/a"
    return f"{float(value):.{digits}f}{suffix}"


def main() -> None:
    st.title("Wind Turbine Surveillance")

    st.sidebar.header("Dataset")
    turbine_id = st.sidebar.selectbox("Turbine", list(TURBINES), index=list(TURBINES).index("w005"))
    accel_dir = st.sidebar.text_input("Accelerometer folder", str(DEFAULT_ACCEL_DIR))
    scada_dir = st.sidebar.text_input("SCADA folder", str(DEFAULT_SCADA_DIR))
    window_minutes = st.sidebar.selectbox("Window length", [5, 10, 20, 30], index=1)
    overlap = st.sidebar.slider("Window overlap", min_value=0.0, max_value=0.8, value=0.5, step=0.1)
    show_thresholds = st.sidebar.checkbox("Show thresholds", value=True)

    try:
        result = load_weekly_result(accel_dir, scada_dir, turbine_id, window_minutes, overlap)
    except Exception as exc:
        st.error(str(exc))
        return

    kpis = result.kpis.copy()
    kpis.index = np.arange(len(kpis))
    filtered = apply_filters(kpis)
    if filtered.empty:
        st.warning("No windows match the current filters.")
        return

    render_summary(result, filtered)
    render_main_plot(filtered, show_thresholds)

    left, right = st.columns([0.48, 0.52])
    with left:
        st.subheader("Metric Overlay")
        render_overlay(filtered)
    with right:
        st.subheader("PSD")
        render_psd(result, filtered)

    st.subheader("Alerts")
    render_alert_table(filtered)


if __name__ == "__main__":
    main()
