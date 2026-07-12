import math
import unittest

import numpy as np

from fus_probe_algorithm import (
    FusProbeDecisionSettings,
    FusProbeDoseTracker,
    FusProbeFeatureSettings,
    analyze_fus_probe_pair,
)


class FusProbeAlgorithmTests(unittest.TestCase):
    sample_rate_hz = 25_000_000.0
    sample_count = 25_000
    f0_hz = 612_000.0
    probe_vpp = 4.5
    treatment_vpp = 7.0

    def setUp(self) -> None:
        self.time_s = np.arange(self.sample_count, dtype=float) / self.sample_rate_hz
        self.rng = np.random.default_rng(20260712)
        self.probe = (
            30.0 * np.sin(2.0 * np.pi * self.f0_hz * self.time_s)
            + 1.0 * np.sin(2.0 * np.pi * 4.0 * self.f0_hz * self.time_s)
            + self.rng.normal(0.0, 0.3, self.sample_count)
        )
        self.scale = self.treatment_vpp / self.probe_vpp
        self.settings = FusProbeFeatureSettings(
            be_system_correction_db=0.0,
        )

    def analyze(self, treatment: np.ndarray):
        return analyze_fus_probe_pair(
            self.probe,
            treatment,
            self.sample_rate_hz,
            self.f0_hz,
            self.probe_vpp,
            self.treatment_vpp,
            self.settings,
        )

    def test_drive_normalization_removes_linear_voltage_scaling(self) -> None:
        metrics = self.analyze(self.scale * self.probe)
        self.assertTrue(metrics.probe_valid)
        self.assertAlmostEqual(metrics.uhe_db, 0.0, places=8)
        self.assertAlmostEqual(metrics.be_db, 0.0, places=8)

    def test_added_broadband_noise_raises_be(self) -> None:
        treatment = self.scale * self.probe + self.rng.normal(0.0, 5.0, self.sample_count)
        metrics = self.analyze(treatment)
        self.assertGreater(metrics.be_db, 10.0)

    def test_added_ultraharmonic_raises_uhe(self) -> None:
        treatment = self.scale * self.probe + 8.0 * np.sin(
            2.0 * np.pi * 2.5 * self.f0_hz * self.time_s
        )
        metrics = self.analyze(treatment)
        self.assertGreater(metrics.uhe_db, 6.0)

    def test_risk_has_priority_over_cumulative_dose(self) -> None:
        metrics = self.analyze(self.scale * self.probe)
        metrics.uhe_db = 10.0
        metrics.ultraharmonic_corrected_ratios_db = [10.0, 10.0, 10.0]
        tracker = FusProbeDoseTracker(
            FusProbeDecisionSettings(
                uhe_activation_threshold_db=2.0,
                be_risk_threshold_db=6.0,
                open_dose_threshold=1.0,
            )
        )
        first = tracker.update(metrics)
        self.assertEqual(first.state, "open")
        metrics.be_db = 7.0
        second = tracker.update(metrics)
        self.assertEqual(second.state, "risk")
        self.assertTrue(math.isclose(second.safe_uhe_increment, 0.0))

    def test_isolated_uhe_bands_do_not_accumulate_dose(self) -> None:
        metrics = self.analyze(self.scale * self.probe)
        metrics.uhe_db = 10.0
        metrics.ultraharmonic_corrected_ratios_db = [10.0, 10.0, -5.0]
        tracker = FusProbeDoseTracker(
            FusProbeDecisionSettings(
                uhe_activation_threshold_db=3.0,
                uhe_minimum_confirmed_bands=3,
                be_risk_threshold_db=6.0,
                open_dose_threshold=1.0,
            )
        )
        decision = tracker.update(metrics)
        self.assertEqual(decision.state, "not_open")
        self.assertFalse(decision.uhe_confirmed)
        self.assertEqual(decision.uhe_active_band_count, 2)
        self.assertTrue(math.isclose(decision.cumulative_safe_uhe_dose, 0.0))


if __name__ == "__main__":
    unittest.main()
