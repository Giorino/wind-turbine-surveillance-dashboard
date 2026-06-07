from __future__ import annotations

from datetime import timedelta
from html import escape
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
DEFAULT_REFERENCE_DIR = BASE_DIR / "reference-matlab-files"


st.set_page_config(
    page_title="Wind Turbine Surveillance",
    layout="wide",
    initial_sidebar_state="expanded",
)


THEMES = {
    "Light": {
        "app_bg": "#f8fafc",
        "sidebar_bg": "#eef2f7",
        "panel_bg": "#ffffff",
        "plot_bg": "#ffffff",
        "border": "#d8dee8",
        "text": "#0f172a",
        "muted": "#475569",
        "grid": "#e2e8f0",
        "input_bg": "#ffffff",
        "stopped": "#334155",
        "stopped_opacity": 0.18,
        "plotly_template": "plotly_white",
    },
    "Dark": {
        "app_bg": "#0f1117",
        "sidebar_bg": "#272832",
        "panel_bg": "#171b24",
        "plot_bg": "#0f1117",
        "border": "#2f3746",
        "text": "#f8fafc",
        "muted": "#cbd5e1",
        "grid": "#2f3746",
        "input_bg": "#111827",
        "stopped": "#94a3b8",
        "stopped_opacity": 0.20,
        "plotly_template": "plotly_dark",
    },
}


def apply_theme(theme_name: str) -> dict[str, object]:
    theme = THEMES[theme_name]
    st.markdown(
        f"""
        <style>
        html, body, [data-testid="stAppViewContainer"] {{
            background: {theme["app_bg"]};
            color: {theme["text"]};
        }}
        [data-testid="stSidebar"] {{
            background: {theme["sidebar_bg"]};
        }}
        [data-testid="stHeader"] {{
            background: transparent;
        }}
        .block-container {{
            padding-top: 1.1rem;
            padding-bottom: 2rem;
        }}
        [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{
            gap: 0.65rem;
        }}
        [data-testid="stMarkdownContainer"],
        [data-testid="stWidgetLabel"],
        [data-testid="stMarkdownContainer"] p,
        h1, h2, h3 {{
            color: {theme["text"]} !important;
            letter-spacing: 0;
        }}
        h1 {{
            font-size: 2.25rem !important;
            line-height: 1.15 !important;
            margin-bottom: 0.7rem !important;
        }}
        input, textarea, [data-baseweb="select"] > div {{
            background: {theme["input_bg"]} !important;
            color: {theme["text"]} !important;
            border-color: {theme["border"]} !important;
        }}
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(155px, 1fr));
            gap: 0.8rem;
            margin: 0.4rem 0 1.2rem;
        }}
        .summary-card {{
            border: 1px solid {theme["border"]};
            border-radius: 6px;
            padding: 0.75rem 0.85rem;
            background: {theme["panel_bg"]};
            min-height: 86px;
        }}
        .summary-label {{
            color: {theme["muted"]};
            font-size: 0.82rem;
            line-height: 1.2;
            margin-bottom: 0.45rem;
        }}
        .summary-value {{
            color: {theme["text"]};
            font-size: clamp(1.35rem, 2.1vw, 1.85rem);
            line-height: 1.1;
            overflow-wrap: anywhere;
        }}
        .js-plotly-plot svg text {{
            fill: {theme["text"]} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    return theme


@st.cache_data(show_spinner="Computing weekly turbine KPIs...")
def load_weekly_result(
    accel_dir: str,
    scada_dir: str,
    turbine_id: str,
    window_minutes: int,
    overlap: float,
    reference_dir: str,
    use_reference_files: bool,
):
    accel_files = discover_accelerometer_files(accel_dir)
    scada_files = discover_scada_files(scada_dir)
    if not accel_files:
        raise ValueError(f"No accelerometer CSV or ZIP files found in {accel_dir}")

    config = AnalysisConfig(
        turbine_id=turbine_id,
        window_minutes=window_minutes,
        overlap=overlap,
        reference_dir=reference_dir,
        use_reference_files=use_reference_files,
    )
    return analyze_dataset(accel_files, scada_files, config)


def utc_date_bounds(series: pd.Series) -> tuple[pd.Timestamp, pd.Timestamp]:
    return pd.Timestamp(series.min()).tz_convert("UTC"), pd.Timestamp(series.max()).tz_convert("UTC")


def apply_filters(kpis: pd.DataFrame) -> pd.DataFrame:
    kpis = kpis.copy()
    start, end = utc_date_bounds(kpis["time_utc"])
    start_naive = start.floor("min").to_pydatetime().replace(tzinfo=None)
    end_naive = end.ceil("min").to_pydatetime().replace(tzinfo=None)
    if start_naive >= end_naive:
        return kpis

    selected = st.sidebar.slider(
        "Visible time range (UTC)",
        min_value=start_naive,
        max_value=end_naive,
        value=(start_naive, end_naive),
        format="MM/DD HH:mm",
        step=timedelta(minutes=1),
    )
    if isinstance(selected, tuple) and len(selected) == 2:
        start_dt = pd.Timestamp(selected[0], tz="UTC")
        end_dt = pd.Timestamp(selected[1], tz="UTC")
        kpis = kpis[(kpis["time_utc"] >= start_dt) & (kpis["time_utc"] <= end_dt)]

    return kpis


def render_summary(result, filtered: pd.DataFrame) -> None:
    summary = result.summary
    latest_week = result.weekly.iloc[-1] if not result.weekly.empty else None
    items = [
        ("Turbine", summary["turbine_id"]),
        ("Sample rate", f"{summary['sample_rate_hz']} Hz"),
        ("Windows", f"{len(filtered):,}"),
        ("Stable", f"{int(filtered['is_stable'].sum()):,}"),
        ("Baseline source", _baseline_source_label(summary.get("reference_source", "internal"))),
        ("f0 AX baseline", _fmt(summary.get("f0_ref_ax_hz"), " Hz", 4)),
        ("f0 AY baseline", _fmt(summary.get("f0_ref_ay_hz"), " Hz", 4)),
        ("Weekly zeta", _fmt(None if latest_week is None else latest_week.get("zeta_fdd_pct"), "%", 2)),
        ("AX drift", _fmt(summary.get("drift_ax_hz_per_day"), " Hz/day", 5)),
        ("AY drift", _fmt(summary.get("drift_ay_hz_per_day"), " Hz/day", 5)),
    ]
    cards = "".join(
        f'<div class="summary-card"><div class="summary-label">{escape(str(label))}</div>'
        f'<div class="summary-value">{escape(str(value))}</div></div>'
        for label, value in items
    )
    st.markdown(f'<div class="summary-grid">{cards}</div>', unsafe_allow_html=True)


def render_drift_plot(result, df: pd.DataFrame, theme: dict[str, object]) -> None:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.075,
        row_heights=[0.62, 0.38],
        specs=[[{}], [{"secondary_y": True}]],
        subplot_titles=(
            "Frequency Drift",
            "Weekly Baseline And Zeta",
        ),
    )

    stable = df[df["is_stable"]].copy()
    fig.add_trace(_markers(stable, "f0_ax_hz", "f0 AX", "#2563eb", size=5), row=1, col=1)
    fig.add_trace(_markers(stable, "f0_ay_hz", "f0 AY", "#15803d", size=5), row=1, col=1)
    fig.add_trace(_line(df, "f0_ax_trend_hz", "AX trend", "#1d4ed8"), row=1, col=1)
    fig.add_trace(_line(df, "f0_ay_trend_hz", "AY trend", "#166534"), row=1, col=1)

    if not df.empty:
        x0, x1 = df["time_utc"].min(), df["time_utc"].max()
        for value, name, color in (
            (result.summary.get("f0_ref_ax_hz"), "AX baseline", "#93c5fd"),
            (result.summary.get("f0_ref_ay_hz"), "AY baseline", "#86efac"),
        ):
            if value is not None and np.isfinite(float(value)):
                fig.add_trace(
                    go.Scatter(
                        x=[x0, x1],
                        y=[float(value), float(value)],
                        mode="lines",
                        name=name,
                        line={"color": color, "width": 1.4, "dash": "dash"},
                    ),
                    row=1,
                    col=1,
                )

    weekly = result.weekly.copy()
    if not weekly.empty and not df.empty:
        visible_start = df["time_utc"].min()
        visible_end = df["time_utc"].max()
        weekly = weekly[
            (weekly["week_end_utc"] >= visible_start) & (weekly["week_start_utc"] <= visible_end)
        ]
    if not weekly.empty:
        fig.add_trace(
            go.Scatter(
                x=weekly["week_start_utc"],
                y=weekly["f0_baseline_ax_hz"],
                mode="markers+lines",
                name="Weekly f0 AX",
                marker={"color": "#2563eb", "size": 8},
                line={"color": "#2563eb", "width": 1.8},
            ),
            row=2,
            col=1,
            secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(
                x=weekly["week_start_utc"],
                y=weekly["f0_baseline_ay_hz"],
                mode="markers+lines",
                name="Weekly f0 AY",
                marker={"color": "#15803d", "size": 8},
                line={"color": "#15803d", "width": 1.8},
            ),
            row=2,
            col=1,
            secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(
                x=weekly["week_start_utc"],
                y=weekly["zeta_fdd_pct"],
                mode="markers+lines",
                name="Zeta FDD",
                marker={"color": "#7c3aed", "size": 8},
                line={"color": "#7c3aed", "width": 1.8},
            ),
            row=2,
            col=1,
            secondary_y=True,
        )

    _add_stopped_regions(fig, df, theme)

    fig.update_yaxes(title_text="Hz", row=1, col=1)
    fig.update_yaxes(title_text="Hz", row=2, col=1, secondary_y=False)
    fig.update_yaxes(title_text="%", row=2, col=1, secondary_y=True)
    _style_plot(
        fig,
        theme,
        height=620,
        margin={"l": 32, "r": 28, "t": 48, "b": 32},
        hovermode="x unified",
        legend={"orientation": "h", "y": -0.08},
    )
    st.plotly_chart(fig, width="stretch")


def render_operating_context(df: pd.DataFrame, theme: dict[str, object]) -> None:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(_line(df, "power_kw", "Generated power", str(theme["text"])), secondary_y=False)
    fig.add_trace(_line(df, "wind_ms", "Wind", "#0891b2"), secondary_y=True)
    fig.add_trace(_line(df, "rpm", "RPM", "#7c3aed"), secondary_y=True)
    _add_stopped_regions(fig, df, theme)
    fig.update_yaxes(title_text="kW", secondary_y=False)
    fig.update_yaxes(title_text="m/s, rpm", secondary_y=True)
    _style_plot(
        fig,
        theme,
        height=340,
        margin={"l": 32, "r": 28, "t": 10, "b": 28},
        hovermode="x unified",
        legend={"orientation": "h", "y": -0.16},
    )
    st.plotly_chart(fig, width="stretch")


def render_psd(result, df: pd.DataFrame, theme: dict[str, object]) -> None:
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
            colorbar={
                "title": {"text": "dB", "font": {"color": str(theme["text"])}},
                "tickfont": {"color": str(theme["text"])},
            },
        )
    )
    _style_plot(
        fig,
        theme,
        height=430,
        margin={"l": 32, "r": 28, "t": 10, "b": 28},
    )
    fig.update_layout(yaxis_title="Frequency (Hz)")
    st.plotly_chart(fig, width="stretch")


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


def _add_stopped_regions(fig: go.Figure, df: pd.DataFrame, theme: dict[str, object]) -> None:
    if df.empty:
        return
    if "is_on" in df:
        stopped = ~df["is_on"].fillna(False).to_numpy(dtype=bool)
    elif "severity" in df:
        stopped = (df["severity"] == "Stopped").to_numpy()
    else:
        return
    if not stopped.any():
        return
    fill_color, opacity = _stopped_region_style(theme)
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


def _stopped_region_style(theme: dict[str, object]) -> tuple[str, float]:
    return str(theme["stopped"]), float(theme["stopped_opacity"])


def _style_plot(
    fig: go.Figure,
    theme: dict[str, object],
    *,
    height: int,
    margin: dict[str, int],
    hovermode: str | None = None,
    legend: dict[str, object] | None = None,
) -> None:
    layout: dict[str, object] = {
        "height": height,
        "margin": margin,
        "template": str(theme["plotly_template"]),
        "paper_bgcolor": str(theme["app_bg"]),
        "plot_bgcolor": str(theme["plot_bg"]),
        "font": {"color": str(theme["text"])},
    }
    if hovermode:
        layout["hovermode"] = hovermode
    if legend:
        layout["legend"] = legend
    fig.update_layout(**layout)
    fig.update_xaxes(
        color=str(theme["text"]),
        gridcolor=str(theme["grid"]),
        zerolinecolor=str(theme["grid"]),
        tickfont={"color": str(theme["text"])},
        title_font={"color": str(theme["text"])},
    )
    fig.update_yaxes(
        color=str(theme["text"]),
        gridcolor=str(theme["grid"]),
        zerolinecolor=str(theme["grid"]),
        tickfont={"color": str(theme["text"])},
        title_font={"color": str(theme["text"])},
    )
    fig.update_annotations(font={"color": str(theme["text"])})


def _baseline_source_label(source: object) -> str:
    return "Fixed file" if str(source).lower() == "file" else "Current data"


def _fmt(value, suffix: str, digits: int) -> str:
    if value is None or not np.isfinite(float(value)):
        return "n/a"
    return f"{float(value):.{digits}f}{suffix}"


def main() -> None:
    st.sidebar.header("Dataset")
    theme_name = st.sidebar.radio("Theme", list(THEMES), horizontal=True)
    theme = apply_theme(theme_name)

    st.title("Wind Turbine Surveillance")

    turbine_id = st.sidebar.selectbox("Turbine", list(TURBINES), index=list(TURBINES).index("w005"))
    accel_dir = st.sidebar.text_input("Accelerometer folder", str(DEFAULT_ACCEL_DIR))
    scada_dir = st.sidebar.text_input("SCADA folder", str(DEFAULT_SCADA_DIR))
    reference_dir = st.sidebar.text_input("Baseline folder", str(DEFAULT_REFERENCE_DIR))
    use_reference_files = st.sidebar.checkbox("Use fixed baseline files", value=True)
    window_minutes = st.sidebar.selectbox("Window length", [5, 10, 20, 30], index=1)
    overlap = st.sidebar.slider("Window overlap", min_value=0.0, max_value=0.8, value=0.5, step=0.1)

    try:
        result = load_weekly_result(
            accel_dir,
            scada_dir,
            turbine_id,
            window_minutes,
            overlap,
            reference_dir,
            use_reference_files,
        )
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
    render_drift_plot(result, filtered, theme)

    st.subheader("Wind, RPM, Power")
    render_operating_context(filtered, theme)

    st.subheader("PSD")
    render_psd(result, filtered, theme)


if __name__ == "__main__":
    main()
