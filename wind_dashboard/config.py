from __future__ import annotations

from dataclasses import dataclass


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


TURBINES: dict[str, TurbineConfig] = {
    "w003": TurbineConfig(
        turbine_id="w003",
        cutin_ms=2.50,
        zone2_wind_min=3.8,
        zone2_wind_max=8.5,
        nominal_power_kw=500.0,
        nominal_rpm=13.0,
    ),
    "w005": TurbineConfig(
        turbine_id="w005",
        cutin_ms=2.75,
        zone2_wind_min=4.0,
        zone2_wind_max=8.5,
        nominal_power_kw=2000.0,
        nominal_rpm=13.0,
    ),
    "w007": TurbineConfig(
        turbine_id="w007",
        cutin_ms=2.25,
        zone2_wind_min=3.8,
        zone2_wind_max=8.5,
        nominal_power_kw=2000.0,
        nominal_rpm=13.0,
    ),
}
