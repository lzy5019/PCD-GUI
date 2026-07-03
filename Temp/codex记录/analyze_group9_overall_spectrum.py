from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
GROUP_DIR = ROOT / "Temp" / "9 重新将线缆摆放位置放回去"
OUT_DIR = ROOT / "Temp" / "codex记录"
SAMPLE_RATE_HZ = 25_000_000.0
TARGET_SAMPLE_COUNT = 25_000


def read_voltage_mv(path: Path) -> np.ndarray:
    data = np.genfromtxt(path, delimiter=",", names=True, dtype=None, encoding=None)
    return np.asarray(data["voltage_mV"], dtype=float)[:TARGET_SAMPLE_COUNT]


def spectrum(signal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    signal = signal - np.mean(signal)
    n = signal.size
    window = 0.5 - 0.5 * np.cos((2.0 * np.pi * np.arange(n, dtype=float)) / n)
    fft_values = np.fft.rfft(signal * window)
    amplitude = np.abs(fft_values / n)
    if amplitude.size > 2:
        amplitude[1:-1] *= 2
    return np.fft.rfftfreq(n, d=1.0 / SAMPLE_RATE_HZ), amplitude**2


def local_peaks(freq: np.ndarray, power: np.ndarray, low_hz: float, high_hz: float, limit: int):
    mask = (freq >= low_hz) & (freq <= high_hz)
    f = freq[mask]
    p = power[mask]
    candidate = np.flatnonzero((p[1:-1] > p[:-2]) & (p[1:-1] >= p[2:])) + 1
    if candidate.size == 0:
        candidate = np.arange(f.size)
    candidate = candidate[np.argsort(p[candidate])[::-1]]
    peaks: list[tuple[float, float]] = []
    for index in candidate.tolist():
        hz = float(f[index])
        if all(abs(hz - existing_hz) >= 4_000.0 for existing_hz, _ in peaks):
            peaks.append((hz, float(p[index])))
        if len(peaks) >= limit:
            break
    return peaks


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(GROUP_DIR.glob("capture_*.csv"), key=lambda p: (p.stat().st_mtime, p.name))
    if not files:
        raise SystemExit("No group 9 capture CSV files found.")

    freq_ref: np.ndarray | None = None
    power_sum: np.ndarray | None = None
    strongest_rows = []
    top_counter: Counter[int] = Counter()

    for path in files:
        voltage = read_voltage_mv(path)
        freq, power = spectrum(voltage)
        if freq_ref is None:
            freq_ref = freq
            power_sum = np.zeros_like(power)
        power_sum += power

        peaks = local_peaks(freq, power, 1_000.0, 5_000_000.0, limit=10)
        strongest_hz, strongest_power = peaks[0]
        strongest_rows.append(
            {
                "file": path.name,
                "strongest_frequency_hz": strongest_hz,
                "strongest_frequency_khz": strongest_hz / 1000.0,
                "strongest_power": strongest_power,
                "std_mv": float(np.std(voltage)),
                "ptp_mv": float(np.ptp(voltage)),
            }
        )
        for hz, _power in peaks[:5]:
            top_counter[int(round(hz / 1000.0))] += 1

    assert freq_ref is not None and power_sum is not None
    mean_power = power_sum / len(files)
    mean_peaks = local_peaks(freq_ref, mean_power, 1_000.0, 5_000_000.0, limit=40)
    mean_rows = [
        {
            "rank": rank,
            "frequency_hz": hz,
            "frequency_khz": hz / 1000.0,
            "mean_power": power,
            "mean_amplitude_mv": float(np.sqrt(power)),
        }
        for rank, (hz, power) in enumerate(mean_peaks, start=1)
    ]
    common_rows = [
        {"frequency_bin_khz": khz, "count_in_top5_per_file": count}
        for khz, count in top_counter.most_common(30)
    ]

    with (OUT_DIR / "group9_overall_mean_spectrum_peaks.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(mean_rows[0].keys()))
        writer.writeheader()
        writer.writerows(mean_rows)

    with (OUT_DIR / "group9_overall_common_peak_bins.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(common_rows[0].keys()))
        writer.writeheader()
        writer.writerows(common_rows)

    with (OUT_DIR / "group9_per_file_strongest_peak.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(strongest_rows[0].keys()))
        writer.writeheader()
        writer.writerows(strongest_rows)

    strongest_freqs = np.asarray([row["strongest_frequency_hz"] for row in strongest_rows], dtype=float)
    strongest_powers = np.asarray([row["strongest_power"] for row in strongest_rows], dtype=float)
    lines = [
        "# Group 9 overall spectrum analysis",
        "",
        f"Files: `{len(files)}`",
        f"Mean-spectrum strongest peak: `{mean_rows[0]['frequency_khz']:.3f} kHz`",
        f"Per-file strongest peak median: `{np.median(strongest_freqs) / 1000.0:.3f} kHz`",
        f"Per-file strongest peak p10-p90: `{np.percentile(strongest_freqs, 10) / 1000.0:.3f}` - `{np.percentile(strongest_freqs, 90) / 1000.0:.3f} kHz`",
        f"Per-file strongest power median: `{np.median(strongest_powers):.6e}`",
        "",
        "## Mean-spectrum top peaks",
        "",
    ]
    for row in mean_rows[:20]:
        lines.append(
            f"- #{row['rank']:02d}: {row['frequency_khz']:.3f} kHz, "
            f"mean_amp={row['mean_amplitude_mv']:.3e} mV"
        )
    lines.extend(["", "## Most common per-file top-5 peak bins", ""])
    for row in common_rows[:20]:
        lines.append(f"- {row['frequency_bin_khz']} kHz: {row['count_in_top5_per_file']} / {len(files)}")
    (OUT_DIR / "group9_overall_spectrum_analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
