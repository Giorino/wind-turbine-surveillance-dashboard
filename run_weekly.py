from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from wind_dashboard import (
    AnalysisConfig,
    accelerometer_dir_for_turbine,
    analyze_dataset,
    discover_turbine_ids,
    scada_dir_for_dataset,
)
from wind_dashboard.analysis import discover_accelerometer_files, discover_scada_files
from wind_dashboard.reports import build_all_weekly_text_reports


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET_DIR = BASE_DIR / "dataset"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one weekly wind turbine surveillance analysis.")
    parser.add_argument("--turbine", default=None, help="Optional turbine id. Defaults to the first dataset/data* folder.")
    parser.add_argument(
        "--dataset-dir",
        default=str(DEFAULT_DATASET_DIR),
        help="Root dataset folder. Expected structure: data<id> turbine folders plus SCADA.",
    )
    parser.add_argument(
        "--accel-dir",
        default=None,
        help="Optional override for the accelerometer folder.",
    )
    parser.add_argument(
        "--scada-dir",
        default=None,
        help="Optional override for the SCADA folder.",
    )
    parser.add_argument("--window-minutes", type=int, default=10)
    parser.add_argument("--overlap", type=float, default=0.5)
    parser.add_argument(
        "--reference-dir",
        default=None,
        help="Optional folder containing REF_<turbine>_*.mat reference files.",
    )
    parser.add_argument(
        "--use-reference-files",
        action="store_true",
        help="Load REF_*.mat files from --reference-dir instead of computing the baseline from current data.",
    )
    parser.add_argument("--output-dir", default=str(BASE_DIR / "outputs" / "latest_week"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    turbine_id = args.turbine
    if turbine_id is None:
        discovered = discover_turbine_ids(args.dataset_dir)
        if not discovered:
            raise SystemExit(f"No turbine folders found in {args.dataset_dir}. Expected folders such as data5 or data7.")
        turbine_id = discovered[0]

    accel_dir = Path(args.accel_dir) if args.accel_dir else accelerometer_dir_for_turbine(args.dataset_dir, turbine_id)
    scada_dir = Path(args.scada_dir) if args.scada_dir else scada_dir_for_dataset(args.dataset_dir)

    accel_files = discover_accelerometer_files(accel_dir)
    scada_files = discover_scada_files(scada_dir)
    config = AnalysisConfig(
        turbine_id=turbine_id,
        window_minutes=args.window_minutes,
        overlap=args.overlap,
        reference_dir=args.reference_dir,
        use_reference_files=args.use_reference_files,
    )
    result = analyze_dataset(accel_files, scada_files, config)

    kpi_path = output_dir / "weekly_kpis.csv"
    weekly_path = output_dir / "weekly_summary.csv"
    summary_path = output_dir / "summary.json"
    psd_path = output_dir / "psd_arrays.npz"
    report_dir = output_dir / "reports"

    result.kpis.to_csv(kpi_path, index=False)
    result.weekly.to_csv(weekly_path, index=False)
    summary_path.write_text(json.dumps(result.summary, indent=2), encoding="utf-8")
    np.savez_compressed(
        psd_path,
        frequencies_hz=result.psd_frequencies_hz,
        psd_ax_db=result.psd_ax_db,
        psd_ay_db=result.psd_ay_db,
    )
    report_dir.mkdir(exist_ok=True)
    for report in build_all_weekly_text_reports(result):
        (report_dir / report.filename).write_text(report.content, encoding="utf-8")

    print(f"Wrote {kpi_path}")
    print(f"Wrote {weekly_path}")
    print(f"Wrote {summary_path}")
    print(f"Wrote {psd_path}")
    print(f"Wrote weekly reports to {report_dir}")
    print(
        "Weekly baseline: "
        f"AX={result.summary.get('latest_week_f0_ax_hz')} Hz, "
        f"AY={result.summary.get('latest_week_f0_ay_hz')} Hz, "
        f"zeta={result.summary.get('latest_week_zeta_pct')}%"
    )


if __name__ == "__main__":
    main()
