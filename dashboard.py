from __future__ import annotations

import hashlib
import tempfile
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
ANALYSIS_CACHE_VERSION = 11


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
        [data-testid="stSidebar"] [data-testid="stFileUploader"] > label {{
            display: none !important;
        }}
        [data-testid="stSidebar"] [data-testid="stFileUploader"] small {{
            display: none !important;
        }}
        [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {{
            min-height: auto !important;
            padding: 0 !important;
            border: none !important;
            background: transparent !important;
        }}
        [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] > div {{
            padding: 0 !important;
        }}
        [data-testid="stSidebar"] [data-testid="stFileUploaderDropzoneInstructions"] {{
            display: none !important;
        }}
        [data-testid="stSidebar"] [data-testid="stFileUploader"] button {{
            width: 100% !important;
            min-height: 2.5rem !important;
            padding: 0.45rem 0.65rem !important;
            border: 1px solid {theme["border"]} !important;
            border-radius: 6px !important;
            background: {theme["panel_bg"]} !important;
            color: transparent !important;
            font-size: 0 !important;
        }}
        [data-testid="stSidebar"] [data-testid="stFileUploader"] button::before {{
            content: "+" !important;
            color: {theme["text"]} !important;
            font-size: 1.35rem !important;
            font-weight: 600 !important;
            line-height: 1 !important;
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


@st.cache_data(show_spinner="Computing turbine KPIs...")
def load_analysis_result(
    accel_dir: str,
    scada_dir: str,
    turbine_id: str,
    window_minutes: int,
    overlap: float,
    reference_dir: str | None,
    use_reference_files: bool,
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
        reference_dir=reference_dir,
        use_reference_files=use_reference_files,
    )
    return analyze_dataset(accel_files, scada_files, config)


def _persist_uploaded_reference(uploaded_file) -> tuple[str | None, str]:
    if uploaded_file is None:
        return None, ""
    payload = uploaded_file.getvalue()
    digest = hashlib.sha1(payload).hexdigest()[:12]
    filename = Path(uploaded_file.name).name or "reference.mat"
    temp_root = Path(tempfile.gettempdir()) / "wind-dashboard-reference"
    temp_root.mkdir(parents=True, exist_ok=True)
    path = temp_root / f"{digest}_{filename}"
    if not path.exists():
        path.write_bytes(payload)
    return str(path), filename


def _recover_persisted_reference_path(filename: str) -> str:
    if not filename:
        return ""
    temp_root = Path(tempfile.gettempdir()) / "wind-dashboard-reference"
    if not temp_root.exists():
        return ""
    matches = sorted(temp_root.glob(f"*_{filename}"), key=lambda item: item.stat().st_mtime, reverse=True)
    return str(matches[0]) if matches else ""


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


def _period_frame(result, period_mode: str) -> pd.DataFrame:
    return result.daily if period_mode == "Daily" else result.weekly


def _period_suffix(period_mode: str) -> str:
    return "day" if period_mode == "Daily" else "week"


def render_summary(result, filtered: pd.DataFrame, period_mode: str) -> None:
    summary = result.summary
    del filtered
    periods = _period_frame(result, period_mode)
    latest_period = periods.iloc[-1] if not periods.empty else None
    suffix = _period_suffix(period_mode)
    current_ax = None if latest_period is None else latest_period.get("f0_baseline_ax_hz")
    current_ay = None if latest_period is None else latest_period.get("f0_baseline_ay_hz")
    items = [
        ("Turbine", summary["turbine_id"]),
        ("Sample rate", f"{summary['sample_rate_hz']} Hz"),
        ("Current f0 AX", _fmt(current_ax, " Hz", 4)),
        ("Current f0 AY", _fmt(current_ay, " Hz", 4)),
        (
            f"{period_mode} AX shift",
            _fmt(None if latest_period is None else latest_period.get("f0_shift_ax_hz"), f" Hz/{suffix}", 5),
        ),
        (
            f"{period_mode} AY shift",
            _fmt(None if latest_period is None else latest_period.get("f0_shift_ay_hz"), f" Hz/{suffix}", 5),
        ),
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
            f"{pd.Timestamp(row['period_start_utc']).strftime('%Y-%m-%d')} to "
            f"{pd.Timestamp(row['period_end_utc']).strftime('%Y-%m-%d')}",
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


def render_drift_plot(result, df: pd.DataFrame, theme: dict[str, object], period_mode: str) -> None:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.075,
        row_heights=[0.62, 0.38],
        specs=[[{}], [{"secondary_y": True}]],
        subplot_titles=(
            "Frequency Drift",
            f"{period_mode} Baselines",
        ),
    )

    _add_reference_overlay(
        fig,
        df,
        "f0_ax_low_hz",
        "f0_ax_high_hz",
        result.summary.get("f0_ref_ax_hz"),
        "#1d4ed8",
        "AX",
        row=1,
    )
    _add_reference_overlay(
        fig,
        df,
        "f0_ay_low_hz",
        "f0_ay_high_hz",
        result.summary.get("f0_ref_ay_hz"),
        "#166534",
        "AY",
        row=1,
    )

    raw_ax = df[df["f0_ax_hz"].notna()].copy()
    raw_ay = df[df["f0_ay_hz"].notna()].copy()
    ax_outlier = _frame_outlier_mask(raw_ax, "f0_ax_hz", "f0_ax_low_hz", "f0_ax_high_hz")
    ay_outlier = _frame_outlier_mask(raw_ay, "f0_ay_hz", "f0_ay_low_hz", "f0_ay_high_hz")

    fig.add_trace(_line(df, "f0_ax_trend_hz", "AX trend", "#1d4ed8"), row=1, col=1)
    fig.add_trace(_line(df, "f0_ay_trend_hz", "AY trend", "#166534"), row=1, col=1)
    fig.add_trace(
        _markers(raw_ax.loc[~ax_outlier], "f0 AX points", "#93c5fd", "f0_ax_hz", size=4, opacity=0.22, symbol="circle"),
        row=1,
        col=1,
    )
    fig.add_trace(
        _markers(raw_ay.loc[~ay_outlier], "f0 AY points", "#86efac", "f0_ay_hz", size=4, opacity=0.22, symbol="diamond"),
        row=1,
        col=1,
    )
    fig.add_trace(
        _markers(
            raw_ax.loc[ax_outlier],
            "f0 AX out of tolerance",
            "#dc2626",
            "f0_ax_hz",
            size=10,
            opacity=0.95,
            symbol="circle",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        _markers(
            raw_ay.loc[ay_outlier],
            "f0 AY out of tolerance",
            "#dc2626",
            "f0_ay_hz",
            size=10,
            opacity=0.95,
            symbol="diamond",
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    periods = _period_frame(result, period_mode).copy()
    if not periods.empty and not df.empty:
        visible_start = df["time_utc"].min()
        visible_end = df["time_utc"].max()
        periods = periods[
            (periods["period_end_utc"] >= visible_start) & (periods["period_start_utc"] <= visible_end)
        ]
    if not periods.empty:
        fig.add_trace(
            go.Scatter(
                x=periods["period_start_utc"],
                y=periods["f0_baseline_ax_hz"],
                mode="markers+lines",
                name=f"{period_mode} f0 AX",
                marker={"color": "#2563eb", "size": 8},
                line={"color": "#2563eb", "width": 1.8},
            ),
            row=2,
            col=1,
            secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(
                x=periods["period_start_utc"],
                y=periods["f0_baseline_ay_hz"],
                mode="markers+lines",
                name=f"{period_mode} f0 AY",
                marker={"color": "#15803d", "size": 8},
                line={"color": "#15803d", "width": 1.8},
            ),
            row=2,
            col=1,
            secondary_y=False,
        )
    fig.update_yaxes(title_text="Hz", row=1, col=1)
    fig.update_yaxes(title_text="Hz", row=2, col=1, secondary_y=False)
    _style_plot(
        fig,
        theme,
        height=620,
        margin={"l": 32, "r": 28, "t": 48, "b": 32},
        hovermode="x unified",
        legend={"orientation": "h", "y": -0.08},
    )
    st.plotly_chart(fig, width="stretch")

    source_label = _baseline_source_label(result.summary.get("reference_source"))
    reference_path = result.summary.get("reference_path")
    if reference_path:
        st.caption(f"Reference source: {source_label} | {reference_path}")
    else:
        st.caption(f"Reference source: {source_label}")


def render_rms_plot(result, df: pd.DataFrame, theme: dict[str, object]) -> None:
    required = {"rms_res_ax", "rms_res_ay", "p95_res_ax", "p95_res_ay", "p99_res_ax", "p99_res_ay"}
    if df.empty or not required.issubset(df.columns):
        return

    stable_mask = _bool_series(df, "is_stable") if "is_stable" in df else pd.Series(True, index=df.index, dtype=bool)
    stable = df.loc[stable_mask].copy()
    if stable.empty:
        return

    fig = go.Figure()
    fig.add_trace(
        _markers(
            stable[stable["rms_res_ax"].notna()],
            "RMS AX",
            "#93c5fd",
            "rms_res_ax",
            size=4,
            opacity=0.2,
            symbol="circle",
        )
    )
    fig.add_trace(
        _markers(
            stable[stable["rms_res_ay"].notna()],
            "RMS AY",
            "#86efac",
            "rms_res_ay",
            size=4,
            opacity=0.2,
            symbol="diamond",
        )
    )

    trend_ax = stable["rms_res_ax"].rolling(19, min_periods=1, center=True).mean()
    trend_ay = stable["rms_res_ay"].rolling(19, min_periods=1, center=True).mean()
    stable = stable.assign(ewma_res_ax=trend_ax, ewma_res_ay=trend_ay)

    fig.add_trace(_line(stable, "ewma_res_ax", "AX trend", "#1d4ed8"),)
    fig.add_trace(_line(stable, "ewma_res_ay", "AY trend", "#166534"),)

    for column, label, color, dash in (
        ("p95_res_ax", "AX P95", "#f97316", "dash"),
        ("p95_res_ay", "AY P95", "#f59e0b", "dash"),
        ("p99_res_ax", "AX P99.5", "#dc2626", "dot"),
        ("p99_res_ay", "AY P99.5", "#b91c1c", "dot"),
    ):
        line_df = stable[["time_utc", column]].dropna()
        if not line_df.empty:
            fig.add_trace(_line(line_df, column, label, color, dash=dash))

    ax_exceed = stable["ewma_res_ax"].notna() & stable["p95_res_ax"].notna() & (stable["ewma_res_ax"] > stable["p95_res_ax"])
    ay_exceed = stable["ewma_res_ay"].notna() & stable["p95_res_ay"].notna() & (stable["ewma_res_ay"] > stable["p95_res_ay"])
    fig.add_trace(
        _markers(
            stable.loc[ax_exceed],
            "AX trend > P95",
            "#f97316",
            "ewma_res_ax",
            size=9,
            opacity=0.9,
            symbol="circle",
        )
    )
    fig.add_trace(
        _markers(
            stable.loc[ay_exceed],
            "AY trend > P95",
            "#dc2626",
            "ewma_res_ay",
            size=9,
            opacity=0.9,
            symbol="diamond",
        )
    )

    _style_plot(
        fig,
        theme,
        height=430,
        margin={"l": 32, "r": 28, "t": 24, "b": 28},
        hovermode="x unified",
        legend={"orientation": "h", "y": -0.18},
    )
    fig.update_layout(title="RMS Resonance")
    fig.update_yaxes(title_text="RMS acc (m/s^2)")
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
    symbol: str = "circle",
    showlegend: bool = True,
) -> go.Scattergl:
    return go.Scattergl(
        x=df["time_utc"],
        y=df[column],
        mode="markers",
        name=name,
        showlegend=showlegend,
        marker={"color": color, "size": size, "opacity": opacity, "symbol": symbol},
    )


def _bool_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df:
        return pd.Series(False, index=df.index, dtype=bool)
    return df[column].fillna(False).astype(bool)


def _frame_outlier_mask(
    df: pd.DataFrame,
    value_column: str,
    low_column: str,
    high_column: str,
) -> pd.Series:
    if df.empty or value_column not in df or low_column not in df or high_column not in df:
        return pd.Series(False, index=df.index, dtype=bool)
    values = pd.to_numeric(df[value_column], errors="coerce")
    low = pd.to_numeric(df[low_column], errors="coerce")
    high = pd.to_numeric(df[high_column], errors="coerce")
    valid_bounds = low.notna() & high.notna()
    return valid_bounds & values.notna() & ((values < low) | (values > high))


def _add_reference_overlay(
    fig: go.Figure,
    df: pd.DataFrame,
    low_column: str,
    high_column: str,
    baseline_value: object,
    color: str,
    label: str,
    *,
    row: int,
) -> None:
    if df.empty or low_column not in df or high_column not in df:
        return
    low = df[low_column].dropna()
    high = df[high_column].dropna()
    if low.empty or high.empty:
        return
    x0 = df["time_utc"].min()
    x1 = df["time_utc"].max()
    low_value = float(low.iloc[0])
    high_value = float(high.iloc[0])
    fig.add_trace(
        go.Scattergl(
            x=[x0, x1],
            y=[low_value, low_value],
            mode="lines",
            name=f"{label} tolerance",
            line={"color": color, "width": 1.0, "dash": "dot"},
        ),
        row=row,
        col=1,
    )
    fig.add_trace(
        go.Scattergl(
            x=[x0, x1],
            y=[high_value, high_value],
            mode="lines",
            name=f"{label} tolerance",
            showlegend=False,
            line={"color": color, "width": 1.0, "dash": "dot"},
        ),
        row=row,
        col=1,
    )
    if baseline_value is None or not np.isfinite(float(baseline_value)):
        return
    baseline = float(baseline_value)
    fig.add_trace(
        go.Scattergl(
            x=[x0, x1],
            y=[baseline, baseline],
            mode="lines",
            name=f"{label} reference",
            line={"color": color, "width": 1.4, "dash": "dash"},
        ),
        row=row,
        col=1,
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
    return "External file" if str(source).lower() == "file" else "Not loaded"


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
    use_reference_files = st.sidebar.checkbox("Use external reference file (.mat)", value=False)
    stored_reference_name = str(st.session_state.get("reference_file_name", ""))
    stored_reference_path = str(st.session_state.get("reference_file_path", ""))
    if stored_reference_name and not stored_reference_path:
        recovered_reference_path = _recover_persisted_reference_path(stored_reference_name)
        if recovered_reference_path:
            stored_reference_path = recovered_reference_path
            st.session_state["reference_file_path"] = recovered_reference_path
    picker_nonce = int(st.session_state.get("reference_picker_nonce", 0))
    st.sidebar.markdown("Reference file (.mat)")
    if use_reference_files and stored_reference_name:
        ref_name_col, ref_remove_col = st.sidebar.columns([4, 1], vertical_alignment="bottom")
        ref_name_col.text_input(
            "Reference file (.mat)",
            value=stored_reference_name,
            disabled=True,
            label_visibility="collapsed",
        )
        if ref_remove_col.button("×", key="remove_reference_file", use_container_width=True):
            st.session_state["reference_file_name"] = ""
            st.session_state["reference_file_path"] = ""
            st.session_state["reference_picker_nonce"] = picker_nonce + 1
            st.rerun()
        reference_dir = stored_reference_path or None
    else:
        uploaded_reference = st.sidebar.file_uploader(
            "Reference file picker",
            type=["mat"],
            accept_multiple_files=False,
            disabled=not use_reference_files,
            label_visibility="collapsed",
            help="Choose a MATLAB reference file.",
            key=f"reference_file_picker_{picker_nonce}",
        )
        if use_reference_files and uploaded_reference is not None:
            reference_dir, reference_name = _persist_uploaded_reference(uploaded_reference)
            st.session_state["reference_file_name"] = reference_name
            st.session_state["reference_file_path"] = reference_dir or ""
            st.rerun()
        elif use_reference_files:
            reference_dir = None
        else:
            reference_dir = None
            st.session_state["reference_file_name"] = ""
            st.session_state["reference_file_path"] = ""

    reference_ready = use_reference_files and bool(reference_dir)
    if use_reference_files and not reference_ready:
        st.info("Select a reference file to apply reference-based overlays and alarms. Current results stay unchanged until a file is selected.")
    st.sidebar.caption(f"Accelerometer: {accel_dir}")
    st.sidebar.caption(f"SCADA: {scada_dir}")
    period_mode = st.sidebar.selectbox("Aggregation", ["Weekly", "Daily"], index=0)
    window_minutes = st.sidebar.selectbox("Window length", [5, 10, 20, 30], index=1)
    overlap = st.sidebar.slider("Window overlap", min_value=0.0, max_value=0.8, value=0.5, step=0.1)

    try:
        result = load_analysis_result(
            str(accel_dir),
            str(scada_dir),
            turbine_id,
            window_minutes,
            overlap,
            reference_dir,
            reference_ready,
            ANALYSIS_CACHE_VERSION,
        )
    except Exception as exc:
        st.error(str(exc))
        return

    if use_reference_files and stored_reference_name:
        expected_prefix = f"REF_{turbine_id.upper()}_"
        if not stored_reference_name.upper().startswith(expected_prefix):
            st.warning(
                f"Selected reference file `{stored_reference_name}` does not match the current turbine "
                f"`{turbine_id.upper()}`. Choose a file starting with `{expected_prefix}`."
            )
        elif result.summary.get("reference_source") != "file":
            st.warning(
                f"Selected reference file `{stored_reference_name}` was not applied. "
                "Check that the `.mat` file contains valid reference fields for this turbine."
            )

    kpis = result.kpis.copy()
    kpis.index = np.arange(len(kpis))
    filtered = apply_filters(kpis)
    if filtered.empty:
        st.warning("No windows match the current filters.")
        return

    render_summary(result, filtered, period_mode)
    render_drift_plot(result, filtered, theme, period_mode)

    st.subheader("RMS")
    render_rms_plot(result, filtered, theme)

    st.subheader("PSD")
    render_psd(result, filtered, theme)

    st.subheader("Modal Diagram")
    render_modal_diagram(result, theme)

    render_weekly_report_download(result)


if __name__ == "__main__":
    main()
