from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
TEMP = ROOT / "Temp"
OUT = TEMP / "codex记录"
FS = 25_000_000.0
N = 25_000
F0 = 608_000.0
SEGMENTS = 2
ICD_HALF_WIDTH = 75_000.0


GROUP_PREFIXES = ("10 ", "11 ")


def read_voltage(path: Path) -> np.ndarray:
    data = np.genfromtxt(path, delimiter=",", names=True, dtype=None, encoding=None)
    return np.asarray(data["voltage_mV"], dtype=float)[:N]


def power_spectrum(signal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    signal = signal - np.mean(signal)
    n = signal.size
    window = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n, dtype=float) / n)
    fft_values = np.fft.rfft(signal * window)
    amp = np.abs(fft_values / n)
    if amp.size > 2:
        amp[1:-1] *= 2
    return np.fft.rfftfreq(n, d=1.0 / FS), amp**2


def segment_average(signal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    segment_len = signal.size // SEGMENTS
    spectra = []
    freq = np.asarray([], dtype=float)
    for index in range(SEGMENTS):
        freq, pwr = power_spectrum(signal[index * segment_len : (index + 1) * segment_len])
        spectra.append(pwr)
    return freq, np.mean(np.vstack(spectra), axis=0)


def band_mean(freq: np.ndarray, pwr: np.ndarray, lo: float, hi: float) -> float:
    mask = (freq >= lo) & (freq <= hi)
    return float(np.mean(pwr[mask])) if np.any(mask) else 0.0


def band_peak(freq: np.ndarray, pwr: np.ndarray, lo: float, hi: float) -> tuple[float, float]:
    mask = (freq >= lo) & (freq <= hi)
    if not np.any(mask):
        return 0.0, 0.0
    local_freq = freq[mask]
    local_pwr = pwr[mask]
    index = int(np.argmax(local_pwr))
    return float(local_freq[index]), float(local_pwr[index])


def icd_fixed(freq: np.ndarray, pwr: np.ndarray) -> float:
    centers = np.asarray([1.75, 2.25, 2.75, 3.25, 3.75, 4.25, 4.75, 5.25], dtype=float) * F0
    values = [band_mean(freq, pwr, center - ICD_HALF_WIDTH, center + ICD_HALF_WIDTH) for center in centers]
    return math.sqrt(float(np.sum(np.asarray(values) ** 2)))


def summarize(values: list[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=float)
    return {
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "p10": float(np.percentile(arr, 10)),
        "p90": float(np.percentile(arr, 90)),
        "max": float(np.max(arr)),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for group_dir in sorted(path for path in TEMP.iterdir() if path.is_dir() and path.name.startswith(GROUP_PREFIXES)):
        for csv_path in sorted(group_dir.glob("capture_*.csv")):
            voltage = read_voltage(csv_path)
            freq, pwr = segment_average(voltage)
            peak_128_hz, peak_128_pwr = band_peak(freq, pwr, 120_000.0, 136_000.0)
            peak_608_hz, peak_608_pwr = band_peak(freq, pwr, 550_000.0, 650_000.0)
            rows.append(
                {
                    "group": group_dir.name,
                    "file": csv_path.name,
                    "std_mv": float(np.std(voltage)),
                    "ptp_mv": float(np.ptp(voltage)),
                    "icd_fixed_608k": icd_fixed(freq, pwr),
                    "peak_128_hz": peak_128_hz,
                    "peak_128_amp_mv": float(np.sqrt(peak_128_pwr)),
                    "peak_608_hz": peak_608_hz,
                    "peak_608_amp_mv": float(np.sqrt(peak_608_pwr)),
                    "band_1_20k": band_mean(freq, pwr, 1_000.0, 20_000.0),
                    "band_45_65k": band_mean(freq, pwr, 45_000.0, 65_000.0),
                    "band_120_136k": band_mean(freq, pwr, 120_000.0, 136_000.0),
                    "band_1_30_1_43m": band_mean(freq, pwr, 1_300_000.0, 1_430_000.0),
                    "band_1_0_3_3m": band_mean(freq, pwr, 1_000_000.0, 3_300_000.0),
                }
            )

    with (OUT / "group10_11_handheld_per_file.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    summary_rows = []
    for group in sorted({row["group"] for row in rows}):
        group_rows = [row for row in rows if row["group"] == group]
        summary = {"group": group, "files": len(group_rows)}
        for key in (
            "std_mv",
            "ptp_mv",
            "icd_fixed_608k",
            "peak_128_amp_mv",
            "peak_608_amp_mv",
            "band_1_20k",
            "band_45_65k",
            "band_120_136k",
            "band_1_30_1_43m",
            "band_1_0_3_3m",
        ):
            stats = summarize([float(row[key]) for row in group_rows])
            for name, value in stats.items():
                summary[f"{key}_{name}"] = value
        summary_rows.append(summary)

    with (OUT / "group10_11_handheld_summary.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    lines = ["# Group 10 vs 11 handheld comparison", ""]
    for row in summary_rows:
        lines.extend(
            [
                f"## {row['group']}",
                "",
                f"- files: `{row['files']}`",
                f"- ICD fixed 608k median: `{row['icd_fixed_608k_median']:.6e}`",
                f"- 128 kHz peak amp median: `{row['peak_128_amp_mv_median']:.6e} mV`",
                f"- 608 kHz-region peak amp median: `{row['peak_608_amp_mv_median']:.6e} mV`",
                f"- 1-20 kHz band median: `{row['band_1_20k_median']:.6e}`",
                f"- 45-65 kHz band median: `{row['band_45_65k_median']:.6e}`",
                f"- 1.30-1.43 MHz band median: `{row['band_1_30_1_43m_median']:.6e}`",
                "",
            ]
        )
    (OUT / "group10_11_handheld_analysis.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
