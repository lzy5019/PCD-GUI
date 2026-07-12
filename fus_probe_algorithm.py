"""Probe-normalized acoustic-emission features for FUS-Probe acquisitions.

The functions in this module are intentionally independent of Qt and hardware.
They operate on one paired Probe/Treatment capture and expose the intermediate
spectra and band powers needed for offline validation and GUI traceability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np


ALGORITHM_VERSION = "fus_probe_uhe_be_v4"
POWER_FLOOR = 1e-30


@dataclass
class FusProbeFeatureSettings:
    ultraharmonic_orders: tuple[float, ...] = (1.5, 2.5, 3.5)
    spectral_half_width_hz: float = 10_000.0
    exclusion_half_width_hz: float = 30_000.0
    broadband_low_order: float = 1.25
    broadband_high_order: float = 6.25
    probe_quality_threshold_db: float = 12.0
    clipping_fraction_threshold: float = 0.001
    uhe_system_correction_db: float = 0.0
    be_system_correction_db: float = 0.0

    def validate(self) -> None:
        if not self.ultraharmonic_orders:
            raise ValueError("At least one UHE ultraharmonic order is required.")
        if self.spectral_half_width_hz <= 0 or self.exclusion_half_width_hz <= 0:
            raise ValueError("Frequency-band widths must be positive.")
        if self.broadband_low_order >= self.broadband_high_order:
            raise ValueError("The broadband lower order must be below the upper order.")


@dataclass
class FusProbePairMetrics:
    cycle_id: int = 0
    captured_at_seconds: float = 0.0
    sample_rate_hz: float = 0.0
    f0_hz: float = 0.0
    probe_vpp: float = 0.0
    treatment_vpp: float = 0.0
    probe_valid: bool = False
    probe_quality_db: float = math.nan
    probe_rms_mv: float = math.nan
    treatment_rms_mv: float = math.nan
    probe_clipping_fraction: float = 0.0
    treatment_clipping_fraction: float = 0.0
    uhe_db: float = math.nan
    be_db: float = math.nan
    uhe_ratio_linear: float = math.nan
    be_ratio_linear: float = math.nan
    ultraharmonic_orders: list[float] = field(default_factory=list)
    ultraharmonic_ratios_db: list[float] = field(default_factory=list)
    ultraharmonic_corrected_ratios_db: list[float] = field(default_factory=list)
    probe_ultraharmonic_power: list[float] = field(default_factory=list)
    treatment_ultraharmonic_power: list[float] = field(default_factory=list)
    probe_broadband_power: float = math.nan
    treatment_broadband_power: float = math.nan
    frequency_hz: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    probe_psd: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    treatment_psd: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))


@dataclass
class FusProbeDecisionSettings:
    uhe_activation_threshold_db: float = 3.0
    uhe_minimum_confirmed_bands: int = 3
    be_risk_threshold_db: float = 6.0
    open_dose_threshold: float = 20.0
    thresholds_calibrated: bool = False

    def validate(self) -> None:
        if self.open_dose_threshold <= 0:
            raise ValueError("The candidate opening-dose threshold must be positive.")
        if self.uhe_minimum_confirmed_bands < 1:
            raise ValueError("At least one UHE band must be required for confirmation.")


@dataclass
class FusProbeDecision:
    state: str
    label: str
    rationale: str
    safe_uhe_increment: float
    cumulative_safe_uhe_dose: float
    be_risk: bool
    uhe_active_band_count: int
    uhe_confirmed: bool
    thresholds_calibrated: bool


class FusProbeDoseTracker:
    def __init__(self, settings: FusProbeDecisionSettings | None = None) -> None:
        self.settings = settings or FusProbeDecisionSettings()
        self.settings.validate()
        self.cumulative_safe_uhe_dose = 0.0

    def reset(self) -> None:
        self.cumulative_safe_uhe_dose = 0.0

    def update(self, metrics: FusProbePairMetrics) -> FusProbeDecision:
        be_risk = bool(metrics.probe_valid and metrics.be_db >= self.settings.be_risk_threshold_db)
        uhe_active_band_count = sum(
            math.isfinite(value) and value >= self.settings.uhe_activation_threshold_db
            for value in metrics.ultraharmonic_corrected_ratios_db
        )
        uhe_confirmed = uhe_active_band_count >= self.settings.uhe_minimum_confirmed_bands
        safe_uhe_increment = 0.0
        if metrics.probe_valid and not be_risk and uhe_confirmed and math.isfinite(metrics.uhe_db):
            threshold_linear = 10.0 ** (self.settings.uhe_activation_threshold_db / 10.0)
            safe_uhe_increment = max(10.0 ** (metrics.uhe_db / 10.0) - threshold_linear, 0.0)
            self.cumulative_safe_uhe_dose += safe_uhe_increment

        if not metrics.probe_valid:
            state = "invalid"
            label = "信号无效"
            rationale = (
                f"Probe 质量 {metrics.probe_quality_db:.1f} dB 未达到要求或采集发生削顶。"
            )
        elif be_risk:
            state = "risk"
            label = "存在安全风险"
            rationale = f"BE {metrics.be_db:.2f} dB ≥ {self.settings.be_risk_threshold_db:.2f} dB"
        elif self.cumulative_safe_uhe_dose >= self.settings.open_dose_threshold:
            state = "open"
            label = "达到开窗剂量"
            rationale = (
                f"累计安全 UHE 剂量 {self.cumulative_safe_uhe_dose:.2f} ≥ "
                f"{self.settings.open_dose_threshold:.2f} a.u."
            )
        else:
            state = "not_open"
            label = "未达到开窗剂量"
            if math.isfinite(metrics.uhe_db) and metrics.uhe_db >= self.settings.uhe_activation_threshold_db and not uhe_confirmed:
                rationale = (
                    f"UHE {metrics.uhe_db:.2f} dB 仅有 {uhe_active_band_count}/"
                    f"{self.settings.uhe_minimum_confirmed_bands} 个子带一致，未计入剂量。"
                )
            else:
                rationale = (
                    f"当前无风险；累计安全 UHE 剂量 {self.cumulative_safe_uhe_dose:.2f} < "
                    f"{self.settings.open_dose_threshold:.2f} a.u."
                )
        return FusProbeDecision(
            state=state,
            label=label,
            rationale=rationale,
            safe_uhe_increment=float(safe_uhe_increment),
            cumulative_safe_uhe_dose=float(self.cumulative_safe_uhe_dose),
            be_risk=be_risk,
            uhe_active_band_count=int(uhe_active_band_count),
            uhe_confirmed=bool(uhe_confirmed),
            thresholds_calibrated=self.settings.thresholds_calibrated,
        )


def compute_one_sided_psd(signal: np.ndarray, sample_rate_hz: float) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(signal, dtype=float).reshape(-1)
    if values.size < 16:
        raise ValueError("At least 16 samples are required for a spectrum.")
    if not np.all(np.isfinite(values)) or not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError("Signal and sample rate must be finite.")

    centered = values - float(np.mean(values))
    sample_count = centered.size
    index = np.arange(sample_count, dtype=float)
    window = 0.5 - 0.5 * np.cos((2.0 * np.pi * index) / sample_count)
    window_power = float(np.sum(window**2))
    fft_values = np.fft.rfft(centered * window)
    psd = np.abs(fft_values) ** 2 / (sample_rate_hz * window_power)
    if sample_count % 2 == 0:
        psd[1:-1] *= 2.0
    else:
        psd[1:] *= 2.0
    frequency_hz = np.fft.rfftfreq(sample_count, d=1.0 / sample_rate_hz)
    return frequency_hz, np.asarray(psd, dtype=float)


def integrate_frequency_mask(frequency_hz: np.ndarray, psd: np.ndarray, mask: np.ndarray) -> float:
    if frequency_hz.size < 2 or not np.any(mask):
        return 0.0
    bin_width_hz = float(frequency_hz[1] - frequency_hz[0])
    return float(np.sum(np.asarray(psd, dtype=float)[mask]) * bin_width_hz)


def integrate_center_band(
    frequency_hz: np.ndarray,
    psd: np.ndarray,
    center_hz: float,
    half_width_hz: float,
) -> float:
    mask = np.abs(frequency_hz - center_hz) <= half_width_hz
    return integrate_frequency_mask(frequency_hz, psd, mask)


def build_broadband_mask(
    frequency_hz: np.ndarray,
    f0_hz: float,
    settings: FusProbeFeatureSettings,
) -> np.ndarray:
    mask = (frequency_hz >= settings.broadband_low_order * f0_hz) & (
        frequency_hz <= settings.broadband_high_order * f0_hz
    )
    highest_half_order = int(math.ceil(2.0 * settings.broadband_high_order))
    for half_order_index in range(2, highest_half_order + 1):
        center_hz = 0.5 * half_order_index * f0_hz
        mask &= np.abs(frequency_hz - center_hz) > settings.exclusion_half_width_hz
    return mask


def _band_mean_psd(
    frequency_hz: np.ndarray,
    psd: np.ndarray,
    low_hz: float,
    high_hz: float,
) -> float:
    mask = (frequency_hz >= low_hz) & (frequency_hz <= high_hz)
    return float(np.mean(psd[mask])) if np.any(mask) else POWER_FLOOR


def compute_probe_quality_db(
    frequency_hz: np.ndarray,
    probe_psd: np.ndarray,
    f0_hz: float,
    signal_half_width_hz: float,
) -> float:
    signal_level = _band_mean_psd(
        frequency_hz,
        probe_psd,
        f0_hz - signal_half_width_hz,
        f0_hz + signal_half_width_hz,
    )
    guard_hz = 2.0 * signal_half_width_hz
    noise_span_hz = 6.0 * signal_half_width_hz
    lower_noise = _band_mean_psd(
        frequency_hz,
        probe_psd,
        f0_hz - noise_span_hz,
        f0_hz - guard_hz,
    )
    upper_noise = _band_mean_psd(
        frequency_hz,
        probe_psd,
        f0_hz + guard_hz,
        f0_hz + noise_span_hz,
    )
    noise_level = max(0.5 * (lower_noise + upper_noise), POWER_FLOOR)
    return float(10.0 * math.log10(max(signal_level, POWER_FLOOR) / noise_level))


def analyze_fus_probe_pair(
    probe_signal_mv: np.ndarray,
    treatment_signal_mv: np.ndarray,
    sample_rate_hz: float,
    f0_hz: float,
    probe_vpp: float,
    treatment_vpp: float,
    settings: FusProbeFeatureSettings | None = None,
    *,
    cycle_id: int = 0,
    captured_at_seconds: float = 0.0,
    adc_full_scale_mv: float = 1_000.0,
) -> FusProbePairMetrics:
    settings = settings or FusProbeFeatureSettings()
    settings.validate()
    if f0_hz <= 0 or probe_vpp <= 0 or treatment_vpp <= 0:
        raise ValueError("FUS frequency and Probe/Treatment Vpp must be positive.")

    probe_signal = np.asarray(probe_signal_mv, dtype=float).reshape(-1)
    treatment_signal = np.asarray(treatment_signal_mv, dtype=float).reshape(-1)
    if probe_signal.size != treatment_signal.size:
        raise ValueError("Probe and Treatment captures must contain the same number of samples.")
    frequency_hz, probe_psd = compute_one_sided_psd(probe_signal, sample_rate_hz)
    treatment_frequency_hz, treatment_psd = compute_one_sided_psd(treatment_signal, sample_rate_hz)
    if not np.array_equal(frequency_hz, treatment_frequency_hz):
        raise ValueError("Probe and Treatment spectra do not share a frequency axis.")

    voltage_power_ratio = (treatment_vpp / probe_vpp) ** 2
    ultraharmonic_orders: list[float] = []
    ultraharmonic_ratios_db: list[float] = []
    ultraharmonic_corrected_ratios_db: list[float] = []
    probe_ultraharmonic_power: list[float] = []
    treatment_ultraharmonic_power: list[float] = []
    for order in settings.ultraharmonic_orders:
        center_hz = order * f0_hz
        if center_hz + settings.spectral_half_width_hz >= frequency_hz[-1]:
            continue
        probe_power = integrate_center_band(
            frequency_hz, probe_psd, center_hz, settings.spectral_half_width_hz
        )
        treatment_power = integrate_center_band(
            frequency_hz, treatment_psd, center_hz, settings.spectral_half_width_hz
        )
        normalized_ratio = max(treatment_power, POWER_FLOOR) / max(
            voltage_power_ratio * probe_power, POWER_FLOOR
        )
        ultraharmonic_orders.append(float(order))
        raw_ratio_db = float(10.0 * math.log10(normalized_ratio))
        ultraharmonic_ratios_db.append(raw_ratio_db)
        ultraharmonic_corrected_ratios_db.append(raw_ratio_db - settings.uhe_system_correction_db)
        probe_ultraharmonic_power.append(float(probe_power))
        treatment_ultraharmonic_power.append(float(treatment_power))
    if not ultraharmonic_orders:
        raise ValueError("No configured UHE ultraharmonic bands fit below Nyquist.")
    total_probe_ultraharmonic = max(float(sum(probe_ultraharmonic_power)), POWER_FLOOR)
    total_treatment_ultraharmonic = max(float(sum(treatment_ultraharmonic_power)), POWER_FLOOR)
    uhe_ratio_linear = total_treatment_ultraharmonic / (
        voltage_power_ratio * total_probe_ultraharmonic
    )
    uhe_db = 10.0 * math.log10(max(uhe_ratio_linear, POWER_FLOOR)) - settings.uhe_system_correction_db

    broadband_mask = build_broadband_mask(frequency_hz, f0_hz, settings)
    probe_broadband_power = max(
        integrate_frequency_mask(frequency_hz, probe_psd, broadband_mask), POWER_FLOOR
    )
    treatment_broadband_power = max(
        integrate_frequency_mask(frequency_hz, treatment_psd, broadband_mask), POWER_FLOOR
    )
    be_ratio_linear = treatment_broadband_power / (voltage_power_ratio * probe_broadband_power)
    be_db = 10.0 * math.log10(max(be_ratio_linear, POWER_FLOOR)) - settings.be_system_correction_db

    probe_quality_db = compute_probe_quality_db(
        frequency_hz,
        probe_psd,
        f0_hz,
        settings.spectral_half_width_hz,
    )
    full_scale = max(float(adc_full_scale_mv), POWER_FLOOR)
    probe_clipping_fraction = float(np.mean(np.abs(probe_signal) >= 0.98 * full_scale))
    treatment_clipping_fraction = float(np.mean(np.abs(treatment_signal) >= 0.98 * full_scale))
    probe_valid = bool(
        math.isfinite(probe_quality_db)
        and probe_quality_db >= settings.probe_quality_threshold_db
        and probe_clipping_fraction <= settings.clipping_fraction_threshold
        and treatment_clipping_fraction <= settings.clipping_fraction_threshold
    )

    return FusProbePairMetrics(
        cycle_id=int(cycle_id),
        captured_at_seconds=float(captured_at_seconds),
        sample_rate_hz=float(sample_rate_hz),
        f0_hz=float(f0_hz),
        probe_vpp=float(probe_vpp),
        treatment_vpp=float(treatment_vpp),
        probe_valid=probe_valid,
        probe_quality_db=probe_quality_db,
        probe_rms_mv=float(np.std(probe_signal)),
        treatment_rms_mv=float(np.std(treatment_signal)),
        probe_clipping_fraction=probe_clipping_fraction,
        treatment_clipping_fraction=treatment_clipping_fraction,
        uhe_db=float(uhe_db),
        be_db=float(be_db),
        uhe_ratio_linear=float(uhe_ratio_linear),
        be_ratio_linear=float(be_ratio_linear),
        ultraharmonic_orders=ultraharmonic_orders,
        ultraharmonic_ratios_db=ultraharmonic_ratios_db,
        ultraharmonic_corrected_ratios_db=ultraharmonic_corrected_ratios_db,
        probe_ultraharmonic_power=probe_ultraharmonic_power,
        treatment_ultraharmonic_power=treatment_ultraharmonic_power,
        probe_broadband_power=float(probe_broadband_power),
        treatment_broadband_power=float(treatment_broadband_power),
        frequency_hz=frequency_hz,
        probe_psd=probe_psd,
        treatment_psd=treatment_psd,
    )
