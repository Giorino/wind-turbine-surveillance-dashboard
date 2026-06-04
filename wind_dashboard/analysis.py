from __future__ import annotations

import math
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.signal import butter, detrend, filtfilt

from .config import TURBINES, TurbineConfig


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

    @property
    def turbine(self) -> TurbineConfig:
        key = self.turbine_id.lower()
        if key not in TURBINES:
            raise ValueError(f"Unknown turbine '{self.turbine_id}'. Expected one of {', '.join(TURBINES)}.")
        return TURBINES[key]


@dataclass
class AnalysisResult:
    kpis: pd.DataFrame
    summary: dict[str, object]
    psd_frequencies_hz: np.ndarray
    psd_ax_db: np.ndarray
    psd_ay_db: np.ndarray
    sample_rate_hz: int


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

    kpis, summary = _classify_windows(windows, sample_rate_hz, cfg)
    summary.update(
        {
            "turbine_id": turbine.turbine_id.upper(),
            "sample_rate_hz": sample_rate_hz,
            "accelerometer_rows": int(len(acc)),
            "scada_rows": int(len(scada)),
        }
    )

    return AnalysisResult(
        kpis=kpis,
        summary=summary,
        psd_frequencies_hz=psd_freqs,
        psd_ax_db=psd_ax_db,
        psd_ay_db=psd_ay_db,
        sample_rate_hz=sample_rate_hz,
    )


def discover_accelerometer_files(path: str | Path) -> list[Path]:
    root = Path(path)
    if root.is_file():
        return [root]
    if not root.exists():
        return []
    files = [
        *root.glob("*.csv"),
        *root.glob("*.csv.zip"),
        *root.glob("*.zip"),
    ]
    return sorted(p for p in files if not p.name.startswith("._"))


def discover_scada_files(path: str | Path) -> list[Path]:
    root = Path(path)
    if root.is_file():
        return [root]
    if not root.exists():
        return []
    return sorted(p for p in root.glob("*.csv") if not p.name.startswith("._"))


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
        frame = pd.read_csv(path)
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
            f0 = _detect_f0(freqs, pxx, m_s, rpm_k, config.harmonic_bandwidth_hz)
            row[f"f0_{axis_name}_hz"] = f0
            row[f"rms_{axis_name}"] = rms_nb
            row[f"broadband_{axis_name}"] = rms_bb
            if axis_name == "ax":
                row["rms_1p_ax"] = _harmonic_rms(freqs, pxx, rpm_k, 1, config.harmonic_bandwidth_hz, df)
                row["rms_3p_ax"] = _harmonic_rms(freqs, pxx, rpm_k, 3, config.harmonic_bandwidth_hz, df)
                psd_ax.append(_to_db(pxx[m_psd]))
            else:
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
    return (2.0 / sample_rate_hz) * np.square(np.abs(spectrum)) / window_power


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
) -> tuple[pd.DataFrame, dict[str, object]]:
    turbine = cfg.turbine
    kpis = windows.copy()
    n = len(kpis)

    has_power = kpis["power_kw"].notna().any()
    has_wind = kpis["wind_ms"].notna().any()
    has_rpm = kpis["rpm"].notna().any()

    if has_power and (kpis["power_kw"] > cfg.power_on_threshold_kw).any():
        mask_on = (kpis["power_kw"] > cfg.power_on_threshold_kw).to_numpy()
    elif has_wind:
        mask_on = (kpis["wind_ms"] > turbine.cutin_ms).to_numpy()
    else:
        floor = np.nanpercentile(np.r_[kpis["rms_ax"], kpis["rms_ay"]], 20)
        mask_on = ((kpis["rms_ax"] > floor) | (kpis["rms_ay"] > floor)).to_numpy()

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

    mask_ref_f0 = mask_ref & f0ax_fbl & f0ay_fbl
    if has_wind:
        wind_ctx = ((kpis["wind_ms"] > 5) & (kpis["wind_ms"] < 9)).to_numpy()
        if (mask_ref_f0 & wind_ctx).sum() >= 10:
            mask_ref_f0 &= wind_ctx

    f0_ref_ax = _safe_median(kpis.loc[mask_ref_f0, "f0_ax_hz"])
    f0_ref_ay = _safe_median(kpis.loc[mask_ref_f0, "f0_ay_hz"])
    if not np.isfinite(f0_ref_ax):
        f0_ref_ax = _safe_median(kpis.loc[f0ax_fbl, "f0_ax_hz"])
    if not np.isfinite(f0_ref_ay):
        f0_ref_ay = _safe_median(kpis.loc[f0ay_fbl, "f0_ay_hz"])

    f0_lo_ax, f0_hi_ax = _bounds(f0_ref_ax, cfg.f0_tolerance)
    f0_lo_ay, f0_hi_ay = _bounds(f0_ref_ay, cfg.f0_tolerance)

    bin_id, threshold_frame = _rms_thresholds(kpis, mask_ref, mask_stable, has_wind)
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
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    n = len(kpis)
    bins = np.array([3, 5, 7, 9, 11, np.inf], dtype=float)
    bin_id = np.zeros(n, dtype=int)
    if has_wind:
        wind = kpis["wind_ms"].to_numpy(dtype=float)
        for idx in range(len(bins) - 1):
            matched = np.isfinite(wind) & (wind >= bins[idx]) & (wind < bins[idx + 1])
            bin_id[matched] = idx + 1
    else:
        bin_id[mask_stable] = 1

    thresholds: dict[str, np.ndarray] = {}
    source_cols = {
        "ax": "rms_ax",
        "ay": "rms_ay",
        "bb": "broadband_ax",
    }
    for suffix, source in source_cols.items():
        p95 = np.full(len(bins) - 1, np.nan)
        p99 = np.full(len(bins) - 1, np.nan)
        values = kpis[source].to_numpy(dtype=float)
        for idx in range(len(bins) - 1):
            matched = mask_ref & (bin_id == idx + 1)
            if matched.sum() >= 5:
                p95[idx] = np.nanpercentile(values[matched], 95)
                p99[idx] = np.nanpercentile(values[matched], 99.5)
        fallback95 = _safe_percentile(values[mask_ref], 95)
        fallback99 = _safe_percentile(values[mask_ref], 99.5)
        if not np.isfinite(fallback95):
            fallback95 = _safe_percentile(values[mask_stable], 95)
        if not np.isfinite(fallback99):
            fallback99 = _safe_percentile(values[mask_stable], 99.5)

        out95 = np.full(n, np.nan)
        out99 = np.full(n, np.nan)
        for row in range(n):
            b = bin_id[row]
            if b > 0:
                out95[row] = p95[b - 1] if np.isfinite(p95[b - 1]) else fallback95
                out99[row] = p99[b - 1] if np.isfinite(p99[b - 1]) else fallback99
        thresholds[f"p95_{suffix}"] = out95
        thresholds[f"p99_{suffix}"] = out99

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
    if not np.isfinite(value):
        return None
    return round(float(value), digits)
