from __future__ import annotations

import csv
import ctypes
import glob
import json
import math
import os
import shutil
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
from PyQt5.QtCore import QObject, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


ACTS1000_INPUT_N5000_P5000mV = 0x00
ACTS1000_INPUT_N1000_P1000mV = 0x01
ACTS1000_COUPLING_DC = 0x00
ACTS1000_IMPED_1M = 0x00
ACTS1000_IMPED_50 = 0x01
ACTS1000_SAMPMODE_FINITE = 0x00
ACTS1000_TRIGMODE_POST = 0x01
ACTS1000_TRIGSRC_DTR = 0x01
ACTS1000_TRIGSRC_TRIGGER = 0x02
ACTS1000_TRIGDIR_POSITIVE = 0x01
ACTS1000_RECLK_ONBOARD = 0x00
ACTS1000_TBCLK_IN = 0x00
ACTS1000_STS_TRIGGER0 = 0x00
ACTS1000_CLKOUT_REFERENCE = 0x00
ACTS1000_TOP_POSITIVE = 0x01
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
FLOAT_EPS = np.finfo(float).eps
ORDER_TOL = 1e-9
POSITIVE_CAVITATION_SCORE_THRESHOLD = 0.60
NEGATIVE_CAVITATION_SCORE_THRESHOLD = 0.28
STRONG_ULTRA_POSITIVE_FRACTION_THRESHOLD = 0.30
BOUNDARY_CAVITATION_SCORE_THRESHOLD = 0.42
BOUNDARY_ULTRA_POSITIVE_FRACTION_THRESHOLD = 0.15
WAVEFORM_PREVIEW_CYCLES = 6.5


def code_root() -> Path:
    return Path(__file__).resolve().parent


def workspace_root() -> Path:
    return code_root()


def settings_path() -> Path:
    return code_root() / "runtime_settings.json"


def resolve_workspace_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return (workspace_root() / path).resolve()


def make_portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace_root()).as_posix()
    except ValueError:
        return str(path.resolve())


def join_patterns(patterns: list[str]) -> str:
    return "; ".join(patterns)


def split_patterns(raw_text: str) -> list[str]:
    parts = [part.strip() for chunk in raw_text.splitlines() for part in chunk.split(";")]
    return [part for part in parts if part]


def closed_interval_sequence(low: float, high: float, offset: float, step: float) -> list[float]:
    if step <= 0:
        raise ValueError("Step must be positive.")
    if high < low:
        return []

    start_index = math.ceil((low - offset - ORDER_TOL) / step)
    end_index = math.floor((high - offset + ORDER_TOL) / step)
    values: list[float] = []
    for index in range(start_index, end_index + 1):
        value = offset + index * step
        if value <= 0:
            continue
        if low - ORDER_TOL <= value <= high + ORDER_TOL:
            values.append(round(value, 10))
    return values


@dataclass
class HardwareSettings:
    dll_path: str = "vendor/ACTS1000_64.dll"
    device_id: int = 0
    sample_rate_hz: float = 25_000_000.0
    points: int = 25_000
    input_range: str = "pm1000"
    input_impedance: str = "50"
    trigger_source: str = "sync0"
    timeout_seconds: float = 30.0
    save_csv: bool = True
    output_dir: str = "output/captures"


@dataclass
class PlaybackSettings:
    source_patterns: list[str] = field(default_factory=lambda: ["data/playback/*.csv"])
    interval_ms: int = 300
    loop_playback: bool = False


@dataclass
class ReferenceSettings:
    no_cavitation_patterns: list[str] = field(
        default_factory=lambda: ["data/reference/no_cavitation/*.csv"]
    )
    cavitation_patterns: list[str] = field(default_factory=lambda: ["data/reference/cavitation/*.csv"])


@dataclass
class AnalysisSettings:
    spectrum_mode: str = "amplitude"
    use_segment_average: bool = True
    segment_count: int = 2
    target_sample_count: int = 25_000
    search_band_low_hz: float = 0.45e6
    search_band_high_hz: float = 0.75e6
    peak_half_width_hz: float = 10e3
    noise_half_width_hz: float = 30e3
    broadband_half_width_hz: float = 75e3
    order_range_low: float = 1.5
    order_range_high: float = 5.25
    min_peak_prominence_db: float = -10.0

    @classmethod
    def from_dict(cls, data: dict) -> "AnalysisSettings":
        allowed_keys = {item.name for item in fields(cls)}
        analysis_data = dict(data)
        order_range_low = analysis_data.get("order_range_low")
        order_range_high = analysis_data.get("order_range_high")

        if order_range_low is None or order_range_high is None:
            legacy_values: list[float] = []
            for key in ("harmonic_orders", "ultraharmonic_orders", "broadband_centers_factor"):
                raw_values = analysis_data.get(key, [])
                if isinstance(raw_values, list):
                    legacy_values.extend(float(value) for value in raw_values)
            if legacy_values:
                if order_range_low is None:
                    order_range_low = min(legacy_values)
                if order_range_high is None:
                    order_range_high = max(legacy_values)

        if order_range_low is not None:
            analysis_data["order_range_low"] = float(order_range_low)
        if order_range_high is not None:
            analysis_data["order_range_high"] = float(order_range_high)

        filtered = {key: value for key, value in analysis_data.items() if key in allowed_keys}
        return cls(**filtered)

    def validate(self) -> None:
        if self.segment_count < 1:
            raise ValueError("Segment count must be at least 1.")
        if self.target_sample_count < 1:
            raise ValueError("Target sample count must be positive.")
        if self.order_range_low > self.order_range_high:
            raise ValueError("Order range lower bound must be less than or equal to the upper bound.")
        if self.peak_half_width_hz <= 0:
            raise ValueError("Peak half-width must be positive.")
        if self.noise_half_width_hz <= 0:
            raise ValueError("Noise half-width must be positive.")
        if self.broadband_half_width_hz <= 0:
            raise ValueError("Broadband half-width must be positive.")

    def harmonic_orders(self) -> list[float]:
        start = max(1, math.ceil(self.order_range_low - ORDER_TOL))
        end = math.floor(self.order_range_high + ORDER_TOL)
        return [float(order) for order in range(start, end + 1)] if start <= end else []

    def ultraharmonic_orders(self) -> list[float]:
        return closed_interval_sequence(self.order_range_low, self.order_range_high, offset=0.5, step=1.0)

    def broadband_centers_factor(self) -> list[float]:
        return closed_interval_sequence(self.order_range_low, self.order_range_high, offset=0.25, step=0.5)


@dataclass
class UiSettings:
    last_mode: str = "playback"
    max_live_points: int = 150


@dataclass
class AppSettings:
    hardware: HardwareSettings = field(default_factory=HardwareSettings)
    playback: PlaybackSettings = field(default_factory=PlaybackSettings)
    reference: ReferenceSettings = field(default_factory=ReferenceSettings)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    ui: UiSettings = field(default_factory=UiSettings)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        return cls(
            hardware=HardwareSettings(**data.get("hardware", {})),
            playback=PlaybackSettings(**data.get("playback", {})),
            reference=ReferenceSettings(**data.get("reference", {})),
            analysis=AnalysisSettings.from_dict(data.get("analysis", {})),
            ui=UiSettings(**data.get("ui", {})),
        )


@dataclass
class PcdMetrics:
    file: str = ""
    relative_path: str = ""
    group_name: str = ""
    spectrum_mode: str = "amplitude"
    frequency_hz: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    spectrum: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    f0_hz: float = math.nan
    harmonic_peak_count: int = 0
    ultraharmonic_peak_count: int = 0
    scd_har: float = 0.0
    scd_ultra: float = 0.0
    icd: float = 0.0
    ultra_to_icd_ratio: float = 0.0
    segment_harmonic_positive_fraction: float = 0.0
    segment_ultra_positive_fraction: float = 0.0
    segment_mean_scd_har: float = 0.0
    segment_mean_scd_ultra: float = 0.0
    segment_mean_icd: float = 0.0
    distance_to_no_cav: float = math.nan
    distance_to_cav: float = math.nan
    cavitation_score: float = math.nan
    risk_score: float = math.nan
    ultra_dose_score: float = math.nan
    ultra_stability_score: float = math.nan
    harmonic_score: float = math.nan
    peak_count_score: float = math.nan
    balance_score: float = math.nan
    conclusion: str = ""

    def plot_coordinates(self) -> tuple[float, float]:
        return (
            math.log10(max(self.scd_ultra, FLOAT_EPS)),
            math.log10(max(self.icd, FLOAT_EPS)),
        )


@dataclass
class ReferenceStats:
    feature_names: list[str]
    feature_scale: np.ndarray
    no_feature_mean: np.ndarray
    cav_feature_mean: np.ndarray
    no_metric_mean: dict[str, float]
    cav_metric_mean: dict[str, float]
    no_score_median: dict[str, float]
    cav_score_median: dict[str, float]
    cav_median_ultra_to_icd: float
    cav_icd_warning_level: float
    no_results: list[PcdMetrics]
    cav_results: list[PcdMetrics]


@dataclass
class AnalysisFrame:
    sequence_index: int
    source_label: str
    captured_at: datetime
    metrics: PcdMetrics
    sample_rate_hz: float
    waveform_time_us: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    waveform_mv: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    saved_csv_path: Path | None = None


@dataclass
class PlaybackUiState:
    is_active: bool = False
    is_paused: bool = False
    current_position: int = 0
    total_frames: int = 0
    current_file: str = ""


@dataclass
class CaptureResult:
    raw_codes: np.ndarray
    voltage_mv: np.ndarray
    sample_rate_hz: float
    model_name: str
    code_count: int
    input_range: int


class ACTS1000_PARA_AD(ctypes.Structure):
    _fields_ = [
        ("bChannelArray", wintypes.LONG * 8),
        ("InputRange", wintypes.LONG * 8),
        ("CouplingType", wintypes.LONG * 8),
        ("InputImped", wintypes.LONG * 8),
        ("FreqDivision", wintypes.LONG),
        ("SampleMode", wintypes.LONG),
        ("M_Length", wintypes.ULONG),
        ("N_Length", wintypes.ULONG),
        ("PFISel", wintypes.LONG),
        ("TriggerMode", wintypes.LONG),
        ("TriggerSource", wintypes.LONG),
        ("TriggerDir", wintypes.LONG),
        ("TrigLevelVolt", wintypes.LONG),
        ("TrigWindow", wintypes.LONG),
        ("TrigCount", wintypes.ULONG),
        ("ReferenceClock", wintypes.LONG),
        ("TimeBaseClock", wintypes.LONG),
        ("bMasterEn", wintypes.LONG),
        ("SyncTrigSignal", wintypes.LONG),
        ("bClkOutEn", wintypes.LONG),
        ("ClkOutSel", wintypes.LONG),
        ("bTrigOutEn", wintypes.LONG),
        ("TrigOutPolarity", wintypes.LONG),
        ("TrigOutWidth", wintypes.LONG),
        ("bSaveFile", wintypes.BOOL),
        ("chFileName", ctypes.c_wchar * 256),
    ]


class ACTS1000_AD_MAIN_INFO(ctypes.Structure):
    _fields_ = [
        ("nDeviceType", wintypes.LONG),
        ("nChannelCount", wintypes.LONG),
        ("nDepthOfMemory", wintypes.LONG),
        ("nSampResolution", wintypes.LONG),
        ("nSampCodeCount", wintypes.LONG),
        ("nTrigLvlResolution", wintypes.LONG),
        ("nTrigLvlCodeCount", wintypes.LONG),
        ("nBaseRate", wintypes.LONG),
        ("nMaxRate", wintypes.LONG),
        ("nMinFreqDivision", wintypes.LONG),
        ("nSupportImped", wintypes.LONG),
        ("nSupportPFI", wintypes.LONG),
        ("nSupportExtClk", wintypes.LONG),
        ("nSupportPXIE100M", wintypes.LONG),
        ("nSupportClkOut", wintypes.LONG),
        ("nReserved0", wintypes.LONG),
        ("nReserved1", wintypes.LONG),
        ("nReserved2", wintypes.LONG),
        ("nReserved3", wintypes.LONG),
    ]

class Acts1000Api:
    def __init__(self, dll_path: Path) -> None:
        dll_path = dll_path.resolve()
        if not dll_path.exists():
            raise FileNotFoundError(f"Driver DLL not found: {dll_path}")

        self._dll_dir_cookie = None
        if hasattr(os, "add_dll_directory"):
            self._dll_dir_cookie = os.add_dll_directory(str(dll_path.parent))

        self.dll = ctypes.WinDLL(str(dll_path), use_last_error=True)
        self.CreateDevice = bind_stdcall(self.dll, "ACTS1000_CreateDevice", wintypes.HANDLE, [ctypes.c_int])
        self.ReleaseDevice = bind_stdcall(self.dll, "ACTS1000_ReleaseDevice", wintypes.BOOL, [wintypes.HANDLE])
        self.GetMainInfo = bind_stdcall(
            self.dll,
            "ACTS1000_GetMainInfo",
            wintypes.BOOL,
            [wintypes.HANDLE, ctypes.POINTER(ACTS1000_AD_MAIN_INFO)],
        )
        self.InitDeviceAD = bind_stdcall(
            self.dll,
            "ACTS1000_InitDeviceAD",
            wintypes.BOOL,
            [wintypes.HANDLE, ctypes.POINTER(ACTS1000_PARA_AD)],
        )
        self.StartDeviceAD = bind_stdcall(self.dll, "ACTS1000_StartDeviceAD", wintypes.BOOL, [wintypes.HANDLE])
        self.StopDeviceAD = bind_stdcall(self.dll, "ACTS1000_StopDeviceAD", wintypes.BOOL, [wintypes.HANDLE])
        self.ReleaseDeviceAD = bind_stdcall(
            self.dll,
            "ACTS1000_ReleaseDeviceAD",
            wintypes.BOOL,
            [wintypes.HANDLE],
        )
        self.ReadDeviceAD = bind_stdcall(
            self.dll,
            "ACTS1000_ReadDeviceAD",
            wintypes.BOOL,
            [
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.WORD),
                wintypes.ULONG,
                ctypes.POINTER(wintypes.ULONG),
                ctypes.POINTER(wintypes.ULONG),
                ctypes.c_double,
            ],
        )


class Acts1000Device:
    def __init__(self, settings: HardwareSettings) -> None:
        self.settings = settings
        self.api = Acts1000Api(resolve_workspace_path(settings.dll_path))
        self.handle = None
        self.main_info = ACTS1000_AD_MAIN_INFO()
        self.model_name = "Unknown"
        self.base_rate = 0
        self.code_count = 0

    def __enter__(self) -> "Acts1000Device":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        if self.handle and not is_invalid_handle(self.handle):
            return

        self.handle = self.api.CreateDevice(self.settings.device_id)
        if is_invalid_handle(self.handle):
            raise RuntimeError(f"ACTS1000_CreateDevice({self.settings.device_id}) failed.")

        if not self.api.GetMainInfo(self.handle, ctypes.byref(self.main_info)):
            self.close()
            raise RuntimeError("ACTS1000_GetMainInfo failed.")

        self.model_name = device_name(self.main_info.nDeviceType)
        self.base_rate = int(self.main_info.nBaseRate or self.main_info.nMaxRate)
        self.code_count = int(self.main_info.nSampCodeCount)
        if self.base_rate <= 0 or self.code_count <= 0:
            self.close()
            raise RuntimeError("Device reported invalid sample rate or code count.")

    def close(self) -> None:
        if self.handle and not is_invalid_handle(self.handle):
            self.api.ReleaseDevice(self.handle)
        self.handle = None

    def capture_once(self) -> CaptureResult:
        self.open()

        requested_points = int(self.settings.points)
        hardware_points = align_up_to_512(requested_points)
        freq_division = max(1, round(self.base_rate / self.settings.sample_rate_hz))
        actual_sample_rate = self.base_rate / freq_division
        input_range = pick_input_range(self.settings.input_range)
        input_impedance = pick_input_impedance(self.settings.input_impedance)
        trigger_source = pick_trigger_source(self.settings.trigger_source)

        para = ACTS1000_PARA_AD()
        para.SampleMode = ACTS1000_SAMPMODE_FINITE
        para.FreqDivision = freq_division
        para.bChannelArray[0] = 1
        para.InputRange[0] = input_range
        para.CouplingType[0] = ACTS1000_COUPLING_DC
        para.InputImped[0] = input_impedance
        para.M_Length = 0
        para.N_Length = hardware_points
        para.TriggerMode = ACTS1000_TRIGMODE_POST
        para.TriggerSource = trigger_source
        para.TriggerDir = ACTS1000_TRIGDIR_POSITIVE
        para.TrigLevelVolt = 0
        para.TrigWindow = 0
        para.ReferenceClock = ACTS1000_RECLK_ONBOARD
        para.TimeBaseClock = ACTS1000_TBCLK_IN
        para.bMasterEn = 0
        para.TrigCount = 1
        para.SyncTrigSignal = ACTS1000_STS_TRIGGER0
        para.bClkOutEn = 0
        para.ClkOutSel = ACTS1000_CLKOUT_REFERENCE
        para.bTrigOutEn = 0
        para.TrigOutPolarity = ACTS1000_TOP_POSITIVE
        para.TrigOutWidth = 50
        para.bSaveFile = 0
        para.chFileName = ""

        ad_initialized = False
        ad_started = False
        try:
            if not self.api.InitDeviceAD(self.handle, ctypes.byref(para)):
                raise RuntimeError("ACTS1000_InitDeviceAD failed.")
            ad_initialized = True

            if not self.api.StartDeviceAD(self.handle):
                raise RuntimeError("ACTS1000_StartDeviceAD failed.")
            ad_started = True

            captured_codes: list[int] = []
            while len(captured_codes) < hardware_points:
                chunk_words = min(4096, hardware_points - len(captured_codes))
                chunk_buffer = (wintypes.WORD * chunk_words)()
                returned_words = wintypes.ULONG(0)
                available_points = wintypes.ULONG(0)
                ok = self.api.ReadDeviceAD(
                    self.handle,
                    chunk_buffer,
                    chunk_words,
                    ctypes.byref(returned_words),
                    ctypes.byref(available_points),
                    float(self.settings.timeout_seconds),
                )
                if not ok:
                    raise RuntimeError("ACTS1000_ReadDeviceAD failed.")
                if returned_words.value == 0:
                    continue
                captured_codes.extend(int(chunk_buffer[index]) for index in range(returned_words.value))

            raw_codes = np.asarray(captured_codes[:requested_points], dtype=np.uint16)
            voltage_mv = raw_codes_to_voltage_mv(raw_codes, input_range, self.code_count)
            return CaptureResult(
                raw_codes=raw_codes,
                voltage_mv=voltage_mv,
                sample_rate_hz=float(actual_sample_rate),
                model_name=self.model_name,
                code_count=self.code_count,
                input_range=input_range,
            )
        finally:
            if ad_started:
                self.api.StopDeviceAD(self.handle)
            if ad_initialized:
                self.api.ReleaseDeviceAD(self.handle)


def bind_stdcall(dll: ctypes.WinDLL, name: str, restype, argtypes):
    last_error = None
    for candidate in stdcall_candidates(name, argtypes):
        try:
            function = getattr(dll, candidate)
            function.restype = restype
            function.argtypes = argtypes
            return function
        except AttributeError as exc:
            last_error = exc
    raise AttributeError(f"Could not bind {name!r} from {dll._name}") from last_error


def stdcall_candidates(name: str, argtypes: Iterable[object]) -> list[str]:
    stack_bytes = sum(ctypes.sizeof(argtype) for argtype in argtypes)
    return [name, f"{name}@{stack_bytes}", f"_{name}@{stack_bytes}"]


def is_invalid_handle(handle) -> bool:
    return handle in (None, 0, INVALID_HANDLE_VALUE)


def align_up_to_512(points: int) -> int:
    return ((points + 511) // 512) * 512


def device_name(device_type: int) -> str:
    bus_kind = device_type >> 16
    model = device_type & 0xFFFF
    if bus_kind == 0x2012:
        return f"PXIE{model:04X}"
    if bus_kind == 0x2111:
        return f"PCIE{model:04X}"
    return f"ACTS1000-{device_type:08X}"


def pick_input_range(range_name: str) -> int:
    if range_name == "pm1000":
        return ACTS1000_INPUT_N1000_P1000mV
    if range_name == "pm5000":
        return ACTS1000_INPUT_N5000_P5000mV
    raise ValueError(f"Unsupported input range: {range_name}")


def pick_input_impedance(impedance_name: str) -> int:
    if impedance_name == "1m":
        return ACTS1000_IMPED_1M
    if impedance_name == "50":
        return ACTS1000_IMPED_50
    raise ValueError(f"Unsupported input impedance: {impedance_name}")


def pick_trigger_source(trigger_source_name: str) -> int:
    if trigger_source_name == "sync0":
        return ACTS1000_TRIGSRC_TRIGGER
    if trigger_source_name == "dtr":
        return ACTS1000_TRIGSRC_DTR
    raise ValueError(f"Unsupported trigger source: {trigger_source_name}")


def raw_codes_to_voltage_mv(raw_codes: np.ndarray, input_range: int, code_count: int) -> np.ndarray:
    mask = code_count - 1
    full_scale_mv = 10000.0 if input_range == ACTS1000_INPUT_N5000_P5000mV else 2000.0
    zero_code = code_count / 2.0
    return ((raw_codes.astype(np.int64) & mask) - zero_code) * (full_scale_mv / code_count)


def write_capture_csv(output_path: Path, raw_codes: np.ndarray, voltage_mv: np.ndarray, sample_rate_hz: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["sample_index", "raw_code", "voltage_mV", "sample_rate_hz"])
        for index, (raw_code, voltage_value) in enumerate(zip(raw_codes.tolist(), voltage_mv.tolist())):
            writer.writerow([index, int(raw_code), f"{voltage_value:.6f}", f"{sample_rate_hz:.3f}"])


def resolve_input_patterns(patterns: list[str]) -> list[Path]:
    matched: list[Path] = []
    for raw_pattern in patterns:
        pattern = raw_pattern.strip()
        if not pattern:
            continue
        resolved = resolve_workspace_path(pattern)
        if resolved.exists() and resolved.is_dir():
            found = sorted(resolved.glob("*.csv"))
        elif resolved.exists() and resolved.is_file():
            found = [resolved]
        else:
            found = [Path(match) for match in sorted(glob.glob(str(resolved)))]
        matched.extend(found)

    deduplicated: list[Path] = []
    seen: set[str] = set()
    for file_path in matched:
        key = str(file_path.resolve()).lower()
        if key not in seen:
            seen.add(key)
            deduplicated.append(file_path.resolve())
    return deduplicated


def relative_to_workspace(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.resolve().relative_to(workspace_root()).as_posix()
    except ValueError:
        return str(path.resolve())

def load_signal_csv(file_path: Path, target_sample_count: int) -> tuple[np.ndarray, float | None]:
    text = file_path.read_text(encoding="utf-8-sig")
    rows = [row for row in csv.reader(text.splitlines()) if row and any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError(f"Empty CSV file: {file_path}")

    first_row = rows[0]
    has_header = any(cell.strip() and cannot_parse_float(cell) for cell in first_row)
    signal_values: list[float] = []
    sample_rate_hz: float | None = None

    if has_header:
        reader = csv.DictReader(text.splitlines())
        signal_columns = ["voltage_mV", "voltage_mv", "signal", "value", "amplitude", "raw_code"]
        for row in reader:
            if sample_rate_hz is None:
                sample_rate_hz = parse_optional_float(row.get("sample_rate_hz"))
            signal_cell = None
            for column_name in signal_columns:
                candidate = row.get(column_name)
                if candidate not in (None, ""):
                    signal_cell = candidate
                    break
            if signal_cell is None:
                numeric_columns = [
                    candidate
                    for key, candidate in row.items()
                    if key.lower() not in {"sample_index", "sample_rate_hz"} and candidate not in (None, "")
                ]
                if numeric_columns:
                    signal_cell = numeric_columns[0]
            if signal_cell is not None:
                signal_values.append(float(signal_cell))
    else:
        for row in rows:
            numeric_values = [parse_optional_float(cell) for cell in row]
            numeric_values = [value for value in numeric_values if value is not None]
            if numeric_values:
                signal_values.append(numeric_values[0])

    signal = np.asarray(signal_values, dtype=float)
    signal = signal[np.isfinite(signal)]
    if signal.size < target_sample_count:
        raise ValueError(f"Signal is shorter than {target_sample_count} points: {file_path}")
    return signal[:target_sample_count], sample_rate_hz


def analyze_signal(
    signal: np.ndarray,
    sample_rate_hz: float,
    analysis_settings: AnalysisSettings,
    reference_stats: ReferenceStats | None = None,
    file_name: str = "",
    relative_path: str = "",
    group_name: str = "Live",
) -> PcdMetrics:
    target_signal = np.asarray(signal, dtype=float).reshape(-1)
    target_signal = target_signal[: analysis_settings.target_sample_count]
    if target_signal.size < analysis_settings.target_sample_count:
        raise ValueError("Signal does not contain enough samples for analysis.")

    if analysis_settings.use_segment_average:
        frequency_hz, spectrum_segments, spectrum_mean = compute_segment_average_spectrum(
            target_signal, sample_rate_hz, analysis_settings
        )
    else:
        frequency_hz, spectrum_mean = compute_single_spectrum(target_signal, sample_rate_hz, analysis_settings)
        spectrum_segments = spectrum_mean.reshape(-1, 1)

    f0_hz = estimate_center_frequency(frequency_hz, spectrum_mean, analysis_settings)
    metrics = compute_cavitation_metrics(frequency_hz, spectrum_mean, f0_hz, analysis_settings)
    segment_metrics = [
        compute_cavitation_metrics(frequency_hz, spectrum_segments[:, index], f0_hz, analysis_settings)
        for index in range(spectrum_segments.shape[1])
    ]
    consistency = summarize_segment_metrics(segment_metrics)

    result = PcdMetrics(
        file=file_name,
        relative_path=relative_path,
        group_name=group_name,
        spectrum_mode=analysis_settings.spectrum_mode,
        frequency_hz=frequency_hz,
        spectrum=spectrum_mean,
        f0_hz=f0_hz,
        harmonic_peak_count=metrics["harmonic_peak_count"],
        ultraharmonic_peak_count=metrics["ultraharmonic_peak_count"],
        scd_har=metrics["scd_har"],
        scd_ultra=metrics["scd_ultra"],
        icd=metrics["icd"],
        ultra_to_icd_ratio=metrics["ultra_to_icd_ratio"],
        segment_harmonic_positive_fraction=consistency["harmonic_positive_fraction"],
        segment_ultra_positive_fraction=consistency["ultra_positive_fraction"],
        segment_mean_scd_har=consistency["mean_scd_har"],
        segment_mean_scd_ultra=consistency["mean_scd_ultra"],
        segment_mean_icd=consistency["mean_icd"],
    )
    if reference_stats is not None:
        classify_result(result, reference_stats)
    return result


def build_reference_statistics(
    reference_settings: ReferenceSettings,
    analysis_settings: AnalysisSettings,
) -> ReferenceStats:
    no_files = resolve_input_patterns(reference_settings.no_cavitation_patterns)
    cav_files = resolve_input_patterns(reference_settings.cavitation_patterns)
    if not no_files:
        raise FileNotFoundError("No no-cavitation reference files matched the configured patterns.")
    if not cav_files:
        raise FileNotFoundError("No cavitation reference files matched the configured patterns.")

    no_results = [
        analyze_file_for_reference(file_path, analysis_settings, "Known No Cavitation")
        for file_path in no_files
    ]
    cav_results = [
        analyze_file_for_reference(file_path, analysis_settings, "Known Cavitation")
        for file_path in cav_files
    ]

    no_features = np.vstack([build_feature_vector(result) for result in no_results])
    cav_features = np.vstack([build_feature_vector(result) for result in cav_results])
    all_reference_features = np.vstack([no_features, cav_features])
    feature_scale = safe_std(all_reference_features, axis=0)
    feature_scale[feature_scale < 0.15] = 0.15

    cav_icd_values = np.asarray([result.icd for result in cav_results], dtype=float)
    cav_icd_warning_level = max(np.percentile(cav_icd_values, 75), compute_metric_mean(cav_results)["ICD"])

    return ReferenceStats(
        feature_names=[
            "logSCDhar",
            "logSCDultra",
            "logICD",
            "logUltraToIcdRatio",
            "UltraPositiveFraction",
        ],
        feature_scale=feature_scale,
        no_feature_mean=np.mean(no_features, axis=0),
        cav_feature_mean=np.mean(cav_features, axis=0),
        no_metric_mean=compute_metric_mean(no_results),
        cav_metric_mean=compute_metric_mean(cav_results),
        no_score_median=compute_score_reference_median(no_results),
        cav_score_median=compute_score_reference_median(cav_results),
        cav_median_ultra_to_icd=float(np.median([result.ultra_to_icd_ratio for result in cav_results])),
        cav_icd_warning_level=float(cav_icd_warning_level),
        no_results=no_results,
        cav_results=cav_results,
    )


def analyze_file_for_reference(file_path: Path, analysis_settings: AnalysisSettings, group_name: str) -> PcdMetrics:
    signal, sample_rate_hz = load_signal_csv(file_path, analysis_settings.target_sample_count)
    effective_sample_rate = sample_rate_hz or 25_000_000.0
    return analyze_signal(
        signal=signal,
        sample_rate_hz=effective_sample_rate,
        analysis_settings=analysis_settings,
        reference_stats=None,
        file_name=file_path.name,
        relative_path=relative_to_workspace(file_path),
        group_name=group_name,
    )


def compute_single_spectrum(
    signal: np.ndarray,
    sample_rate_hz: float,
    analysis_settings: AnalysisSettings,
) -> tuple[np.ndarray, np.ndarray]:
    centered = signal - np.mean(signal)
    sample_count = centered.size
    window = periodic_hann(sample_count)
    fft_values = np.fft.rfft(centered * window)
    amplitude = np.abs(fft_values / sample_count)
    if amplitude.size > 2:
        amplitude[1:-1] *= 2

    if analysis_settings.spectrum_mode.lower() == "amplitude":
        spectrum = amplitude
    elif analysis_settings.spectrum_mode.lower() == "power":
        spectrum = amplitude ** 2
    else:
        raise ValueError(f"Unsupported spectrum mode: {analysis_settings.spectrum_mode}")

    frequency_hz = np.fft.rfftfreq(sample_count, d=1.0 / sample_rate_hz)
    return frequency_hz, spectrum


def compute_segment_average_spectrum(
    signal: np.ndarray,
    sample_rate_hz: float,
    analysis_settings: AnalysisSettings,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    segment_count = int(analysis_settings.segment_count)
    if signal.size % segment_count != 0:
        raise ValueError("Signal length must be divisible by the segment count.")

    segment_length = signal.size // segment_count
    spectrum_segments = []
    frequency_hz = np.asarray([], dtype=float)
    for index in range(segment_count):
        start = index * segment_length
        end = start + segment_length
        frequency_hz, current_spectrum = compute_single_spectrum(
            signal[start:end], sample_rate_hz, analysis_settings
        )
        spectrum_segments.append(current_spectrum)

    stacked = np.column_stack(spectrum_segments)
    return frequency_hz, stacked, np.mean(stacked, axis=1)


def periodic_hann(sample_count: int) -> np.ndarray:
    index = np.arange(sample_count, dtype=float)
    return 0.5 - 0.5 * np.cos((2.0 * np.pi * index) / sample_count)


def estimate_center_frequency(
    frequency_hz: np.ndarray,
    spectrum: np.ndarray,
    analysis_settings: AnalysisSettings,
) -> float:
    mask = (frequency_hz >= analysis_settings.search_band_low_hz) & (
        frequency_hz <= analysis_settings.search_band_high_hz
    )
    if not np.any(mask):
        raise ValueError("Configured center-frequency search band does not overlap the FFT range.")
    local_index = int(np.argmax(spectrum[mask]))
    return float(frequency_hz[mask][local_index])


def compute_cavitation_metrics(
    frequency_hz: np.ndarray,
    spectrum: np.ndarray,
    f0_hz: float,
    analysis_settings: AnalysisSettings,
) -> dict[str, float | int]:
    harmonics = np.asarray(analysis_settings.harmonic_orders(), dtype=float) * f0_hz
    ultraharmonics = np.asarray(analysis_settings.ultraharmonic_orders(), dtype=float) * f0_hz
    broadband_centers = np.asarray(analysis_settings.broadband_centers_factor(), dtype=float) * f0_hz

    harmonic_peaks, harmonic_detected = extract_feature_peaks(
        frequency_hz, spectrum, harmonics, analysis_settings
    )
    ultraharmonic_peaks, ultraharmonic_detected = extract_feature_peaks(
        frequency_hz, spectrum, ultraharmonics, analysis_settings
    )

    broadband_means: list[float] = []
    for center_hz in broadband_centers.tolist():
        band_mask = (frequency_hz >= center_hz - analysis_settings.broadband_half_width_hz) & (
            frequency_hz <= center_hz + analysis_settings.broadband_half_width_hz
        )
        broadband_means.append(float(np.mean(spectrum[band_mask])) if np.any(band_mask) else 0.0)

    scd_har = math.sqrt(float(np.sum((np.asarray(harmonic_peaks) * np.asarray(harmonic_detected, dtype=float)) ** 2)))
    scd_ultra = math.sqrt(
        float(np.sum((np.asarray(ultraharmonic_peaks) * np.asarray(ultraharmonic_detected, dtype=float)) ** 2))
    )
    icd = math.sqrt(float(np.sum(np.asarray(broadband_means, dtype=float) ** 2)))
    return {
        "harmonic_peak_count": int(sum(harmonic_detected)),
        "ultraharmonic_peak_count": int(sum(ultraharmonic_detected)),
        "scd_har": float(scd_har),
        "scd_ultra": float(scd_ultra),
        "icd": float(icd),
        "ultra_to_icd_ratio": float(scd_ultra / max(icd, FLOAT_EPS)),
    }


def extract_feature_peaks(
    frequency_hz: np.ndarray,
    spectrum: np.ndarray,
    target_frequencies_hz: np.ndarray,
    analysis_settings: AnalysisSettings,
) -> tuple[list[float], list[bool]]:
    peak_values: list[float] = []
    detected_values: list[bool] = []
    for center_hz in target_frequencies_hz.tolist():
        peak_mask = (frequency_hz >= center_hz - analysis_settings.peak_half_width_hz) & (
            frequency_hz <= center_hz + analysis_settings.peak_half_width_hz
        )
        noise_mask = (
            (frequency_hz >= center_hz - analysis_settings.noise_half_width_hz)
            & (frequency_hz <= center_hz + analysis_settings.noise_half_width_hz)
            & (~peak_mask)
        )
        if not np.any(peak_mask):
            peak_values.append(0.0)
            detected_values.append(False)
            continue
        peak_value = float(np.max(spectrum[peak_mask]))
        local_floor = float(np.mean(spectrum[noise_mask])) if np.any(noise_mask) else float(np.mean(spectrum[peak_mask]))
        prominence_db = ratio_to_db((peak_value + FLOAT_EPS) / (local_floor + FLOAT_EPS), analysis_settings.spectrum_mode)
        detected = prominence_db >= analysis_settings.min_peak_prominence_db
        peak_values.append(peak_value if detected else 0.0)
        detected_values.append(bool(detected))
    return peak_values, detected_values


def summarize_segment_metrics(segment_metrics: list[dict[str, float | int]]) -> dict[str, float]:
    harmonic_positive = [metrics["harmonic_peak_count"] >= 1 for metrics in segment_metrics]
    ultra_positive = [metrics["ultraharmonic_peak_count"] >= 1 for metrics in segment_metrics]
    return {
        "harmonic_positive_fraction": float(np.mean(harmonic_positive)),
        "ultra_positive_fraction": float(np.mean(ultra_positive)),
        "mean_scd_har": float(np.mean([metrics["scd_har"] for metrics in segment_metrics])),
        "mean_scd_ultra": float(np.mean([metrics["scd_ultra"] for metrics in segment_metrics])),
        "mean_icd": float(np.mean([metrics["icd"] for metrics in segment_metrics])),
    }

def classify_result(result: PcdMetrics, reference_stats: ReferenceStats) -> None:
    feature_vector = build_feature_vector(result)
    result.distance_to_no_cav = float(
        np.linalg.norm((feature_vector - reference_stats.no_feature_mean) / reference_stats.feature_scale)
    )
    result.distance_to_cav = float(
        np.linalg.norm((feature_vector - reference_stats.cav_feature_mean) / reference_stats.feature_scale)
    )
    scores = compute_cavitation_scores(result, reference_stats)
    result.cavitation_score = scores["cavitation_score"]
    result.risk_score = scores["risk_score"]
    result.ultra_dose_score = scores["ultra_dose_score"]
    result.ultra_stability_score = scores["ultra_stability_score"]
    result.harmonic_score = scores["harmonic_score"]
    result.peak_count_score = scores["peak_count_score"]
    result.balance_score = scores["balance_score"]
    result.conclusion = interpret_result(result, reference_stats)


def build_feature_vector(result: PcdMetrics) -> np.ndarray:
    return np.asarray(
        [
            math.log10(result.scd_har + FLOAT_EPS),
            math.log10(result.scd_ultra + FLOAT_EPS),
            math.log10(result.icd + FLOAT_EPS),
            math.log10(result.ultra_to_icd_ratio + FLOAT_EPS),
            result.segment_ultra_positive_fraction,
        ],
        dtype=float,
    )


def compute_metric_mean(results: list[PcdMetrics]) -> dict[str, float]:
    return {
        "SCDhar": float(np.mean([result.scd_har for result in results])),
        "SCDultra": float(np.mean([result.scd_ultra for result in results])),
        "ICD": float(np.mean([result.icd for result in results])),
        "UltraToIcdRatio": float(np.mean([result.ultra_to_icd_ratio for result in results])),
    }


def compute_score_reference_median(results: list[PcdMetrics]) -> dict[str, float]:
    return {
        "logSCDhar": float(np.median([math.log10(result.scd_har + FLOAT_EPS) for result in results])),
        "logSCDultra": float(np.median([math.log10(result.scd_ultra + FLOAT_EPS) for result in results])),
        "logICD": float(np.median([math.log10(result.icd + FLOAT_EPS) for result in results])),
        "logUltraToIcdRatio": float(
            np.median([math.log10(result.ultra_to_icd_ratio + FLOAT_EPS) for result in results])
        ),
        "ultraPositiveFraction": float(np.median([result.segment_ultra_positive_fraction for result in results])),
        "ultraharmonicPeakCount": float(np.median([result.ultraharmonic_peak_count for result in results])),
    }


def compute_cavitation_scores(result: PcdMetrics, reference_stats: ReferenceStats) -> dict[str, float]:
    no_ref = reference_stats.no_score_median
    cav_ref = reference_stats.cav_score_median
    ultra_dose_score = scaled_position(
        math.log10(result.scd_ultra + FLOAT_EPS),
        no_ref["logSCDultra"],
        cav_ref["logSCDultra"],
    )
    ultra_stability_score = scaled_position(
        result.segment_ultra_positive_fraction,
        no_ref["ultraPositiveFraction"],
        cav_ref["ultraPositiveFraction"],
    )
    harmonic_score = scaled_position(
        math.log10(result.scd_har + FLOAT_EPS),
        no_ref["logSCDhar"],
        cav_ref["logSCDhar"],
    )
    peak_count_score = scaled_position(
        float(result.ultraharmonic_peak_count),
        no_ref["ultraharmonicPeakCount"],
        cav_ref["ultraharmonicPeakCount"],
    )
    balance_score = scaled_position(
        math.log10(result.ultra_to_icd_ratio + FLOAT_EPS),
        no_ref["logUltraToIcdRatio"],
        cav_ref["logUltraToIcdRatio"],
    )
    cavitation_score = (
        0.45 * ultra_dose_score
        + 0.28 * ultra_stability_score
        + 0.17 * harmonic_score
        + 0.10 * balance_score
    )
    icd_high_score = scaled_position(
        math.log10(result.icd + FLOAT_EPS),
        no_ref["logICD"],
        cav_ref["logICD"],
    )
    low_balance_score = 1.0 - balance_score
    risk_score = min(max(0.7 * icd_high_score + 0.3 * low_balance_score, 0.0), 1.0)
    return {
        "ultra_dose_score": float(ultra_dose_score),
        "ultra_stability_score": float(ultra_stability_score),
        "harmonic_score": float(harmonic_score),
        "peak_count_score": float(peak_count_score),
        "balance_score": float(balance_score),
        "cavitation_score": float(cavitation_score),
        "risk_score": float(risk_score),
    }


def interpret_result(result: PcdMetrics, reference_stats: ReferenceStats) -> str:
    score = result.cavitation_score
    risk_score = result.risk_score
    if score >= POSITIVE_CAVITATION_SCORE_THRESHOLD:
        main_class = "更像已知有空化组"
    elif score <= NEGATIVE_CAVITATION_SCORE_THRESHOLD:
        main_class = "更像已知无空化组"
    else:
        main_class = "介于无空化组和有空化组之间"

    if (
        score >= POSITIVE_CAVITATION_SCORE_THRESHOLD
        and result.ultraharmonic_peak_count >= 1
        and result.segment_ultra_positive_fraction >= STRONG_ULTRA_POSITIVE_FRACTION_THRESHOLD
    ):
        if risk_score >= 0.65:
            pattern = "空化证据较强，但宽带偏高，需要警惕偏剧烈空化"
        elif result.ultra_to_icd_ratio >= reference_stats.cav_median_ultra_to_icd:
            pattern = "超谐波较稳定，偏稳定空化"
        else:
            pattern = "存在超谐波，但宽带成分偏强"
    elif result.icd > reference_stats.cav_icd_warning_level and result.ultraharmonic_peak_count == 0:
        pattern = "宽带成分偏强，需要警惕偏剧烈空化"
    elif (
        score >= BOUNDARY_CAVITATION_SCORE_THRESHOLD
        and result.segment_ultra_positive_fraction >= BOUNDARY_ULTRA_POSITIVE_FRACTION_THRESHOLD
    ):
        pattern = "已有一定超谐波证据，但整体仍偏边界"
    elif result.harmonic_peak_count >= 1:
        pattern = "有一定非线性响应，但超谐波证据偏弱"
    else:
        pattern = "空化证据较弱"
    return f"{main_class}，{pattern}"


def scaled_position(x: float, x_no: float, x_cav: float) -> float:
    delta = x_cav - x_no
    if abs(delta) < 1e-6:
        score = float(x > x_no)
    else:
        score = (x - x_no) / delta
    return float(min(max(score, 0.0), 1.0))


def ratio_to_db(ratio: float, spectrum_mode: str) -> float:
    if spectrum_mode.lower() == "amplitude":
        return 20.0 * math.log10(ratio)
    if spectrum_mode.lower() == "power":
        return 10.0 * math.log10(ratio)
    raise ValueError(f"Unsupported spectrum mode: {spectrum_mode}")


def spectrum_to_db(values: np.ndarray, spectrum_mode: str) -> np.ndarray:
    clipped = np.maximum(np.asarray(values, dtype=float), FLOAT_EPS)
    if spectrum_mode.lower() == "amplitude":
        return 20.0 * np.log10(clipped)
    if spectrum_mode.lower() == "power":
        return 10.0 * np.log10(clipped)
    raise ValueError(f"Unsupported spectrum mode: {spectrum_mode}")


def build_center_waveform_excerpt(
    signal: np.ndarray,
    sample_rate_hz: float,
    f0_hz: float,
    cycle_count: float = WAVEFORM_PREVIEW_CYCLES,
) -> tuple[np.ndarray, np.ndarray]:
    signal_values = np.asarray(signal, dtype=float).reshape(-1)
    if signal_values.size < 2 or not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)
    if not math.isfinite(f0_hz) or f0_hz <= 0:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)

    sample_span = max(16, int(round(cycle_count * sample_rate_hz / f0_hz)))
    sample_span = min(sample_span, signal_values.size)
    center_index = signal_values.size // 2
    start = max(0, center_index - sample_span // 2)
    end = min(signal_values.size, start + sample_span)
    start = max(0, end - sample_span)

    excerpt = np.asarray(signal_values[start:end], dtype=float)
    if excerpt.size < 2:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)

    centered_excerpt = excerpt - float(np.mean(excerpt))
    time_us = ((np.arange(centered_excerpt.size, dtype=float) - (centered_excerpt.size - 1) / 2.0) / sample_rate_hz) * 1e6
    return time_us, centered_excerpt


def safe_std(values: np.ndarray, axis: int = 0) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if values.shape[axis] <= 1:
        shape = list(values.shape)
        del shape[axis]
        return np.zeros(shape, dtype=float)
    return np.std(values, axis=axis, ddof=1)


def cannot_parse_float(text: str) -> bool:
    try:
        float(text)
        return False
    except (TypeError, ValueError):
        return True


def parse_optional_float(text: str | None) -> float | None:
    if text in (None, ""):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def score_to_color(score: float) -> QColor:
    if not math.isfinite(score):
        return QColor("#2563eb")
    if score >= POSITIVE_CAVITATION_SCORE_THRESHOLD:
        return QColor("#dc2626")
    if score <= NEGATIVE_CAVITATION_SCORE_THRESHOLD:
        return QColor("#2563eb")
    return QColor("#d97706")


class SpectrumWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.frequency_hz = np.asarray([], dtype=float)
        self.spectrum = np.asarray([], dtype=float)
        self.spectrum_mode = "amplitude"
        self.f0_hz = math.nan
        self.setMinimumHeight(210)
        self.setStyleSheet("background: white;")

    def clear_data(self) -> None:
        self.frequency_hz = np.asarray([], dtype=float)
        self.spectrum = np.asarray([], dtype=float)
        self.f0_hz = math.nan
        self.update()

    def set_spectrum(
        self,
        frequency_hz: np.ndarray,
        spectrum: np.ndarray,
        spectrum_mode: str,
        f0_hz: float,
    ) -> None:
        self.frequency_hz = np.asarray(frequency_hz, dtype=float)
        self.spectrum = np.asarray(spectrum, dtype=float)
        self.spectrum_mode = spectrum_mode
        self.f0_hz = f0_hz
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))

        chart_rect = self.rect().adjusted(72, 28, -24, -54)
        painter.setPen(QPen(QColor("#d0d7de"), 1))
        painter.drawRect(chart_rect)
        painter.setPen(QPen(QColor("#111827"), 1))
        painter.drawText(chart_rect.left(), 18, "Segment-Averaged Spectrum")

        if self.frequency_hz.size == 0 or self.spectrum.size == 0:
            painter.drawText(chart_rect.center().x() - 42, chart_rect.center().y(), "No spectrum yet")
            painter.drawText(chart_rect.center().x() - 48, self.height() - 18, "Frequency (MHz)")
            painter.drawText(12, 18 + 18, "Magnitude (dB)")
            return

        mask = (self.frequency_hz >= 0.0) & (self.frequency_hz <= 4.0e6)
        if not np.any(mask):
            mask = np.ones_like(self.frequency_hz, dtype=bool)
        x_values = self.frequency_hz[mask] / 1.0e6
        y_values = spectrum_to_db(self.spectrum[mask], self.spectrum_mode)

        x_min = float(np.min(x_values))
        x_max = float(np.max(x_values))
        if abs(x_max - x_min) < 1e-6:
            x_max = x_min + 1.0
        y_min = min(-80.0, float(np.floor(np.min(y_values) / 10.0) * 10.0))
        y_max = max(-10.0, float(np.ceil(np.max(y_values) / 10.0) * 10.0))
        if y_max - y_min < 20.0:
            y_min -= 10.0
            y_max += 10.0

        def to_pixel(x_value: float, y_value: float) -> tuple[float, float]:
            x_ratio = (x_value - x_min) / (x_max - x_min)
            y_ratio = (y_value - y_min) / (y_max - y_min)
            x_pixel = chart_rect.left() + x_ratio * chart_rect.width()
            y_pixel = chart_rect.bottom() - y_ratio * chart_rect.height()
            return x_pixel, y_pixel

        tick_count = 5
        painter.setPen(QPen(QColor("#e5e7eb"), 1, Qt.DashLine))
        for tick_index in range(tick_count + 1):
            x_ratio = tick_index / tick_count
            y_ratio = tick_index / tick_count
            x_pixel = chart_rect.left() + x_ratio * chart_rect.width()
            y_pixel = chart_rect.bottom() - y_ratio * chart_rect.height()
            painter.drawLine(int(x_pixel), chart_rect.top(), int(x_pixel), chart_rect.bottom())
            painter.drawLine(chart_rect.left(), int(y_pixel), chart_rect.right(), int(y_pixel))

        painter.setPen(QPen(QColor("#111827"), 1))
        for tick_index in range(tick_count + 1):
            x_ratio = tick_index / tick_count
            y_ratio = tick_index / tick_count
            x_value = x_min + x_ratio * (x_max - x_min)
            y_value = y_min + y_ratio * (y_max - y_min)
            x_pixel = chart_rect.left() + x_ratio * chart_rect.width()
            y_pixel = chart_rect.bottom() - y_ratio * chart_rect.height()
            painter.drawText(int(x_pixel) - 12, chart_rect.bottom() + 20, f"{x_value:.1f}")
            painter.drawText(10, int(y_pixel) + 5, f"{y_value:.0f}")

        painter.drawText(chart_rect.center().x() - 48, self.height() - 18, "Frequency (MHz)")
        painter.drawText(12, 18 + 18, "Magnitude (dB)")

        step = max(1, int(math.ceil(len(x_values) / max(chart_rect.width(), 1))))
        plot_x = x_values[::step]
        plot_y = y_values[::step]

        painter.setPen(QPen(QColor("#0f4c81"), 2))
        for index in range(1, len(plot_x)):
            x1, y1 = to_pixel(float(plot_x[index - 1]), float(plot_y[index - 1]))
            x2, y2 = to_pixel(float(plot_x[index]), float(plot_y[index]))
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        if math.isfinite(self.f0_hz):
            f0_mhz = self.f0_hz / 1.0e6
            if x_min <= f0_mhz <= x_max:
                x_pixel, _ = to_pixel(f0_mhz, y_min)
                painter.setPen(QPen(QColor("#dc2626"), 1, Qt.DashLine))
                painter.drawLine(int(x_pixel), chart_rect.top(), int(x_pixel), chart_rect.bottom())
                painter.setPen(QPen(QColor("#111827"), 1))
                painter.drawText(chart_rect.right() - 128, chart_rect.top() + 18, f"f0 = {f0_mhz:.3f} MHz")


class WaveformWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.time_us = np.asarray([], dtype=float)
        self.waveform_mv = np.asarray([], dtype=float)
        self.f0_hz = math.nan
        self.setMinimumHeight(170)
        self.setStyleSheet("background: white;")

    def clear_data(self) -> None:
        self.time_us = np.asarray([], dtype=float)
        self.waveform_mv = np.asarray([], dtype=float)
        self.f0_hz = math.nan
        self.update()

    def set_waveform(self, time_us: np.ndarray, waveform_mv: np.ndarray, f0_hz: float) -> None:
        self.time_us = np.asarray(time_us, dtype=float)
        self.waveform_mv = np.asarray(waveform_mv, dtype=float)
        self.f0_hz = f0_hz
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))

        chart_rect = self.rect().adjusted(72, 28, -24, -44)
        painter.setPen(QPen(QColor("#d0d7de"), 1))
        painter.drawRect(chart_rect)
        painter.setPen(QPen(QColor("#111827"), 1))
        painter.drawText(chart_rect.left(), 18, f"Center Waveform (~{WAVEFORM_PREVIEW_CYCLES:.1f} cycles)")

        if self.time_us.size == 0 or self.waveform_mv.size == 0:
            painter.drawText(chart_rect.center().x() - 48, chart_rect.center().y(), "No waveform yet")
            painter.drawText(chart_rect.center().x() - 32, self.height() - 14, "Time (us)")
            painter.drawText(12, 36, "Amplitude")
            return

        x_min = float(np.min(self.time_us))
        x_max = float(np.max(self.time_us))
        if abs(x_max - x_min) < 1e-9:
            x_max = x_min + 1.0

        y_min = float(np.min(self.waveform_mv))
        y_max = float(np.max(self.waveform_mv))
        if abs(y_max - y_min) < 1e-9:
            y_min -= 1.0
            y_max += 1.0
        y_pad = max(0.1, (y_max - y_min) * 0.12)
        y_min -= y_pad
        y_max += y_pad

        def to_pixel(x_value: float, y_value: float) -> tuple[float, float]:
            x_ratio = (x_value - x_min) / (x_max - x_min)
            y_ratio = (y_value - y_min) / (y_max - y_min)
            x_pixel = chart_rect.left() + x_ratio * chart_rect.width()
            y_pixel = chart_rect.bottom() - y_ratio * chart_rect.height()
            return x_pixel, y_pixel

        tick_count = 4
        painter.setPen(QPen(QColor("#e5e7eb"), 1, Qt.DashLine))
        for tick_index in range(tick_count + 1):
            x_ratio = tick_index / tick_count
            x_pixel = chart_rect.left() + x_ratio * chart_rect.width()
            painter.drawLine(int(x_pixel), chart_rect.top(), int(x_pixel), chart_rect.bottom())

        if y_min <= 0.0 <= y_max:
            _, zero_y = to_pixel(x_min, 0.0)
            painter.drawLine(chart_rect.left(), int(zero_y), chart_rect.right(), int(zero_y))

        painter.setPen(QPen(QColor("#111827"), 1))
        for tick_index in range(tick_count + 1):
            x_ratio = tick_index / tick_count
            x_value = x_min + x_ratio * (x_max - x_min)
            x_pixel = chart_rect.left() + x_ratio * chart_rect.width()
            painter.drawText(int(x_pixel) - 18, chart_rect.bottom() + 18, f"{x_value:.2f}")

        painter.drawText(chart_rect.center().x() - 32, self.height() - 14, "Time (us)")
        painter.drawText(12, 36, "Amplitude")

        step = max(1, int(math.ceil(len(self.time_us) / max(chart_rect.width(), 1))))
        plot_x = self.time_us[::step]
        plot_y = self.waveform_mv[::step]

        painter.setPen(QPen(QColor("#0b6e4f"), 2))
        for index in range(1, len(plot_x)):
            x1, y1 = to_pixel(float(plot_x[index - 1]), float(plot_y[index - 1]))
            x2, y2 = to_pixel(float(plot_x[index]), float(plot_y[index]))
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        if math.isfinite(self.f0_hz):
            painter.setPen(QPen(QColor("#111827"), 1))
            painter.drawText(chart_rect.right() - 148, chart_rect.top() + 18, f"f0 = {self.f0_hz / 1e6:.3f} MHz")


class PcdScatterWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.no_results: list[PcdMetrics] = []
        self.cav_results: list[PcdMetrics] = []
        self.live_results: list[PcdMetrics] = []
        self.setMinimumHeight(320)
        self.setStyleSheet("background: white;")

    def set_reference_results(self, no_results: list[PcdMetrics], cav_results: list[PcdMetrics]) -> None:
        self.no_results = no_results
        self.cav_results = cav_results
        self.update()

    def clear_live_results(self) -> None:
        self.live_results = []
        self.update()

    def add_live_result(self, metrics: PcdMetrics, max_points: int) -> None:
        self.live_results.append(metrics)
        if len(self.live_results) > max_points:
            self.live_results = self.live_results[-max_points:]
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))

        chart_rect = self.rect().adjusted(72, 28, -24, -58)
        painter.setPen(QPen(QColor("#d0d7de"), 1))
        painter.drawRect(chart_rect)

        points = [metrics.plot_coordinates() for metrics in self.no_results + self.cav_results + self.live_results]
        if points:
            x_values = [point[0] for point in points]
            y_values = [point[1] for point in points]
            x_min, x_max = min(x_values), max(x_values)
            y_min, y_max = min(y_values), max(y_values)
        else:
            x_min = y_min = -6.0
            x_max = y_max = 0.0

        if abs(x_max - x_min) < 0.5:
            x_min -= 0.25
            x_max += 0.25
        if abs(y_max - y_min) < 0.5:
            y_min -= 0.25
            y_max += 0.25

        x_pad = max(0.2, (x_max - x_min) * 0.08)
        y_pad = max(0.2, (y_max - y_min) * 0.08)
        x_min -= x_pad
        x_max += x_pad
        y_min -= y_pad
        y_max += y_pad

        def to_pixel(x_value: float, y_value: float) -> tuple[float, float]:
            x_ratio = (x_value - x_min) / (x_max - x_min)
            y_ratio = (y_value - y_min) / (y_max - y_min)
            x_pixel = chart_rect.left() + x_ratio * chart_rect.width()
            y_pixel = chart_rect.bottom() - y_ratio * chart_rect.height()
            return x_pixel, y_pixel

        tick_count = 5
        painter.setPen(QPen(QColor("#e5e7eb"), 1, Qt.DashLine))
        for tick_index in range(tick_count + 1):
            x_ratio = tick_index / tick_count
            y_ratio = tick_index / tick_count
            x_pixel = chart_rect.left() + x_ratio * chart_rect.width()
            y_pixel = chart_rect.bottom() - y_ratio * chart_rect.height()
            painter.drawLine(int(x_pixel), chart_rect.top(), int(x_pixel), chart_rect.bottom())
            painter.drawLine(chart_rect.left(), int(y_pixel), chart_rect.right(), int(y_pixel))

        painter.setPen(QPen(QColor("#111827"), 1))
        for tick_index in range(tick_count + 1):
            x_ratio = tick_index / tick_count
            y_ratio = tick_index / tick_count
            x_value = x_min + x_ratio * (x_max - x_min)
            y_value = y_min + y_ratio * (y_max - y_min)
            x_pixel = chart_rect.left() + x_ratio * chart_rect.width()
            y_pixel = chart_rect.bottom() - y_ratio * chart_rect.height()
            painter.drawText(int(x_pixel) - 18, chart_rect.bottom() + 20, f"{x_value:.1f}")
            painter.drawText(10, int(y_pixel) + 5, f"{y_value:.1f}")

        painter.drawText(chart_rect.center().x() - 85, self.height() - 18, "log10(SCDultra)")
        painter.drawText(12, 18, "log10(ICD)")
        painter.drawText(chart_rect.left(), 18, "Real-time PCD Scatter")

        self._draw_result_group(painter, self.no_results, to_pixel, QColor("#444444"), 4)
        self._draw_result_group(painter, self.cav_results, to_pixel, QColor("#d97706"), 5)
        self._draw_result_group(painter, self.live_results[:-1], to_pixel, QColor("#2563eb"), 6)

        if self.live_results:
            latest = self.live_results[-1]
            latest_color = score_to_color(latest.cavitation_score)
            latest_x, latest_y = to_pixel(*latest.plot_coordinates())
            painter.setBrush(latest_color)
            painter.setPen(QPen(QColor("#0f172a"), 2))
            painter.drawEllipse(int(latest_x) - 6, int(latest_y) - 6, 12, 12)
            painter.setPen(QPen(QColor("#0f172a"), 1))
            painter.drawText(int(latest_x) + 10, int(latest_y) - 10, latest.file or "latest")

        self._draw_legend(painter, chart_rect)

    def _draw_result_group(self, painter: QPainter, results: list[PcdMetrics], mapper, color: QColor, radius: int) -> None:
        painter.setBrush(color)
        painter.setPen(QPen(color, 1))
        for metrics in results:
            x_pixel, y_pixel = mapper(*metrics.plot_coordinates())
            painter.drawEllipse(int(x_pixel) - radius // 2, int(y_pixel) - radius // 2, radius, radius)

    def _draw_legend(self, painter: QPainter, chart_rect) -> None:
        legend_items = [
            (QColor("#444444"), "Reference: no cavitation"),
            (QColor("#d97706"), "Reference: cavitation"),
            (QColor("#2563eb"), "Live / playback"),
        ]
        base_x = chart_rect.right() - 220
        base_y = chart_rect.top() + 10
        painter.setPen(QPen(QColor("#111827"), 1))
        for index, (color, text) in enumerate(legend_items):
            y = base_y + index * 18
            painter.setBrush(color)
            painter.drawEllipse(base_x, y, 8, 8)
            painter.drawText(base_x + 14, y + 8, text)

class AcquisitionWorker(QObject):
    log_message = pyqtSignal(str)
    reference_ready = pyqtSignal(object)
    frame_ready = pyqtSignal(object)
    playback_state_changed = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.settings = settings
        self._stop_requested = False
        self._playback_lock = threading.Lock()
        self._playback_files: list[Path] = []
        self._playback_current_index = -1
        self._playback_paused = False
        self._playback_step_delta = 0
        self._playback_delete_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def toggle_playback_pause(self) -> None:
        with self._playback_lock:
            if self.settings.ui.last_mode != "playback" or not self._playback_files:
                return
            self._playback_paused = not self._playback_paused
            paused = self._playback_paused
        self.log_message.emit("Playback paused." if paused else "Playback resumed.")
        self._emit_playback_state(is_active=True)

    def step_playback(self, delta: int) -> None:
        if delta == 0:
            return
        with self._playback_lock:
            if self.settings.ui.last_mode != "playback" or not self._playback_files or not self._playback_paused:
                return
            self._playback_step_delta += int(delta)
        self._emit_playback_state(is_active=True)

    def delete_current_playback_frame(self) -> None:
        with self._playback_lock:
            if self.settings.ui.last_mode != "playback" or not self._playback_files or not self._playback_paused:
                return
            self._playback_delete_requested = True
        self._emit_playback_state(is_active=True)

    @pyqtSlot()
    def run(self) -> None:
        try:
            self.log_message.emit("Loading reference database...")
            reference_stats = build_reference_statistics(self.settings.reference, self.settings.analysis)
            self.reference_ready.emit(reference_stats)
            self.log_message.emit(
                f"Reference loaded: no cavitation {len(reference_stats.no_results)} files, cavitation {len(reference_stats.cav_results)} files."
            )
            if self.settings.ui.last_mode == "hardware":
                self._run_hardware(reference_stats)
            else:
                self._run_playback(reference_stats)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self._emit_playback_state(is_active=False)
            self.finished.emit()

    def _run_playback(self, reference_stats: ReferenceStats) -> None:
        playback_files = resolve_input_patterns(self.settings.playback.source_patterns)
        if not playback_files:
            raise FileNotFoundError("No playback CSV files matched the configured input.")

        with self._playback_lock:
            self._playback_files = list(playback_files)
            self._playback_current_index = 0
            self._playback_paused = False
            self._playback_step_delta = 0
            self._playback_delete_requested = False
        self._emit_playback_state(is_active=True)
        self.log_message.emit(f"Playback queue prepared with {len(playback_files)} files.")
        frame_index = 0
        while not self._stop_requested:
            file_path, current_position, total_frames = self._current_playback_file_state()
            if file_path is None:
                self.log_message.emit("Playback queue is empty.")
                return

            signal, sample_rate_hz = load_signal_csv(file_path, self.settings.analysis.target_sample_count)
            effective_sample_rate = sample_rate_hz or self.settings.hardware.sample_rate_hz
            metrics = analyze_signal(
                signal=signal,
                sample_rate_hz=effective_sample_rate,
                analysis_settings=self.settings.analysis,
                reference_stats=reference_stats,
                file_name=file_path.name,
                relative_path=relative_to_workspace(file_path),
                group_name="Playback",
            )
            waveform_time_us, waveform_mv = build_center_waveform_excerpt(signal, effective_sample_rate, metrics.f0_hz)
            frame_index += 1
            self._emit_playback_state(is_active=True)
            self.frame_ready.emit(
                AnalysisFrame(
                    sequence_index=frame_index,
                    source_label=file_path.name,
                    captured_at=datetime.now(),
                    metrics=metrics,
                    sample_rate_hz=effective_sample_rate,
                    waveform_time_us=waveform_time_us,
                    waveform_mv=waveform_mv,
                )
            )
            self.log_message.emit(
                f"Playback frame {current_position}/{total_frames}: {file_path.name} -> score {metrics.cavitation_score:.3f}, risk {metrics.risk_score:.3f}"
            )

            next_action, delta = self._wait_for_playback_action(self.settings.playback.interval_ms)
            if next_action == "stop":
                return
            if next_action == "delete":
                if not self._delete_current_playback_file():
                    return
                continue
            if next_action == "step":
                self._shift_playback_index(delta)
                continue
            if next_action == "advance":
                if not self._advance_playback_index():
                    return
                continue

    def _run_hardware(self, reference_stats: ReferenceStats) -> None:
        output_dir = resolve_workspace_path(self.settings.hardware.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        frame_index = 0
        with Acts1000Device(self.settings.hardware) as device:
            self.log_message.emit(
                f"ART device ready: {device.model_name}, base rate {device.base_rate:.0f} Hz, code count {device.code_count}."
            )
            while not self._stop_requested:
                capture_result = device.capture_once()
                saved_csv_path = None
                if self.settings.hardware.save_csv:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                    saved_csv_path = output_dir / f"capture_{timestamp}.csv"
                    write_capture_csv(
                        saved_csv_path,
                        capture_result.raw_codes,
                        capture_result.voltage_mv,
                        capture_result.sample_rate_hz,
                    )
                metrics = analyze_signal(
                    signal=capture_result.voltage_mv,
                    sample_rate_hz=capture_result.sample_rate_hz,
                    analysis_settings=self.settings.analysis,
                    reference_stats=reference_stats,
                    file_name=saved_csv_path.name if saved_csv_path else f"capture_{frame_index + 1:04d}",
                    relative_path=relative_to_workspace(saved_csv_path) if saved_csv_path else "",
                    group_name="Live Acquisition",
                )
                waveform_time_us, waveform_mv = build_center_waveform_excerpt(
                    capture_result.voltage_mv, capture_result.sample_rate_hz, metrics.f0_hz
                )
                frame_index += 1
                self.frame_ready.emit(
                    AnalysisFrame(
                        sequence_index=frame_index,
                        source_label=metrics.file,
                        captured_at=datetime.now(),
                        metrics=metrics,
                        sample_rate_hz=capture_result.sample_rate_hz,
                        waveform_time_us=waveform_time_us,
                        waveform_mv=waveform_mv,
                        saved_csv_path=saved_csv_path,
                    )
                )
                self.log_message.emit(
                    f"Hardware frame {frame_index}: score {metrics.cavitation_score:.3f}, risk {metrics.risk_score:.3f}, file {metrics.file or 'memory'}"
                )

    def _sleep_with_stop(self, interval_ms: int) -> bool:
        deadline = time.time() + max(interval_ms, 0) / 1000.0
        while time.time() < deadline:
            if self._stop_requested:
                return False
            time.sleep(0.05)
        return True

    def _current_playback_file_state(self) -> tuple[Path | None, int, int]:
        with self._playback_lock:
            total_frames = len(self._playback_files)
            current_index = self._playback_current_index
            if total_frames == 0 or current_index < 0 or current_index >= total_frames:
                return None, 0, total_frames
            return self._playback_files[current_index], current_index + 1, total_frames

    def _snapshot_playback_state(self, is_active: bool) -> PlaybackUiState:
        with self._playback_lock:
            total_frames = len(self._playback_files)
            current_index = self._playback_current_index
            current_file = ""
            if 0 <= current_index < total_frames:
                current_file = self._playback_files[current_index].name
            current_position = current_index + 1 if 0 <= current_index < total_frames else 0
            is_paused = self._playback_paused if is_active else False
        return PlaybackUiState(
            is_active=is_active,
            is_paused=is_paused,
            current_position=current_position,
            total_frames=total_frames,
            current_file=current_file,
        )

    def _emit_playback_state(self, is_active: bool) -> None:
        self.playback_state_changed.emit(self._snapshot_playback_state(is_active))

    def _wait_for_playback_action(self, interval_ms: int) -> tuple[str, int]:
        deadline = time.time() + max(interval_ms, 0) / 1000.0
        while True:
            if self._stop_requested:
                return "stop", 0

            with self._playback_lock:
                delete_requested = self._playback_delete_requested
                step_delta = self._playback_step_delta
                is_paused = self._playback_paused
                if delete_requested:
                    self._playback_delete_requested = False
                if step_delta != 0:
                    self._playback_step_delta = 0

            if delete_requested:
                return "delete", 0
            if step_delta != 0:
                return "step", step_delta
            if is_paused:
                time.sleep(0.05)
                continue
            if time.time() >= deadline:
                return "advance", 1
            time.sleep(0.05)

    def _advance_playback_index(self) -> bool:
        with self._playback_lock:
            if not self._playback_files:
                return False
            next_index = self._playback_current_index + 1
            if next_index >= len(self._playback_files):
                if self.settings.playback.loop_playback:
                    next_index = 0
                else:
                    return False
            self._playback_current_index = next_index
        self._emit_playback_state(is_active=True)
        return True

    def _shift_playback_index(self, delta: int) -> None:
        with self._playback_lock:
            if not self._playback_files:
                return
            next_index = self._playback_current_index + delta
            next_index = min(max(next_index, 0), len(self._playback_files) - 1)
            self._playback_current_index = next_index
        self._emit_playback_state(is_active=True)

    def _delete_current_playback_file(self) -> bool:
        with self._playback_lock:
            if not self._playback_files:
                return False
            current_index = self._playback_current_index
            current_file = self._playback_files[current_index]

        target_dir = current_file.parent / "deleted_frames"
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / current_file.name
        if destination.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            destination = target_dir / f"{current_file.stem}_{timestamp}{current_file.suffix}"
        shutil.move(str(current_file), str(destination))
        self.log_message.emit(f"Moved playback frame to deleted_frames: {current_file.name}")

        with self._playback_lock:
            if current_index < len(self._playback_files) and self._playback_files[current_index] == current_file:
                self._playback_files.pop(current_index)
            else:
                self._playback_files = [path for path in self._playback_files if path != current_file]
            if not self._playback_files:
                self._playback_current_index = -1
                should_continue = False
            else:
                if current_index >= len(self._playback_files):
                    current_index = len(self._playback_files) - 1
                self._playback_current_index = current_index
                should_continue = True
        self._emit_playback_state(is_active=True)
        return should_continue


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_app_settings()
        self.worker_thread: QThread | None = None
        self.worker: AcquisitionWorker | None = None
        self.playback_state = PlaybackUiState()

        self.setWindowTitle("ART + PCD Integrated Monitor")
        self.resize(1480, 900)
        self._build_ui()
        self._apply_settings_to_ui(self.settings)
        self._update_mode_visibility()
        self.statusBar().showMessage("Ready")
        self.append_log(f"Workspace root: {workspace_root()}")

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)

        splitter = QSplitter(Qt.Horizontal)
        root_layout.addWidget(splitter)

        left_panel = QWidget()
        left_panel.setMaximumWidth(540)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(10)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Playback 回放", "playback")
        self.mode_combo.addItem("Hardware 真机", "hardware")
        self.mode_combo.currentIndexChanged.connect(self._update_mode_visibility)

        mode_group = QGroupBox("运行模式")
        mode_form = QFormLayout(mode_group)
        mode_form.addRow("模式", self.mode_combo)
        left_layout.addWidget(mode_group)

        self.playback_source_edit = QLineEdit()
        self.playback_browse_button = QPushButton("选择文件夹")
        self.playback_browse_button.clicked.connect(self._browse_playback_source)
        self.playback_interval_spin = QSpinBox()
        self.playback_interval_spin.setRange(50, 10_000)
        self.playback_interval_spin.setSuffix(" ms")
        self.playback_loop_check = QCheckBox("循环回放")
        self.playback_position_label = QLabel("- / -")
        self.playback_file_label = QLabel("-")
        self.playback_file_label.setWordWrap(True)
        self.playback_pause_button = QPushButton("暂停")
        self.playback_pause_button.clicked.connect(self._toggle_playback_pause)
        self.playback_back10_button = QPushButton("-10")
        self.playback_back10_button.clicked.connect(lambda: self._step_playback(-10))
        self.playback_back1_button = QPushButton("-1")
        self.playback_back1_button.clicked.connect(lambda: self._step_playback(-1))
        self.playback_forward1_button = QPushButton("+1")
        self.playback_forward1_button.clicked.connect(lambda: self._step_playback(1))
        self.playback_forward10_button = QPushButton("+10")
        self.playback_forward10_button.clicked.connect(lambda: self._step_playback(10))
        self.playback_delete_button = QPushButton("删除当前帧")
        self.playback_delete_button.clicked.connect(self._delete_playback_frame)

        playback_control_row = QWidget()
        playback_control_layout = QHBoxLayout(playback_control_row)
        playback_control_layout.setContentsMargins(0, 0, 0, 0)
        playback_control_layout.setSpacing(4)
        playback_control_layout.addWidget(self.playback_back10_button)
        playback_control_layout.addWidget(self.playback_back1_button)
        playback_control_layout.addWidget(self.playback_forward1_button)
        playback_control_layout.addWidget(self.playback_forward10_button)

        self.playback_group = QGroupBox("回放设置")
        playback_form = QFormLayout(self.playback_group)
        playback_form.addRow("CSV 来源", self._build_path_row(self.playback_source_edit, self.playback_browse_button))
        playback_form.addRow("帧间隔", self.playback_interval_spin)
        playback_form.addRow("", self.playback_loop_check)
        playback_form.addRow("当前位置", self.playback_position_label)
        playback_form.addRow("当前文件", self.playback_file_label)
        playback_form.addRow("", self.playback_pause_button)
        playback_form.addRow("跳帧", playback_control_row)
        playback_form.addRow("", self.playback_delete_button)
        left_layout.addWidget(self.playback_group)

        self.dll_path_edit = QLineEdit()
        self.dll_browse_button = QPushButton("选择 DLL")
        self.dll_browse_button.clicked.connect(self._browse_dll_path)
        self.device_id_spin = QSpinBox()
        self.device_id_spin.setRange(0, 16)
        self.sample_rate_spin = QDoubleSpinBox()
        self.sample_rate_spin.setRange(1_000_000.0, 100_000_000.0)
        self.sample_rate_spin.setDecimals(0)
        self.sample_rate_spin.setSingleStep(1_000_000.0)
        self.sample_rate_spin.setSuffix(" Hz")
        self.points_spin = QSpinBox()
        self.points_spin.setRange(512, 1_000_000)
        self.points_spin.setSingleStep(512)
        self.input_range_combo = QComboBox()
        self.input_range_combo.addItems(["pm1000", "pm5000"])
        self.input_impedance_combo = QComboBox()
        self.input_impedance_combo.addItems(["50", "1m"])
        self.trigger_source_combo = QComboBox()
        self.trigger_source_combo.addItems(["sync0", "dtr"])
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(0.1, 120.0)
        self.timeout_spin.setDecimals(1)
        self.timeout_spin.setSuffix(" s")
        self.save_csv_check = QCheckBox("保存采集 CSV")
        self.output_dir_edit = QLineEdit()
        self.output_dir_browse_button = QPushButton("选择目录")
        self.output_dir_browse_button.clicked.connect(self._browse_output_dir)

        self.hardware_group = QGroupBox("ART 采集设置")
        hardware_form = QFormLayout(self.hardware_group)
        hardware_form.addRow("DLL 路径", self._build_path_row(self.dll_path_edit, self.dll_browse_button))
        hardware_form.addRow("设备 ID", self.device_id_spin)
        hardware_form.addRow("采样率", self.sample_rate_spin)
        hardware_form.addRow("点数", self.points_spin)
        hardware_form.addRow("输入量程", self.input_range_combo)
        hardware_form.addRow("输入阻抗", self.input_impedance_combo)
        hardware_form.addRow("触发源", self.trigger_source_combo)
        hardware_form.addRow("读超时", self.timeout_spin)
        hardware_form.addRow("", self.save_csv_check)
        hardware_form.addRow("输出目录", self._build_path_row(self.output_dir_edit, self.output_dir_browse_button))
        left_layout.addWidget(self.hardware_group)

        self.reference_no_edit = QLineEdit()
        self.reference_cav_edit = QLineEdit()
        self.spectrum_mode_combo = QComboBox()
        self.spectrum_mode_combo.addItems(["amplitude", "power"])
        self.segment_count_spin = QSpinBox()
        self.segment_count_spin.setRange(1, 20)
        self.peak_half_width_spin = QDoubleSpinBox()
        self.peak_half_width_spin.setRange(0.1, 1_000.0)
        self.peak_half_width_spin.setDecimals(1)
        self.peak_half_width_spin.setSingleStep(1.0)
        self.peak_half_width_spin.setSuffix(" kHz")
        self.noise_half_width_spin = QDoubleSpinBox()
        self.noise_half_width_spin.setRange(0.1, 1_000.0)
        self.noise_half_width_spin.setDecimals(1)
        self.noise_half_width_spin.setSingleStep(1.0)
        self.noise_half_width_spin.setSuffix(" kHz")
        self.broadband_half_width_spin = QDoubleSpinBox()
        self.broadband_half_width_spin.setRange(0.1, 1_000.0)
        self.broadband_half_width_spin.setDecimals(1)
        self.broadband_half_width_spin.setSingleStep(1.0)
        self.broadband_half_width_spin.setSuffix(" kHz")
        self.order_range_low_spin = QDoubleSpinBox()
        self.order_range_low_spin.setRange(0.0, 20.0)
        self.order_range_low_spin.setDecimals(2)
        self.order_range_low_spin.setSingleStep(0.25)
        self.order_range_high_spin = QDoubleSpinBox()
        self.order_range_high_spin.setRange(0.0, 20.0)
        self.order_range_high_spin.setDecimals(2)
        self.order_range_high_spin.setSingleStep(0.25)
        self.peak_prominence_spin = QDoubleSpinBox()
        self.peak_prominence_spin.setRange(-120.0, 120.0)
        self.peak_prominence_spin.setDecimals(1)
        self.peak_prominence_spin.setSingleStep(1.0)
        self.peak_prominence_spin.setSuffix(" dB")

        analysis_group = QGroupBox("PCD 分析设置")
        analysis_form = QFormLayout(analysis_group)
        analysis_form.addRow("无空化参考", self.reference_no_edit)
        analysis_form.addRow("有空化参考", self.reference_cav_edit)
        analysis_form.addRow("频谱模式", self.spectrum_mode_combo)
        analysis_form.addRow("分段数量", self.segment_count_spin)
        analysis_form.addRow("峰值窗半宽", self.peak_half_width_spin)
        analysis_form.addRow("噪声窗半宽", self.noise_half_width_spin)
        analysis_form.addRow("宽带窗半宽", self.broadband_half_width_spin)
        analysis_form.addRow("倍频范围下限", self.order_range_low_spin)
        analysis_form.addRow("倍频范围上限", self.order_range_high_spin)
        analysis_form.addRow("峰显著性阈值", self.peak_prominence_spin)
        left_layout.addWidget(analysis_group)

        self.max_live_points_spin = QSpinBox()
        self.max_live_points_spin.setRange(10, 5000)
        self.max_live_points_spin.setSingleStep(10)

        display_group = QGroupBox("显示设置")
        display_form = QFormLayout(display_group)
        display_form.addRow("最大实时点数", self.max_live_points_spin)
        left_layout.addWidget(display_group)

        button_row = QHBoxLayout()
        self.start_button = QPushButton("开始")
        self.start_button.clicked.connect(self.start_processing)
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self.stop_processing)
        self.stop_button.setEnabled(False)
        self.clear_button = QPushButton("清空实时点")
        self.clear_button.clicked.connect(self._clear_live_points)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        button_row.addWidget(self.clear_button)
        left_layout.addLayout(button_row)
        left_layout.addStretch(1)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(10)

        self.scatter_widget = PcdScatterWidget()
        self.spectrum_widget = SpectrumWidget()
        self.waveform_widget = WaveformWidget()
        right_layout.addWidget(self.scatter_widget, stretch=4)
        right_layout.addWidget(self.spectrum_widget, stretch=2)
        right_layout.addWidget(self.waveform_widget, stretch=2)

        metrics_group = QGroupBox("最新结果")
        metrics_layout = QGridLayout(metrics_group)
        metrics_layout.setContentsMargins(8, 8, 8, 8)
        metrics_layout.setHorizontalSpacing(8)
        metrics_layout.setVerticalSpacing(4)
        self.latest_source_label = QLabel("-")
        self.latest_source_label.setWordWrap(True)
        self.latest_time_label = QLabel("-")
        self.latest_rate_label = QLabel("-")
        self.latest_f0_label = QLabel("-")
        self.latest_scdhar_label = QLabel("-")
        self.latest_scdultra_label = QLabel("-")
        self.latest_icd_label = QLabel("-")
        self.latest_ratio_label = QLabel("-")
        self.latest_score_label = QLabel("-")
        self.latest_risk_label = QLabel("-")
        self.latest_conclusion_label = QLabel("-")
        self.latest_conclusion_label.setWordWrap(True)
        metrics_layout.addWidget(QLabel("来源"), 0, 0)
        metrics_layout.addWidget(self.latest_source_label, 0, 1)
        metrics_layout.addWidget(QLabel("时间"), 0, 2)
        metrics_layout.addWidget(self.latest_time_label, 0, 3)
        metrics_layout.addWidget(QLabel("采样率"), 1, 0)
        metrics_layout.addWidget(self.latest_rate_label, 1, 1)
        metrics_layout.addWidget(QLabel("f0"), 1, 2)
        metrics_layout.addWidget(self.latest_f0_label, 1, 3)
        metrics_layout.addWidget(QLabel("SCDhar"), 2, 0)
        metrics_layout.addWidget(self.latest_scdhar_label, 2, 1)
        metrics_layout.addWidget(QLabel("SCDultra"), 2, 2)
        metrics_layout.addWidget(self.latest_scdultra_label, 2, 3)
        metrics_layout.addWidget(QLabel("ICD"), 3, 0)
        metrics_layout.addWidget(self.latest_icd_label, 3, 1)
        metrics_layout.addWidget(QLabel("Ultra/ICD"), 3, 2)
        metrics_layout.addWidget(self.latest_ratio_label, 3, 3)
        metrics_layout.addWidget(QLabel("Score"), 4, 0)
        metrics_layout.addWidget(self.latest_score_label, 4, 1)
        metrics_layout.addWidget(QLabel("Risk"), 4, 2)
        metrics_layout.addWidget(self.latest_risk_label, 4, 3)
        metrics_layout.addWidget(QLabel("结论"), 5, 0)
        metrics_layout.addWidget(self.latest_conclusion_label, 5, 1, 1, 3)
        right_layout.addWidget(metrics_group, stretch=0)

        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout(log_group)
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        log_layout.addWidget(self.log_output)
        right_layout.addWidget(log_group, stretch=1)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([420, 1020])

    def _build_path_row(self, line_edit: QLineEdit, button: QPushButton) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(line_edit)
        layout.addWidget(button)
        return container

    def _apply_settings_to_ui(self, settings: AppSettings) -> None:
        self.mode_combo.setCurrentIndex(0 if settings.ui.last_mode == "playback" else 1)
        self.playback_source_edit.setText(join_patterns(settings.playback.source_patterns))
        self.playback_interval_spin.setValue(settings.playback.interval_ms)
        self.playback_loop_check.setChecked(settings.playback.loop_playback)
        self.dll_path_edit.setText(settings.hardware.dll_path)
        self.device_id_spin.setValue(settings.hardware.device_id)
        self.sample_rate_spin.setValue(settings.hardware.sample_rate_hz)
        self.points_spin.setValue(settings.hardware.points)
        self.input_range_combo.setCurrentText(settings.hardware.input_range)
        self.input_impedance_combo.setCurrentText(settings.hardware.input_impedance)
        self.trigger_source_combo.setCurrentText(settings.hardware.trigger_source)
        self.timeout_spin.setValue(settings.hardware.timeout_seconds)
        self.save_csv_check.setChecked(settings.hardware.save_csv)
        self.output_dir_edit.setText(settings.hardware.output_dir)
        self.reference_no_edit.setText(join_patterns(settings.reference.no_cavitation_patterns))
        self.reference_cav_edit.setText(join_patterns(settings.reference.cavitation_patterns))
        self.spectrum_mode_combo.setCurrentText(settings.analysis.spectrum_mode)
        self.segment_count_spin.setValue(settings.analysis.segment_count)
        self.peak_half_width_spin.setValue(settings.analysis.peak_half_width_hz / 1e3)
        self.noise_half_width_spin.setValue(settings.analysis.noise_half_width_hz / 1e3)
        self.broadband_half_width_spin.setValue(settings.analysis.broadband_half_width_hz / 1e3)
        self.order_range_low_spin.setValue(settings.analysis.order_range_low)
        self.order_range_high_spin.setValue(settings.analysis.order_range_high)
        self.peak_prominence_spin.setValue(settings.analysis.min_peak_prominence_db)
        self.max_live_points_spin.setValue(settings.ui.max_live_points)
        self._set_playback_ui_state(PlaybackUiState())

    def _collect_settings_from_ui(self) -> AppSettings:
        analysis_settings = AnalysisSettings.from_dict(asdict(self.settings.analysis))
        analysis_settings.spectrum_mode = self.spectrum_mode_combo.currentText()
        analysis_settings.use_segment_average = self.segment_count_spin.value() > 1
        analysis_settings.segment_count = self.segment_count_spin.value()
        analysis_settings.target_sample_count = self.points_spin.value()
        analysis_settings.peak_half_width_hz = self.peak_half_width_spin.value() * 1e3
        analysis_settings.noise_half_width_hz = self.noise_half_width_spin.value() * 1e3
        analysis_settings.broadband_half_width_hz = self.broadband_half_width_spin.value() * 1e3
        analysis_settings.order_range_low = self.order_range_low_spin.value()
        analysis_settings.order_range_high = self.order_range_high_spin.value()
        analysis_settings.min_peak_prominence_db = self.peak_prominence_spin.value()
        analysis_settings.validate()

        return AppSettings(
            hardware=HardwareSettings(
                dll_path=self.dll_path_edit.text().strip(),
                device_id=self.device_id_spin.value(),
                sample_rate_hz=self.sample_rate_spin.value(),
                points=self.points_spin.value(),
                input_range=self.input_range_combo.currentText(),
                input_impedance=self.input_impedance_combo.currentText(),
                trigger_source=self.trigger_source_combo.currentText(),
                timeout_seconds=self.timeout_spin.value(),
                save_csv=self.save_csv_check.isChecked(),
                output_dir=self.output_dir_edit.text().strip(),
            ),
            playback=PlaybackSettings(
                source_patterns=split_patterns(self.playback_source_edit.text()),
                interval_ms=self.playback_interval_spin.value(),
                loop_playback=self.playback_loop_check.isChecked(),
            ),
            reference=ReferenceSettings(
                no_cavitation_patterns=split_patterns(self.reference_no_edit.text()),
                cavitation_patterns=split_patterns(self.reference_cav_edit.text()),
            ),
            analysis=analysis_settings,
            ui=UiSettings(last_mode=self.mode_combo.currentData(), max_live_points=self.max_live_points_spin.value()),
        )

    def _update_mode_visibility(self) -> None:
        is_hardware = self.mode_combo.currentData() == "hardware"
        self.playback_group.setEnabled(not is_hardware)
        self.hardware_group.setEnabled(is_hardware)
        self._set_playback_ui_state(self.playback_state)

    def _set_playback_ui_state(self, state: PlaybackUiState) -> None:
        self.playback_state = state
        is_playback_mode = self.mode_combo.currentData() == "playback"
        position_text = (
            f"{state.current_position} / {state.total_frames}"
            if state.total_frames > 0 and state.current_position > 0
            else "- / -"
        )
        self.playback_position_label.setText(position_text)
        self.playback_file_label.setText(state.current_file or "-")

        can_pause = is_playback_mode and state.is_active and state.total_frames > 0
        can_step = can_pause and state.is_paused
        self.playback_pause_button.setEnabled(can_pause)
        self.playback_pause_button.setText("继续" if state.is_paused and can_pause else "暂停")
        self.playback_back10_button.setEnabled(can_step)
        self.playback_back1_button.setEnabled(can_step)
        self.playback_forward1_button.setEnabled(can_step)
        self.playback_forward10_button.setEnabled(can_step)
        self.playback_delete_button.setEnabled(can_step)

    def _toggle_playback_pause(self) -> None:
        if self.worker is None or self.mode_combo.currentData() != "playback":
            return
        self.worker.toggle_playback_pause()

    def _step_playback(self, delta: int) -> None:
        if self.worker is None or self.mode_combo.currentData() != "playback":
            return
        self.worker.step_playback(delta)

    def _delete_playback_frame(self) -> None:
        if self.worker is None or self.mode_combo.currentData() != "playback" or not self.playback_state.current_file:
            return
        answer = QMessageBox.question(
            self,
            "删除当前帧",
            f"将当前帧移动到 deleted_frames？\n\n{self.playback_state.current_file}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.worker.delete_current_playback_frame()

    def _browse_playback_source(self) -> None:
        selected_dir = QFileDialog.getExistingDirectory(
            self,
            "选择包含回放 CSV 的目录",
            str(resolve_workspace_path(self.playback_source_edit.text() or "data")),
        )
        if selected_dir:
            portable = make_portable_path(Path(selected_dir))
            self.playback_source_edit.setText(f"{portable}/*.csv")

    def _browse_dll_path(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 ACTS1000 DLL",
            str(resolve_workspace_path(self.dll_path_edit.text() or "vendor")),
            "DLL (*.dll)",
        )
        if file_path:
            self.dll_path_edit.setText(make_portable_path(Path(file_path)))

    def _browse_output_dir(self) -> None:
        selected_dir = QFileDialog.getExistingDirectory(
            self,
            "选择采集输出目录",
            str(resolve_workspace_path(self.output_dir_edit.text() or "output")),
        )
        if selected_dir:
            self.output_dir_edit.setText(make_portable_path(Path(selected_dir)))

    def _clear_live_points(self) -> None:
        self.scatter_widget.clear_live_results()
        self.spectrum_widget.clear_data()
        self.waveform_widget.clear_data()
        self.append_log("Live points cleared.")

    def start_processing(self) -> None:
        if self.worker_thread and self.worker_thread.isRunning():
            return
        try:
            self.settings = self._collect_settings_from_ui()
            save_app_settings(self.settings)
        except Exception as exc:
            QMessageBox.critical(self, "配置错误", str(exc))
            return

        self.scatter_widget.clear_live_results()
        self.spectrum_widget.clear_data()
        self.waveform_widget.clear_data()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.statusBar().showMessage("Running")
        self.append_log("Processing started.")

        self.worker_thread = QThread(self)
        self.worker = AcquisitionWorker(self.settings)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.log_message.connect(self.append_log)
        self.worker.reference_ready.connect(self._on_reference_ready)
        self.worker.frame_ready.connect(self._on_frame_ready)
        self.worker.playback_state_changed.connect(self._on_playback_state_changed)
        self.worker.error.connect(self._on_worker_error)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self._on_worker_finished)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    def stop_processing(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.append_log("Stop requested. Waiting for the current acquisition to finish...")
            self.statusBar().showMessage("Stopping...")

    def _on_reference_ready(self, reference_stats: ReferenceStats) -> None:
        self.scatter_widget.set_reference_results(reference_stats.no_results, reference_stats.cav_results)

    def _on_playback_state_changed(self, state: PlaybackUiState) -> None:
        self._set_playback_ui_state(state)

    def _on_frame_ready(self, frame: AnalysisFrame) -> None:
        self.scatter_widget.add_live_result(frame.metrics, self.max_live_points_spin.value())
        self.spectrum_widget.set_spectrum(
            frame.metrics.frequency_hz,
            frame.metrics.spectrum,
            frame.metrics.spectrum_mode,
            frame.metrics.f0_hz,
        )
        self.waveform_widget.set_waveform(frame.waveform_time_us, frame.waveform_mv, frame.metrics.f0_hz)
        self.latest_source_label.setText(frame.source_label or "-")
        self.latest_time_label.setText(frame.captured_at.strftime("%Y-%m-%d %H:%M:%S"))
        self.latest_rate_label.setText(f"{frame.sample_rate_hz / 1e6:.3f} MHz")
        self.latest_f0_label.setText(f"{frame.metrics.f0_hz / 1e6:.3f} MHz")
        self.latest_scdhar_label.setText(f"{frame.metrics.scd_har:.4e}")
        self.latest_scdultra_label.setText(f"{frame.metrics.scd_ultra:.4e}")
        self.latest_icd_label.setText(f"{frame.metrics.icd:.4e}")
        self.latest_ratio_label.setText(f"{frame.metrics.ultra_to_icd_ratio:.3f}")
        self.latest_score_label.setText(f"{frame.metrics.cavitation_score:.3f}")
        self.latest_risk_label.setText(f"{frame.metrics.risk_score:.3f}")
        self.latest_conclusion_label.setText(frame.metrics.conclusion or "-")
        self.statusBar().showMessage(f"Latest frame {frame.sequence_index}: {frame.source_label}")

    def _on_worker_error(self, message: str) -> None:
        self.append_log(f"ERROR: {message}")
        QMessageBox.critical(self, "运行错误", message)

    def _on_worker_finished(self) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.statusBar().showMessage("Ready")
        self._set_playback_ui_state(
            PlaybackUiState(
                is_active=False,
                is_paused=False,
                current_position=self.playback_state.current_position,
                total_frames=self.playback_state.total_frames,
                current_file=self.playback_state.current_file,
            )
        )
        self.worker = None
        self.worker_thread = None
        self.append_log("Processing finished.")

    def append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.appendPlainText(f"[{timestamp}] {message}")

    def closeEvent(self, event) -> None:
        try:
            self.settings = self._collect_settings_from_ui()
            save_app_settings(self.settings)
        except Exception:
            pass
        if self.worker is not None:
            self.worker.stop()
        if self.worker_thread is not None:
            self.worker_thread.quit()
            self.worker_thread.wait(1500)
        event.accept()


def load_app_settings() -> AppSettings:
    config_path = settings_path()
    if not config_path.exists():
        settings = AppSettings()
        save_app_settings(settings)
        return settings
    with config_path.open("r", encoding="utf-8") as handle:
        return AppSettings.from_dict(json.load(handle))


def save_app_settings(settings: AppSettings) -> None:
    with settings_path().open("w", encoding="utf-8") as handle:
        json.dump(settings.to_dict(), handle, indent=2, ensure_ascii=False)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())







