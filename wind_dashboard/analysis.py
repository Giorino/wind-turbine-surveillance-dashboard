from __future__ import annotations

import math
import re
import csv
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.io import loadmat
from scipy.signal import butter, detrend, filtfilt

from .config import TurbineConfig, get_turbine_config


DATE_CSV_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.csv$", re.IGNORECASE)
DATE_CSV_ZIP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.csv\.zip$", re.IGNORECASE)
SCADA_COLUMNS_18 = [
    "w001Speed",
    "w001Power",
    "w002Speed",
    "w002Power",
    "w003Speed",
    "w003Power",
    "w003DirectionNacelle",
    "w003Direction",
    "w004Speed",
    "w004Power",
    "w005Speed",
    "w005Power",
    "w006Speed",
    "w006Power",
    "w007Speed",
    "w007Power",
    "pointTime",
    "",
]
SCADA_COLUMNS_37 = [
    "w001Speed",
    "w001Power",
    "w001RotorSpeed",
    "w001DirectionNacelle",
    "w001Direction",
    "w002Speed",
    "w002Power",
    "w002RotorSpeed",
    "w002DirectionNacelle",
    "w002Direction",
    "w003Speed",
    "w003Power",
    "w003RotorSpeed",
    "w003DirectionNacelle",
    "w003Direction",
    "w004Speed",
    "w004Power",
    "w004RotorSpeed",
    "w004DirectionNacelle",
    "w004Direction",
    "w005Speed",
    "w005Power",
    "w005RotorSpeed",
    "w005DirectionNacelle",
    "w005Direction",
    "w006Speed",
    "w006Power",
    "w006RotorSpeed",
    "w006DirectionNacelle",
    "w006Direction",
    "w007Speed",
    "w007Power",
    "w007RotorSpeed",
    "w007DirectionNacelle",
    "w007Direction",
    "pointTime",
    "",
]


@dataclass(frozen=True)
class AnalysisConfig:
    turbine_id: str = "w005"
    accel_timezone: str = "Europe/Brussels"
    scada_timezone: str = "UTC"
    low_freq_hz: float = 0.20
    high_freq_hz: float = 0.40
    f0_low_hz: float = 0.31
    f0_high_hz: float = 0.34
    broadband_low_hz: float = 0.05
    broadband_high_hz: float = 0.45
    low_frequency_band_low_hz: float = 0.05
    low_frequency_band_high_hz: float = 0.25
    resonance_band_low_hz: float = 0.25
    resonance_band_high_hz: float = 0.35
    harmonic_bandwidth_hz: float = 0.020
    window_minutes: int = 10
    overlap: float = 0.50
    transition_windows: int = 3
    f0_tolerance: float = 0.05
    power_on_threshold_kw: float = 10.0
    context_wind_min_ms: float = 5.0
    context_power_min_fraction: float = 0.05
    reference_days: int = 30
    ewma_windows: int = 19
    drift_alert_hz_per_day: float = 0.001
    psd_min_hz: float = 0.05
    psd_max_hz: float = 0.80
    use_reference_files: bool = False
    reference_dir: str | None = None
    fdd_segment_minutes: int = 30
    fdd_overlap: float = 0.50
    fdd_low_hz: float = 0.10
    fdd_high_hz: float = 0.50
    half_power_bandwidth_fraction: float = 0.707

    @property
    def turbine(self) -> TurbineConfig:
        return get_turbine_config(self.turbine_id)


@dataclass
class AnalysisResult:
    kpis: pd.DataFrame
    daily: pd.DataFrame
    weekly: pd.DataFrame
    summary: dict[str, object]
    psd_frequencies_hz: np.ndarray
    psd_ax_db: np.ndarray
    psd_ay_db: np.ndarray
    modal: dict[str, object]
    sample_rate_hz: int


@dataclass(frozen=True)
class ReferenceData:
    path: Path
    turbine_id: str | None
    f0_ref_ax_hz: float
    f0_ref_ay_hz: float
    vbins: np.ndarray
    thresholds: dict[str, np.ndarray]
    fallback_thresholds: dict[str, float]
    metadata: dict[str, object]


def analyze_dataset(
    accel_files: Iterable[str | Path],
    scada_files: Iterable[str | Path] | None = None,
    config: AnalysisConfig | None = None,
) -> AnalysisResult:
    cfg = config or AnalysisConfig()
    turbine = cfg.turbine

    acc = _load_accelerometer(accel_files, cfg.accel_timezone)
    if acc.empty:
        raise ValueError("No accelerometer rows were loaded.")

    scada = _load_scada(scada_files or [], cfg.scada_timezone)
    times_utc = pd.DatetimeIndex(acc["time_utc"])
    sample_rate_hz = _detect_sample_rate(times_utc)
    if sample_rate_hz > 1 and _has_non_monotonic_seconds(times_utc):
        times_utc = _reconstruct_regular_time_axis(times_utc, sample_rate_hz)
        acc["time_utc"] = times_utc

    power, wind, rpm_direct = _interpolate_scada_to_accelerometer(scada, times_utc, turbine)
    ax_raw = _fill_signal(acc["ax"].to_numpy(dtype=float))
    ay_raw = _fill_signal(acc["ay"].to_numpy(dtype=float))
    ax_filtered, ay_filtered = _bandpass_pair(ax_raw, ay_raw, sample_rate_hz, cfg)
    rpm_est, rpm_valid = _estimate_rpm(power, wind, rpm_direct, turbine, cfg)

    windows, psd_freqs, psd_ax_db, psd_ay_db = _sliding_kpis(
        times_utc=times_utc,
        ax_filtered=ax_filtered,
        ay_filtered=ay_filtered,
        power=power,
        wind=wind,
        rpm_est=rpm_est,
        rpm_valid=rpm_valid,
        sample_rate_hz=sample_rate_hz,
        config=cfg,
    )

    reference = _load_reference_for_turbine(cfg)
    kpis, summary = _classify_windows(windows, sample_rate_hz, cfg, reference)
    fdd_summary = _fdd_modal_summary(
        times_utc=times_utc,
        ax_filtered=ax_filtered,
        ay_filtered=ay_filtered,
        kpis=kpis,
        sample_rate_hz=sample_rate_hz,
        config=cfg,
    )
    modal = _modal_diagram_summary(
        ax_filtered=ax_filtered,
        ay_filtered=ay_filtered,
        sample_rate_hz=sample_rate_hz,
        config=cfg,
        summary=summary,
        fdd_summary=fdd_summary,
    )
    daily = _period_modal_summary(
        times_utc=times_utc,
        ax_filtered=ax_filtered,
        ay_filtered=ay_filtered,
        kpis=kpis,
        sample_rate_hz=sample_rate_hz,
        config=cfg,
        period="daily",
    )
    weekly = _period_modal_summary(
        times_utc=times_utc,
        ax_filtered=ax_filtered,
        ay_filtered=ay_filtered,
        kpis=kpis,
        sample_rate_hz=sample_rate_hz,
        config=cfg,
        period="weekly",
    )
    summary.update(
        {
            "turbine_id": turbine.turbine_id.upper(),
            "sample_rate_hz": sample_rate_hz,
            "accelerometer_rows": int(len(acc)),
            "scada_rows": int(len(scada)),
            **fdd_summary,
            "daily_count": int(len(daily)),
            "weekly_count": int(len(weekly)),
        }
    )
    if not daily.empty:
        latest_day = daily.iloc[-1]
        summary.update(
            {
                "latest_day_start_utc": latest_day["period_start_utc"].isoformat(),
                "latest_day_end_utc": latest_day["period_end_utc"].isoformat(),
                "latest_day_f0_ax_hz": _rounded(latest_day["f0_baseline_ax_hz"], 5),
                "latest_day_f0_ay_hz": _rounded(latest_day["f0_baseline_ay_hz"], 5),
                "latest_day_zeta_pct": _rounded(latest_day["zeta_fdd_pct"], 3),
            }
        )
    if not weekly.empty:
        latest_week = weekly.iloc[-1]
        summary.update(
            {
                "latest_week_start_utc": latest_week["period_start_utc"].isoformat(),
                "latest_week_end_utc": latest_week["period_end_utc"].isoformat(),
                "latest_week_f0_ax_hz": _rounded(latest_week["f0_baseline_ax_hz"], 5),
                "latest_week_f0_ay_hz": _rounded(latest_week["f0_baseline_ay_hz"], 5),
                "latest_week_zeta_pct": _rounded(latest_week["zeta_fdd_pct"], 3),
            }
        )

    return AnalysisResult(
        kpis=kpis,
        daily=daily,
        weekly=weekly,
        summary=summary,
        psd_frequencies_hz=psd_freqs,
        psd_ax_db=psd_ax_db,
        psd_ay_db=psd_ay_db,
        modal=modal,
        sample_rate_hz=sample_rate_hz,
    )


def discover_accelerometer_files(path: str | Path) -> list[Path]:
    root = Path(path)
    if root.is_file():
        return [root]
    if not root.exists():
        return []
    files = [*root.glob("*.csv"), *root.glob("*.csv.zip")]
    return sorted(
        p
        for p in files
        if not p.name.startswith("._") and (DATE_CSV_RE.fullmatch(p.name) or DATE_CSV_ZIP_RE.fullmatch(p.name))
    )


def discover_scada_files(path: str | Path) -> list[Path]:
    root = Path(path)
    if root.is_file():
        return [root]
    if not root.exists():
        return []
    return sorted(p for p in root.glob("*.csv") if not p.name.startswith("._") and DATE_CSV_RE.fullmatch(p.name))


def _load_reference_for_turbine(cfg: AnalysisConfig) -> ReferenceData | None:
    if not cfg.use_reference_files or not cfg.reference_dir:
        return None

    root = Path(cfg.reference_dir)
    if not root.exists():
        return None

    turbine_id = cfg.turbine_id.lower()
    pattern = f"REF_{turbine_id.upper()}_*.mat"
    if root.is_file():
        candidates = [root]
    else:
        candidates = sorted(root.rglob(pattern))
    if not candidates:
        return None
    path = max(candidates, key=lambda item: item.stat().st_mtime)

    try:
        mat = loadmat(path, squeeze_me=True, struct_as_record=False)
    except Exception as exc:  # pragma: no cover - depends on external MATLAB files.
        raise ValueError(f"Could not read reference file {path}: {exc}") from exc

    def number(name: str) -> float:
        if name not in mat:
            return np.nan
        arr = np.asarray(mat[name], dtype=float)
        return float(arr.reshape(-1)[0]) if arr.size else np.nan

    def vector(name: str) -> np.ndarray:
        if name not in mat:
            return np.array([], dtype=float)
        return np.asarray(mat[name], dtype=float).reshape(-1)

    threshold_names = (
        "p95_ax",
        "p99_ax",
        "p95_ay",
        "p99_ay",
        "p95_bb",
        "p99_bb",
        "p95_bf_ax",
        "p99_bf_ax",
        "p95_bf_ay",
        "p99_bf_ay",
        "p95_res_ax",
        "p99_res_ax",
        "p95_res_ay",
        "p99_res_ay",
    )
    fallback_names = (
        "rep_ax95",
        "rep_ax99",
        "rep_ay95",
        "rep_ay99",
        "rep_bb95",
        "rep_bb99",
        "rep_bf_ax95",
        "rep_bf_ax99",
        "rep_bf_ay95",
        "rep_bf_ay99",
        "rep_res_ax95",
        "rep_res_ax99",
        "rep_res_ay95",
        "rep_res_ay99",
    )
    metadata = _mat_struct_to_dict(mat.get("ref_meta"))
    ref_turbine = str(metadata.get("turbine_id", "")).lower() or None
    vbins = vector("vbins")
    if vbins.size < 2:
        vbins = np.array([3, 5, 7, 9, 11, np.inf], dtype=float)

    return ReferenceData(
        path=path,
        turbine_id=ref_turbine,
        f0_ref_ax_hz=number("f0_ref_ax"),
        f0_ref_ay_hz=number("f0_ref_ay"),
        vbins=vbins,
        thresholds={name: vector(name) for name in threshold_names},
        fallback_thresholds={name: number(name) for name in fallback_names},
        metadata=metadata,
    )


def _mat_struct_to_dict(value: object) -> dict[str, object]:
    if value is None or not hasattr(value, "_fieldnames"):
        return {}
    out: dict[str, object] = {}
    for field_name in value._fieldnames:
        field_value = getattr(value, field_name)
        arr = np.asarray(field_value)
        if arr.shape == ():
            item = arr.item()
            if isinstance(item, np.generic):
                item = item.item()
            out[field_name] = item
        else:
            out[field_name] = arr
    return out


def _load_accelerometer(files: Iterable[str | Path], timezone: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for file in files:
        path = Path(file)
        if not path.exists() or path.name.startswith("._"):
            continue
        frame = _read_csv_or_zip(path)
        required = {"datetime", "ax", "ay"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{path} is missing accelerometer columns: {', '.join(sorted(missing))}")
        frame = frame[["datetime", "ax", "ay"]].copy()
        frame["time_utc"] = _parse_time(frame["datetime"], timezone).dt.tz_convert("UTC")
        frame["ax"] = pd.to_numeric(frame["ax"], errors="coerce")
        frame["ay"] = pd.to_numeric(frame["ay"], errors="coerce")
        frames.append(frame[["time_utc", "ax", "ay"]])

    if not frames:
        return pd.DataFrame(columns=["time_utc", "ax", "ay"])

    out = pd.concat(frames, ignore_index=True)
    out = out.dropna(subset=["time_utc"]).sort_values("time_utc").reset_index(drop=True)
    return out


def _load_scada(files: Iterable[str | Path], timezone: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for file in files:
        path = Path(file)
        if not path.exists() or path.name.startswith("._"):
            continue
        frame = _read_scada_csv(path)
        frame = frame.loc[:, ~frame.columns.str.match(r"^Unnamed")]
        time_col = _find_time_column(frame)
        if not time_col:
            continue
        frame = frame.copy()
        frame[time_col] = _parse_time(frame[time_col], timezone)
        frame = frame.rename(columns={time_col: "pointTime"})
        frames.append(frame)

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    out = out.dropna(subset=["pointTime"]).sort_values("pointTime").reset_index(drop=True)
    return out


def _read_csv_or_zip(path: Path) -> pd.DataFrame:
    if path.suffix.lower() != ".zip":
        return pd.read_csv(path)

    with zipfile.ZipFile(path) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError(f"{path} does not contain a CSV file.")
        with archive.open(csv_names[0]) as csv_file:
            return pd.read_csv(csv_file)


def _read_scada_csv(path: Path) -> pd.DataFrame:
    segments: dict[tuple[str, ...], list[list[str]]] = {}
    current_header: list[str] | None = None

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        for line_number, row in enumerate(reader, start=1):
            if not row or all(not cell.strip() for cell in row):
                continue
            if "pointTime" in row:
                current_header = _normalize_csv_header(row)
                continue

            header = current_header if current_header and len(current_header) == len(row) else _scada_header_for_width(len(row))
            if header is None:
                raise ValueError(f"{path} has an unsupported SCADA row width at line {line_number}: {len(row)} fields.")
            segments.setdefault(tuple(header), []).append(row)

    frames = [pd.DataFrame(rows, columns=list(header)) for header, rows in segments.items()]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _normalize_csv_header(row: list[str]) -> list[str]:
    return [column.strip() if column.strip() else f"Unnamed: {index}" for index, column in enumerate(row)]


def _scada_header_for_width(width: int) -> list[str] | None:
    if width == len(SCADA_COLUMNS_18):
        return _normalize_csv_header(SCADA_COLUMNS_18)
    if width == len(SCADA_COLUMNS_37):
        return _normalize_csv_header(SCADA_COLUMNS_37)
    return None


def _parse_time(values: pd.Series, timezone: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce", format="mixed")
    if parsed.dt.tz is None:
        return parsed.dt.tz_localize(timezone, ambiguous="infer", nonexistent="shift_forward")
    return parsed.dt.tz_convert(timezone)


def _find_time_column(frame: pd.DataFrame) -> str | None:
    if "pointTime" in frame.columns:
        return "pointTime"
    for column in frame.columns:
        if "time" in column.lower() or "date" in column.lower():
            return column
    return None


def _detect_sample_rate(times_utc: pd.DatetimeIndex) -> int:
    if len(times_utc) < 2:
        return 1
    sample = times_utc[: min(500, len(times_utc))]
    diffs = sample.to_series().diff().dt.total_seconds().dropna().to_numpy()
    dt_median = float(np.nanmedian(diffs)) if diffs.size else np.nan
    if not np.isfinite(dt_median) or dt_median <= 0:
        span_s = (times_utc[-1] - times_utc[0]).total_seconds()
        dt_median = span_s / max(len(times_utc) - 1, 1) if span_s > 0 else 1.0
    if not np.isfinite(dt_median) or dt_median <= 0:
        return 1
    return max(1, int(round(1.0 / dt_median)))


def _has_non_monotonic_seconds(times_utc: pd.DatetimeIndex) -> bool:
    if len(times_utc) < 3:
        return False
    diffs = times_utc.to_series().diff().dt.total_seconds().dropna().to_numpy()
    return bool(np.mean(diffs <= 0) > 0.05)


def _reconstruct_regular_time_axis(times_utc: pd.DatetimeIndex, sample_rate_hz: int) -> pd.DatetimeIndex:
    start = times_utc[0]
    offsets_ns = np.arange(len(times_utc), dtype=np.int64) * int(1_000_000_000 / sample_rate_hz)
    return pd.DatetimeIndex(start.value + offsets_ns, tz="UTC")


def _interpolate_scada_to_accelerometer(
    scada: pd.DataFrame,
    times_utc: pd.DatetimeIndex,
    turbine: TurbineConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(times_utc)
    if scada.empty:
        return np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)

    scada_times = pd.DatetimeIndex(scada["pointTime"]).tz_convert("UTC")
    origin = min(times_utc[0], scada_times[0])
    x_acc = (times_utc - origin).total_seconds().to_numpy(dtype=float)
    x_scada = (scada_times - origin).total_seconds().to_numpy(dtype=float)

    def interp_column(column: str) -> np.ndarray:
        if column not in scada.columns:
            return np.full(n, np.nan)
        y = pd.to_numeric(scada[column], errors="coerce").to_numpy(dtype=float)
        ok = np.isfinite(x_scada) & np.isfinite(y)
        if ok.sum() < 2:
            return np.full(n, np.nan)
        values = np.interp(x_acc, x_scada[ok], y[ok], left=np.nan, right=np.nan)
        return np.maximum(values, 0.0)

    return (
        interp_column(turbine.power_column),
        interp_column(turbine.wind_column),
        interp_column(turbine.rpm_column),
    )


def _fill_signal(values: np.ndarray) -> np.ndarray:
    out = values.astype(float, copy=True)
    ok = np.isfinite(out)
    if ok.all():
        return out
    idx = np.arange(len(out), dtype=float)
    if ok.sum() >= 2:
        out[~ok] = np.interp(idx[~ok], idx[ok], out[ok])
    else:
        out[~ok] = 0.0
    return out


def _bandpass_pair(
    ax_raw: np.ndarray,
    ay_raw: np.ndarray,
    sample_rate_hz: int,
    cfg: AnalysisConfig,
) -> tuple[np.ndarray, np.ndarray]:
    nyquist = sample_rate_hz / 2
    low = max(1e-4, min(cfg.low_freq_hz / nyquist, 0.99))
    high_hz = min(cfg.high_freq_hz, nyquist * 0.92)
    high = max(low + 1e-4, min(high_hz / nyquist, 0.999))
    try:
        b, a = butter(1, [low, high], btype="bandpass")
    except ValueError:
        b, a = butter(1, low, btype="highpass")
    return filtfilt(b, a, ax_raw), filtfilt(b, a, ay_raw)


def _estimate_rpm(
    power: np.ndarray,
    wind: np.ndarray,
    rpm_direct: np.ndarray,
    turbine: TurbineConfig,
    cfg: AnalysisConfig,
) -> tuple[np.ndarray, np.ndarray]:
    rpm = np.full_like(power, np.nan, dtype=float)
    direct_ok = np.isfinite(rpm_direct) & (rpm_direct > 2)
    if direct_ok.any():
        rpm[direct_ok] = rpm_direct[direct_ok]
        return rpm, direct_ok

    ok = np.isfinite(power) & np.isfinite(wind)
    zone2 = (
        ok
        & (power > cfg.power_on_threshold_kw)
        & (wind >= turbine.zone2_wind_min)
        & (wind <= turbine.zone2_wind_max)
        & (power < turbine.power_zone2_max_kw)
    )
    rpm[zone2] = turbine.nominal_rpm * np.power(power[zone2] / turbine.nominal_power_kw, 1.0 / 3.0)
    rpm[zone2] = np.clip(rpm[zone2], 4.0, turbine.nominal_rpm)
    return rpm, zone2


def _sliding_kpis(
    times_utc: pd.DatetimeIndex,
    ax_filtered: np.ndarray,
    ay_filtered: np.ndarray,
    power: np.ndarray,
    wind: np.ndarray,
    rpm_est: np.ndarray,
    rpm_valid: np.ndarray,
    sample_rate_hz: int,
    config: AnalysisConfig,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    win_s = max(8, int(round(config.window_minutes * 60 * sample_rate_hz)))
    step_s = max(1, int(round(win_s * (1 - config.overlap))))
    starts = np.arange(0, max(len(ax_filtered) - win_s + 1, 0), step_s, dtype=int)
    if starts.size == 0:
        raise ValueError("The selected period is shorter than one analysis window.")

    nfft = min(1 << int(math.ceil(math.log2(win_s))), 2**17)
    window = np.hamming(win_s)
    window_power = float(np.dot(window, window))
    freqs = np.fft.rfftfreq(nfft, d=1 / sample_rate_hz)
    df = float(np.nanmean(np.diff(freqs))) if len(freqs) > 1 else 0.0

    m_nb = (freqs >= config.low_freq_hz) & (freqs <= min(config.high_freq_hz, sample_rate_hz / 2))
    m_s = (freqs >= config.f0_low_hz) & (freqs <= min(config.f0_high_hz, sample_rate_hz / 2))
    m_bb = (freqs >= config.broadband_low_hz) & (
        freqs <= min(config.broadband_high_hz, sample_rate_hz / 2)
    )
    m_bf = (freqs >= config.low_frequency_band_low_hz) & (
        freqs <= min(config.low_frequency_band_high_hz, sample_rate_hz / 2)
    )
    m_res = (freqs >= config.resonance_band_low_hz) & (
        freqs <= min(config.resonance_band_high_hz, sample_rate_hz / 2)
    )
    m_psd = (freqs >= config.psd_min_hz) & (freqs <= min(config.psd_max_hz, sample_rate_hz / 2))
    psd_freqs = freqs[m_psd]

    rows: list[dict[str, object]] = []
    psd_ax: list[np.ndarray] = []
    psd_ay: list[np.ndarray] = []

    for start in starts:
        idx = slice(start, start + win_s)
        rpm_k = _nanmean(rpm_est[idx])
        center_idx = start + win_s // 2
        row = {
            "time_utc": times_utc[min(center_idx, len(times_utc) - 1)],
            "power_kw": _nanmean(power[idx]),
            "wind_ms": _nanmean(wind[idx]),
            "rpm": rpm_k,
            "rpm_valid": _nanmean(rpm_valid[idx].astype(float)) > 0.5,
        }

        for axis_name, signal_values in (("ax", ax_filtered[idx]), ("ay", ay_filtered[idx])):
            pxx = _window_psd(signal_values, window, nfft, sample_rate_hz, window_power)
            rms_nb = math.sqrt(max(float(np.nansum(pxx[m_nb]) * df), 0.0)) if m_nb.any() else np.nan
            rms_bb = math.sqrt(max(float(np.nansum(pxx[m_bb]) * df), 0.0)) if m_bb.any() else np.nan
            rms_bf = math.sqrt(max(float(np.nansum(pxx[m_bf]) * df), 0.0)) if m_bf.any() else np.nan
            rms_res = math.sqrt(max(float(np.nansum(pxx[m_res]) * df), 0.0)) if m_res.any() else np.nan
            f0 = _detect_f0(freqs, pxx, m_s, rpm_k, config.harmonic_bandwidth_hz)
            row[f"f0_{axis_name}_hz"] = f0
            row[f"rms_{axis_name}"] = rms_nb
            row[f"broadband_{axis_name}"] = rms_bb
            row[f"rms_bf_{axis_name}"] = rms_bf
            row[f"rms_res_{axis_name}"] = rms_res
            if axis_name == "ax":
                row["rms_1p_ax"] = _harmonic_rms(freqs, pxx, rpm_k, 1, config.harmonic_bandwidth_hz, df)
                row["rms_3p_ax"] = _harmonic_rms(freqs, pxx, rpm_k, 3, config.harmonic_bandwidth_hz, df)
                psd_ax.append(_to_db(pxx[m_psd]))
            else:
                row["rms_1p_ay"] = _harmonic_rms(freqs, pxx, rpm_k, 1, config.harmonic_bandwidth_hz, df)
                row["rms_3p_ay"] = _harmonic_rms(freqs, pxx, rpm_k, 3, config.harmonic_bandwidth_hz, df)
                psd_ay.append(_to_db(pxx[m_psd]))

        rows.append(row)

    return (
        pd.DataFrame(rows),
        psd_freqs,
        np.vstack(psd_ax).astype(np.float32),
        np.vstack(psd_ay).astype(np.float32),
    )


def _window_psd(
    values: np.ndarray,
    window: np.ndarray,
    nfft: int,
    sample_rate_hz: int,
    window_power: float,
) -> np.ndarray:
    spectrum = np.fft.rfft(detrend(values) * window, n=nfft)
    pxx = 2.0 * np.square(np.abs(spectrum)) / (sample_rate_hz * window_power)
    if pxx.size:
        pxx[0] *= 0.5
        if nfft % 2 == 0:
            pxx[-1] *= 0.5
    return pxx


def _detect_f0(
    freqs: np.ndarray,
    pxx: np.ndarray,
    search_mask: np.ndarray,
    rpm: float,
    harmonic_bandwidth_hz: float,
) -> float:
    if not search_mask.any():
        return np.nan
    search_freqs = freqs[search_mask]
    search_power = pxx[search_mask].copy()
    harmonic_mask = np.ones_like(search_freqs, dtype=bool)
    if np.isfinite(rpm) and rpm > 1:
        f_1p = rpm / 60.0
        for harmonic in range(1, 5):
            harmonic_mask &= np.abs(search_freqs - harmonic * f_1p) > harmonic_bandwidth_hz
    search_power[~harmonic_mask] = 0.0
    positive = search_power[search_power > 0]
    if positive.size == 0:
        return np.nan
    peak_idx = int(np.argmax(search_power))
    background = float(np.nanmedian(positive))
    if np.isfinite(background) and search_power[peak_idx] > 4.0 * background:
        return float(search_freqs[peak_idx])
    return np.nan


def _harmonic_rms(
    freqs: np.ndarray,
    pxx: np.ndarray,
    rpm: float,
    harmonic: int,
    bandwidth_hz: float,
    df: float,
) -> float:
    if not np.isfinite(rpm) or rpm <= 1:
        return np.nan
    center = harmonic * rpm / 60.0
    mask = (freqs >= center - bandwidth_hz) & (freqs <= center + bandwidth_hz)
    if not mask.any():
        return np.nan
    return math.sqrt(max(float(np.nansum(pxx[mask]) * df), 0.0))


def _classify_windows(
    windows: pd.DataFrame,
    sample_rate_hz: int,
    cfg: AnalysisConfig,
    reference: ReferenceData | None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    turbine = cfg.turbine
    kpis = windows.copy()
    n = len(kpis)

    has_power = kpis["power_kw"].notna().any()
    has_wind = kpis["wind_ms"].notna().any()
    has_rpm = kpis["rpm"].notna().any()

    rms_floor = np.nanpercentile(np.r_[kpis["rms_ax"], kpis["rms_ay"]], 20)
    vibration_on = ((kpis["rms_ax"] > rms_floor) | (kpis["rms_ay"] > rms_floor)).to_numpy()

    if has_power and (kpis["power_kw"] > cfg.power_on_threshold_kw).any():
        power_values = kpis["power_kw"]
        mask_on = (power_values > cfg.power_on_threshold_kw).to_numpy().copy()
        missing_power = power_values.isna().to_numpy()
        mask_on[missing_power] = vibration_on[missing_power]
    elif has_wind:
        wind_values = kpis["wind_ms"]
        mask_on = (wind_values > turbine.cutin_ms).to_numpy().copy()
        missing_wind = wind_values.isna().to_numpy()
        mask_on[missing_wind] = vibration_on[missing_wind]
    else:
        mask_on = vibration_on

    mask_transition = _transition_mask(mask_on, cfg.transition_windows)
    mask_stable = mask_on & ~mask_transition
    kpis["is_on"] = mask_on
    kpis["is_stable"] = mask_stable

    ref_end = kpis["time_utc"].iloc[0] + pd.Timedelta(days=cfg.reference_days)
    mask_ref = mask_stable & (kpis["time_utc"] <= ref_end).to_numpy()
    if mask_ref.sum() < 20:
        mask_ref = mask_stable.copy()

    amp_thr_ax = _safe_percentile(kpis.loc[mask_ref, "rms_ax"], 40)
    amp_thr_ay = _safe_percentile(kpis.loc[mask_ref, "rms_ay"], 40)
    f0ax_fbl = mask_stable & kpis["f0_ax_hz"].notna().to_numpy()
    f0ay_fbl = mask_stable & kpis["f0_ay_hz"].notna().to_numpy()
    if np.isfinite(amp_thr_ax):
        f0ax_fbl &= (kpis["rms_ax"] >= amp_thr_ax).to_numpy()
    if np.isfinite(amp_thr_ay):
        f0ay_fbl &= (kpis["rms_ay"] >= amp_thr_ay).to_numpy()

    f0_ref_ax = np.nan
    f0_ref_ay = np.nan
    reference_source = "none"
    reference_path = None
    if reference is not None:
        ref_matches = reference.turbine_id in {None, turbine.turbine_id.lower()}
        has_ref_f0 = np.isfinite(reference.f0_ref_ax_hz) and np.isfinite(reference.f0_ref_ay_hz)
        if ref_matches and has_ref_f0:
            f0_ref_ax = reference.f0_ref_ax_hz
            f0_ref_ay = reference.f0_ref_ay_hz
            reference_source = "file"
            reference_path = str(reference.path)

    f0_lo_ax, f0_hi_ax = _bounds(f0_ref_ax, cfg.f0_tolerance)
    f0_lo_ay, f0_hi_ay = _bounds(f0_ref_ay, cfg.f0_tolerance)

    bin_id, threshold_frame = _rms_thresholds(
        kpis,
        mask_ref,
        mask_stable,
        has_wind,
        reference if reference_source == "file" else None,
    )
    for column, values in threshold_frame.items():
        kpis[column] = values
    kpis["wind_bin"] = bin_id

    stable_ax = kpis["rms_ax"].where(mask_stable)
    stable_ay = kpis["rms_ay"].where(mask_stable)
    stable_bb = kpis["broadband_ax"].where(mask_stable)
    kpis["ewma_ax"] = stable_ax.rolling(cfg.ewma_windows, min_periods=1, center=True).mean()
    kpis["ewma_ay"] = stable_ay.rolling(cfg.ewma_windows, min_periods=1, center=True).mean()
    kpis["ewma_broadband_ax"] = stable_bb.rolling(cfg.ewma_windows, min_periods=1, center=True).mean()

    ewma_ax_high = (kpis["ewma_ax"] > kpis["p95_ax"]).fillna(False).to_numpy()
    ewma_ay_high = (kpis["ewma_ay"] > kpis["p95_ay"]).fillna(False).to_numpy()
    alert_ewma_ax = ewma_ax_high & _previous(ewma_ax_high, 1) & _previous(ewma_ax_high, 2)
    alert_ewma_ay = ewma_ay_high & _previous(ewma_ay_high, 1) & _previous(ewma_ay_high, 2)

    cond_ctx = np.zeros(n, dtype=bool)
    if has_power:
        cond_ctx |= (kpis["power_kw"] > cfg.context_power_min_fraction * turbine.nominal_power_kw).fillna(False).to_numpy()
    if has_wind:
        cond_ctx |= (kpis["wind_ms"] >= cfg.context_wind_min_ms).fillna(False).to_numpy()
    if has_rpm:
        cond_ctx |= (kpis["rpm"] >= 5).fillna(False).to_numpy()
    if not has_power and not has_wind:
        cond_ctx[:] = True

    hb_ax = (
        f0ax_fbl
        & mask_stable
        & cond_ctx
        & _outside(kpis["f0_ax_hz"].to_numpy(dtype=float), f0_lo_ax, f0_hi_ax)
    )
    hb_ay = (
        f0ay_fbl
        & mask_stable
        & cond_ctx
        & _outside(kpis["f0_ay_hz"].to_numpy(dtype=float), f0_lo_ay, f0_hi_ay)
    )
    alert_f0 = (
        (hb_ax & hb_ay)
        | (hb_ax & _previous(hb_ax, 1) & _previous(hb_ax, 2))
        | (hb_ay & _previous(hb_ay, 1) & _previous(hb_ay, 2))
    )

    alert_rms_ax = mask_stable & (kpis["rms_ax"] > kpis["p99_ax"]).fillna(False).to_numpy()
    alert_rms_ay = mask_stable & (kpis["rms_ay"] > kpis["p99_ay"]).fillna(False).to_numpy()
    alert_rms = alert_rms_ax | alert_rms_ay | alert_ewma_ax | alert_ewma_ay
    info_ax = mask_stable & (kpis["rms_ax"] > kpis["p95_ax"]).fillna(False).to_numpy() & ~alert_rms_ax
    info_ay = mask_stable & (kpis["rms_ay"] > kpis["p95_ay"]).fillna(False).to_numpy() & ~alert_rms_ay

    score = alert_f0.astype(int) + alert_rms.astype(int) + (alert_f0 & alert_rms).astype(int)
    confirmed = mask_stable & (score >= 2)
    warning = mask_stable & (score == 1)

    kpis["f0_ax_ref_hz"] = f0_ref_ax
    kpis["f0_ay_ref_hz"] = f0_ref_ay
    kpis["f0_ax_low_hz"] = f0_lo_ax
    kpis["f0_ax_high_hz"] = f0_hi_ax
    kpis["f0_ay_low_hz"] = f0_lo_ay
    kpis["f0_ay_high_hz"] = f0_hi_ay
    kpis["f0_ax_visible"] = f0ax_fbl & mask_stable
    kpis["f0_ay_visible"] = f0ay_fbl & mask_stable
    kpis["f0_ax_outlier"] = hb_ax
    kpis["f0_ay_outlier"] = hb_ay
    kpis["alert_f0"] = alert_f0
    kpis["alert_rms_ax"] = alert_rms_ax
    kpis["alert_rms_ay"] = alert_rms_ay
    kpis["alert_ewma_ax"] = alert_ewma_ax
    kpis["alert_ewma_ay"] = alert_ewma_ay
    kpis["alert_rms"] = alert_rms
    kpis["info_ax"] = info_ax
    kpis["info_ay"] = info_ay
    kpis["score"] = score
    kpis["alert_confirmed"] = confirmed
    kpis["alert_warning"] = warning
    kpis["severity"] = _severity(mask_on, confirmed, warning)
    kpis["reasons"] = _reasons(kpis)

    drift_ax, trend_ax = _drift_and_trend(kpis, "f0_ax_hz", f0ax_fbl & mask_stable, cfg)
    drift_ay, trend_ay = _drift_and_trend(kpis, "f0_ay_hz", f0ay_fbl & mask_stable, cfg)
    kpis["f0_ax_trend_hz"] = trend_ax
    kpis["f0_ay_trend_hz"] = trend_ay

    duration_h = (n * cfg.window_minutes * (1 - cfg.overlap)) / 60.0
    summary = {
        "start_utc": kpis["time_utc"].iloc[0].isoformat(),
        "end_utc": kpis["time_utc"].iloc[-1].isoformat(),
        "duration_hours": round(float(duration_h), 2),
        "window_count": int(n),
        "on_count": int(mask_on.sum()),
        "stable_count": int(mask_stable.sum()),
        "f0_ref_ax_hz": _rounded(f0_ref_ax, 5),
        "f0_ref_ay_hz": _rounded(f0_ref_ay, 5),
        "reference_source": reference_source,
        "reference_path": reference_path,
        "drift_ax_hz_per_day": _rounded(drift_ax, 6),
        "drift_ay_hz_per_day": _rounded(drift_ay, 6),
        "drift_ax_significant": bool(np.isfinite(drift_ax) and abs(drift_ax) > cfg.drift_alert_hz_per_day),
        "drift_ay_significant": bool(np.isfinite(drift_ay) and abs(drift_ay) > cfg.drift_alert_hz_per_day),
        "alert_f0_count": int(alert_f0.sum()),
        "alert_rms_count": int(alert_rms.sum()),
        "confirmed_count": int(confirmed.sum()),
        "warning_count": int(warning.sum()),
        "window_minutes": cfg.window_minutes,
        "overlap": cfg.overlap,
    }
    return kpis, summary


def _fdd_modal_summary(
    times_utc: pd.DatetimeIndex,
    ax_filtered: np.ndarray,
    ay_filtered: np.ndarray,
    kpis: pd.DataFrame,
    sample_rate_hz: int,
    config: AnalysisConfig,
) -> dict[str, object]:
    empty = {
        "fdd_f0_hz": None,
        "zeta_fdd_pct": None,
        "fdd_f1_half_power_hz": None,
        "fdd_f2_half_power_hz": None,
        "fdd_segments_used": 0,
    }
    n = len(ax_filtered)
    if n < max(8, sample_rate_hz * 60) or kpis.empty:
        return empty

    win_fdd = min(max(8, int(round(config.fdd_segment_minutes * 60 * sample_rate_hz))), n)
    nfft_fdd = min(1 << int(math.ceil(math.log2(win_fdd))), 2**17)
    step_fdd = max(1, int(round(win_fdd * (1 - config.fdd_overlap))))
    starts = np.arange(0, max(n - win_fdd + 1, 0), step_fdd, dtype=int)
    if starts.size == 0:
        starts = np.array([0], dtype=int)

    freqs = np.fft.rfftfreq(nfft_fdd, d=1 / sample_rate_hz)
    window = np.hamming(win_fdd)
    n_freq = len(freqs)
    sxx = np.zeros(n_freq, dtype=float)
    syy = np.zeros(n_freq, dtype=float)
    sxy = np.zeros(n_freq, dtype=complex)
    used = 0

    kpi_times_ns = _datetime_ns(kpis["time_utc"])
    is_on = kpis["is_on"].to_numpy(dtype=bool) if "is_on" in kpis else np.ones(len(kpis), dtype=bool)

    for start in starts:
        idx = slice(start, start + win_fdd)
        center_ns = int(pd.Timestamp(times_utc[min(start + win_fdd // 2, n - 1)]).value)
        nearest = _nearest_index(kpi_times_ns, center_ns)
        if nearest is None or not is_on[nearest]:
            continue

        fx = np.fft.rfft(detrend(ax_filtered[idx]) * window, n=nfft_fdd) / nfft_fdd
        fy = np.fft.rfft(detrend(ay_filtered[idx]) * window, n=nfft_fdd) / nfft_fdd
        sxx += np.real(fx * np.conj(fx))
        syy += np.real(fy * np.conj(fy))
        sxy += fx * np.conj(fy)
        used += 1

    if used == 0:
        return empty

    sxx /= used
    syy /= used
    sxy /= used
    trace = sxx + syy
    delta = np.sqrt(np.square(sxx - syy) + 4 * np.square(np.abs(sxy)))
    singular_1 = np.maximum(0.5 * (trace + delta), 0.0)

    modal_mask = (freqs >= config.fdd_low_hz) & (freqs <= min(config.fdd_high_hz, sample_rate_hz / 2))
    if modal_mask.sum() < 5:
        return {**empty, "fdd_segments_used": int(used)}

    f_modal = freqs[modal_mask]
    s1_modal = singular_1[modal_mask]
    s1_db = 10.0 * np.log10(np.maximum(s1_modal, np.finfo(float).eps))
    s1_sm_db = _gaussian_smooth(s1_db, 9)
    s1_sm = np.power(10.0, s1_sm_db / 10.0)

    rpm_median = _robust_rpm_median(kpis, f_modal, s1_sm_db, config)
    search_mask = (f_modal >= config.f0_low_hz) & (f_modal <= config.f0_high_hz)
    if np.isfinite(rpm_median):
        f_1p = rpm_median / 60.0
        for harmonic in range(1, 9):
            search_mask &= np.abs(f_modal - harmonic * f_1p) > config.harmonic_bandwidth_hz
    if search_mask.sum() < 5:
        search_mask = (f_modal >= config.f0_low_hz) & (f_modal <= config.f0_high_hz)
    if not search_mask.any():
        return {**empty, "fdd_segments_used": int(used)}

    search_values = s1_modal.copy()
    search_values[~search_mask] = 0.0
    peak_idx = int(np.argmax(search_values))
    f0_fdd = float(f_modal[peak_idx])

    if 0 < peak_idx < len(f_modal) - 1:
        local_lo = max(0, peak_idx - 3)
        local_hi = min(len(f_modal), peak_idx + 4)
        local_idx = local_lo + int(np.argmax(s1_modal[local_lo:local_hi]))
        if 0 < local_idx < len(f_modal) - 1:
            y3 = s1_modal[local_idx - 1 : local_idx + 2]
            x3 = f_modal[local_idx - 1 : local_idx + 2]
            dx = x3[1] - x3[0]
            denom = 2 * (y3[0] - 2 * y3[1] + y3[2])
            if abs(denom) > np.finfo(float).eps * max(float(np.max(np.abs(y3))), 1.0):
                candidate = x3[1] - ((y3[2] - y3[0]) / denom) * dx
                if config.f0_low_hz <= candidate <= config.f0_high_hz and abs(candidate - f0_fdd) < 4 * dx:
                    f0_fdd = float(candidate)

    smooth_peak_idx = int(np.argmin(np.abs(f_modal - f0_fdd)))
    peak_value = float(s1_sm[smooth_peak_idx])
    half_power_level = peak_value * config.half_power_bandwidth_fraction**2
    left_idx = np.where(s1_sm[:smooth_peak_idx] <= half_power_level)[0]
    right_idx = np.where(s1_sm[smooth_peak_idx + 1 :] <= half_power_level)[0]
    f1_hp = float(f_modal[left_idx[-1]]) if left_idx.size else np.nan
    f2_hp = float(f_modal[smooth_peak_idx + 1 + right_idx[0]]) if right_idx.size else np.nan
    zeta_pct = np.nan
    if np.isfinite(f1_hp) and np.isfinite(f2_hp) and f0_fdd > 0:
        zeta_pct = float((f2_hp - f1_hp) / (2 * f0_fdd) * 100.0)

    return {
        "fdd_f0_hz": _rounded(f0_fdd, 5),
        "zeta_fdd_pct": _rounded(zeta_pct, 3),
        "fdd_f1_half_power_hz": _rounded(f1_hp, 5),
        "fdd_f2_half_power_hz": _rounded(f2_hp, 5),
        "fdd_segments_used": int(used),
    }


def _modal_diagram_summary(
    ax_filtered: np.ndarray,
    ay_filtered: np.ndarray,
    sample_rate_hz: int,
    config: AnalysisConfig,
    summary: dict[str, object],
    fdd_summary: dict[str, object],
) -> dict[str, object]:
    n = len(ax_filtered)
    if n < max(8, sample_rate_hz * 60):
        return {"available": False, "reason": "Not enough samples for modal diagram."}

    win = min(max(8, int(round(3600 * sample_rate_hz))), n)
    step = max(1, int(round(win * 0.50)))
    starts = np.arange(0, max(n - win + 1, 0), step, dtype=int)
    if starts.size == 0:
        starts = np.array([0], dtype=int)
    selected = np.rint(np.linspace(0, starts.size - 1, min(300, starts.size))).astype(int)
    selected_starts = starts[selected]

    nfft = min(1 << int(math.ceil(math.log2(win))), 2**17)
    freqs = np.fft.rfftfreq(nfft, d=1 / sample_rate_hz)
    freq_mask = (freqs >= config.fdd_low_hz) & (freqs <= min(config.psd_max_hz, sample_rate_hz / 2))
    modal_freqs = freqs[freq_mask]
    if modal_freqs.size < 5:
        return {"available": False, "reason": "Modal frequency range is too narrow."}

    window = np.hamming(win)
    ax_db = np.empty((selected_starts.size, modal_freqs.size), dtype=np.float32)
    ay_db = np.empty_like(ax_db)
    for row_idx, start in enumerate(selected_starts):
        idx = slice(start, start + win)
        ax_db[row_idx] = _modal_fft_amplitude_db(ax_filtered[idx], window, nfft)[freq_mask]
        ay_db[row_idx] = _modal_fft_amplitude_db(ay_filtered[idx], window, nfft)[freq_mask]

    threshold_db = _safe_percentile(ax_db, 65)
    env_freqs, ax_env = _modal_envelopes(modal_freqs, ax_db)
    _, ay_env = _modal_envelopes(modal_freqs, ay_db)
    smooth_window = max(5, round(len(env_freqs) / 60))
    ax_p90_smooth = _gaussian_smooth(ax_env["p90"], smooth_window)
    ay_p90_smooth = _gaussian_smooth(ay_env["p90"], smooth_window)
    ax_params = _envelope_modal_params(
        env_freqs, ax_p90_smooth, config.f0_low_hz, config.f0_high_hz, config.half_power_bandwidth_fraction
    )
    ay_params = _envelope_modal_params(
        env_freqs, ay_p90_smooth, config.f0_low_hz, config.f0_high_hz, config.half_power_bandwidth_fraction
    )

    fdd_f0 = fdd_summary.get("fdd_f0_hz")
    fdd_zeta = fdd_summary.get("zeta_fdd_pct")
    f0_ref_ax = summary.get("f0_ref_ax_hz")
    f0_ref_ay = summary.get("f0_ref_ay_hz")

    return {
        "available": True,
        "frequencies_hz": modal_freqs,
        "ax_db": ax_db,
        "ay_db": ay_db,
        "threshold_db": threshold_db,
        "env_frequencies_hz": env_freqs,
        "ax_envelope": {**ax_env, "p90_smooth": ax_p90_smooth},
        "ay_envelope": {**ay_env, "p90_smooth": ay_p90_smooth},
        "ax": ax_params,
        "ay": ay_params,
        "window_count": int(selected_starts.size),
        "fdd_f0_hz": fdd_f0,
        "fdd_zeta_pct": fdd_zeta,
        "fdd_match_ax": _frequency_match(fdd_f0, f0_ref_ax, config.f0_tolerance),
        "fdd_match_ay": _frequency_match(fdd_f0, f0_ref_ay, config.f0_tolerance),
    }


def _modal_fft_amplitude_db(values: np.ndarray, window: np.ndarray, nfft: int) -> np.ndarray:
    spectrum = np.fft.rfft(detrend(values) * window, n=nfft)
    amplitude = np.abs(spectrum) / nfft * 2.0
    return 20.0 * np.log10(np.maximum(amplitude, np.finfo(float).eps))


def _modal_envelopes(freqs: np.ndarray, amplitudes_db: np.ndarray) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    bins = 300
    edges = np.linspace(float(np.nanmin(freqs)), float(np.nanmax(freqs)), bins + 1)
    centers = edges[:-1] + np.diff(edges) / 2.0
    out = {
        "p10": np.full(bins, np.nan, dtype=float),
        "p50": np.full(bins, np.nan, dtype=float),
        "p90": np.full(bins, np.nan, dtype=float),
    }
    for idx in range(bins):
        if idx == bins - 1:
            mask = (freqs >= edges[idx]) & (freqs <= edges[idx + 1])
        else:
            mask = (freqs >= edges[idx]) & (freqs < edges[idx + 1])
        if mask.any():
            values = amplitudes_db[:, mask].reshape(-1)
            out["p10"][idx] = _safe_percentile(values, 10)
            out["p50"][idx] = _safe_percentile(values, 50)
            out["p90"][idx] = _safe_percentile(values, 90)
    return centers, out


def _envelope_modal_params(
    freqs: np.ndarray,
    envelope: np.ndarray,
    f0_low_hz: float,
    f0_high_hz: float,
    half_power_fraction: float,
) -> dict[str, object]:
    result = {"f0_hz": None, "zeta_pct": None, "f1_hz": None, "f2_hz": None}
    freqs = np.asarray(freqs, dtype=float)
    envelope = np.asarray(envelope, dtype=float)
    band = (freqs >= f0_low_hz) & (freqs <= f0_high_hz) & np.isfinite(envelope)
    if band.sum() < 3:
        return result
    band_indices = np.flatnonzero(band)
    peak_local = int(np.nanargmax(envelope[band]))
    peak_index = int(band_indices[peak_local])
    peak_value = float(envelope[peak_index])
    f0 = float(freqs[peak_index])
    half_power_level = peak_value * half_power_fraction**2

    left = np.flatnonzero(envelope[:peak_index] <= half_power_level)
    right = np.flatnonzero(envelope[peak_index + 1 :] <= half_power_level)
    f1 = float(freqs[left[-1]]) if left.size else np.nan
    f2 = float(freqs[peak_index + 1 + right[0]]) if right.size else np.nan
    zeta = np.nan
    if np.isfinite(f1) and np.isfinite(f2) and f0 > 0:
        zeta = float((f2 - f1) / (2 * f0) * 100.0)

    return {
        "f0_hz": _rounded(f0, 5),
        "zeta_pct": _rounded(zeta, 3),
        "f1_hz": _rounded(f1, 5),
        "f2_hz": _rounded(f2, 5),
    }


def _frequency_match(value: object, reference: object, tolerance: float) -> bool:
    if value is None or reference is None:
        return False
    value_f = float(value)
    reference_f = float(reference)
    return bool(np.isfinite(value_f) and np.isfinite(reference_f) and abs(value_f - reference_f) <= tolerance * reference_f)


def _period_modal_summary(
    times_utc: pd.DatetimeIndex,
    ax_filtered: np.ndarray,
    ay_filtered: np.ndarray,
    kpis: pd.DataFrame,
    sample_rate_hz: int,
    config: AnalysisConfig,
    period: str,
) -> pd.DataFrame:
    columns = [
        "period_start_utc",
        "period_end_utc",
        "window_count",
        "stable_count",
        "f0_baseline_ax_hz",
        "f0_baseline_ay_hz",
        "f0_shift_ax_hz",
        "f0_shift_ay_hz",
        "f0_drift_ax_hz_per_day",
        "f0_drift_ay_hz_per_day",
        "fdd_f0_hz",
        "zeta_fdd_pct",
        "fdd_segments_used",
    ]
    if kpis.empty:
        return pd.DataFrame(columns=columns)

    if period == "daily":
        period_start = _day_start_utc(kpis["time_utc"])
        period_length = pd.Timedelta(days=1)
    elif period == "weekly":
        period_start = _week_start_utc(kpis["time_utc"])
        period_length = pd.Timedelta(days=7)
    else:
        raise ValueError(f"Unsupported period {period!r}")
    frame = kpis.assign(period_start_utc=period_start)
    rows: list[dict[str, object]] = []

    for period_start_value, group in frame.groupby("period_start_utc", sort=True):
        period_start_ts = pd.Timestamp(period_start_value)
        period_end_ts = period_start_ts + period_length
        sample_mask = (times_utc >= period_start_ts) & (times_utc < period_end_ts)
        stable = group["is_stable"].to_numpy(dtype=bool) if "is_stable" in group else np.ones(len(group), dtype=bool)
        ax_values = group["f0_ax_hz"].to_numpy(dtype=float)
        ay_values = group["f0_ay_hz"].to_numpy(dtype=float)
        ax_ok = stable & np.isfinite(ax_values)
        ay_ok = stable & np.isfinite(ay_values)

        fdd = _fdd_modal_summary(
            times_utc=times_utc[sample_mask],
            ax_filtered=ax_filtered[sample_mask],
            ay_filtered=ay_filtered[sample_mask],
            kpis=group.reset_index(drop=True),
            sample_rate_hz=sample_rate_hz,
            config=config,
        )
        rows.append(
            {
                "period_start_utc": period_start_ts,
                "period_end_utc": min(
                    period_end_ts,
                    pd.Timestamp(group["time_utc"].max()) + pd.Timedelta(minutes=config.window_minutes),
                ),
                "window_count": int(len(group)),
                "stable_count": int(stable.sum()),
                "f0_baseline_ax_hz": _rounded(_safe_median(ax_values[ax_ok]), 5),
                "f0_baseline_ay_hz": _rounded(_safe_median(ay_values[ay_ok]), 5),
                "f0_drift_ax_hz_per_day": _rounded(_window_drift(group["time_utc"], ax_values, ax_ok), 6),
                "f0_drift_ay_hz_per_day": _rounded(_window_drift(group["time_utc"], ay_values, ay_ok), 6),
                "fdd_f0_hz": fdd["fdd_f0_hz"],
                "zeta_fdd_pct": fdd["zeta_fdd_pct"],
                "fdd_segments_used": fdd["fdd_segments_used"],
            }
        )

    periods = pd.DataFrame(rows)
    if periods.empty:
        return pd.DataFrame(columns=columns)
    periods["f0_shift_ax_hz"] = periods["f0_baseline_ax_hz"].astype(float).diff()
    periods["f0_shift_ay_hz"] = periods["f0_baseline_ay_hz"].astype(float).diff()
    periods["f0_shift_ax_hz"] = periods["f0_shift_ax_hz"].map(lambda value: _rounded(value, 6))
    periods["f0_shift_ay_hz"] = periods["f0_shift_ay_hz"].map(lambda value: _rounded(value, 6))
    return periods.reindex(columns=columns)


def _datetime_ns(values: object) -> np.ndarray:
    return pd.to_datetime(values, utc=True).to_numpy(dtype="datetime64[ns]").astype(np.int64)


def _nearest_index(sorted_ns: np.ndarray, value_ns: int) -> int | None:
    if sorted_ns.size == 0:
        return None
    pos = int(np.searchsorted(sorted_ns, value_ns))
    if pos <= 0:
        return 0
    if pos >= sorted_ns.size:
        return sorted_ns.size - 1
    before = pos - 1
    return before if value_ns - sorted_ns[before] <= sorted_ns[pos] - value_ns else pos


def _robust_rpm_median(
    kpis: pd.DataFrame,
    frequencies: np.ndarray,
    singular_db: np.ndarray,
    config: AnalysisConfig,
) -> float:
    rpm_min, rpm_max = 5.0, 22.0
    rpm_values = kpis["rpm"].to_numpy(dtype=float) if "rpm" in kpis else np.array([], dtype=float)
    is_on = kpis["is_on"].to_numpy(dtype=bool) if "is_on" in kpis else np.ones(len(kpis), dtype=bool)
    is_stable = kpis["is_stable"].to_numpy(dtype=bool) if "is_stable" in kpis else is_on
    rpm_valid = kpis["rpm_valid"].to_numpy(dtype=bool) if "rpm_valid" in kpis else np.isfinite(rpm_values)

    candidate_masks = (
        (rpm_valid & is_on, 3),
        (is_stable & np.isfinite(rpm_values), 3),
        (is_on & np.isfinite(rpm_values), 1),
    )
    for mask, minimum_count in candidate_masks:
        values = rpm_values[mask]
        values = values[(values >= rpm_min) & (values <= rpm_max)]
        if values.size >= minimum_count:
            return float(np.nanmedian(values))

    f3p_lo = rpm_min * 3 / 60
    f3p_hi = rpm_max * 3 / 60
    harmonic_zone = (
        (frequencies >= f3p_lo)
        & (frequencies <= f3p_hi)
        & ((frequencies < config.f0_low_hz) | (frequencies > config.f0_high_hz))
    )
    if harmonic_zone.any():
        zone_values = singular_db[harmonic_zone]
        peak = float(np.nanmax(zone_values))
        background = float(np.nanmedian(zone_values))
        if np.isfinite(peak) and np.isfinite(background) and peak > background + 3:
            zone_freqs = frequencies[harmonic_zone]
            return float(zone_freqs[int(np.nanargmax(zone_values))] / 3 * 60)
    return np.nan


def _gaussian_smooth(values: np.ndarray, window_size: int) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.size < 3 or window_size <= 1:
        return arr.copy()
    window_size = min(window_size, arr.size if arr.size % 2 else arr.size - 1)
    if window_size < 3:
        return arr.copy()
    radius = window_size // 2
    x = np.arange(-radius, radius + 1, dtype=float)
    sigma = max(window_size / 6.0, 1e-6)
    weights = np.exp(-0.5 * np.square(x / sigma))
    weights /= weights.sum()
    padded = np.pad(arr, radius, mode="edge")
    return np.convolve(padded, weights, mode="valid")


def _week_start_utc(times: pd.Series) -> pd.Series:
    series = pd.to_datetime(times, utc=True)
    normalized = series.dt.normalize()
    return normalized - pd.to_timedelta(normalized.dt.weekday, unit="D")


def _day_start_utc(times: pd.Series) -> pd.Series:
    return pd.to_datetime(times, utc=True).dt.normalize()


def _window_drift(times: pd.Series, values: np.ndarray, mask: np.ndarray) -> float:
    time_series = pd.to_datetime(times, utc=True)
    t_days = (time_series - time_series.iloc[0]).dt.total_seconds().to_numpy() / 86400.0
    ok = mask & np.isfinite(values)
    if ok.sum() <= 20:
        return np.nan
    return float(np.polyfit(t_days[ok], values[ok], 1)[0])


def _transition_mask(mask_on: np.ndarray, transition_windows: int) -> np.ndarray:
    changed = np.diff(np.r_[False, mask_on, False].astype(int))
    starts = np.where(changed == 1)[0]
    stops = np.where(changed == -1)[0]
    mask = np.zeros_like(mask_on, dtype=bool)
    n = len(mask_on)
    for start in starts:
        lo = max(0, start - transition_windows)
        hi = min(n, start + transition_windows + 1)
        mask[lo:hi] = True
    for stop in stops:
        lo = max(0, stop - transition_windows - 1)
        hi = min(n, stop + transition_windows)
        mask[lo:hi] = True
    return mask


def _rms_thresholds(
    kpis: pd.DataFrame,
    mask_ref: np.ndarray,
    mask_stable: np.ndarray,
    has_wind: bool,
    reference: ReferenceData | None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    n = len(kpis)
    bins = (
        reference.vbins.astype(float, copy=False)
        if reference is not None and reference.vbins.size >= 2
        else np.array([3, 5, 7, 9, 11, np.inf], dtype=float)
    )
    nb = len(bins) - 1
    bin_id = np.zeros(n, dtype=int)
    if has_wind:
        wind = kpis["wind_ms"].to_numpy(dtype=float)
        for idx in range(nb):
            matched = np.isfinite(wind) & (wind >= bins[idx]) & (wind < bins[idx + 1])
            bin_id[matched] = idx + 1
    else:
        bin_id[mask_stable] = 1

    ref_columns = {
        "p95_ax": ("p95_ax", "rep_ax95"),
        "p99_ax": ("p99_ax", "rep_ax99"),
        "p95_ay": ("p95_ay", "rep_ay95"),
        "p99_ay": ("p99_ay", "rep_ay99"),
        "p95_bb": ("p95_bb", "rep_bb95"),
        "p99_bb": ("p99_bb", "rep_bb99"),
        "p95_bf_ax": ("p95_bf_ax", "rep_bf_ax95"),
        "p99_bf_ax": ("p99_bf_ax", "rep_bf_ax99"),
        "p95_bf_ay": ("p95_bf_ay", "rep_bf_ay95"),
        "p99_bf_ay": ("p99_bf_ay", "rep_bf_ay99"),
        "p95_res_ax": ("p95_res_ax", "rep_res_ax95"),
        "p99_res_ax": ("p99_res_ax", "rep_res_ax99"),
        "p95_res_ay": ("p95_res_ay", "rep_res_ay95"),
        "p99_res_ay": ("p99_res_ay", "rep_res_ay99"),
    }
    thresholds: dict[str, np.ndarray] = {}
    if reference is None:
        for output_column in ref_columns:
            thresholds[output_column] = np.full(n, np.nan)
        return bin_id, thresholds

    for output_column, (source_name, fallback_name) in ref_columns.items():
        source_values = np.asarray(reference.thresholds.get(source_name, []), dtype=float)
        fallback = float(reference.fallback_thresholds.get(fallback_name, np.nan))
        out = np.full(n, np.nan)
        for row, bin_value in enumerate(bin_id):
            if bin_value <= 0:
                continue
            idx = bin_value - 1
            value = source_values[idx] if idx < source_values.size else np.nan
            out[row] = value if np.isfinite(value) else fallback
        thresholds[output_column] = out
    return bin_id, thresholds


def _drift_and_trend(
    kpis: pd.DataFrame,
    column: str,
    mask: np.ndarray,
    cfg: AnalysisConfig,
) -> tuple[float, np.ndarray]:
    values = kpis[column].to_numpy(dtype=float)
    t_days = (kpis["time_utc"] - kpis["time_utc"].iloc[0]).dt.total_seconds().to_numpy() / 86400.0
    ok = mask & np.isfinite(values)
    drift = np.nan
    if ok.sum() > 20:
        drift = float(np.polyfit(t_days[ok], values[ok], 1)[0])
    trend_window = max(1, int(round(24 * 60 / (cfg.window_minutes * (1 - cfg.overlap)))))
    trend_source = pd.Series(np.where(mask, values, np.nan))
    trend = trend_source.rolling(trend_window, min_periods=1, center=True).median().to_numpy()
    return drift, trend


def _outside(values: np.ndarray, low: float, high: float) -> np.ndarray:
    if not np.isfinite(low) or not np.isfinite(high):
        return np.zeros_like(values, dtype=bool)
    return np.isfinite(values) & ((values < low) | (values > high))


def _previous(values: np.ndarray, periods: int) -> np.ndarray:
    if periods <= 0:
        return values
    out = np.zeros_like(values, dtype=bool)
    out[periods:] = values[:-periods]
    return out


def _severity(mask_on: np.ndarray, confirmed: np.ndarray, warning: np.ndarray) -> list[str]:
    labels: list[str] = []
    for on, is_confirmed, is_warning in zip(mask_on, confirmed, warning, strict=True):
        if not on:
            labels.append("Stopped")
        elif is_confirmed:
            labels.append("Alert")
        elif is_warning:
            labels.append("Warning")
        else:
            labels.append("OK")
    return labels


def _reasons(kpis: pd.DataFrame) -> list[str]:
    reasons: list[str] = []
    for _, row in kpis.iterrows():
        parts: list[str] = []
        if bool(row["alert_f0"]):
            parts.append("f0 outside reference band")
        if bool(row["alert_rms_ax"]) or bool(row["alert_rms_ay"]):
            parts.append("RMS above P99.5")
        if bool(row["alert_ewma_ax"]) or bool(row["alert_ewma_ay"]):
            parts.append("EWMA above P95 for 3 windows")
        if bool(row["info_ax"]) or bool(row["info_ay"]):
            parts.append("RMS above P95")
        reasons.append("; ".join(parts))
    return reasons


def _bounds(reference: float, tolerance: float) -> tuple[float, float]:
    if not np.isfinite(reference):
        return np.nan, np.nan
    return reference * (1 - tolerance), reference * (1 + tolerance)


def _safe_percentile(values: pd.Series | np.ndarray, q: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return np.nan
    return float(np.nanpercentile(arr, q))


def _safe_median(values: pd.Series | np.ndarray) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return np.nan
    return float(np.nanmedian(arr))


def _nanmean(values: np.ndarray) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return np.nan
    return float(np.mean(arr))


def _to_db(values: np.ndarray) -> np.ndarray:
    return 10.0 * np.log10(np.maximum(values, 1e-18))


def _rounded(value: float, digits: int) -> float | None:
    if value is None:
        return None
    if not np.isfinite(value):
        return None
    return round(float(value), digits)
