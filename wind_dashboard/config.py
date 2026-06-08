from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class TurbineConfig:
    turbine_id: str
    cutin_ms: float
    zone2_wind_min: float
    zone2_wind_max: float
    nominal_power_kw: float
    nominal_rpm: float
    rpm_resonance_hz: float = 7.0
    rpm_margin: float = 1.0

    @property
    def power_zone2_max_kw(self) -> float:
        return 0.85 * self.nominal_power_kw

    @property
    def power_column(self) -> str:
        return f"{self.turbine_id}Power"

    @property
    def wind_column(self) -> str:
        return f"{self.turbine_id}Speed"

    @property
    def rpm_column(self) -> str:
        return f"{self.turbine_id}RotorSpeed"


DEFAULT_TURBINE_CONFIG = TurbineConfig(
    turbine_id="w000",
    cutin_ms=2.75,
    zone2_wind_min=4.0,
    zone2_wind_max=8.5,
    nominal_power_kw=2000.0,
    nominal_rpm=13.0,
)

TURBINE_PARAMETER_OVERRIDES: dict[str, dict[str, float]] = {
    "w003": {
        "cutin_ms": 2.50,
        "zone2_wind_min": 3.8,
        "nominal_power_kw": 500.0,
    },
    "w007": {
        "cutin_ms": 2.25,
        "zone2_wind_min": 3.8,
    },
}


def turbine_id_from_dataset_dir(path: str | Path) -> str:
    name = Path(path).name.lower()
    match = re.fullmatch(r"data(\d+)", name)
    if match:
        return f"w{int(match.group(1)):03d}"
    return name


def accelerometer_dir_for_turbine(dataset_dir: str | Path, turbine_id: str) -> Path:
    suffix = turbine_id.lower().removeprefix("w").lstrip("0") or turbine_id.lower()
    return Path(dataset_dir) / f"data{suffix}"


def scada_dir_for_dataset(dataset_dir: str | Path) -> Path:
    return Path(dataset_dir) / "SCADA"


def discover_turbine_ids(dataset_dir: str | Path) -> list[str]:
    root = Path(dataset_dir)
    if not root.exists():
        return []
    turbine_ids = [
        turbine_id_from_dataset_dir(path)
        for path in root.iterdir()
        if path.is_dir() and path.name.lower() != "scada"
    ]
    return sorted(set(turbine_ids))


def get_turbine_config(turbine_id: str) -> TurbineConfig:
    key = turbine_id.lower()
    config = replace(DEFAULT_TURBINE_CONFIG, turbine_id=key)
    overrides = TURBINE_PARAMETER_OVERRIDES.get(key)
    if overrides:
        config = replace(config, **overrides)
    return config
