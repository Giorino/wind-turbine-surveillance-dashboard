from __future__ import annotations

from datetime import timedelta
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from wind_dashboard import (
    AnalysisConfig,
    accelerometer_dir_for_turbine,
    analyze_dataset,
    discover_turbine_ids,
    scada_dir_for_dataset,
)
from wind_dashboard.analysis import discover_accelerometer_files, discover_scada_files
from wind_dashboard.reports import build_weekly_text_report


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET_DIR = BASE_DIR / "dataset"
ANALYSIS_CACHE_VERSION = 9


st.set_page_config(
    page_title="Wind Turbine Surveillance",
    layout="wide",
    initial_sidebar_state="expanded",
)


THEME = {
    "app_bg": "#f8fafc",
    "sidebar_bg": "#eef2f7",
    "panel_bg": "#ffffff",
    "plot_bg": "#ffffff",
    "border": "#d8dee8",
    "text": "#0f172a",
    "muted": "#475569",
    "grid": "#e2e8f0",
    "input_bg": "#ffffff",
    "code_bg": "#f1f5f9",
    "stopped": "#334155",
    "stopped_opacity": 0.18,
    "plotly_template": "plotly_white",
}


def apply_theme() -> dict[str, object]:
    theme = THEME
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
        [data-testid="stButton"] button,
        [data-testid="stDownloadButton"] button {{
            background: {theme["panel_bg"]} !important;
            color: {theme["text"]} !important;
            border: 1px solid {theme["border"]} !important;
            border-radius: 6px !important;
        }}
        [data-testid="stButton"] button *,
        [data-testid="stDownloadButton"] button * {{
            color: {theme["text"]} !important;
        }}
        [data-testid="stButton"] button:hover,
        [data-testid="stDownloadButton"] button:hover {{
            background: {theme["input_bg"]} !important;
            border-color: {theme["muted"]} !important;
        }}
        [data-testid="stExpander"] details {{
            background: {theme["panel_bg"]} !important;
            border: 1px solid {theme["border"]} !important;
            border-radius: 6px !important;
        }}
        [data-testid="stExpander"] summary,
        [data-testid="stExpander"] summary *,
        [data-testid="stExpander"] [data-testid="stMarkdownContainer"],
        [data-testid="stExpander"] [data-testid="stMarkdownContainer"] p {{
            color: {theme["text"]} !important;
        }}
        [data-testid="stCode"],
        [data-testid="stCode"] pre {{
            background: {theme["code_bg"]} !important;
            color: {theme["text"]} !important;
        }}
        [data-testid="stCode"] code,
        [data-testid="stCode"] span {{
            background: transparent !important;
            color: {theme["text"]} !important;
        }}
        [data-testid="stCode"] {{
            border: 1px solid {theme["border"]} !important;
            border-radius: 6px !important;
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
    analysis_cache_version: int,
):
    _ = analysis_cache_version
    accel_files = discover_accelerometer_files(accel_dir)
    scada_files = discover_scada_files(scada_dir)
    if not accel_files:
        raise ValueError(f"No accelerometer CSV or ZIP files found in {accel_dir}")

    config = AnalysisConfig(
        turbine_id=turbine_id,
        window_minutes=window_minutes,
        overlap=overlap,
        reference_dir=None,
        use_reference_files=False,
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
        ("f0 AX baseline", _fmt(summary.get("f0_ref_ax_hz"), " Hz", 4)),
        ("f0 AY baseline", _fmt(summary.get("f0_ref_ay_hz"), " Hz", 4)),
        ("Weekly zeta", _fmt(None if latest_week is None else latest_week.get("zeta_fdd_pct"), "%", 2)),
        ("Weekly AX shift", _fmt(None if latest_week is None else latest_week.get("f0_shift_ax_hz"), " Hz/week", 5)),
        ("Weekly AY shift", _fmt(None if latest_week is None else latest_week.get("f0_shift_ay_hz"), " Hz/week", 5)),
    ]
    cards = "".join(
        f'<div class="summary-card"><div class="summary-label">{escape(str(label))}</div>'
        f'<div class="summary-value">{escape(str(value))}</div></div>'
        for label, value in items
    )
    st.markdown(f'<div class="summary-grid">{cards}</div>', unsafe_allow_html=True)


def render_weekly_report_download(result) -> None:
    if result.weekly.empty:
        return

    st.subheader("Weekly Report")
    options = [
        (
            index,
            f"{pd.Timestamp(row['week_start_utc']).strftime('%Y-%m-%d')} to "
            f"{pd.Timestamp(row['week_end_utc']).strftime('%Y-%m-%d')}",
        )
        for index, row in result.weekly.iterrows()
    ]
    selected_index = st.selectbox(
        "Report period",
        [index for index, _ in options],
        format_func=lambda index: dict(options)[index],
    )
    report = build_weekly_text_report(result, int(selected_index))
    st.download_button(
        f"Download {report.filename}",
        data=report.content.encode("utf-8"),
        file_name=report.filename,
        mime="text/plain",
    )
    with st.expander("Preview report"):
        st.code(report.content, language="text")


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

    valid_ax = df[df["f0_ax_hz"].notna()].copy()
    valid_ay = df[df["f0_ay_hz"].notna()].copy()
    fig.add_trace(_markers(valid_ax, "f0 AX", "#93c5fd", "f0_ax_hz", size=4, opacity=0.38), row=1, col=1)
    fig.add_trace(_markers(valid_ay, "f0 AY", "#86efac", "f0_ay_hz", size=4, opacity=0.38), row=1, col=1)
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


def render_modal_diagram(result, theme: dict[str, object]) -> None:
    modal = getattr(result, "modal", {})
    if not modal or not modal.get("available"):
        st.info(str(modal.get("reason", "Modal diagram is not available for this dataset.")))
        return

    freqs = np.asarray(modal["frequencies_hz"], dtype=float)
    threshold = float(modal["threshold_db"])
    fdd_f0 = modal.get("fdd_f0_hz")
    fdd_zeta = modal.get("fdd_zeta_pct")
    # title = (
    #     f"[{result.summary['turbine_id']}] Modal Diagram | "
    #     f"AX P90: {_modal_num(modal['ax'].get('f0_hz'), 4)}Hz | "
    #     f"AY P90: {_modal_num(modal['ay'].get('f0_hz'), 4)}Hz | "
    #     f"FDD: f0={_modal_num(fdd_f0, 4)}Hz zeta={_modal_num(fdd_zeta, 2)}%"
    # )
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=False,
        vertical_spacing=0.16,
        subplot_titles=(
            _modal_subplot_title(result, modal, "ax", "AX (fore-aft)"),
            _modal_subplot_title(result, modal, "ay", "AY (lateral)"),
        ),
    )

    _add_modal_axis(fig, modal, freqs, threshold, "ax", 1, "#2563eb", show_legend=True)
    _add_modal_axis(fig, modal, freqs, threshold, "ay", 2, "#16a34a", show_legend=False)

    match_ax = "YES" if modal.get("fdd_match_ax") else "NO"
    match_ay = "YES" if modal.get("fdd_match_ay") else "NO"
    fig.add_annotation(
        x=0.99,
        y=-0.10,
        xref="paper",
        yref="paper",
        text=f"Match ref: AX={match_ax} | AY={match_ay}",
        showarrow=False,
        align="right",
        bgcolor=str(theme["panel_bg"]),
        bordercolor=str(theme["border"]),
        borderwidth=1,
        font={"color": str(theme["text"]), "size": 12},
    )
    _style_plot(
        fig,
        theme,
        height=900,
        margin={"l": 42, "r": 76, "t": 76, "b": 82},
        legend={"orientation": "h", "y": 1.06, "x": 0.01, "groupclick": "togglegroup"},
    )
    #fig.update_layout(title={"text": title, "x": 0.5, "xanchor": "center"})
    fig.update_xaxes(title_text="Frequency (Hz)", range=[0.10, min(0.80, result.sample_rate_hz / 2)])
    fig.update_yaxes(title_text="Amplitude (dB)", range=[-160, -40])
    st.plotly_chart(fig, width="stretch")


def _add_modal_axis(
    fig: go.Figure,
    modal: dict[str, object],
    freqs: np.ndarray,
    threshold: float,
    axis: str,
    row: int,
    axis_color: str,
    *,
    show_legend: bool,
) -> None:
    amplitudes = np.asarray(modal[f"{axis}_db"], dtype=float)
    env_freqs = np.asarray(modal["env_frequencies_hz"], dtype=float)
    env = modal[f"{axis}_envelope"]
    p10 = np.asarray(env["p10"], dtype=float)
    scatter_x = np.tile(freqs, amplitudes.shape[0])
    scatter_y = amplitudes.reshape(-1)

    valid_floor = np.isfinite(env_freqs) & np.isfinite(p10)
    if valid_floor.sum() >= 2:
        floor_by_freq = np.interp(freqs, env_freqs[valid_floor], p10[valid_floor])
        scatter_floor = np.tile(floor_by_freq, amplitudes.shape[0])
        # A global MATLAB-style percentile cutoff becomes a flat floor on 1 Hz data.
        shown = np.isfinite(scatter_y) & (scatter_y >= scatter_floor)
    else:
        shown = np.isfinite(scatter_y) & (scatter_y > threshold)

    if shown.any():
        vmin = float(np.nanpercentile(scatter_y[shown], 1))
        vmax = float(np.nanmax(scatter_y[shown]))
        fig.add_trace(
            go.Scattergl(
                x=scatter_x[shown],
                y=scatter_y[shown],
                mode="markers",
                name="FFT scatter",
                showlegend=show_legend,
                legendgroup="modal-fft-scatter",
                marker={
                    "color": scatter_y[shown],
                    "colorscale": "Jet",
                    "cmin": vmin,
                    "cmax": vmax,
                    "size": 3,
                    "opacity": 0.55,
                    "colorbar": {
                        "title": {"text": "dB"},
                        "len": 0.38,
                        "y": 0.78 if row == 1 else 0.22,
                    },
                },
                hovertemplate="Frequency=%{x:.4f} Hz<br>Amplitude=%{y:.1f} dB<extra></extra>",
            ),
            row=row,
            col=1,
        )

    for name, values, color, dash, width, legend_group in (
        ("P10 (floor)", env["p10"], "#e5e7eb", "solid", 1.1, "modal-p10"),
        ("P50 (median)", env["p50"], "#facc15", "solid", 1.2, "modal-p50"),
        ("P90 smooth (estimator)", env["p90_smooth"], "#111827", "dash", 1.6, "modal-p90"),
    ):
        fig.add_trace(
            go.Scattergl(
                x=env_freqs,
                y=np.asarray(values, dtype=float),
                mode="lines",
                name=name,
                showlegend=show_legend,
                legendgroup=legend_group,
                line={"color": color, "dash": dash, "width": width},
                hovertemplate="Frequency=%{x:.4f} Hz<br>Amplitude=%{y:.1f} dB<extra></extra>",
            ),
            row=row,
            col=1,
        )

    params = modal[axis]
    for value, color, dash, width in (
        (params.get("f0_hz"), axis_color, "solid", 2.0),
        (params.get("f1_hz"), axis_color, "dot", 1.2),
        (params.get("f2_hz"), axis_color, "dot", 1.2),
        (modal.get("fdd_f0_hz"), "#ef4444", "dash", 1.6),
    ):
        if value is not None and np.isfinite(float(value)):
            fig.add_trace(
                go.Scatter(
                    x=[float(value), float(value)],
                    y=[-160, -40],
                    mode="lines",
                    showlegend=False,
                    line={"color": color, "dash": dash, "width": width},
                    hoverinfo="skip",
                ),
                row=row,
                col=1,
            )

    label_x = float(np.nanmin(freqs) + 0.03 * (np.nanmax(freqs) - np.nanmin(freqs)))
    if params.get("f0_hz") is not None:
        fig.add_annotation(
            x=label_x,
            y=-128,
            text=f"P90: f0={_modal_num(params.get('f0_hz'), 4)}Hz  zeta={_modal_num(params.get('zeta_pct'), 2)}%",
            showarrow=False,
            bgcolor="#ffffff",
            bordercolor=axis_color,
            borderwidth=1,
            font={"color": axis_color, "size": 12},
            row=row,
            col=1,
        )
    if modal.get("fdd_f0_hz") is not None:
        fig.add_annotation(
            x=label_x,
            y=-139,
            text=f"FDD: f0={_modal_num(modal.get('fdd_f0_hz'), 4)}Hz  zeta={_modal_num(modal.get('fdd_zeta_pct'), 2)}%",
            showarrow=False,
            bgcolor="#ffffff",
            bordercolor="#ef4444",
            borderwidth=1,
            font={"color": "#ef4444", "size": 12},
            row=row,
            col=1,
        )


def _modal_subplot_title(result, modal: dict[str, object], axis: str, label: str) -> str:
    params = modal[axis]
    return (
        f"[{result.summary['turbine_id']}] Modal Diagram {label} | "
        f"{modal['window_count']} windows | "
        f"P90: f0={_modal_num(params.get('f0_hz'), 4)}Hz | "
        f"FDD: zeta={_modal_num(modal.get('fdd_zeta_pct'), 2)}%"
    )


def _modal_num(value: object, digits: int) -> str:
    if value is None:
        return "n/a"
    value_f = float(value)
    if not np.isfinite(value_f):
        return "n/a"
    return f"{value_f:.{digits}f}"


def _markers(
    df: pd.DataFrame,
    name: str,
    color: str,
    column: str,
    *,
    size: int,
    opacity: float = 0.9,
) -> go.Scattergl:
    return go.Scattergl(
        x=df["time_utc"],
        y=df[column],
        mode="markers",
        name=name,
        marker={"color": color, "size": size, "opacity": opacity},
    )


def _line(df: pd.DataFrame, column: str, name: str, color: str, dash: str = "solid") -> go.Scattergl:
    visible = "legendonly" if name in {"AX P95", "AX P99.5", "AY P95", "AY P99.5"} else True
    return go.Scattergl(
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
    theme = apply_theme()

    st.title("Wind Turbine Surveillance")

    dataset_dir = st.sidebar.text_input("Dataset folder", str(DEFAULT_DATASET_DIR))
    turbine_ids = discover_turbine_ids(dataset_dir)
    if not turbine_ids:
        st.error(f"No turbine folders found in {dataset_dir}. Expected folders such as data5 or data7.")
        return
    turbine_id = st.sidebar.selectbox("Turbine", turbine_ids, format_func=str.upper)
    accel_dir = accelerometer_dir_for_turbine(dataset_dir, turbine_id)
    scada_dir = scada_dir_for_dataset(dataset_dir)
    st.sidebar.caption(f"Accelerometer: {accel_dir}")
    st.sidebar.caption(f"SCADA: {scada_dir}")
    window_minutes = st.sidebar.selectbox("Window length", [5, 10, 20, 30], index=1)
    overlap = st.sidebar.slider("Window overlap", min_value=0.0, max_value=0.8, value=0.5, step=0.1)

    try:
        result = load_weekly_result(
            str(accel_dir),
            str(scada_dir),
            turbine_id,
            window_minutes,
            overlap,
            ANALYSIS_CACHE_VERSION,
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

    st.subheader("Modal Diagram")
    render_modal_diagram(result, theme)

    render_weekly_report_download(result)


if __name__ == "__main__":
    main()
