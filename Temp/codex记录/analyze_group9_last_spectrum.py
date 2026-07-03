from __future__ import annotations

import csv
import math
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


def hann_spectrum(signal: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    signal = np.asarray(signal, dtype=float).reshape(-1)
    signal = signal - np.mean(signal)
    n = signal.size
    window = 0.5 - 0.5 * np.cos((2.0 * np.pi * np.arange(n, dtype=float)) / n)
    fft_values = np.fft.rfft(signal * window)
    amplitude = np.abs(fft_values / n)
    if amplitude.size > 2:
        amplitude[1:-1] *= 2
    frequency_hz = np.fft.rfftfreq(n, d=1.0 / SAMPLE_RATE_HZ)
    power = amplitude**2
    return frequency_hz, amplitude, power


def local_peaks(
    frequency_hz: np.ndarray,
    power: np.ndarray,
    low_hz: float,
    high_hz: float,
    min_separation_hz: float,
    limit: int,
) -> list[tuple[float, float, float]]:
    mask = (frequency_hz >= low_hz) & (frequency_hz <= high_hz)
    local_freq = frequency_hz[mask]
    local_power = power[mask]
    local_amp = np.sqrt(local_power)
    if local_freq.size < 3:
        return []

    candidate = np.flatnonzero((local_power[1:-1] > local_power[:-2]) & (local_power[1:-1] >= local_power[2:])) + 1
    if candidate.size == 0:
        candidate = np.arange(local_freq.size)
    candidate = candidate[np.argsort(local_power[candidate])[::-1]]

    peaks: list[tuple[float, float, float]] = []
    for index in candidate.tolist():
        freq = float(local_freq[index])
        if all(abs(freq - existing[0]) >= min_separation_hz for existing in peaks):
            peaks.append((freq, float(local_amp[index]), float(local_power[index])))
        if len(peaks) >= limit:
            break
    return peaks


def grid_score(peaks: list[tuple[float, float, float]], fundamental_hz: float) -> tuple[float, list[tuple[float, int, float]]]:
    matches: list[tuple[float, int, float]] = []
    score = 0.0
    for freq_hz, _amp, power in peaks:
        harmonic = max(1, int(round(freq_hz / fundamental_hz)))
        expected = harmonic * fundamental_hz
        error_hz = abs(freq_hz - expected)
        if error_hz <= 3_000.0:
            weight = power / (1.0 + error_hz / 1_000.0)
            score += weight
            matches.append((freq_hz, harmonic, error_hz))
    return score, matches


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_files = sorted(GROUP_DIR.glob("*.csv"), key=lambda path: (path.stat().st_mtime, path.name))
    if not csv_files:
        raise SystemExit(f"No CSV files found in {GROUP_DIR}")
    last_csv = csv_files[-1]

    voltage_mv = read_voltage_mv(last_csv)
    frequency_hz, amplitude, power = hann_spectrum(voltage_mv)
    peaks = local_peaks(
        frequency_hz,
        power,
        low_hz=1_000.0,
        high_hz=5_000_000.0,
        min_separation_hz=4_000.0,
        limit=80,
    )
    low_peaks = [peak for peak in peaks if peak[0] <= 1_000_000.0]

    grid_candidates = [
        50.0,
        60.0,
        1_000.0,
        50_000.0,
        64_000.0,
        72_000.0,
        128_000.0,
        608_000.0,
    ]
    grid_rows = []
    for candidate in grid_candidates:
        score, matches = grid_score(peaks[:50], candidate)
        grid_rows.append(
            {
                "candidate_fundamental_hz": candidate,
                "score": score,
                "matches": "; ".join(
                    f"{freq / 1000:.1f}k=n{harmonic},err{error:.0f}Hz" for freq, harmonic, error in matches[:12]
                ),
            }
        )
    grid_rows.sort(key=lambda row: float(row["score"]), reverse=True)

    peak_rows = [
        {
            "rank": rank,
            "frequency_hz": freq,
            "frequency_khz": freq / 1000.0,
            "amplitude_mv": amp,
            "power": pwr,
            "nearest_72k_multiple": round(freq / 72_000.0),
            "delta_to_72k_hz": freq - round(freq / 72_000.0) * 72_000.0,
            "nearest_128k_multiple": round(freq / 128_000.0),
            "delta_to_128k_hz": freq - round(freq / 128_000.0) * 128_000.0,
        }
        for rank, (freq, amp, pwr) in enumerate(peaks[:40], start=1)
    ]

    with (OUT_DIR / "group9_last_spectrum_peaks.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(peak_rows[0].keys()))
        writer.writeheader()
        writer.writerows(peak_rows)

    with (OUT_DIR / "group9_last_spectrum_grid_scores.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(grid_rows[0].keys()))
        writer.writeheader()
        writer.writerows(grid_rows)

    strongest = peaks[0]
    strongest_low = low_peaks[0] if low_peaks else strongest
    lines = [
        "# Group 9 last spectrum analysis",
        "",
        f"Last CSV: `{last_csv.name}`",
        f"Sample count: `{voltage_mv.size}`",
        f"Frequency resolution: `{SAMPLE_RATE_HZ / voltage_mv.size:.1f} Hz`",
        f"Time std: `{np.std(voltage_mv):.6f} mV`",
        f"Time peak-to-peak: `{np.ptp(voltage_mv):.6f} mV`",
        "",
        "## Strongest peak",
        "",
        f"- Strongest peak from 1 kHz to 5 MHz: `{strongest[0] / 1000:.3f} kHz`",
        f"- Amplitude: `{strongest[1]:.6e} mV`, power: `{strongest[2]:.6e}`",
        f"- Strongest peak below 1 MHz: `{strongest_low[0] / 1000:.3f} kHz`",
        "",
        "## Top peaks",
        "",
    ]
    for row in peak_rows[:20]:
        lines.append(
            f"- #{row['rank']:02d}: {row['frequency_khz']:.3f} kHz, "
            f"amp={row['amplitude_mv']:.3e} mV, "
            f"delta72={row['delta_to_72k_hz']:.0f} Hz, "
            f"delta128={row['delta_to_128k_hz']:.0f} Hz"
        )
    lines.extend(["", "## Harmonic-grid scores", ""])
    for row in grid_rows:
        lines.append(
            f"- {float(row['candidate_fundamental_hz']) / 1000:.3f} kHz: score={float(row['score']):.6e}; "
            f"{row['matches']}"
        )

    (OUT_DIR / "group9_last_spectrum_analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
