from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from wind_dashboard import AnalysisConfig, analyze_dataset
from wind_dashboard.analysis import discover_accelerometer_files, discover_scada_files


BASE_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one weekly wind turbine surveillance analysis.")
    parser.add_argument("--turbine", default="w005", help="Turbine id such as w003, w005, or w007.")
    parser.add_argument(
        "--accel-dir",
        default=str(BASE_DIR / "Echantillon 1Hz" / "data5"),
        help="Folder containing accelerometer CSV or CSV ZIP files.",
    )
    parser.add_argument(
        "--scada-dir",
        default=str(BASE_DIR / "Echantillon 1Hz" / "SCADA"),
        help="Folder containing SCADA CSV files.",
    )
    parser.add_argument("--window-minutes", type=int, default=10)
    parser.add_argument("--overlap", type=float, default=0.5)
    parser.add_argument(
        "--reference-dir",
        default=str(BASE_DIR / "reference-matlab-files"),
        help="Folder containing REF_<turbine>_*.mat reference files.",
    )
    parser.add_argument(
        "--no-reference-files",
        action="store_true",
        help="Compute the f0 reference from the current data instead of loading REF_*.mat.",
    )
    parser.add_argument("--output-dir", default=str(BASE_DIR / "outputs" / "latest_week"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    accel_files = discover_accelerometer_files(args.accel_dir)
    scada_files = discover_scada_files(args.scada_dir)
    config = AnalysisConfig(
        turbine_id=args.turbine,
        window_minutes=args.window_minutes,
        overlap=args.overlap,
        reference_dir=args.reference_dir,
        use_reference_files=not args.no_reference_files,
    )
    result = analyze_dataset(accel_files, scada_files, config)

    kpi_path = output_dir / "weekly_kpis.csv"
    weekly_path = output_dir / "weekly_summary.csv"
    summary_path = output_dir / "summary.json"
    psd_path = output_dir / "psd_arrays.npz"

    result.kpis.to_csv(kpi_path, index=False)
    result.weekly.to_csv(weekly_path, index=False)
    summary_path.write_text(json.dumps(result.summary, indent=2), encoding="utf-8")
    np.savez_compressed(
        psd_path,
        frequencies_hz=result.psd_frequencies_hz,
        psd_ax_db=result.psd_ax_db,
        psd_ay_db=result.psd_ay_db,
    )

    print(f"Wrote {kpi_path}")
    print(f"Wrote {weekly_path}")
    print(f"Wrote {summary_path}")
    print(f"Wrote {psd_path}")
    print(
        "Weekly baseline: "
        f"AX={result.summary.get('latest_week_f0_ax_hz')} Hz, "
        f"AY={result.summary.get('latest_week_f0_ay_hz')} Hz, "
        f"zeta={result.summary.get('latest_week_zeta_pct')}%"
    )


if __name__ == "__main__":
    main()
