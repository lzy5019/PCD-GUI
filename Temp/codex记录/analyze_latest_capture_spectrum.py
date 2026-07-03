from __future__ import annotations

import csv
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
TEMP = ROOT / "Temp"
OUT_DIR = TEMP / "codex记录"
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
    peaks = []
    for index in candidate.tolist():
        hz = float(f[index])
        if all(abs(hz - old_hz) >= 4_000.0 for old_hz, _old_power in peaks):
            peaks.append((hz, float(p[index])))
        if len(peaks) >= limit:
            break
    return peaks


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = [
        path
        for path in TEMP.rglob("capture_*.csv")
        if "codex记录" not in path.parts and "codex忽略" not in str(path)
    ]
    if not files:
        raise SystemExit("No capture CSV files found.")
    latest = max(files, key=lambda path: (path.stat().st_mtime, path.name))
    voltage = read_voltage_mv(latest)
    freq, power = spectrum(voltage)
    peaks = local_peaks(freq, power, 1_000.0, 5_000_000.0, limit=40)
    rows = [
        {
            "rank": rank,
            "frequency_hz": hz,
            "frequency_khz": hz / 1000.0,
            "amplitude_mv": float(np.sqrt(pwr)),
            "power": pwr,
        }
        for rank, (hz, pwr) in enumerate(peaks, start=1)
    ]
    with (OUT_DIR / "latest_capture_spectrum_peaks.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Latest capture spectrum analysis",
        "",
        f"Latest CSV: `{latest.relative_to(ROOT)}`",
        f"Sample count: `{voltage.size}`",
        f"Frequency resolution: `{SAMPLE_RATE_HZ / voltage.size:.1f} Hz`",
        f"Time std: `{np.std(voltage):.6f} mV`",
        f"Time peak-to-peak: `{np.ptp(voltage):.6f} mV`",
        f"Strongest peak from 1 kHz to 5 MHz: `{rows[0]['frequency_khz']:.3f} kHz`",
        f"Strongest peak amplitude: `{rows[0]['amplitude_mv']:.6e} mV`",
        "",
        "## Top peaks",
        "",
    ]
    for row in rows[:20]:
        lines.append(
            f"- #{row['rank']:02d}: {row['frequency_khz']:.3f} kHz, amp={row['amplitude_mv']:.3e} mV"
        )
    (OUT_DIR / "latest_capture_spectrum_analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
