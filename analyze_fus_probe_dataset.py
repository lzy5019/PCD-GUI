"""Offline analysis utility for paired FUS-Probe run folders."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import statistics

import numpy as np

from fus_probe_algorithm import FusProbeFeatureSettings, analyze_fus_probe_pair


def load_capture_csv(path: Path) -> tuple[np.ndarray, float]:
    loaded = np.loadtxt(path, delimiter=",", skiprows=1, usecols=(2, 3), dtype=float)
    if loaded.ndim != 2 or loaded.shape[0] < 16:
        raise ValueError(f"Capture CSV has insufficient data: {path}")
    sample_rate_hz = float(loaded[0, 1])
    return np.asarray(loaded[:, 0], dtype=float), sample_rate_hz


def analyze_run(run_dir: Path, settings: FusProbeFeatureSettings | None = None) -> list[dict[str, object]]:
    manifest_path = run_dir / "manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing manifest.csv: {run_dir}")

    rows: list[dict[str, object]] = []
    with manifest_path.open("r", newline="", encoding="utf-8-sig") as manifest_file:
        for manifest_row in csv.DictReader(manifest_file):
            if manifest_row.get("status") != "complete":
                continue
            probe_signal, probe_rate_hz = load_capture_csv(run_dir / manifest_row["probe_file"])
            treatment_signal, treatment_rate_hz = load_capture_csv(run_dir / manifest_row["treatment_file"])
            if abs(probe_rate_hz - treatment_rate_hz) > 1e-6:
                raise ValueError(f"Sample-rate mismatch in cycle {manifest_row['cycle_id']}")
            metrics = analyze_fus_probe_pair(
                probe_signal,
                treatment_signal,
                probe_rate_hz,
                float(manifest_row["frequency_hz"]),
                float(manifest_row["probe_vpp"]),
                float(manifest_row["treatment_vpp"]),
                settings,
                cycle_id=int(manifest_row["cycle_id"]),
            )
            rows.append(
                {
                    "run": run_dir.name,
                    "cycle_id": metrics.cycle_id,
                    "probe_valid": metrics.probe_valid,
                    "probe_quality_db": metrics.probe_quality_db,
                    "probe_rms_mv": metrics.probe_rms_mv,
                    "treatment_rms_mv": metrics.treatment_rms_mv,
                    "uhe_db": metrics.uhe_db,
                    "be_db": metrics.be_db,
                    **{
                        f"uh{order:g}_db": ratio
                        for order, ratio in zip(
                            metrics.ultraharmonic_orders,
                            metrics.ultraharmonic_corrected_ratios_db,
                        )
                    },
                    **{
                        f"uh{order:g}_raw_db": ratio
                        for order, ratio in zip(
                            metrics.ultraharmonic_orders,
                            metrics.ultraharmonic_ratios_db,
                        )
                    },
                }
            )
    return rows


def percentile(values: list[float], quantile: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), quantile))


def summarize(rows: list[dict[str, object]]) -> dict[str, float | int | str]:
    if not rows:
        raise ValueError("No complete FUS-Probe pairs were analyzed.")

    def values(key: str) -> list[float]:
        return [float(row[key]) for row in rows if np.isfinite(float(row[key]))]

    uhe = values("uhe_db")
    be = values("be_db")
    quality = values("probe_quality_db")
    summary: dict[str, float | int | str] = {
        "run": str(rows[0]["run"]),
        "cycles": len(rows),
        "valid_probe_fraction": float(np.mean([bool(row["probe_valid"]) for row in rows])),
        "probe_quality_median_db": statistics.median(quality),
        "probe_rms_median_mv": statistics.median(values("probe_rms_mv")),
        "treatment_rms_median_mv": statistics.median(values("treatment_rms_mv")),
        "uhe_median_db": statistics.median(uhe),
        "uhe_p05_db": percentile(uhe, 5),
        "uhe_p95_db": percentile(uhe, 95),
        "be_median_db": statistics.median(be),
        "be_p05_db": percentile(be, 5),
        "be_p95_db": percentile(be, 95),
    }
    return summary


def write_rows(rows: list[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with output_path.open("w", newline="", encoding="utf-8-sig") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, help="Optional per-cycle CSV output")
    parser.add_argument("--uhe-correction-db", type=float, default=0.0)
    parser.add_argument("--be-correction-db", type=float, default=0.0)
    args = parser.parse_args()

    feature_settings = FusProbeFeatureSettings(
        uhe_system_correction_db=args.uhe_correction_db,
        be_system_correction_db=args.be_correction_db,
    )
    all_rows: list[dict[str, object]] = []
    for run_dir in args.run_dirs:
        run_rows = analyze_run(run_dir.resolve(), feature_settings)
        all_rows.extend(run_rows)
        summary = summarize(run_rows)
        print(", ".join(f"{key}={value}" for key, value in summary.items()))
    if args.output and all_rows:
        write_rows(all_rows, args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
