from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
TEMP = ROOT / "Temp"
OUT = TEMP / "codex记录"

SAMPLE_RATE_HZ = 25_000_000.0
TARGET_SAMPLE_COUNT = 25_000
SEGMENT_COUNT = 2
SEARCH_BAND_LOW_HZ = 450_000.0
SEARCH_BAND_HIGH_HZ = 750_000.0
BROADBAND_HALF_WIDTH_HZ = 75_000.0
ORDER_RANGE_LOW = 1.5
ORDER_RANGE_HIGH = 5.25
FIXED_F0_HZ = 608_000.0
FLOAT_EPS = 1e-30


@dataclass
class FileMetrics:
    group: str
    file: str
    f0_hz: float
    icd_estimated_f0: float
    icd_fixed_608k: float
    std_mv: float
    ptp_mv: float
    mean_mv: float
    band_60_85k_power: float
    band_450_750k_power: float
    band_1_30_1_43m_power: float
    top_peak_hz: float
    top_peak_power: float


def closed_interval_sequence(start: float, end: float, offset: float, step: float) -> list[float]:
    first = math.ceil((start - offset - 1e-9) / step) * step + offset
    values: list[float] = []
    value = first
    while value <= end + 1e-9:
        values.append(float(value))
        value += step
    return values


def broadband_centers(f0_hz: float) -> np.ndarray:
    factors = closed_interval_sequence(ORDER_RANGE_LOW, ORDER_RANGE_HIGH, offset=0.25, step=0.5)
    return np.asarray(factors, dtype=float) * f0_hz


def read_voltage_mv(path: Path) -> np.ndarray:
    data = np.genfromtxt(path, delimiter=",", names=True, dtype=None, encoding=None)
    return np.asarray(data["voltage_mV"], dtype=float)[:TARGET_SAMPLE_COUNT]


def periodic_hann(sample_count: int) -> np.ndarray:
    index = np.arange(sample_count, dtype=float)
    return 0.5 - 0.5 * np.cos((2.0 * np.pi * index) / sample_count)


def single_power_spectrum(signal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    signal = np.asarray(signal, dtype=float).reshape(-1)
    centered = signal - np.mean(signal)
    n = centered.size
    fft_values = np.fft.rfft(centered * periodic_hann(n))
    amplitude = np.abs(fft_values / n)
    if amplitude.size > 2:
        amplitude[1:-1] *= 2
    frequency_hz = np.fft.rfftfreq(n, d=1.0 / SAMPLE_RATE_HZ)
    return frequency_hz, amplitude**2


def segment_average_power_spectrum(signal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    signal = np.asarray(signal, dtype=float).reshape(-1)
    segment_len = signal.size // SEGMENT_COUNT
    spectra: list[np.ndarray] = []
    frequency_hz = np.asarray([], dtype=float)
    for i in range(SEGMENT_COUNT):
        segment = signal[i * segment_len : (i + 1) * segment_len]
        frequency_hz, power = single_power_spectrum(segment)
        spectra.append(power)
    return frequency_hz, np.mean(np.vstack(spectra), axis=0)


def estimate_f0_hz(frequency_hz: np.ndarray, power: np.ndarray) -> float:
    mask = (frequency_hz >= SEARCH_BAND_LOW_HZ) & (frequency_hz <= SEARCH_BAND_HIGH_HZ)
    if not np.any(mask):
        return FIXED_F0_HZ
    return float(frequency_hz[mask][int(np.argmax(power[mask]))])


def band_mean(frequency_hz: np.ndarray, power: np.ndarray, low_hz: float, high_hz: float) -> float:
    mask = (frequency_hz >= low_hz) & (frequency_hz <= high_hz)
    if not np.any(mask):
        return 0.0
    return float(np.mean(power[mask]))


def compute_icd(frequency_hz: np.ndarray, power: np.ndarray, f0_hz: float) -> float:
    values: list[float] = []
    for center_hz in broadband_centers(f0_hz):
        values.append(
            band_mean(
                frequency_hz,
                power,
                center_hz - BROADBAND_HALF_WIDTH_HZ,
                center_hz + BROADBAND_HALF_WIDTH_HZ,
            )
        )
    return math.sqrt(float(np.sum(np.asarray(values, dtype=float) ** 2)))


def top_peak(frequency_hz: np.ndarray, power: np.ndarray, low_hz: float = 20_000.0, high_hz: float = 3_500_000.0):
    mask = (frequency_hz >= low_hz) & (frequency_hz <= high_hz)
    if not np.any(mask):
        return 0.0, 0.0
    local_frequency = frequency_hz[mask]
    local_power = power[mask]
    index = int(np.argmax(local_power))
    return float(local_frequency[index]), float(local_power[index])


def analyze_file(group: str, path: Path) -> FileMetrics:
    voltage_mv = read_voltage_mv(path)
    frequency_hz, power = segment_average_power_spectrum(voltage_mv)
    f0_hz = estimate_f0_hz(frequency_hz, power)
    peak_hz, peak_power = top_peak(frequency_hz, power)
    return FileMetrics(
        group=group,
        file=path.name,
        f0_hz=f0_hz,
        icd_estimated_f0=compute_icd(frequency_hz, power, f0_hz),
        icd_fixed_608k=compute_icd(frequency_hz, power, FIXED_F0_HZ),
        std_mv=float(np.std(voltage_mv)),
        ptp_mv=float(np.ptp(voltage_mv)),
        mean_mv=float(np.mean(voltage_mv)),
        band_60_85k_power=band_mean(frequency_hz, power, 60_000.0, 85_000.0),
        band_450_750k_power=band_mean(frequency_hz, power, 450_000.0, 750_000.0),
        band_1_30_1_43m_power=band_mean(frequency_hz, power, 1_300_000.0, 1_430_000.0),
        top_peak_hz=peak_hz,
        top_peak_power=peak_power,
    )


def percentiles(values: np.ndarray) -> dict[str, float]:
    return {
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "p10": float(np.percentile(values, 10)),
        "p90": float(np.percentile(values, 90)),
        "max": float(np.max(values)),
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    file_metrics: list[FileMetrics] = []
    for group_dir in sorted([p for p in TEMP.iterdir() if p.is_dir()]):
        if group_dir.name == "codex记录" or "codex忽略" in group_dir.name:
            continue
        csv_files = sorted(group_dir.glob("capture_*.csv"))
        if not csv_files:
            continue
        for csv_file in csv_files:
            file_metrics.append(analyze_file(group_dir.name, csv_file))

    per_file_rows = [metric.__dict__ for metric in file_metrics]
    write_csv(OUT / "noise_icd_per_file.csv", per_file_rows)

    summary_rows: list[dict[str, object]] = []
    for group in sorted({metric.group for metric in file_metrics}):
        group_metrics = [metric for metric in file_metrics if metric.group == group]
        icd_est = np.asarray([metric.icd_estimated_f0 for metric in group_metrics], dtype=float)
        icd_fixed = np.asarray([metric.icd_fixed_608k for metric in group_metrics], dtype=float)
        std_mv = np.asarray([metric.std_mv for metric in group_metrics], dtype=float)
        f0 = np.asarray([metric.f0_hz for metric in group_metrics], dtype=float)
        band_72 = np.asarray([metric.band_60_85k_power for metric in group_metrics], dtype=float)
        band_icd = np.asarray([metric.band_1_30_1_43m_power for metric in group_metrics], dtype=float)
        row: dict[str, object] = {
            "group": group,
            "files": len(group_metrics),
            "f0_median_hz": float(np.median(f0)),
            "f0_p10_hz": float(np.percentile(f0, 10)),
            "f0_p90_hz": float(np.percentile(f0, 90)),
            "std_mv_median": float(np.median(std_mv)),
            "band_60_85k_power_median": float(np.median(band_72)),
            "band_1_30_1_43m_power_median": float(np.median(band_icd)),
        }
        for prefix, values in (("icd_est", icd_est), ("icd_fixed", icd_fixed)):
            for name, value in percentiles(values).items():
                row[f"{prefix}_{name}"] = value
        summary_rows.append(row)
    write_csv(OUT / "noise_icd_group_summary.csv", summary_rows)

    lines = [
        "# Noise ICD summary",
        "",
        "This file is generated by analyze_noise_icd.py.",
        "",
        "## ICD windows for fixed 608 kHz f0",
        "",
    ]
    for center_hz in broadband_centers(FIXED_F0_HZ):
        nearest_72 = round(center_hz / 72_000.0) * 72_000.0
        lines.append(
            f"- {center_hz / 1e6:.3f} MHz, nearest 72 kHz multiple "
            f"{nearest_72 / 1e6:.3f} MHz, delta {(center_hz - nearest_72) / 1e3:.1f} kHz"
        )
    lines.extend(["", "## Group summary", ""])
    for row in summary_rows:
        lines.append(
            f"- {row['group']}: files={row['files']}, "
            f"ICD(est f0) median={row['icd_est_median']:.3e}, "
            f"ICD(fixed 608k) median={row['icd_fixed_median']:.3e}, "
            f"std median={row['std_mv_median']:.3f} mV, "
            f"f0 median={row['f0_median_hz'] / 1e3:.1f} kHz"
        )
    (OUT / "noise_icd_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
