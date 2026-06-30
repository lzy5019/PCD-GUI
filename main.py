from __future__ import annotations

import csv
import ctypes
import glob
import json
import math
import os
import re
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
from PyQt5.QtCore import QObject, QRect, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
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
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class WheelSafeComboBox(QComboBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class WheelSafeSpinBox(QSpinBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class WheelSafeDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


QComboBox = WheelSafeComboBox
QSpinBox = WheelSafeSpinBox
QDoubleSpinBox = WheelSafeDoubleSpinBox


class RangeSlider(QWidget):
    rangeChanged = pyqtSignal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self.minimum = 1
        self.maximum = 1
        self.lower_value = 1
        self.upper_value = 1
        self._active_handle: str | None = None
        self.setMinimumHeight(30)
        self.setMinimumWidth(140)

    def setRange(self, minimum: int, maximum: int) -> None:
        minimum = max(1, int(minimum))
        maximum = max(minimum, int(maximum))
        self.minimum = minimum
        self.maximum = maximum
        self.setValues(self.lower_value, self.upper_value, emit_signal=False)

    def setValues(self, lower: int, upper: int, emit_signal: bool = True) -> None:
        lower = min(max(int(lower), self.minimum), self.maximum)
        upper = min(max(int(upper), self.minimum), self.maximum)
        if lower > upper:
            lower, upper = upper, lower
        changed = lower != self.lower_value or upper != self.upper_value
        self.lower_value = lower
        self.upper_value = upper
        self.update()
        if changed and emit_signal:
            self.rangeChanged.emit(self.lower_value, self.upper_value)

    def _track_rect(self) -> QRect:
        return self.rect().adjusted(12, 11, -12, -11)

    def _value_to_x(self, value: int) -> int:
        track = self._track_rect()
        if self.maximum <= self.minimum:
            return track.left()
        ratio = (value - self.minimum) / (self.maximum - self.minimum)
        return int(track.left() + ratio * track.width())

    def _x_to_value(self, x_pos: int) -> int:
        track = self._track_rect()
        if self.maximum <= self.minimum or track.width() <= 0:
            return self.minimum
        ratio = (x_pos - track.left()) / track.width()
        ratio = min(max(ratio, 0.0), 1.0)
        return int(round(self.minimum + ratio * (self.maximum - self.minimum)))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = self._track_rect()
        center_y = track.center().y()
        painter.setPen(QPen(QColor("#cbd5e1"), 4, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(track.left(), center_y, track.right(), center_y)

        lower_x = self._value_to_x(self.lower_value)
        upper_x = self._value_to_x(self.upper_value)
        painter.setPen(QPen(QColor("#2563eb"), 5, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(lower_x, center_y, upper_x, center_y)

        for x_pos in (lower_x, upper_x):
            painter.setBrush(QColor("#ffffff"))
            painter.setPen(QPen(QColor("#1d4ed8"), 2))
            painter.drawEllipse(x_pos - 7, center_y - 7, 14, 14)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        lower_x = self._value_to_x(self.lower_value)
        upper_x = self._value_to_x(self.upper_value)
        self._active_handle = "lower" if abs(event.x() - lower_x) <= abs(event.x() - upper_x) else "upper"
        self._move_active_handle(event.x())

    def mouseMoveEvent(self, event) -> None:
        if self._active_handle:
            self._move_active_handle(event.x())

    def mouseReleaseEvent(self, event) -> None:
        self._active_handle = None

    def _move_active_handle(self, x_pos: int) -> None:
        value = self._x_to_value(x_pos)
        if self._active_handle == "lower":
            self.setValues(min(value, self.upper_value), self.upper_value)
        elif self._active_handle == "upper":
            self.setValues(self.lower_value, max(value, self.lower_value))


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
SCATTER_BACKGROUND_GRID = 60
SCATTER_BACKGROUND_MIN_BANDWIDTH = 0.18


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


def unique_destination_path(target_dir: Path, source_name: str) -> Path:
    destination = target_dir / source_name
    if destination.exists():
        source_path = Path(source_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        destination = target_dir / f"{source_path.stem}_{timestamp}{source_path.suffix}"
    return destination


def reference_pattern_base_directory(patterns: list[str]) -> Path:
    raw_pattern = next((pattern.strip() for pattern in patterns if pattern.strip()), "")
    if not raw_pattern:
        raise ValueError("Reference destination pattern is empty.")

    raw_path = Path(raw_pattern).expanduser()
    parts = list(raw_path.parts)
    static_parts: list[str] = []
    for part in parts:
        if any(char in part for char in "*?[]"):
            break
        static_parts.append(part)

    if static_parts:
        base_path = Path(*static_parts)
        if len(static_parts) == len(parts) and raw_path.suffix:
            base_path = raw_path.parent
    else:
        base_path = raw_path.parent if raw_path.suffix else raw_path

    if not base_path.is_absolute():
        base_path = workspace_root() / base_path
    return base_path.resolve()


def join_patterns(patterns: list[str]) -> str:
    return "; ".join(patterns)


def split_patterns(raw_text: str) -> list[str]:
    parts = [part.strip() for chunk in raw_text.splitlines() for part in chunk.split(";")]
    return [part for part in parts if part]


def parse_float_list(raw_text: str) -> list[float]:
    normalized = raw_text.replace("，", ",").replace(";", ",")
    return [float(part.strip()) for part in normalized.split(",") if part.strip()]


def parse_capture_time_from_name(file_name: str) -> datetime | None:
    match = re.search(r"(\d{8})[_-](\d{6})[_-](\d{3,6})", Path(file_name).stem)
    if match:
        date_text, time_text, fraction_text = match.groups()
        microseconds = int(fraction_text.ljust(6, "0")[:6])
        parsed = datetime.strptime(date_text + time_text, "%Y%m%d%H%M%S")
        return parsed.replace(microsecond=microseconds)
    return None


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
class SignalGeneratorSettings:
    resource_name: str = ""
    timeout_ms: int = 3000
    channel: int = 1
    frequency_hz: float = 0.6e6
    amplitude_vpp: float = 5.0
    offset_v: float = 0.0
    load: str = "INF"
    prf_hz: float = 1.0
    burst_cycles: int = 6000


@dataclass
class PlaybackSettings:
    source_patterns: list[str] = field(default_factory=lambda: ["data/playback/*.csv"])
    interval_ms: int = 300
    loop_playback: bool = False


@dataclass
class ContrastSettings:
    source_patterns: list[str] = field(default_factory=lambda: ["data/playback/*.csv"])
    recursive: bool = False
    max_files_per_group: int = 0
    show_reference_background: bool = True
    last_browse_dir: str = ""


@dataclass
class ContrastSourceSpec:
    pattern: str
    start_index: int = 1
    end_index: int = 0
    group_name: str = ""


@dataclass
class ReferenceSettings:
    no_cavitation_patterns: list[str] = field(
        default_factory=lambda: ["data/reference/no_cavitation/*.csv"]
    )
    cavitation_patterns: list[str] = field(default_factory=lambda: ["data/reference/cavitation/*.csv"])


@dataclass
class AnalysisSettings:
    algorithm_id: str = "scd_icd_peak_v1"
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
class IudSettings:
    window_count: int = 75
    ultraharmonic_orders: list[float] = field(default_factory=lambda: [1.5, 2.5, 3.5])
    auc_half_width_hz: float = 25e3
    noise_half_width_hz: float = 75e3
    subtract_local_noise: bool = False
    baseline_window_count: int = 1
    instability_threshold_db: float = 8.0
    normal_reference_db: float = 4.0
    fixed_f0_hz: float = 0.0

    def validate(self, sample_count: int) -> None:
        if self.window_count < 2:
            raise ValueError("IUD window count must be at least 2.")
        if sample_count // self.window_count < 16:
            raise ValueError("IUD window count is too large for the configured sample count.")
        if not self.ultraharmonic_orders or any(order <= 0 for order in self.ultraharmonic_orders):
            raise ValueError("IUD ultraharmonic orders must contain positive values.")
        if self.auc_half_width_hz <= 0:
            raise ValueError("IUD AUC half-width must be positive.")
        if self.noise_half_width_hz <= self.auc_half_width_hz:
            raise ValueError("IUD noise half-width must be greater than the AUC half-width.")
        if not 1 <= self.baseline_window_count <= self.window_count:
            raise ValueError("IUD baseline window count must be between 1 and the total window count.")
        if self.fixed_f0_hz < 0:
            raise ValueError("IUD fixed f0 cannot be negative.")


@dataclass
class UiSettings:
    last_mode: str = "playback"
    max_live_points: int = 150
    show_reference_points: bool = True
    hardware_section_expanded: bool = False
    signal_generator_section_expanded: bool = False
    analysis_section_expanded: bool = False


@dataclass
class AppSettings:
    hardware: HardwareSettings = field(default_factory=HardwareSettings)
    signal_generator: SignalGeneratorSettings = field(default_factory=SignalGeneratorSettings)
    playback: PlaybackSettings = field(default_factory=PlaybackSettings)
    contrast: ContrastSettings = field(default_factory=ContrastSettings)
    reference: ReferenceSettings = field(default_factory=ReferenceSettings)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    iud: IudSettings = field(default_factory=IudSettings)
    ui: UiSettings = field(default_factory=UiSettings)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        return cls(
            hardware=HardwareSettings(**data.get("hardware", {})),
            signal_generator=SignalGeneratorSettings(**data.get("signal_generator", {})),
            playback=PlaybackSettings(**data.get("playback", {})),
            contrast=ContrastSettings(**data.get("contrast", {})),
            reference=ReferenceSettings(**data.get("reference", {})),
            analysis=AnalysisSettings.from_dict(data.get("analysis", {})),
            iud=IudSettings(**data.get("iud", {})),
            ui=UiSettings(**data.get("ui", {})),
        )


class SignalGeneratorError(RuntimeError):
    pass


class SignalGeneratorClient:
    def __init__(self) -> None:
        self._resource_manager = None
        self._instrument = None
        self.resource_name = ""
        self.identity = ""

    @property
    def connected(self) -> bool:
        return self._instrument is not None

    def _get_resource_manager(self):
        if self._resource_manager is not None:
            return self._resource_manager
        try:
            import pyvisa
        except ImportError as exc:
            raise SignalGeneratorError(
                "未安装 PyVISA。请先在当前环境安装 requirements.txt 中的 PyVISA。"
            ) from exc
        try:
            self._resource_manager = pyvisa.ResourceManager()
        except Exception as exc:
            raise SignalGeneratorError(
                "无法加载 VISA 运行库。请确认本机已安装 NI-VISA 或 RIGOL Ultra Sigma 的 VISA 组件。\n"
                f"原始信息：{exc}"
            ) from exc
        return self._resource_manager

    def list_resources(self) -> tuple[str, ...]:
        try:
            return tuple(str(item) for item in self._get_resource_manager().list_resources())
        except SignalGeneratorError:
            raise
        except Exception as exc:
            raise SignalGeneratorError(f"枚举 VISA 设备失败：{exc}") from exc

    def connect(self, resource_name: str, timeout_ms: int) -> str:
        resource_name = resource_name.strip()
        if not resource_name:
            raise SignalGeneratorError("请先选择或输入 VISA 资源地址。")
        self.disconnect()
        try:
            instrument = self._get_resource_manager().open_resource(resource_name)
            instrument.timeout = int(timeout_ms)
            instrument.read_termination = "\n"
            instrument.write_termination = "\n"
            identity = str(instrument.query("*IDN?")).strip()
        except Exception as exc:
            try:
                instrument.close()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            raise SignalGeneratorError(f"无法连接信号发生器或读取 *IDN?：{exc}") from exc
        self._instrument = instrument
        self.resource_name = resource_name
        self.identity = identity
        return identity

    def disconnect(self) -> None:
        instrument, self._instrument = self._instrument, None
        self.resource_name = ""
        self.identity = ""
        if instrument is not None:
            try:
                instrument.close()
            except Exception:
                pass

    def close(self) -> None:
        self.disconnect()
        manager, self._resource_manager = self._resource_manager, None
        if manager is not None:
            try:
                manager.close()
            except Exception:
                pass

    def _require_instrument(self):
        if self._instrument is None:
            raise SignalGeneratorError("尚未连接信号发生器。")
        return self._instrument

    def write(self, command: str) -> None:
        command = command.strip()
        if not command:
            raise SignalGeneratorError("SCPI 命令不能为空。")
        try:
            self._require_instrument().write(command)
        except SignalGeneratorError:
            raise
        except Exception as exc:
            raise SignalGeneratorError(f"发送命令失败：{command}\n原始信息：{exc}") from exc

    def query(self, command: str) -> str:
        command = command.strip()
        if not command:
            raise SignalGeneratorError("SCPI 查询不能为空。")
        try:
            return str(self._require_instrument().query(command)).strip()
        except SignalGeneratorError:
            raise
        except Exception as exc:
            raise SignalGeneratorError(f"查询失败：{command}\n原始信息：{exc}") from exc

    def set_output(self, channel: int, enabled: bool) -> None:
        if channel == 1:
            self.write("OUTP ON" if enabled else "OUTP OFF")
        elif channel == 2:
            self.write("OUTP:CH2 ON" if enabled else "OUTP:CH2 OFF")
        else:
            raise SignalGeneratorError("通道只能是 CH1 或 CH2。")

    def configure_burst_sine(
        self,
        channel: int,
        frequency_hz: float,
        amplitude_vpp: float,
        offset_v: float,
        load: str,
        prf_hz: float,
        burst_cycles: int,
    ) -> None:
        if channel not in (1, 2):
            raise SignalGeneratorError("通道只能是 CH1 或 CH2。")
        if frequency_hz <= 0:
            raise SignalGeneratorError("频率必须大于 0。")
        if amplitude_vpp <= 0:
            raise SignalGeneratorError("Vpp 必须大于 0。")
        if prf_hz <= 0:
            raise SignalGeneratorError("PRF 必须大于 0。")
        if burst_cycles < 1:
            raise SignalGeneratorError("Burst cycles 必须至少为 1。")
        if load not in ("INF", "50"):
            raise SignalGeneratorError("负载只能选择 High-Z 或 50 Ω。")

        if channel == 1:
            self.write(f"OUTP:LOAD {load}")
            self.write(f"APPL:SIN {frequency_hz:.12g},{amplitude_vpp:.12g},{offset_v:.12g}")
        else:
            self.write(f"OUTP:LOAD:CH2 {load}")
            self.write(f"APPL:SIN:CH2 {frequency_hz:.12g},{amplitude_vpp:.12g},{offset_v:.12g}")

        source = f"SOURce{channel}"
        period_s = 1.0 / prf_hz
        for command in (
            f"{source}:BURSt:MODE TRIGgered",
            f"{source}:BURSt:NCYCles {int(burst_cycles)}",
            f"{source}:BURSt:INTernal:PERiod {period_s:.12g}",
            f"{source}:BURSt:TRIGger:SOURce INTernal",
            f"{source}:BURSt:STATe ON",
        ):
            self.write(command)

    def query_error(self) -> str:
        return self.query("SYST:ERR?")


SCPI_HINT_TEXT = """常用 SCPI 指令速查

查询：
*IDN?                 查询仪器身份
APPL?                 查询 CH1 波形/频率/Vpp/偏置
OUTP?                 查询 CH1 输出状态
OUTP:CH2?             查询 CH2 输出状态
SYST:ERR?             查询最近一次仪器错误

CH1 正弦与输出：
APPL:SIN 600000,5,0   CH1 正弦，0.6 MHz，5 Vpp，0 V offset
OUTP ON               打开 CH1 输出
OUTP OFF              关闭 CH1 输出

CH2 正弦与输出：
APPL:SIN:CH2 600000,5,0
OUTP:CH2 ON
OUTP:CH2 OFF

Burst（N-cycle，内部 PRF）：
SOURce1:BURSt:MODE TRIGgered
SOURce1:BURSt:NCYCles 6000
SOURce1:BURSt:INTernal:PERiod 1
SOURce1:BURSt:TRIGger:SOURce INTernal
SOURce1:BURSt:STATe ON
"""


class ScpiConsoleDialog(QDialog):
    def __init__(self, client: SignalGeneratorClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("SCPI 控制台")
        self.resize(900, 520)

        root_layout = QHBoxLayout(self)
        console_panel = QWidget()
        console_layout = QVBoxLayout(console_panel)
        console_layout.setContentsMargins(0, 0, 0, 0)

        self.history_output = QPlainTextEdit()
        self.history_output.setReadOnly(True)
        self.command_edit = QLineEdit()
        self.command_edit.setPlaceholderText("输入 SCPI，例如 *IDN? 或 APPL:SIN 600000,5,0")
        self.command_edit.returnPressed.connect(self._query_or_write)

        button_row = QHBoxLayout()
        self.query_button = QPushButton("查询 Query")
        self.write_button = QPushButton("发送 Write")
        self.error_button = QPushButton("查错误 SYST:ERR?")
        for button in (self.query_button, self.write_button, self.error_button):
            button.setAutoDefault(False)
            button.setDefault(False)
        self.query_button.clicked.connect(self._query)
        self.write_button.clicked.connect(self._write)
        self.error_button.clicked.connect(self._query_error)
        button_row.addWidget(self.query_button)
        button_row.addWidget(self.write_button)
        button_row.addWidget(self.error_button)

        console_layout.addWidget(self.history_output, stretch=1)
        console_layout.addWidget(self.command_edit)
        console_layout.addLayout(button_row)

        hint_output = QPlainTextEdit()
        hint_output.setReadOnly(True)
        hint_output.setPlainText(SCPI_HINT_TEXT)
        hint_output.setMinimumWidth(330)

        root_layout.addWidget(console_panel, stretch=3)
        root_layout.addWidget(hint_output, stretch=2)

    def _append(self, text: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.history_output.appendPlainText(f"[{timestamp}] {text}")

    def _query_or_write(self) -> None:
        command = self.command_edit.text().strip()
        if command.endswith("?"):
            self._query()
        else:
            self._write()

    def _query(self) -> None:
        command = self.command_edit.text().strip()
        try:
            response = self.client.query(command)
            self._append(f"> {command}\n< {response}")
        except Exception as exc:
            self._append(f"ERROR: {exc}")

    def _write(self) -> None:
        command = self.command_edit.text().strip()
        try:
            self.client.write(command)
            self._append(f"> {command}\nOK")
        except Exception as exc:
            self._append(f"ERROR: {exc}")

    def _query_error(self) -> None:
        self.command_edit.setText("SYST:ERR?")
        self._query()


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
class IudMetrics:
    file: str = ""
    relative_path: str = ""
    group_name: str = ""
    spectrum_mode: str = "power"
    frequency_hz: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    spectrum: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    f0_hz: float = math.nan
    window_indices: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=int))
    iud_db: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    window_energy: np.ndarray = field(default_factory=lambda: np.asarray([], dtype=float))
    ultraharmonic_auc: np.ndarray = field(default_factory=lambda: np.empty((0, 0), dtype=float))
    ultraharmonic_orders: list[float] = field(default_factory=list)
    max_iud_db: float = math.nan
    max_window_index: int = 0
    first_crossing_window: int = 0
    unstable: bool = False
    segment_sample_count: int = 0
    window_duration_us: float = math.nan
    frequency_resolution_hz: float = math.nan
    conclusion: str = ""


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
    metrics: PcdMetrics | IudMetrics
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
class ContrastPoint:
    metrics: PcdMetrics
    group_name: str
    file_name: str
    relative_path: str
    sequence_index: int


@dataclass
class ContrastProgress:
    current: int = 0
    total: int = 0
    current_group: str = ""
    current_file: str = ""
    skipped: int = 0


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


def parse_contrast_index(raw_text: str, default: int | None, allow_open_end: bool = False) -> int | None:
    text = raw_text.strip()
    if not text:
        return default
    if allow_open_end and text.lower() in {"不限", "全部", "all", "end", "last", "*"}:
        return None
    value = int(text)
    if value < 1:
        raise ValueError("Contrast point indices are 1-based and must be positive.")
    return value


def parse_contrast_source_spec(raw_line: str) -> ContrastSourceSpec:
    group_name = ""
    line = raw_line.strip()
    if "||" in line:
        group_name, line = [part.strip() for part in line.split("||", 1)]

    parts = [part.strip() for part in line.split("|")]
    pattern = parts[0] if parts else ""
    if not pattern:
        raise ValueError("Contrast source path is empty.")

    start_index = 1
    end_index = 0
    if len(parts) >= 3:
        start_index = parse_contrast_index(parts[1], default=1) or 1
        parsed_end = parse_contrast_index(parts[2], default=None, allow_open_end=True)
        end_index = int(parsed_end) if parsed_end is not None else 0
    elif len(parts) == 2 and parts[1]:
        range_text = parts[1].replace("：", ":").replace("～", "-").replace("—", "-").replace("–", "-")
        separator = next((candidate for candidate in ("-", ":", "~") if candidate in range_text), "")
        if separator:
            start_text, end_text = range_text.split(separator, 1)
            start_index = parse_contrast_index(start_text, default=1) or 1
            parsed_end = parse_contrast_index(end_text, default=None, allow_open_end=True)
            end_index = int(parsed_end) if parsed_end is not None else 0
        else:
            start_index = parse_contrast_index(range_text, default=1) or 1

    if end_index and end_index < start_index:
        raise ValueError(f"Contrast range end ({end_index}) is smaller than start ({start_index}): {raw_line}")
    return ContrastSourceSpec(pattern=pattern, start_index=start_index, end_index=end_index, group_name=group_name)


def format_contrast_source_spec(spec: ContrastSourceSpec) -> str:
    end_text = str(spec.end_index) if spec.end_index > 0 else ""
    prefix = f"{spec.group_name.strip()} || " if spec.group_name.strip() else ""
    return f"{prefix}{spec.pattern.strip()} | {max(1, spec.start_index)} | {end_text}"


def find_contrast_candidate_files(pattern: str, recursive: bool = False) -> tuple[str, list[Path]]:
    resolved = resolve_workspace_path(pattern)
    if resolved.exists() and resolved.is_dir():
        group_name = resolved.name or str(resolved)
        found = sorted(resolved.rglob("*.csv") if recursive else resolved.glob("*.csv"))
    elif resolved.exists() and resolved.is_file():
        group_name = resolved.parent.name or resolved.stem
        found = [resolved]
    else:
        found = [Path(match).resolve() for match in sorted(glob.glob(str(resolved), recursive=recursive))]
        static_base = reference_pattern_base_directory([pattern])
        group_name = static_base.name or pattern

    deduplicated: list[Path] = []
    seen: set[str] = set()
    for file_path in found:
        if not file_path.exists() or not file_path.is_file() or file_path.suffix.lower() != ".csv":
            continue
        key = str(file_path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(file_path.resolve())
    return group_name, deduplicated


def resolve_contrast_sources(
    patterns: list[str],
    recursive: bool = False,
    max_files_per_group: int = 0,
) -> list[tuple[str, list[Path]]]:
    groups: list[tuple[str, list[Path]]] = []
    used_names: dict[str, int] = {}

    for raw_pattern in patterns:
        raw_pattern = raw_pattern.strip()
        if not raw_pattern:
            continue
        spec = parse_contrast_source_spec(raw_pattern)
        default_group_name, deduplicated = find_contrast_candidate_files(spec.pattern, recursive=recursive)

        start_offset = spec.start_index - 1
        end_offset = spec.end_index if spec.end_index > 0 else None
        selected = deduplicated[start_offset:end_offset]
        if max_files_per_group > 0:
            selected = selected[:max_files_per_group]

        if not selected:
            continue

        base_name = spec.group_name.strip() or default_group_name.strip() or f"Group {len(groups) + 1}"
        count = used_names.get(base_name, 0)
        used_names[base_name] = count + 1
        display_name = base_name if count == 0 else f"{base_name} #{count + 1}"
        groups.append((display_name, selected))

    return groups


def relative_to_workspace(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.resolve().relative_to(workspace_root()).as_posix()
    except ValueError:
        return str(path.resolve())


def try_load_standard_capture_csv(file_path: Path, target_sample_count: int) -> tuple[np.ndarray, float | None] | None:
    try:
        with file_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            header = csv_file.readline().strip().lower().replace(" ", "")
    except UnicodeDecodeError:
        return None

    if header != "sample_index,raw_code,voltage_mv,sample_rate_hz":
        return None

    try:
        loaded = np.loadtxt(
            file_path,
            delimiter=",",
            skiprows=1,
            usecols=(2, 3),
            max_rows=target_sample_count,
            dtype=float,
            encoding="utf-8-sig",
        )
    except Exception:
        return None

    loaded = np.asarray(loaded, dtype=float)
    if loaded.ndim == 1:
        loaded = loaded.reshape(1, -1)
    if loaded.shape[0] < target_sample_count:
        raise ValueError(f"Signal is shorter than {target_sample_count} points: {file_path}")
    signal = loaded[:target_sample_count, 0]
    signal = signal[np.isfinite(signal)]
    if signal.size < target_sample_count:
        raise ValueError(f"Signal contains non-finite samples before {target_sample_count} points: {file_path}")
    sample_rate_hz = float(loaded[0, 1]) if loaded.shape[1] > 1 and math.isfinite(float(loaded[0, 1])) else None
    return signal, sample_rate_hz


def load_signal_csv(file_path: Path, target_sample_count: int) -> tuple[np.ndarray, float | None]:
    fast_result = try_load_standard_capture_csv(file_path, target_sample_count)
    if fast_result is not None:
        return fast_result

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


def analyze_iud_signal(
    signal: np.ndarray,
    sample_rate_hz: float,
    analysis_settings: AnalysisSettings,
    iud_settings: IudSettings,
    file_name: str = "",
    relative_path: str = "",
    group_name: str = "Live",
) -> IudMetrics:
    target_signal = np.asarray(signal, dtype=float).reshape(-1)[: analysis_settings.target_sample_count]
    if target_signal.size < analysis_settings.target_sample_count:
        raise ValueError("Signal does not contain enough samples for IUD analysis.")
    iud_settings.validate(target_signal.size)

    if iud_settings.fixed_f0_hz > 0:
        f0_hz = float(iud_settings.fixed_f0_hz)
    else:
        full_frequency_hz, full_spectrum = compute_single_spectrum(
            target_signal, sample_rate_hz, analysis_settings
        )
        f0_hz = estimate_center_frequency(full_frequency_hz, full_spectrum, analysis_settings)

    window_count = int(iud_settings.window_count)
    segment_sample_count = target_signal.size // window_count
    usable_sample_count = segment_sample_count * window_count
    segmented_signal = target_signal[:usable_sample_count].reshape(window_count, segment_sample_count)

    ultraharmonic_orders = [float(order) for order in iud_settings.ultraharmonic_orders]
    auc_matrix = np.zeros((window_count, len(ultraharmonic_orders)), dtype=float)
    spectra: list[np.ndarray] = []
    frequency_hz = np.asarray([], dtype=float)

    for window_index in range(window_count):
        frequency_hz, display_spectrum = compute_single_spectrum(
            segmented_signal[window_index], sample_rate_hz, analysis_settings
        )
        spectra.append(display_spectrum)
        power_spectrum = display_spectrum if analysis_settings.spectrum_mode.lower() == "power" else display_spectrum**2
        bin_width_hz = sample_rate_hz / segment_sample_count

        for order_index, order in enumerate(ultraharmonic_orders):
            center_hz = order * f0_hz
            auc_mask = (frequency_hz >= center_hz - iud_settings.auc_half_width_hz) & (
                frequency_hz <= center_hz + iud_settings.auc_half_width_hz
            )
            if not np.any(auc_mask):
                nearest_index = int(np.argmin(np.abs(frequency_hz - center_hz)))
                auc_mask = np.zeros_like(frequency_hz, dtype=bool)
                auc_mask[nearest_index] = True

            selected_power = np.asarray(power_spectrum[auc_mask], dtype=float)
            if iud_settings.subtract_local_noise:
                noise_mask = (
                    (frequency_hz >= center_hz - iud_settings.noise_half_width_hz)
                    & (frequency_hz <= center_hz + iud_settings.noise_half_width_hz)
                    & (~auc_mask)
                )
                local_floor = float(np.mean(power_spectrum[noise_mask])) if np.any(noise_mask) else 0.0
                selected_power = np.maximum(selected_power - local_floor, 0.0)
            auc_matrix[window_index, order_index] = float(np.sum(selected_power) * bin_width_hz)

    window_energy = np.sum(auc_matrix, axis=1)
    baseline_count = int(iud_settings.baseline_window_count)
    baseline_energy = float(np.mean(window_energy[:baseline_count]))
    energy_floor = max(baseline_energy * 1e-12, FLOAT_EPS)
    iud_db = 10.0 * np.log10(np.maximum(window_energy, energy_floor) / max(baseline_energy, energy_floor))
    iud_db = np.asarray(iud_db, dtype=float)
    max_position = int(np.argmax(iud_db))
    crossing_positions = np.flatnonzero(iud_db >= iud_settings.instability_threshold_db)
    first_crossing_window = int(crossing_positions[0] + 1) if crossing_positions.size else 0
    unstable = bool(crossing_positions.size)
    max_iud_db = float(iud_db[max_position])
    conclusion = (
        f"检测到窗内失稳，首次越阈值位于第 {first_crossing_window} 窗"
        if unstable
        else "未检测到窗内失稳"
    )

    return IudMetrics(
        file=file_name,
        relative_path=relative_path,
        group_name=group_name,
        spectrum_mode=analysis_settings.spectrum_mode,
        frequency_hz=frequency_hz,
        spectrum=np.asarray(spectra[max_position], dtype=float),
        f0_hz=f0_hz,
        window_indices=np.arange(1, window_count + 1, dtype=int),
        iud_db=iud_db,
        window_energy=window_energy,
        ultraharmonic_auc=auc_matrix,
        ultraharmonic_orders=ultraharmonic_orders,
        max_iud_db=max_iud_db,
        max_window_index=max_position + 1,
        first_crossing_window=first_crossing_window,
        unstable=unstable,
        segment_sample_count=segment_sample_count,
        window_duration_us=(segment_sample_count / sample_rate_hz) * 1e6,
        frequency_resolution_hz=sample_rate_hz / segment_sample_count,
        conclusion=conclusion,
    )


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


def build_full_waveform(signal: np.ndarray, sample_rate_hz: float) -> tuple[np.ndarray, np.ndarray]:
    signal_values = np.asarray(signal, dtype=float).reshape(-1)
    if signal_values.size < 2 or not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)
    centered = signal_values - float(np.mean(signal_values))
    time_us = np.arange(centered.size, dtype=float) / sample_rate_hz * 1e6
    return time_us, centered


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
    def __init__(self, title: str = "Segment-Averaged Spectrum") -> None:
        super().__init__()
        self.title = title
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
        painter.drawText(chart_rect.left(), 18, self.title)

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
    def __init__(self, title: str | None = None) -> None:
        super().__init__()
        self.title = title or f"Center Waveform (~{WAVEFORM_PREVIEW_CYCLES:.1f} cycles)"
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
        painter.drawText(chart_rect.left(), 18, self.title)

        if self.time_us.size == 0 or self.waveform_mv.size == 0:
            painter.drawText(chart_rect.center().x() - 48, chart_rect.center().y(), "No waveform yet")
            painter.drawText(chart_rect.center().x() - 32, self.height() - 14, "Time (us)")
            painter.drawText(12, 36, "Amplitude")
            return

        x_min = float(np.min(self.time_us))
        x_max = float(np.max(self.time_us))
        if abs(x_max - x_min) < 1e-9:
            x_max = x_min + 1.0

        signal_y_min = float(np.min(self.waveform_mv))
        signal_y_max = float(np.max(self.waveform_mv))
        if abs(signal_y_max - signal_y_min) < 1e-9:
            signal_y_min -= 1.0
            signal_y_max += 1.0
        vpp_mv = signal_y_max - signal_y_min
        y_min = signal_y_min
        y_max = signal_y_max
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

        _, top_y = to_pixel(x_min, signal_y_max)
        _, bottom_y = to_pixel(x_min, signal_y_min)
        painter.drawText(10, int(top_y) + 5, f"{signal_y_max:.1f}")
        if signal_y_min <= 0.0 <= signal_y_max:
            _, zero_y = to_pixel(x_min, 0.0)
            painter.drawText(18, int(zero_y) + 5, "0")
        painter.drawText(10, int(bottom_y) + 5, f"{signal_y_min:.1f}")

        painter.drawText(chart_rect.center().x() - 32, self.height() - 14, "Time (us)")
        painter.drawText(12, 36, "Amplitude (mV)")

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
        painter.drawText(chart_rect.right() - 148, chart_rect.top() + 36, f"Vpp = {vpp_mv:.1f} mV")


class IudCurveWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.window_indices = np.asarray([], dtype=float)
        self.iud_db = np.asarray([], dtype=float)
        self.normal_reference_db = 4.0
        self.instability_threshold_db = 8.0
        self.setMinimumHeight(260)
        self.setStyleSheet("background: white;")

    def clear_data(self) -> None:
        self.window_indices = np.asarray([], dtype=float)
        self.iud_db = np.asarray([], dtype=float)
        self.update()

    def set_curve(
        self,
        window_indices: np.ndarray,
        iud_db: np.ndarray,
        normal_reference_db: float,
        instability_threshold_db: float,
    ) -> None:
        self.window_indices = np.asarray(window_indices, dtype=float)
        self.iud_db = np.asarray(iud_db, dtype=float)
        self.normal_reference_db = float(normal_reference_db)
        self.instability_threshold_db = float(instability_threshold_db)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        chart_rect = self.rect().adjusted(72, 30, -28, -48)
        painter.setPen(QPen(QColor("#d0d7de"), 1))
        painter.drawRect(chart_rect)
        painter.setPen(QPen(QColor("#111827"), 1))
        painter.drawText(chart_rect.left(), 18, "Intrapulse IUD Curve")

        if self.window_indices.size == 0 or self.iud_db.size == 0:
            painter.drawText(chart_rect.center().x() - 42, chart_rect.center().y(), "No IUD data yet")
            painter.drawText(chart_rect.center().x() - 34, self.height() - 14, "Window k")
            painter.drawText(12, 36, "IUD (dB)")
            return

        x_min = 1.0
        x_max = max(float(np.max(self.window_indices)), 2.0)
        y_min = min(-2.0, float(np.floor(np.min(self.iud_db) / 2.0) * 2.0))
        y_max = max(
            self.instability_threshold_db + 2.0,
            float(np.ceil(np.max(self.iud_db) / 2.0) * 2.0),
        )
        if y_max - y_min < 4.0:
            y_max = y_min + 4.0

        def to_pixel(x_value: float, y_value: float) -> tuple[float, float]:
            x_ratio = (x_value - x_min) / (x_max - x_min)
            y_ratio = (y_value - y_min) / (y_max - y_min)
            return (
                chart_rect.left() + x_ratio * chart_rect.width(),
                chart_rect.bottom() - y_ratio * chart_rect.height(),
            )

        painter.setPen(QPen(QColor("#e5e7eb"), 1, Qt.DashLine))
        for tick_index in range(6):
            ratio = tick_index / 5
            x_pixel = chart_rect.left() + ratio * chart_rect.width()
            y_pixel = chart_rect.bottom() - ratio * chart_rect.height()
            painter.drawLine(int(x_pixel), chart_rect.top(), int(x_pixel), chart_rect.bottom())
            painter.drawLine(chart_rect.left(), int(y_pixel), chart_rect.right(), int(y_pixel))

        for value, color, label in (
            (self.normal_reference_db, QColor("#d97706"), "normal reference"),
            (self.instability_threshold_db, QColor("#dc2626"), "instability threshold"),
        ):
            if y_min <= value <= y_max:
                _, y_pixel = to_pixel(x_min, value)
                painter.setPen(QPen(color, 1, Qt.DashLine))
                painter.drawLine(chart_rect.left(), int(y_pixel), chart_rect.right(), int(y_pixel))
                painter.drawText(chart_rect.right() - 150, int(y_pixel) - 4, f"{label}: {value:.1f} dB")

        painter.setPen(QPen(QColor("#111827"), 1))
        for tick_index in range(6):
            ratio = tick_index / 5
            x_value = x_min + ratio * (x_max - x_min)
            y_value = y_min + ratio * (y_max - y_min)
            x_pixel = chart_rect.left() + ratio * chart_rect.width()
            y_pixel = chart_rect.bottom() - ratio * chart_rect.height()
            painter.drawText(int(x_pixel) - 10, chart_rect.bottom() + 20, f"{x_value:.0f}")
            painter.drawText(12, int(y_pixel) + 5, f"{y_value:.1f}")
        painter.drawText(chart_rect.center().x() - 34, self.height() - 14, "Window k")
        painter.drawText(12, 36, "IUD (dB)")

        painter.setPen(QPen(QColor("#2563eb"), 2))
        for index in range(1, self.window_indices.size):
            x1, y1 = to_pixel(float(self.window_indices[index - 1]), float(self.iud_db[index - 1]))
            x2, y2 = to_pixel(float(self.window_indices[index]), float(self.iud_db[index]))
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        for x_value, y_value in zip(self.window_indices, self.iud_db):
            x_pixel, y_pixel = to_pixel(float(x_value), float(y_value))
            color = QColor("#dc2626") if y_value >= self.instability_threshold_db else QColor("#2563eb")
            painter.setPen(QPen(color, 1))
            painter.setBrush(color)
            painter.drawEllipse(int(x_pixel) - 3, int(y_pixel) - 3, 6, 6)


class IudTreatmentTrendWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.elapsed_seconds = np.asarray([], dtype=float)
        self.mean_iud_db = np.asarray([], dtype=float)
        self.file_names: list[str] = []
        self.current_index = -1
        self._hardware_start_time: datetime | None = None
        self._playback_points: dict[str, tuple[datetime | None, float, int]] = {}
        self.setMinimumHeight(240)
        self.setStyleSheet("background: white;")

    def clear_data(self) -> None:
        self.elapsed_seconds = np.asarray([], dtype=float)
        self.mean_iud_db = np.asarray([], dtype=float)
        self.file_names = []
        self.current_index = -1
        self._hardware_start_time = None
        self._playback_points = {}
        self.update()

    def add_playback_point(
        self,
        file_name: str,
        mean_iud_db: float,
        fallback_position: int,
    ) -> None:
        capture_time = parse_capture_time_from_name(file_name)
        self._playback_points[file_name] = (
            capture_time,
            float(mean_iud_db),
            max(1, int(fallback_position)),
        )
        self._hardware_start_time = None
        valid_times = [item[0] for item in self._playback_points.values() if item[0] is not None]
        origin = min(valid_times) if valid_times else None
        records: list[tuple[float, int, str, float]] = []
        for name, (timestamp, value, position) in self._playback_points.items():
            elapsed = (
                max(0.0, (timestamp - origin).total_seconds())
                if timestamp is not None and origin is not None
                else float(position - 1)
            )
            records.append((elapsed, position, name, value))
        records.sort(key=lambda item: (item[0], item[1], item[2]))
        self.elapsed_seconds = np.asarray([item[0] for item in records], dtype=float)
        self.mean_iud_db = np.asarray([item[3] for item in records], dtype=float)
        self.file_names = [item[2] for item in records]
        self.set_current_file(file_name)

    def set_current_file(self, file_name: str) -> None:
        try:
            self.current_index = self.file_names.index(Path(file_name).name)
        except ValueError:
            self.current_index = -1
        self.update()

    def add_hardware_point(
        self,
        captured_at: datetime,
        mean_iud_db: float,
        source_label: str,
        max_points: int,
    ) -> None:
        if self._hardware_start_time is None:
            self._hardware_start_time = captured_at
        elapsed = max(0.0, (captured_at - self._hardware_start_time).total_seconds())
        self.elapsed_seconds = np.append(self.elapsed_seconds, elapsed)
        self.mean_iud_db = np.append(self.mean_iud_db, float(mean_iud_db))
        self.file_names.append(source_label)
        limit = max(1, int(max_points))
        if self.mean_iud_db.size > limit:
            remove_count = self.mean_iud_db.size - limit
            self.elapsed_seconds = self.elapsed_seconds[remove_count:]
            self.mean_iud_db = self.mean_iud_db[remove_count:]
            self.file_names = self.file_names[remove_count:]
        self.current_index = self.mean_iud_db.size - 1
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        chart_rect = self.rect().adjusted(72, 30, -28, -48)
        painter.setPen(QPen(QColor("#d0d7de"), 1))
        painter.drawRect(chart_rect)
        painter.setPen(QPen(QColor("#111827"), 1))
        painter.drawText(chart_rect.left(), 18, "Treatment-Cycle Mean IUD")

        if self.elapsed_seconds.size == 0 or self.mean_iud_db.size == 0:
            painter.drawText(chart_rect.center().x() - 58, chart_rect.center().y(), "No treatment trend yet")
            painter.drawText(chart_rect.center().x() - 42, self.height() - 14, "Elapsed time (s)")
            painter.drawText(12, 36, "Mean IUD (dB)")
            return

        x_min = float(np.min(self.elapsed_seconds))
        x_max = float(np.max(self.elapsed_seconds))
        if x_max - x_min < 1e-9:
            x_max = x_min + 1.0
        y_min_data = float(np.min(self.mean_iud_db))
        y_max_data = float(np.max(self.mean_iud_db))
        y_pad = max(1.0, (y_max_data - y_min_data) * 0.15)
        y_min = y_min_data - y_pad
        y_max = y_max_data + y_pad

        def to_pixel(x_value: float, y_value: float) -> tuple[float, float]:
            x_ratio = (x_value - x_min) / (x_max - x_min)
            y_ratio = (y_value - y_min) / (y_max - y_min)
            return (
                chart_rect.left() + x_ratio * chart_rect.width(),
                chart_rect.bottom() - y_ratio * chart_rect.height(),
            )

        painter.setPen(QPen(QColor("#e5e7eb"), 1, Qt.DashLine))
        for tick_index in range(6):
            ratio = tick_index / 5
            x_pixel = chart_rect.left() + ratio * chart_rect.width()
            y_pixel = chart_rect.bottom() - ratio * chart_rect.height()
            painter.drawLine(int(x_pixel), chart_rect.top(), int(x_pixel), chart_rect.bottom())
            painter.drawLine(chart_rect.left(), int(y_pixel), chart_rect.right(), int(y_pixel))

        painter.setPen(QPen(QColor("#111827"), 1))
        for tick_index in range(6):
            ratio = tick_index / 5
            x_value = x_min + ratio * (x_max - x_min)
            y_value = y_min + ratio * (y_max - y_min)
            x_pixel = chart_rect.left() + ratio * chart_rect.width()
            y_pixel = chart_rect.bottom() - ratio * chart_rect.height()
            painter.drawText(int(x_pixel) - 14, chart_rect.bottom() + 20, f"{x_value:.1f}")
            painter.drawText(10, int(y_pixel) + 5, f"{y_value:.1f}")
        painter.drawText(chart_rect.center().x() - 42, self.height() - 14, "Elapsed time (s)")
        painter.drawText(12, 36, "Mean IUD (dB)")

        painter.setPen(QPen(QColor("#93c5fd"), 1))
        for index in range(1, self.mean_iud_db.size):
            x1, y1 = to_pixel(float(self.elapsed_seconds[index - 1]), float(self.mean_iud_db[index - 1]))
            x2, y2 = to_pixel(float(self.elapsed_seconds[index]), float(self.mean_iud_db[index]))
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        for index, (x_value, y_value) in enumerate(zip(self.elapsed_seconds, self.mean_iud_db)):
            x_pixel, y_pixel = to_pixel(float(x_value), float(y_value))
            painter.setPen(QPen(QColor("#2563eb"), 1))
            painter.setBrush(QColor("#2563eb"))
            painter.drawEllipse(int(x_pixel) - 3, int(y_pixel) - 3, 6, 6)
            if index == self.current_index:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor("#f59e0b"), 3))
                painter.drawEllipse(int(x_pixel) - 8, int(y_pixel) - 8, 16, 16)
                label = self.file_names[index] if index < len(self.file_names) else f"Point {index + 1}"
                painter.setPen(QPen(QColor("#111827"), 1))
                painter.drawText(chart_rect.left() + 8, chart_rect.top() + 18, label)


class PcdScatterWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.no_results: list[PcdMetrics] = []
        self.cav_results: list[PcdMetrics] = []
        self.live_results: list[PcdMetrics] = []
        self.show_reference_points = True
        self.setMinimumHeight(320)
        self.setStyleSheet("background: white;")

    def set_reference_results(self, no_results: list[PcdMetrics], cav_results: list[PcdMetrics]) -> None:
        self.no_results = no_results
        self.cav_results = cav_results
        self.update()

    def set_show_reference_points(self, enabled: bool) -> None:
        self.show_reference_points = bool(enabled)
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

        self._draw_reference_background(painter, chart_rect, x_min, x_max, y_min, y_max)

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
        painter.drawText(chart_rect.left(), 18, "Reference Field + Real-time Scatter")

        if self.show_reference_points:
            self._draw_result_group(painter, self.no_results, to_pixel, QColor("#1d4ed8"), 5, QColor("#eff6ff"))
            self._draw_result_group(painter, self.cav_results, to_pixel, QColor("#dc2626"), 5, QColor("#fff1f2"))
        self._draw_result_group(painter, self.live_results[:-1], to_pixel, QColor("#22c55e"), 7, QColor("#ecfccb"))

        if self.live_results:
            latest = self.live_results[-1]
            latest_x, latest_y = to_pixel(*latest.plot_coordinates())
            painter.setBrush(QColor("#facc15"))
            painter.setPen(QPen(QColor("#0f172a"), 2))
            painter.drawEllipse(int(latest_x) - 7, int(latest_y) - 7, 14, 14)
            painter.setBrush(QColor("#ffffff"))
            painter.setPen(QPen(Qt.NoPen))
            painter.drawEllipse(int(latest_x) - 2, int(latest_y) - 2, 4, 4)
            painter.setPen(QPen(QColor("#0f172a"), 1))
            painter.drawText(int(latest_x) + 10, int(latest_y) - 10, latest.file or "latest")

        self._draw_legend(painter, chart_rect)

    def _draw_reference_background(
        self,
        painter: QPainter,
        chart_rect,
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
    ) -> None:
        if not self.no_results or not self.cav_results:
            return

        no_points = np.asarray([metrics.plot_coordinates() for metrics in self.no_results], dtype=float)
        cav_points = np.asarray([metrics.plot_coordinates() for metrics in self.cav_results], dtype=float)
        if no_points.size == 0 or cav_points.size == 0:
            return

        cols = max(28, min(SCATTER_BACKGROUND_GRID, chart_rect.width()))
        rows = max(28, min(SCATTER_BACKGROUND_GRID, chart_rect.height()))
        x_centers = np.linspace(x_min, x_max, cols, dtype=float)
        y_centers = np.linspace(y_max, y_min, rows, dtype=float)
        grid_x, grid_y = np.meshgrid(x_centers, y_centers)

        bandwidth_x = max(SCATTER_BACKGROUND_MIN_BANDWIDTH, (x_max - x_min) * 0.16)
        bandwidth_y = max(SCATTER_BACKGROUND_MIN_BANDWIDTH, (y_max - y_min) * 0.16)
        no_density = self._compute_class_density(grid_x, grid_y, no_points, bandwidth_x, bandwidth_y)
        cav_density = self._compute_class_density(grid_x, grid_y, cav_points, bandwidth_x, bandwidth_y)
        total_density = no_density + cav_density
        peak_density = float(np.max(total_density))
        if peak_density <= FLOAT_EPS:
            return

        cav_probability = cav_density / np.maximum(total_density, FLOAT_EPS)
        density_strength = np.sqrt(np.clip(total_density / peak_density, 0.0, 1.0))

        x_edges = np.linspace(chart_rect.left(), chart_rect.left() + chart_rect.width(), cols + 1)
        y_edges = np.linspace(chart_rect.top(), chart_rect.top() + chart_rect.height(), rows + 1)
        low_color = np.asarray((55, 130, 246), dtype=float)
        high_color = np.asarray((239, 68, 68), dtype=float)

        for row in range(rows):
            top = int(math.floor(y_edges[row]))
            bottom = int(math.ceil(y_edges[row + 1]))
            for col in range(cols):
                left = int(math.floor(x_edges[col]))
                right = int(math.ceil(x_edges[col + 1]))
                probability = float(cav_probability[row, col])
                base_rgb = low_color * (1.0 - probability) + high_color * probability
                alpha = int(24 + 132 * float(density_strength[row, col]))
                painter.fillRect(
                    left,
                    top,
                    max(1, right - left),
                    max(1, bottom - top),
                    QColor(int(base_rgb[0]), int(base_rgb[1]), int(base_rgb[2]), alpha),
                )

    def _compute_class_density(
        self,
        grid_x: np.ndarray,
        grid_y: np.ndarray,
        points: np.ndarray,
        bandwidth_x: float,
        bandwidth_y: float,
    ) -> np.ndarray:
        dx = (grid_x[..., None] - points[:, 0]) / max(bandwidth_x, FLOAT_EPS)
        dy = (grid_y[..., None] - points[:, 1]) / max(bandwidth_y, FLOAT_EPS)
        kernel = np.exp(-0.5 * (dx * dx + dy * dy))
        return np.mean(kernel, axis=2)

    def _draw_result_group(
        self,
        painter: QPainter,
        results: list[PcdMetrics],
        mapper,
        color: QColor,
        radius: int,
        halo_color: QColor | None = None,
    ) -> None:
        for metrics in results:
            x_pixel, y_pixel = mapper(*metrics.plot_coordinates())
            if halo_color is not None:
                painter.setBrush(halo_color)
                painter.setPen(QPen(Qt.NoPen))
                halo_radius = radius + 4
                painter.drawEllipse(int(x_pixel) - halo_radius // 2, int(y_pixel) - halo_radius // 2, halo_radius, halo_radius)
            painter.setBrush(color)
            painter.setPen(QPen(QColor("#0f172a"), 1))
            painter.drawEllipse(int(x_pixel) - radius // 2, int(y_pixel) - radius // 2, radius, radius)

    def _draw_legend(self, painter: QPainter, chart_rect) -> None:
        legend_items = []
        if self.show_reference_points:
            legend_items.extend(
                [
                    (QColor("#1d4ed8"), "Reference points: no cavitation"),
                    (QColor("#dc2626"), "Reference points: cavitation"),
                ]
            )
        legend_items.extend(
            [
                (QColor("#22c55e"), "Live / playback history"),
                (QColor("#facc15"), "Latest live point"),
            ]
        )
        base_x = chart_rect.right() - 220
        base_y = chart_rect.top() + 10
        painter.setPen(QPen(QColor("#111827"), 1))
        for index, (color, text) in enumerate(legend_items):
            y = base_y + index * 18
            painter.setBrush(color)
            painter.setPen(QPen(Qt.NoPen))
            painter.drawEllipse(base_x, y, 8, 8)
            painter.setPen(QPen(QColor("#111827"), 1))
            painter.drawText(base_x + 14, y + 8, text)

        scale_top = base_y + len(legend_items) * 18 + 6
        painter.setPen(QPen(QColor("#cbd5e1"), 1))
        painter.drawRect(base_x, scale_top, 118, 10)
        for index in range(24):
            ratio = index / 23 if 23 else 0.0
            r = int(55 * (1.0 - ratio) + 239 * ratio)
            g = int(130 * (1.0 - ratio) + 68 * ratio)
            b = int(246 * (1.0 - ratio) + 68 * ratio)
            painter.fillRect(base_x + 1 + index * 5, scale_top + 1, 5, 9, QColor(r, g, b, 190))
        painter.setPen(QPen(QColor("#111827"), 1))
        painter.drawText(base_x, scale_top + 24, "Background: no cav")
        painter.drawText(base_x + 86, scale_top + 24, "cav")


class PcdContrastWidget(QWidget):
    PALETTE = [
        "#2563eb",
        "#dc2626",
        "#16a34a",
        "#9333ea",
        "#ea580c",
        "#0891b2",
        "#be123c",
        "#4f46e5",
        "#65a30d",
        "#a16207",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.no_results: list[PcdMetrics] = []
        self.cav_results: list[PcdMetrics] = []
        self.group_results: dict[str, list[PcdMetrics]] = {}
        self.show_reference_background = True
        self.setMinimumHeight(520)
        self.setStyleSheet("background: white;")

    def set_reference_results(self, no_results: list[PcdMetrics], cav_results: list[PcdMetrics]) -> None:
        self.no_results = no_results
        self.cav_results = cav_results
        self.update()

    def set_show_reference_background(self, enabled: bool) -> None:
        self.show_reference_background = bool(enabled)
        self.update()

    def clear_results(self) -> None:
        self.group_results = {}
        self.update()

    def add_point(self, point: ContrastPoint) -> None:
        self.group_results.setdefault(point.group_name, []).append(point.metrics)
        self.update()

    def summary_text(self) -> str:
        if not self.group_results:
            return "尚未生成对比结果。"
        parts = []
        total = 0
        for group_name, results in self.group_results.items():
            total += len(results)
            if results:
                scores = [result.cavitation_score for result in results if math.isfinite(result.cavitation_score)]
                score_text = f"，平均 Score {float(np.mean(scores)):.3f}" if scores else ""
                parts.append(f"{group_name}: {len(results)} 点{score_text}")
        return f"共 {len(self.group_results)} 组，{total} 点。 " + "； ".join(parts)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))

        chart_rect = self.rect().adjusted(78, 32, -210, -66)
        legend_rect = self.rect().adjusted(self.width() - 190, 36, -16, -24)
        painter.setPen(QPen(QColor("#d0d7de"), 1))
        painter.drawRect(chart_rect)

        grouped_points = [
            metrics.plot_coordinates()
            for results in self.group_results.values()
            for metrics in results
        ]
        points = list(grouped_points)
        if points:
            x_values = [point[0] for point in points]
            y_values = [point[1] for point in points]
            for results in self.group_results.values():
                if len(results) <= 1:
                    continue
                group_points = np.asarray([metrics.plot_coordinates() for metrics in results], dtype=float)
                center = np.mean(group_points, axis=0)
                spread = np.std(group_points, axis=0)
                x_values.extend([float(center[0] - spread[0]), float(center[0] + spread[0])])
                y_values.extend([float(center[1] - spread[1]), float(center[1] + spread[1])])
            x_min, x_max = min(x_values), max(x_values)
            y_min, y_max = min(y_values), max(y_values)
        else:
            x_min = y_min = -6.0
            x_max = y_max = 0.0

        minimum_span = 0.18 if grouped_points else 0.5
        if abs(x_max - x_min) < minimum_span:
            center = (x_min + x_max) / 2
            x_min = center - minimum_span / 2
            x_max = center + minimum_span / 2
        if abs(y_max - y_min) < minimum_span:
            center = (y_min + y_max) / 2
            y_min = center - minimum_span / 2
            y_max = center + minimum_span / 2

        x_pad = max(0.04 if grouped_points else 0.2, (x_max - x_min) * 0.12)
        y_pad = max(0.04 if grouped_points else 0.2, (y_max - y_min) * 0.12)
        x_min -= x_pad
        x_max += x_pad
        y_min -= y_pad
        y_max += y_pad

        def to_pixel(x_value: float, y_value: float) -> tuple[float, float]:
            x_ratio = (x_value - x_min) / max(x_max - x_min, FLOAT_EPS)
            y_ratio = (y_value - y_min) / max(y_max - y_min, FLOAT_EPS)
            return (
                chart_rect.left() + x_ratio * chart_rect.width(),
                chart_rect.bottom() - y_ratio * chart_rect.height(),
            )

        if self.show_reference_background:
            self._draw_reference_background(painter, chart_rect, x_min, x_max, y_min, y_max)

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
            painter.drawText(12, int(y_pixel) + 5, f"{y_value:.1f}")

        painter.drawText(chart_rect.center().x() - 88, self.height() - 22, "log10(SCDultra)")
        painter.drawText(12, 20, "log10(ICD)")
        painter.drawText(chart_rect.left(), 22, "Contrast Mode: grouped SCD–ICD scatter")

        for group_index, (group_name, results) in enumerate(self.group_results.items()):
            color = QColor(self.PALETTE[group_index % len(self.PALETTE)])
            self._draw_group(painter, group_name, results, to_pixel, color)

        self._draw_legend(painter, legend_rect)

        if not self.group_results:
            painter.setPen(QPen(QColor("#64748b"), 1))
            painter.drawText(chart_rect.adjusted(12, 16, -12, -16), Qt.AlignCenter, "进入 Contrast 对比模式，选择多个路径后点击开始。")

    def _draw_reference_background(
        self,
        painter: QPainter,
        chart_rect,
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
    ) -> None:
        if not self.no_results or not self.cav_results:
            return
        no_points = np.asarray([metrics.plot_coordinates() for metrics in self.no_results], dtype=float)
        cav_points = np.asarray([metrics.plot_coordinates() for metrics in self.cav_results], dtype=float)
        cols = max(24, min(52, chart_rect.width()))
        rows = max(24, min(52, chart_rect.height()))
        x_centers = np.linspace(x_min, x_max, cols, dtype=float)
        y_centers = np.linspace(y_max, y_min, rows, dtype=float)
        grid_x, grid_y = np.meshgrid(x_centers, y_centers)
        bandwidth_x = max(SCATTER_BACKGROUND_MIN_BANDWIDTH, (x_max - x_min) * 0.16)
        bandwidth_y = max(SCATTER_BACKGROUND_MIN_BANDWIDTH, (y_max - y_min) * 0.16)
        no_density = self._compute_class_density(grid_x, grid_y, no_points, bandwidth_x, bandwidth_y)
        cav_density = self._compute_class_density(grid_x, grid_y, cav_points, bandwidth_x, bandwidth_y)
        total_density = no_density + cav_density
        peak_density = float(np.max(total_density))
        if peak_density <= FLOAT_EPS:
            return
        cav_probability = cav_density / np.maximum(total_density, FLOAT_EPS)
        density_strength = np.sqrt(np.clip(total_density / peak_density, 0.0, 1.0))
        x_edges = np.linspace(chart_rect.left(), chart_rect.left() + chart_rect.width(), cols + 1)
        y_edges = np.linspace(chart_rect.top(), chart_rect.top() + chart_rect.height(), rows + 1)
        low_color = np.asarray((219, 234, 254), dtype=float)
        high_color = np.asarray((254, 226, 226), dtype=float)
        for row in range(rows):
            top = int(math.floor(y_edges[row]))
            bottom = int(math.ceil(y_edges[row + 1]))
            for col in range(cols):
                probability = float(cav_probability[row, col])
                base_rgb = low_color * (1.0 - probability) + high_color * probability
                alpha = int(18 + 96 * float(density_strength[row, col]))
                left = int(math.floor(x_edges[col]))
                right = int(math.ceil(x_edges[col + 1]))
                painter.fillRect(
                    left,
                    top,
                    max(1, right - left),
                    max(1, bottom - top),
                    QColor(int(base_rgb[0]), int(base_rgb[1]), int(base_rgb[2]), alpha),
                )

    def _compute_class_density(
        self,
        grid_x: np.ndarray,
        grid_y: np.ndarray,
        points: np.ndarray,
        bandwidth_x: float,
        bandwidth_y: float,
    ) -> np.ndarray:
        dx = (grid_x[..., None] - points[:, 0]) / max(bandwidth_x, FLOAT_EPS)
        dy = (grid_y[..., None] - points[:, 1]) / max(bandwidth_y, FLOAT_EPS)
        return np.mean(np.exp(-0.5 * (dx * dx + dy * dy)), axis=2)

    def _draw_group(self, painter: QPainter, group_name: str, results: list[PcdMetrics], mapper, color: QColor) -> None:
        if not results:
            return
        points = np.asarray([metrics.plot_coordinates() for metrics in results], dtype=float)
        point_color = QColor(color)
        point_color.setAlpha(105)
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 150), 1))
        painter.setBrush(point_color)
        for x_value, y_value in points:
            x_pixel, y_pixel = mapper(float(x_value), float(y_value))
            painter.drawEllipse(int(x_pixel) - 3, int(y_pixel) - 3, 6, 6)

        center = np.mean(points, axis=0)
        spread = np.std(points, axis=0) if len(points) > 1 else np.asarray([0.08, 0.08], dtype=float)
        left_top = mapper(float(center[0] - spread[0]), float(center[1] + spread[1]))
        right_bottom = mapper(float(center[0] + spread[0]), float(center[1] - spread[1]))
        width = max(12, int(abs(right_bottom[0] - left_top[0])))
        height = max(12, int(abs(right_bottom[1] - left_top[1])))
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 24))
        painter.setPen(QPen(color, 2, Qt.DashLine))
        painter.drawEllipse(int(min(left_top[0], right_bottom[0])), int(min(left_top[1], right_bottom[1])), width, height)

        center_x, center_y = mapper(float(center[0]), float(center[1]))
        painter.setBrush(color)
        painter.setPen(QPen(QColor("#0f172a"), 2))
        painter.drawEllipse(int(center_x) - 7, int(center_y) - 7, 14, 14)
        painter.setPen(QPen(QColor("#111827"), 1))
        painter.drawText(int(center_x) + 10, int(center_y) - 8, group_name)

    def _draw_legend(self, painter: QPainter, legend_rect) -> None:
        painter.setPen(QPen(QColor("#cbd5e1"), 1))
        painter.setBrush(QColor("#f8fafc"))
        painter.drawRoundedRect(legend_rect, 8, 8)
        painter.setPen(QPen(QColor("#0f172a"), 1))
        painter.drawText(legend_rect.left() + 10, legend_rect.top() + 22, "Groups")
        y = legend_rect.top() + 44
        for group_index, (group_name, results) in enumerate(self.group_results.items()):
            if y > legend_rect.bottom() - 18:
                painter.drawText(legend_rect.left() + 10, y, "...")
                break
            color = QColor(self.PALETTE[group_index % len(self.PALETTE)])
            painter.setBrush(color)
            painter.setPen(QPen(Qt.NoPen))
            painter.drawEllipse(legend_rect.left() + 10, y - 8, 10, 10)
            painter.setPen(QPen(QColor("#111827"), 1))
            painter.drawText(legend_rect.left() + 28, y, f"{group_name}: {len(results)}")
            y += 20
        if self.show_reference_background:
            y += 8
            painter.setPen(QPen(QColor("#64748b"), 1))
            painter.drawText(legend_rect.left() + 10, y, "background: reference")


class ContrastSourceRow(QWidget):
    removed = pyqtSignal(object)
    changed = pyqtSignal()

    def __init__(self, spec: ContrastSourceSpec, recursive_provider, browse_start_provider, browse_memory_updater) -> None:
        super().__init__()
        self.recursive_provider = recursive_provider
        self.browse_start_provider = browse_start_provider
        self.browse_memory_updater = browse_memory_updater
        self.csv_count = 0
        self._updating = False

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("组名")
        self.name_label = QLabel("名")
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("选择 CSV 文件夹 / 文件 / glob")
        self.path_label = QLabel("路")
        self.path_edit.setText(spec.pattern)
        self.browse_button = QPushButton("...")
        self.browse_button.setMaximumWidth(34)
        self.remove_button = QPushButton("-")
        self.remove_button.setMaximumWidth(30)
        self.count_label = QLabel("CSV: 0")
        self.count_label.setMinimumWidth(52)
        self.range_label = QLabel("点")
        self.to_label = QLabel("到")
        self.start_spin = QSpinBox()
        self.start_spin.setRange(1, 1)
        self.start_spin.setMaximumWidth(58)
        self.end_spin = QSpinBox()
        self.end_spin.setRange(1, 1)
        self.end_spin.setMaximumWidth(58)
        self.full_range_button = QPushButton("全选")
        self.full_range_button.setMaximumWidth(48)

        self.name_edit.setText(spec.group_name)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(4)
        top_row.addWidget(self.name_label)
        top_row.addWidget(self.name_edit, stretch=1)
        top_row.addWidget(self.count_label)
        top_row.addWidget(self.remove_button)

        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(4)
        path_row.addWidget(self.path_label)
        path_row.addWidget(self.path_edit, stretch=1)
        path_row.addWidget(self.browse_button)

        range_row = QHBoxLayout()
        range_row.setContentsMargins(0, 0, 0, 0)
        range_row.setSpacing(4)
        range_row.addWidget(self.range_label)
        range_row.addWidget(self.start_spin)
        range_row.addWidget(self.to_label)
        range_row.addWidget(self.end_spin)
        range_row.addWidget(self.full_range_button)
        range_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(3)
        layout.addLayout(top_row)
        layout.addLayout(path_row)
        layout.addLayout(range_row)

        self.setStyleSheet(
            """
            ContrastSourceRow {
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                background: #ffffff;
            }
            ContrastSourceRow QLabel {
                font-size: 12px;
            }
            ContrastSourceRow QLineEdit,
            ContrastSourceRow QSpinBox,
            ContrastSourceRow QPushButton {
                font-size: 12px;
                padding: 2px 5px;
                min-height: 22px;
            }
            """
        )

        self.browse_button.clicked.connect(self._browse_path)
        self.remove_button.clicked.connect(lambda: self.removed.emit(self))
        self.path_edit.textChanged.connect(self._on_path_changed)
        self.name_edit.textChanged.connect(self.changed.emit)
        self.start_spin.valueChanged.connect(self._on_spin_changed)
        self.end_spin.valueChanged.connect(self._on_spin_changed)
        self.full_range_button.clicked.connect(self._select_full_range)

        self._refresh_file_count()
        initial_end = spec.end_index if spec.end_index > 0 else max(1, self.csv_count)
        self._set_range_values(spec.start_index, initial_end)

    def to_spec(self) -> ContrastSourceSpec:
        return ContrastSourceSpec(
            pattern=self.path_edit.text().strip(),
            start_index=self.start_spin.value(),
            end_index=self.end_spin.value(),
            group_name=self.name_edit.text().strip(),
        )

    def refresh_file_count(self) -> None:
        self._refresh_file_count()

    def _browse_path(self) -> None:
        start_dir = self.browse_start_provider(self.path_edit.text().strip())
        selected_dir = QFileDialog.getExistingDirectory(
            self,
            "选择一个对比数据目录",
            str(start_dir),
        )
        if selected_dir:
            selected_path = Path(selected_dir)
            self.browse_memory_updater(selected_path)
            self.path_edit.setText(make_portable_path(selected_path))

    def _on_path_changed(self) -> None:
        self._refresh_file_count()
        self.changed.emit()

    def _refresh_file_count(self) -> None:
        path_text = self.path_edit.text().strip()
        count = 0
        default_name = ""
        if path_text:
            try:
                default_name, files = find_contrast_candidate_files(path_text, recursive=bool(self.recursive_provider()))
                count = len(files)
            except Exception:
                count = 0
        self.csv_count = count
        self.count_label.setText(f"CSV: {count}")
        self.count_label.setStyleSheet("color: #15803d;" if count > 0 else "color: #dc2626;")

        if not self.name_edit.text().strip() and default_name:
            self.name_edit.setText(default_name)

        max_value = max(1, count)
        old_start = min(max(1, self.start_spin.value()), max_value)
        old_end = min(max(old_start, self.end_spin.value()), max_value)
        self._updating = True
        self.start_spin.setRange(1, max_value)
        self.end_spin.setRange(1, max_value)
        self._updating = False
        self._set_range_values(old_start, old_end if count > 0 else 1)
        enabled = count > 0
        self.start_spin.setEnabled(enabled)
        self.end_spin.setEnabled(enabled)
        self.full_range_button.setEnabled(enabled)

    def _set_range_values(self, start_value: int, end_value: int) -> None:
        max_value = max(1, self.csv_count)
        start_value = min(max(1, int(start_value)), max_value)
        end_value = min(max(start_value, int(end_value)), max_value)
        self._updating = True
        self.start_spin.setValue(start_value)
        self.end_spin.setValue(end_value)
        self._updating = False
        self.changed.emit()

    def _on_spin_changed(self) -> None:
        if self._updating:
            return
        start_value = self.start_spin.value()
        end_value = self.end_spin.value()
        if start_value > end_value:
            sender = self.sender()
            if sender is self.start_spin:
                end_value = start_value
            else:
                start_value = end_value
        self._set_range_values(start_value, end_value)

    def _select_full_range(self) -> None:
        self._set_range_values(1, max(1, self.csv_count))


class AcquisitionWorker(QObject):
    log_message = pyqtSignal(str)
    reference_ready = pyqtSignal(object)
    frame_ready = pyqtSignal(object)
    contrast_point_ready = pyqtSignal(object)
    contrast_progress_changed = pyqtSignal(object)
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
        self._playback_reference_copy_target: str | None = None

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

    def copy_current_playback_to_reference(self, reference_group: str) -> None:
        if reference_group not in {"no_cavitation", "cavitation"}:
            return
        with self._playback_lock:
            if self.settings.ui.last_mode != "playback" or not self._playback_files or not self._playback_paused:
                return
            self._playback_reference_copy_target = reference_group
        self._emit_playback_state(is_active=True)

    @pyqtSlot()
    def run(self) -> None:
        try:
            if self.settings.ui.last_mode == "contrast" and self.settings.analysis.algorithm_id != "scd_icd_peak_v1":
                raise ValueError("Contrast 对比模式初版仅支持经典峰值法（SCD–ICD）。请先把当前算法切回经典峰值法。")

            reference_stats: ReferenceStats | None = None
            if self.settings.analysis.algorithm_id == "scd_icd_peak_v1":
                self.log_message.emit("Loading reference database...")
                reference_stats = build_reference_statistics(self.settings.reference, self.settings.analysis)
                self.reference_ready.emit(reference_stats)
                self.log_message.emit(
                    f"Reference loaded: no cavitation {len(reference_stats.no_results)} files, cavitation {len(reference_stats.cav_results)} files."
                )
            elif self.settings.analysis.algorithm_id == "iud_intrapulse_v1":
                self.log_message.emit(
                    f"IUD analysis ready: {self.settings.iud.window_count} windows, "
                    f"threshold {self.settings.iud.instability_threshold_db:.1f} dB."
                )
            else:
                raise ValueError(f"Unsupported analysis algorithm: {self.settings.analysis.algorithm_id}")
            if self.settings.ui.last_mode == "hardware":
                self._run_hardware(reference_stats)
            elif self.settings.ui.last_mode == "contrast":
                if reference_stats is None:
                    raise ValueError("Contrast mode requires SCD–ICD reference statistics.")
                self._run_contrast(reference_stats)
            else:
                self._run_playback(reference_stats)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self._emit_playback_state(is_active=False)
            self.finished.emit()

    def _analyze_current_signal(
        self,
        signal: np.ndarray,
        sample_rate_hz: float,
        reference_stats: ReferenceStats | None,
        file_name: str,
        relative_path: str,
        group_name: str,
    ) -> tuple[PcdMetrics | IudMetrics, np.ndarray, np.ndarray]:
        if self.settings.analysis.algorithm_id == "iud_intrapulse_v1":
            metrics = analyze_iud_signal(
                signal=signal,
                sample_rate_hz=sample_rate_hz,
                analysis_settings=self.settings.analysis,
                iud_settings=self.settings.iud,
                file_name=file_name,
                relative_path=relative_path,
                group_name=group_name,
            )
            return metrics, np.asarray([], dtype=float), np.asarray([], dtype=float)

        metrics = analyze_signal(
            signal=signal,
            sample_rate_hz=sample_rate_hz,
            analysis_settings=self.settings.analysis,
            reference_stats=reference_stats,
            file_name=file_name,
            relative_path=relative_path,
            group_name=group_name,
        )
        waveform_time_us, waveform_mv = build_center_waveform_excerpt(signal, sample_rate_hz, metrics.f0_hz)
        return metrics, waveform_time_us, waveform_mv

    def _run_playback(self, reference_stats: ReferenceStats | None) -> None:
        playback_files = resolve_input_patterns(self.settings.playback.source_patterns)
        if not playback_files:
            raise FileNotFoundError("No playback CSV files matched the configured input.")

        with self._playback_lock:
            self._playback_files = list(playback_files)
            self._playback_current_index = 0
            self._playback_paused = False
            self._playback_step_delta = 0
            self._playback_delete_requested = False
            self._playback_reference_copy_target = None
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
            metrics, waveform_time_us, waveform_mv = self._analyze_current_signal(
                signal,
                effective_sample_rate,
                reference_stats,
                file_path.name,
                relative_to_workspace(file_path),
                "Playback",
            )
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
            if isinstance(metrics, IudMetrics):
                self.log_message.emit(
                    f"Playback frame {current_position}/{total_frames}: {file_path.name} -> "
                    f"max IUD {metrics.max_iud_db:.3f} dB, unstable {'yes' if metrics.unstable else 'no'}"
                )
            else:
                self.log_message.emit(
                    f"Playback frame {current_position}/{total_frames}: {file_path.name} -> score {metrics.cavitation_score:.3f}, risk {metrics.risk_score:.3f}"
                )

            while True:
                next_action, delta = self._wait_for_playback_action(self.settings.playback.interval_ms)
                if next_action != "copy_reference":
                    break
                if reference_stats is None:
                    self.log_message.emit("The active IUD algorithm does not use the cavitation reference database.")
                    continue
                try:
                    destination = self._copy_current_playback_file_to_reference(str(delta))
                    reference_stats = build_reference_statistics(self.settings.reference, self.settings.analysis)
                    self.reference_ready.emit(reference_stats)
                    self.log_message.emit(
                        f"Copied playback frame into reference '{destination.parent.name}': {destination.name}"
                    )
                    self.log_message.emit(
                        f"Reference reloaded: no cavitation {len(reference_stats.no_results)} files, "
                        f"cavitation {len(reference_stats.cav_results)} files."
                    )
                except Exception as exc:
                    self.log_message.emit(f"Failed to copy playback frame into reference: {exc}")
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

    def _run_contrast(self, reference_stats: ReferenceStats) -> None:
        sources = resolve_contrast_sources(
            self.settings.contrast.source_patterns,
            recursive=self.settings.contrast.recursive,
            max_files_per_group=self.settings.contrast.max_files_per_group,
        )
        total_files = sum(len(files) for _, files in sources)
        if total_files <= 0:
            raise FileNotFoundError("No contrast CSV files matched the configured input.")

        self.contrast_progress_changed.emit(ContrastProgress(current=0, total=total_files))
        self.log_message.emit(
            f"Contrast queue prepared with {len(sources)} groups and {total_files} CSV files."
        )

        processed = 0
        skipped = 0
        for group_name, files in sources:
            if self._stop_requested:
                break
            self.log_message.emit(f"Contrast group '{group_name}' started: {len(files)} files.")
            for file_path in files:
                if self._stop_requested:
                    break
                processed += 1
                try:
                    signal, sample_rate_hz = load_signal_csv(file_path, self.settings.analysis.target_sample_count)
                    effective_sample_rate = sample_rate_hz or self.settings.hardware.sample_rate_hz
                    metrics = analyze_signal(
                        signal=signal,
                        sample_rate_hz=effective_sample_rate,
                        analysis_settings=self.settings.analysis,
                        reference_stats=reference_stats,
                        file_name=file_path.name,
                        relative_path=relative_to_workspace(file_path),
                        group_name=group_name,
                    )
                    self.contrast_point_ready.emit(
                        ContrastPoint(
                            metrics=metrics,
                            group_name=group_name,
                            file_name=file_path.name,
                            relative_path=relative_to_workspace(file_path),
                            sequence_index=processed,
                        )
                    )
                except Exception as exc:
                    skipped += 1
                    self.log_message.emit(f"Contrast skipped {file_path.name}: {exc}")
                self.contrast_progress_changed.emit(
                    ContrastProgress(
                        current=processed,
                        total=total_files,
                        current_group=group_name,
                        current_file=file_path.name,
                        skipped=skipped,
                    )
                )

        self.log_message.emit(
            f"Contrast finished: {processed - skipped}/{total_files} files analyzed, {skipped} skipped."
        )

    def _run_hardware(self, reference_stats: ReferenceStats | None) -> None:
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
                metrics, waveform_time_us, waveform_mv = self._analyze_current_signal(
                    capture_result.voltage_mv,
                    capture_result.sample_rate_hz,
                    reference_stats,
                    saved_csv_path.name if saved_csv_path else f"capture_{frame_index + 1:04d}",
                    relative_to_workspace(saved_csv_path) if saved_csv_path else "",
                    "Live Acquisition",
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
                if isinstance(metrics, IudMetrics):
                    self.log_message.emit(
                        f"Hardware frame {frame_index}: max IUD {metrics.max_iud_db:.3f} dB, "
                        f"unstable {'yes' if metrics.unstable else 'no'}, file {metrics.file or 'memory'}"
                    )
                else:
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

    def _wait_for_playback_action(self, interval_ms: int) -> tuple[str, int | str]:
        deadline = time.time() + max(interval_ms, 0) / 1000.0
        while True:
            if self._stop_requested:
                return "stop", 0

            with self._playback_lock:
                delete_requested = self._playback_delete_requested
                step_delta = self._playback_step_delta
                is_paused = self._playback_paused
                reference_copy_target = self._playback_reference_copy_target
                if delete_requested:
                    self._playback_delete_requested = False
                if step_delta != 0:
                    self._playback_step_delta = 0
                if reference_copy_target is not None:
                    self._playback_reference_copy_target = None

            if delete_requested:
                return "delete", 0
            if step_delta != 0:
                return "step", step_delta
            if reference_copy_target is not None:
                return "copy_reference", reference_copy_target
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
        destination = unique_destination_path(target_dir, current_file.name)
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

    def _copy_current_playback_file_to_reference(self, reference_group: str) -> Path:
        with self._playback_lock:
            if not self._playback_files:
                raise FileNotFoundError("Playback queue is empty.")
            current_index = self._playback_current_index
            current_file = self._playback_files[current_index]

        if reference_group == "no_cavitation":
            target_patterns = self.settings.reference.no_cavitation_patterns
        elif reference_group == "cavitation":
            target_patterns = self.settings.reference.cavitation_patterns
        else:
            raise ValueError(f"Unknown reference group: {reference_group}")

        target_dir = reference_pattern_base_directory(target_patterns)
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = unique_destination_path(target_dir, current_file.name)
        shutil.copy2(str(current_file), str(destination))
        return destination


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_app_settings()
        self.worker_thread: QThread | None = None
        self.worker: AcquisitionWorker | None = None
        self.playback_state = PlaybackUiState()
        self.signal_generator_client = SignalGeneratorClient()
        self.signal_generator_output_is_on: bool | None = None
        self.scpi_console_dialog: ScpiConsoleDialog | None = None
        self.last_contrast_browse_dir: Path | None = None

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
        left_panel.setMaximumWidth(600)
        left_outer_layout = QVBoxLayout(left_panel)
        left_outer_layout.setContentsMargins(8, 8, 8, 8)
        left_outer_layout.setSpacing(8)

        left_settings_panel = QWidget()
        left_settings_layout = QVBoxLayout(left_settings_panel)
        left_settings_layout.setContentsMargins(0, 0, 0, 0)
        left_settings_layout.setSpacing(10)
        left_layout = left_settings_layout

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Playback 回放", "playback")
        self.mode_combo.addItem("Hardware 真机", "hardware")
        self.mode_combo.addItem("Contrast 对比", "contrast")
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
        self.playback_mark_no_button = QPushButton("记为无空化")
        self.playback_mark_no_button.clicked.connect(lambda: self._copy_playback_frame_to_reference("no_cavitation"))
        self.playback_mark_cav_button = QPushButton("记为空化")
        self.playback_mark_cav_button.clicked.connect(lambda: self._copy_playback_frame_to_reference("cavitation"))
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

        playback_action_row = QWidget()
        playback_action_layout = QHBoxLayout(playback_action_row)
        playback_action_layout.setContentsMargins(0, 0, 0, 0)
        playback_action_layout.setSpacing(4)
        playback_action_layout.addWidget(self.playback_mark_no_button)
        playback_action_layout.addWidget(self.playback_mark_cav_button)
        playback_action_layout.addWidget(self.playback_delete_button)

        self.playback_group = QGroupBox("回放设置")
        playback_form = QFormLayout(self.playback_group)
        playback_form.addRow("CSV 来源", self._build_path_row(self.playback_source_edit, self.playback_browse_button))
        playback_form.addRow("帧间隔", self.playback_interval_spin)
        playback_form.addRow("", self.playback_loop_check)
        playback_form.addRow("当前位置", self.playback_position_label)
        playback_form.addRow("当前文件", self.playback_file_label)
        playback_form.addRow("", self.playback_pause_button)
        playback_form.addRow("跳帧", playback_control_row)
        playback_form.addRow("当前帧", playback_action_row)
        left_layout.addWidget(self.playback_group)

        self.contrast_rows: list[ContrastSourceRow] = []
        self.contrast_rows_widget = QWidget()
        self.contrast_rows_layout = QVBoxLayout(self.contrast_rows_widget)
        self.contrast_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.contrast_rows_layout.setSpacing(6)
        self.contrast_rows_layout.setAlignment(Qt.AlignTop)
        self.contrast_rows_scroll = QScrollArea()
        self.contrast_rows_scroll.setWidgetResizable(True)
        self.contrast_rows_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.contrast_rows_scroll.setFrameShape(QScrollArea.NoFrame)
        self.contrast_rows_scroll.setFixedHeight(96)
        self.contrast_rows_scroll.setWidget(self.contrast_rows_widget)
        self.contrast_range_hint_label = QLabel("每组可自定义组名；CSV 数量按当前路径实时统计；点范围按文件名排序后的序号裁剪。")
        self.contrast_range_hint_label.setWordWrap(True)
        self.contrast_add_dir_button = QPushButton("+ 添加数据组")
        self.contrast_add_dir_button.clicked.connect(self._add_contrast_source_group)
        self.contrast_clear_sources_button = QPushButton("清空")
        self.contrast_clear_sources_button.clicked.connect(self._clear_contrast_source_groups)
        self.contrast_recursive_check = QCheckBox("递归包含子文件夹")
        self.contrast_recursive_check.toggled.connect(self._refresh_contrast_source_rows)
        self.contrast_reference_background_check = QCheckBox("显示参考背景")
        self.contrast_max_files_spin = QSpinBox()
        self.contrast_max_files_spin.setRange(0, 100_000)
        self.contrast_max_files_spin.setSpecialValueText("不限")

        contrast_source_button_row = QWidget()
        contrast_source_button_layout = QHBoxLayout(contrast_source_button_row)
        contrast_source_button_layout.setContentsMargins(0, 0, 0, 0)
        contrast_source_button_layout.setSpacing(4)
        contrast_source_button_layout.addWidget(self.contrast_add_dir_button)
        contrast_source_button_layout.addWidget(self.contrast_clear_sources_button)

        self.contrast_group = QGroupBox("对比设置")
        contrast_layout = QVBoxLayout(self.contrast_group)
        contrast_layout.setContentsMargins(8, 8, 8, 8)
        contrast_layout.setSpacing(6)
        contrast_layout.addWidget(self.contrast_rows_scroll)
        contrast_layout.addWidget(self.contrast_range_hint_label)
        contrast_layout.addWidget(contrast_source_button_row)
        contrast_layout.addWidget(self.contrast_recursive_check)
        contrast_layout.addWidget(self.contrast_reference_background_check)

        contrast_limit_row = QWidget()
        contrast_limit_layout = QHBoxLayout(contrast_limit_row)
        contrast_limit_layout.setContentsMargins(0, 0, 0, 0)
        contrast_limit_layout.setSpacing(6)
        contrast_limit_layout.addWidget(QLabel("每组最多文件"))
        contrast_limit_layout.addWidget(self.contrast_max_files_spin, stretch=1)
        contrast_layout.addWidget(contrast_limit_row)
        left_layout.addWidget(self.contrast_group)

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
        hardware_group_layout = QVBoxLayout(self.hardware_group)
        hardware_group_layout.setContentsMargins(8, 8, 8, 8)
        hardware_group_layout.setSpacing(6)
        hardware_header_row = QWidget()
        hardware_header_layout = QHBoxLayout(hardware_header_row)
        hardware_header_layout.setContentsMargins(0, 0, 0, 0)
        self.hardware_status_label = QLabel("ART 采集卡连接和采样参数")
        self.hardware_status_label.setWordWrap(True)
        self.hardware_toggle_button = QPushButton("展开")
        self.hardware_toggle_button.clicked.connect(self._toggle_hardware_section)
        hardware_header_layout.addWidget(self.hardware_status_label, stretch=1)
        hardware_header_layout.addWidget(self.hardware_toggle_button)
        hardware_group_layout.addWidget(hardware_header_row)
        self.hardware_content_widget = QWidget()
        hardware_form = QFormLayout(self.hardware_content_widget)
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
        hardware_group_layout.addWidget(self.hardware_content_widget)
        left_layout.addWidget(self.hardware_group)
        self.hardware_section_expanded = True
        self._set_hardware_section_expanded(False)

        self.signal_resource_combo = QComboBox()
        self.signal_resource_combo.setEditable(True)
        self.signal_scan_button = QPushButton("扫描")
        self.signal_scan_button.clicked.connect(self._scan_signal_generator_resources)
        self.signal_connect_button = QPushButton("连接")
        self.signal_connect_button.clicked.connect(self._connect_signal_generator)
        self.signal_disconnect_button = QPushButton("断开")
        self.signal_disconnect_button.clicked.connect(self._disconnect_signal_generator)
        self.signal_identity_label = QLabel("未连接")
        self.signal_identity_label.setWordWrap(True)

        self.signal_timeout_spin = QSpinBox()
        self.signal_timeout_spin.setRange(500, 30_000)
        self.signal_timeout_spin.setSingleStep(500)
        self.signal_timeout_spin.setSuffix(" ms")
        self.signal_channel_combo = QComboBox()
        self.signal_channel_combo.addItem("CH1", 1)
        self.signal_channel_combo.addItem("CH2", 2)
        self.signal_channel_combo.currentIndexChanged.connect(self._refresh_signal_generator_output_state)
        self.signal_frequency_spin = QDoubleSpinBox()
        self.signal_frequency_spin.setRange(1.0, 60_000_000.0)
        self.signal_frequency_spin.setDecimals(0)
        self.signal_frequency_spin.setSingleStep(100_000.0)
        self.signal_frequency_spin.setSuffix(" Hz")
        self.signal_amplitude_spin = QDoubleSpinBox()
        self.signal_amplitude_spin.setRange(0.001, 20.0)
        self.signal_amplitude_spin.setDecimals(3)
        self.signal_amplitude_spin.setSingleStep(0.1)
        self.signal_amplitude_spin.setSuffix(" Vpp")
        self.signal_load_combo = QComboBox()
        self.signal_load_combo.addItem("High-Z", "INF")
        self.signal_load_combo.addItem("50 Ω", "50")
        self.signal_burst_mode_label = QLabel("N-cycle（固定）")
        self.signal_prf_spin = QDoubleSpinBox()
        self.signal_prf_spin.setRange(0.001, 10_000.0)
        self.signal_prf_spin.setDecimals(3)
        self.signal_prf_spin.setSingleStep(1.0)
        self.signal_prf_spin.setSuffix(" Hz")
        self.signal_cycles_spin = QSpinBox()
        self.signal_cycles_spin.setRange(1, 1_000_000)
        self.signal_cycles_spin.setSingleStep(100)
        self.signal_duty_label = QLabel("-")
        self.signal_frequency_spin.valueChanged.connect(self._refresh_signal_generator_summary)
        self.signal_prf_spin.valueChanged.connect(self._refresh_signal_generator_summary)
        self.signal_cycles_spin.valueChanged.connect(self._refresh_signal_generator_summary)

        self.signal_apply_button = QPushButton("输入参数")
        self.signal_apply_button.clicked.connect(self._apply_signal_generator_settings)
        self.signal_output_on_button = QPushButton("打开输出")
        self.signal_output_on_button.clicked.connect(lambda: self._set_signal_generator_output(True))
        self.signal_output_off_button = QPushButton("关闭输出")
        self.signal_output_off_button.clicked.connect(lambda: self._set_signal_generator_output(False))
        self.signal_scpi_button = QPushButton("打开 SCPI 控制台")
        self.signal_scpi_button.clicked.connect(self._open_scpi_console)

        signal_resource_row = QWidget()
        signal_resource_layout = QHBoxLayout(signal_resource_row)
        signal_resource_layout.setContentsMargins(0, 0, 0, 0)
        signal_resource_layout.setSpacing(4)
        signal_resource_layout.addWidget(self.signal_resource_combo, stretch=1)
        signal_resource_layout.addWidget(self.signal_scan_button)

        signal_connection_row = QWidget()
        signal_connection_layout = QHBoxLayout(signal_connection_row)
        signal_connection_layout.setContentsMargins(0, 0, 0, 0)
        signal_connection_layout.setSpacing(4)
        signal_connection_layout.addWidget(self.signal_connect_button)
        signal_connection_layout.addWidget(self.signal_disconnect_button)

        signal_action_row = QWidget()
        signal_action_layout = QHBoxLayout(signal_action_row)
        signal_action_layout.setContentsMargins(0, 0, 0, 0)
        signal_action_layout.setSpacing(4)
        signal_action_layout.addWidget(self.signal_apply_button)
        signal_action_layout.addWidget(self.signal_output_off_button)
        signal_action_layout.addWidget(self.signal_output_on_button)

        self.signal_generator_group = QGroupBox("信号发生器手动设置")
        signal_group_layout = QVBoxLayout(self.signal_generator_group)
        signal_group_layout.setContentsMargins(8, 8, 8, 8)
        signal_group_layout.setSpacing(6)
        signal_header_row = QWidget()
        signal_header_layout = QHBoxLayout(signal_header_row)
        signal_header_layout.setContentsMargins(0, 0, 0, 0)
        signal_header_layout.setSpacing(6)
        signal_header_layout.addWidget(QLabel("电压"), stretch=0)
        signal_header_layout.addWidget(self.signal_amplitude_spin, stretch=1)
        self.signal_connection_badge = QLabel("未连接")
        self.signal_connection_badge.setAlignment(Qt.AlignCenter)
        self.signal_output_badge = QLabel("输出未知")
        self.signal_output_badge.setAlignment(Qt.AlignCenter)
        signal_header_layout.addWidget(self.signal_connection_badge)
        signal_header_layout.addWidget(self.signal_output_badge)
        self.signal_generator_toggle_button = QPushButton("展开")
        self.signal_generator_toggle_button.clicked.connect(self._toggle_signal_generator_section)
        signal_header_layout.addWidget(self.signal_generator_toggle_button)
        signal_group_layout.addWidget(signal_header_row)

        self.signal_generator_content_widget = QWidget()
        signal_form = QFormLayout(self.signal_generator_content_widget)
        signal_form.addRow("VISA 地址", signal_resource_row)
        signal_form.addRow("连接", signal_connection_row)
        signal_form.addRow("身份返回", self.signal_identity_label)
        signal_form.addRow("超时", self.signal_timeout_spin)
        signal_form.addRow("通道", self.signal_channel_combo)
        signal_form.addRow("频率", self.signal_frequency_spin)
        signal_form.addRow("负载", self.signal_load_combo)
        signal_form.addRow("Burst 模式", self.signal_burst_mode_label)
        signal_form.addRow("PRF", self.signal_prf_spin)
        signal_form.addRow("Cycles", self.signal_cycles_spin)
        signal_form.addRow("估算占空比", self.signal_duty_label)
        signal_form.addRow("操作", signal_action_row)
        signal_form.addRow("", self.signal_scpi_button)
        signal_group_layout.addWidget(self.signal_generator_content_widget)
        left_layout.addWidget(self.signal_generator_group)
        self.signal_generator_section_expanded = True
        self._set_signal_generator_section_expanded(False)
        self._update_signal_generator_output_buttons()

        self.algorithm_combo = QComboBox()
        self.algorithm_combo.addItem("经典峰值法（SCD–ICD）", "scd_icd_peak_v1")
        self.algorithm_combo.addItem("IUD 窗内失稳分析", "iud_intrapulse_v1")

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

        self.iud_window_count_spin = QSpinBox()
        self.iud_window_count_spin.setRange(2, 500)
        self.iud_orders_edit = QLineEdit()
        self.iud_auc_half_width_spin = QDoubleSpinBox()
        self.iud_auc_half_width_spin.setRange(0.1, 1_000.0)
        self.iud_auc_half_width_spin.setDecimals(1)
        self.iud_auc_half_width_spin.setSuffix(" kHz")
        self.iud_noise_half_width_spin = QDoubleSpinBox()
        self.iud_noise_half_width_spin.setRange(0.1, 2_000.0)
        self.iud_noise_half_width_spin.setDecimals(1)
        self.iud_noise_half_width_spin.setSuffix(" kHz")
        self.iud_subtract_noise_check = QCheckBox("扣除局部噪声基线")
        self.iud_baseline_window_count_spin = QSpinBox()
        self.iud_baseline_window_count_spin.setRange(1, 500)
        self.iud_threshold_spin = QDoubleSpinBox()
        self.iud_threshold_spin.setRange(-100.0, 100.0)
        self.iud_threshold_spin.setDecimals(1)
        self.iud_threshold_spin.setSuffix(" dB")
        self.iud_normal_reference_spin = QDoubleSpinBox()
        self.iud_normal_reference_spin.setRange(-100.0, 100.0)
        self.iud_normal_reference_spin.setDecimals(1)
        self.iud_normal_reference_spin.setSuffix(" dB")
        self.iud_fixed_f0_spin = QDoubleSpinBox()
        self.iud_fixed_f0_spin.setRange(0.0, 20.0)
        self.iud_fixed_f0_spin.setDecimals(3)
        self.iud_fixed_f0_spin.setSingleStep(0.05)
        self.iud_fixed_f0_spin.setSuffix(" MHz")
        self.iud_fixed_f0_spin.setSpecialValueText("自动搜索")

        self.analysis_group = QGroupBox("PCD 分析设置")
        analysis_group_layout = QVBoxLayout(self.analysis_group)
        analysis_group_layout.setContentsMargins(8, 8, 8, 8)
        analysis_group_layout.setSpacing(6)

        algorithm_selector = QWidget()
        algorithm_selector_form = QFormLayout(algorithm_selector)
        algorithm_selector_form.setContentsMargins(0, 0, 0, 0)
        algorithm_selector_form.addRow("当前算法", self.algorithm_combo)
        analysis_group_layout.addWidget(algorithm_selector)

        analysis_header_row = QWidget()
        analysis_header_layout = QHBoxLayout(analysis_header_row)
        analysis_header_layout.setContentsMargins(0, 0, 0, 0)
        analysis_header_layout.setSpacing(6)
        self.analysis_summary_label = QLabel("PCD 分析参数")
        self.analysis_summary_label.setWordWrap(True)
        self.analysis_toggle_button = QPushButton("展开")
        self.analysis_toggle_button.clicked.connect(self._toggle_analysis_section)
        analysis_header_layout.addWidget(self.analysis_summary_label, stretch=1)
        analysis_header_layout.addWidget(self.analysis_toggle_button)
        analysis_group_layout.addWidget(analysis_header_row)

        self.analysis_content_widget = QWidget()
        analysis_content_layout = QVBoxLayout(self.analysis_content_widget)
        analysis_content_layout.setContentsMargins(0, 0, 0, 0)
        analysis_content_layout.setSpacing(8)

        common_analysis_group = QGroupBox("公共分析设置")
        common_analysis_form = QFormLayout(common_analysis_group)
        common_analysis_form.addRow("频谱模式", self.spectrum_mode_combo)
        common_analysis_form.addRow("分段数量", self.segment_count_spin)
        analysis_content_layout.addWidget(common_analysis_group)

        self.algorithm_settings_stack = QStackedWidget()
        peak_algorithm_group = QGroupBox("经典峰值法参数")
        peak_algorithm_form = QFormLayout(peak_algorithm_group)
        peak_algorithm_form.addRow("无空化参考", self.reference_no_edit)
        peak_algorithm_form.addRow("有空化参考", self.reference_cav_edit)
        peak_algorithm_form.addRow("峰值窗半宽", self.peak_half_width_spin)
        peak_algorithm_form.addRow("噪声窗半宽", self.noise_half_width_spin)
        peak_algorithm_form.addRow("宽带窗半宽", self.broadband_half_width_spin)
        peak_algorithm_form.addRow("倍频范围下限", self.order_range_low_spin)
        peak_algorithm_form.addRow("倍频范围上限", self.order_range_high_spin)
        peak_algorithm_form.addRow("峰显著性阈值", self.peak_prominence_spin)
        self.algorithm_settings_stack.addWidget(peak_algorithm_group)

        iud_algorithm_group = QGroupBox("IUD 窗内失稳参数")
        iud_algorithm_form = QFormLayout(iud_algorithm_group)
        iud_algorithm_form.addRow("窗数量", self.iud_window_count_spin)
        iud_algorithm_form.addRow("超谐波阶次", self.iud_orders_edit)
        iud_algorithm_form.addRow("AUC 窗半宽", self.iud_auc_half_width_spin)
        iud_algorithm_form.addRow("噪声窗半宽", self.iud_noise_half_width_spin)
        iud_algorithm_form.addRow("", self.iud_subtract_noise_check)
        iud_algorithm_form.addRow("基线窗数量", self.iud_baseline_window_count_spin)
        iud_algorithm_form.addRow("正常参考线", self.iud_normal_reference_spin)
        iud_algorithm_form.addRow("失稳阈值", self.iud_threshold_spin)
        iud_algorithm_form.addRow("固定 f0", self.iud_fixed_f0_spin)
        self.algorithm_settings_stack.addWidget(iud_algorithm_group)
        analysis_content_layout.addWidget(self.algorithm_settings_stack)
        analysis_group_layout.addWidget(self.analysis_content_widget)
        left_layout.addWidget(self.analysis_group)

        self.analysis_section_expanded = True
        self._connect_analysis_summary_signals()
        self._set_analysis_section_expanded(False)

        self.max_live_points_spin = QSpinBox()
        self.max_live_points_spin.setRange(10, 5000)
        self.max_live_points_spin.setSingleStep(10)
        self.show_reference_points_check = QCheckBox("显示 reference points")

        display_group = QGroupBox("显示设置")
        display_form = QFormLayout(display_group)
        display_form.addRow("最大实时点数", self.max_live_points_spin)
        display_form.addRow("", self.show_reference_points_check)
        left_layout.addWidget(display_group)

        self.import_settings_button = QPushButton("导入设置")
        self.import_settings_button.clicked.connect(self._import_settings_profile)
        self.export_settings_button = QPushButton("导出设置")
        self.export_settings_button.clicked.connect(self._export_settings_profile)
        profile_button_row = QHBoxLayout()
        profile_button_row.addWidget(self.import_settings_button)
        profile_button_row.addWidget(self.export_settings_button)
        self.profile_group = QGroupBox("设置方案")
        profile_group_layout = QVBoxLayout(self.profile_group)
        profile_group_layout.setContentsMargins(8, 8, 8, 8)
        profile_group_layout.setSpacing(6)
        profile_group_layout.addLayout(profile_button_row)

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
        self.contrast_progress_bar = QProgressBar()
        self.contrast_progress_bar.setRange(0, 100)
        self.contrast_progress_bar.setValue(0)
        self.contrast_progress_bar.setTextVisible(True)
        self.contrast_progress_label = QLabel("对比进度：-")
        self.contrast_progress_label.setWordWrap(True)

        self.left_action_panel = QWidget()
        left_action_layout = QVBoxLayout(self.left_action_panel)
        left_action_layout.setContentsMargins(0, 0, 0, 0)
        left_action_layout.setSpacing(6)
        left_action_layout.addWidget(self.profile_group)
        left_action_layout.addLayout(button_row)
        left_action_layout.addWidget(self.contrast_progress_bar)
        left_action_layout.addWidget(self.contrast_progress_label)

        right_panel = QWidget()
        right_panel_layout = QVBoxLayout(right_panel)
        right_panel_layout.setContentsMargins(0, 0, 0, 0)

        self.algorithm_result_stack = QStackedWidget()
        peak_result_page = QWidget()
        right_layout = QVBoxLayout(peak_result_page)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(10)

        self.scatter_widget = PcdScatterWidget()
        self.spectrum_widget = SpectrumWidget()
        self.waveform_widget = WaveformWidget()
        self.show_reference_points_check.toggled.connect(self.scatter_widget.set_show_reference_points)
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
        self.algorithm_result_stack.addWidget(peak_result_page)

        iud_result_page = QWidget()
        iud_result_layout = QVBoxLayout(iud_result_page)
        iud_result_layout.setContentsMargins(8, 8, 8, 8)
        iud_result_layout.setSpacing(10)
        self.iud_curve_widget = IudCurveWidget()
        self.iud_treatment_trend_widget = IudTreatmentTrendWidget()
        iud_result_layout.addWidget(self.iud_curve_widget, stretch=4)
        iud_result_layout.addWidget(self.iud_treatment_trend_widget, stretch=4)

        iud_metrics_group = QGroupBox("最新 IUD 结果")
        iud_metrics_layout = QGridLayout(iud_metrics_group)
        iud_metrics_layout.setContentsMargins(8, 8, 8, 8)
        iud_metrics_layout.setHorizontalSpacing(8)
        iud_metrics_layout.setVerticalSpacing(4)
        self.iud_latest_source_label = QLabel("-")
        self.iud_latest_source_label.setWordWrap(True)
        self.iud_latest_time_label = QLabel("-")
        self.iud_latest_rate_label = QLabel("-")
        self.iud_latest_f0_label = QLabel("-")
        self.iud_latest_max_label = QLabel("-")
        self.iud_latest_mean_label = QLabel("-")
        self.iud_latest_max_window_label = QLabel("-")
        self.iud_latest_crossing_label = QLabel("-")
        self.iud_latest_window_duration_label = QLabel("-")
        self.iud_latest_resolution_label = QLabel("-")
        self.iud_latest_status_label = QLabel("-")
        self.iud_latest_status_label.setWordWrap(True)
        iud_metrics_layout.addWidget(QLabel("来源"), 0, 0)
        iud_metrics_layout.addWidget(self.iud_latest_source_label, 0, 1)
        iud_metrics_layout.addWidget(QLabel("时间"), 0, 2)
        iud_metrics_layout.addWidget(self.iud_latest_time_label, 0, 3)
        iud_metrics_layout.addWidget(QLabel("采样率"), 1, 0)
        iud_metrics_layout.addWidget(self.iud_latest_rate_label, 1, 1)
        iud_metrics_layout.addWidget(QLabel("f0"), 1, 2)
        iud_metrics_layout.addWidget(self.iud_latest_f0_label, 1, 3)
        iud_metrics_layout.addWidget(QLabel("最大 IUD"), 2, 0)
        iud_metrics_layout.addWidget(self.iud_latest_max_label, 2, 1)
        iud_metrics_layout.addWidget(QLabel("最大值窗"), 2, 2)
        iud_metrics_layout.addWidget(self.iud_latest_max_window_label, 2, 3)
        iud_metrics_layout.addWidget(QLabel("首次越阈窗"), 3, 0)
        iud_metrics_layout.addWidget(self.iud_latest_crossing_label, 3, 1)
        iud_metrics_layout.addWidget(QLabel("每窗时长"), 3, 2)
        iud_metrics_layout.addWidget(self.iud_latest_window_duration_label, 3, 3)
        iud_metrics_layout.addWidget(QLabel("平均 IUD"), 4, 0)
        iud_metrics_layout.addWidget(self.iud_latest_mean_label, 4, 1)
        iud_metrics_layout.addWidget(QLabel("频率分辨率"), 4, 2)
        iud_metrics_layout.addWidget(self.iud_latest_resolution_label, 4, 3)
        iud_metrics_layout.addWidget(QLabel("结论"), 5, 0)
        iud_metrics_layout.addWidget(self.iud_latest_status_label, 5, 1, 1, 3)
        iud_result_layout.addWidget(iud_metrics_group, stretch=0)

        iud_log_group = QGroupBox("日志")
        iud_log_layout = QVBoxLayout(iud_log_group)
        self.iud_log_output = QPlainTextEdit()
        self.iud_log_output.setReadOnly(True)
        iud_log_layout.addWidget(self.iud_log_output)
        iud_result_layout.addWidget(iud_log_group, stretch=1)
        self.algorithm_result_stack.addWidget(iud_result_page)

        contrast_result_page = QWidget()
        contrast_result_layout = QVBoxLayout(contrast_result_page)
        contrast_result_layout.setContentsMargins(8, 8, 8, 8)
        contrast_result_layout.setSpacing(10)
        self.contrast_chart_widget = PcdContrastWidget()
        contrast_result_layout.addWidget(self.contrast_chart_widget, stretch=7)
        self.contrast_summary_label = QLabel("尚未生成对比结果。")
        self.contrast_summary_label.setWordWrap(True)
        contrast_summary_group = QGroupBox("对比结果概览")
        contrast_summary_layout = QVBoxLayout(contrast_summary_group)
        contrast_summary_layout.addWidget(self.contrast_summary_label)
        contrast_result_layout.addWidget(contrast_summary_group, stretch=0)
        contrast_log_group = QGroupBox("日志")
        contrast_log_layout = QVBoxLayout(contrast_log_group)
        self.contrast_log_output = QPlainTextEdit()
        self.contrast_log_output.setReadOnly(True)
        contrast_log_layout.addWidget(self.contrast_log_output)
        contrast_result_layout.addWidget(contrast_log_group, stretch=2)
        self.algorithm_result_stack.addWidget(contrast_result_page)

        self.log_outputs = [self.log_output, self.iud_log_output, self.contrast_log_output]
        right_panel_layout.addWidget(self.algorithm_result_stack)

        self.algorithm_combo.currentIndexChanged.connect(self._on_algorithm_changed)
        self.contrast_reference_background_check.toggled.connect(self.contrast_chart_widget.set_show_reference_background)
        self._on_algorithm_changed(self.algorithm_combo.currentIndex())

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        left_scroll.setMaximumWidth(620)
        left_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        left_scroll.setWidget(left_settings_panel)
        left_outer_layout.addWidget(left_scroll, stretch=1)
        left_outer_layout.addWidget(self.left_action_panel, stretch=0)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([500, 940])
        self._apply_visual_theme()
        self._set_signal_connection_visual(False)

    def _build_path_row(self, line_edit: QLineEdit, button: QPushButton) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(line_edit)
        layout.addWidget(button)
        return container

    def _button_style(self, background: str, border: str, foreground: str = "white") -> str:
        return f"""
            QPushButton {{
                background-color: {background};
                color: {foreground};
                border: 1px solid {border};
                border-radius: 5px;
                padding: 4px 10px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                border: 1px solid #111827;
            }}
            QPushButton:pressed {{
                padding-top: 5px;
                padding-bottom: 3px;
            }}
            QPushButton:disabled {{
                background-color: #e5e7eb;
                color: #9ca3af;
                border: 1px solid #d1d5db;
            }}
        """

    def _badge_style(self, background: str, border: str, foreground: str) -> str:
        return (
            f"QLabel {{ background-color: {background}; color: {foreground}; "
            f"border: 1px solid {border}; border-radius: 8px; "
            "padding: 3px 8px; font-weight: 700; }}"
        )

    def _apply_visual_theme(self) -> None:
        panel_group_style = """
            QGroupBox {
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                margin-top: 8px;
                background-color: #f8fafc;
                font-weight: 700;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #1f2937;
            }
        """
        for group in (
            self.hardware_group,
            self.contrast_group,
            self.signal_generator_group,
            self.analysis_group,
            self.profile_group,
        ):
            group.setStyleSheet(panel_group_style)

        subtle_button = self._button_style("#f3f4f6", "#cbd5e1", "#1f2937")
        self.hardware_toggle_button.setStyleSheet(subtle_button)
        self.signal_generator_toggle_button.setStyleSheet(subtle_button)
        self.analysis_toggle_button.setStyleSheet(subtle_button)
        self.signal_scan_button.setStyleSheet(self._button_style("#2563eb", "#1d4ed8"))
        self.signal_connect_button.setStyleSheet(self._button_style("#16a34a", "#15803d"))
        self.signal_disconnect_button.setStyleSheet(self._button_style("#64748b", "#475569"))
        self.signal_apply_button.setStyleSheet(self._button_style("#0ea5e9", "#0284c7"))
        self.signal_output_on_button.setStyleSheet(self._button_style("#f97316", "#ea580c"))
        self.signal_output_off_button.setStyleSheet(self._button_style("#dc2626", "#b91c1c"))
        self.signal_scpi_button.setStyleSheet(self._button_style("#7c3aed", "#6d28d9"))
        self.start_button.setStyleSheet(self._button_style("#16a34a", "#15803d"))
        self.stop_button.setStyleSheet(self._button_style("#dc2626", "#b91c1c"))
        self.clear_button.setStyleSheet(self._button_style("#64748b", "#475569"))
        self.import_settings_button.setStyleSheet(self._button_style("#f3f4f6", "#cbd5e1", "#1f2937"))
        self.export_settings_button.setStyleSheet(self._button_style("#f3f4f6", "#cbd5e1", "#1f2937"))
        self.hardware_status_label.setStyleSheet("color: #475569; font-weight: 600;")

    def _set_signal_connection_visual(self, connected: bool) -> None:
        if connected:
            style = self._badge_style("#dcfce7", "#22c55e", "#166534")
            self.signal_connection_badge.setText("已连接")
            self.signal_connection_badge.setStyleSheet(style)
            self.signal_identity_label.setStyleSheet(style)
        else:
            style = self._badge_style("#fee2e2", "#ef4444", "#991b1b")
            self.signal_connection_badge.setText("未连接")
            self.signal_connection_badge.setStyleSheet(style)
            self.signal_identity_label.setStyleSheet(style)

    def _set_signal_output_visual(self, state: bool | None) -> None:
        if state is True:
            self.signal_output_badge.setText("输出 ON")
            self.signal_output_badge.setStyleSheet(self._badge_style("#ffedd5", "#f97316", "#9a3412"))
        elif state is False:
            self.signal_output_badge.setText("输出 OFF")
            self.signal_output_badge.setStyleSheet(self._badge_style("#e0f2fe", "#38bdf8", "#075985"))
        else:
            self.signal_output_badge.setText("输出未知")
            self.signal_output_badge.setStyleSheet(self._badge_style("#f1f5f9", "#94a3b8", "#475569"))

    def _on_algorithm_changed(self, index: int) -> None:
        if index < 0:
            return
        self.algorithm_settings_stack.setCurrentIndex(index)
        if self.mode_combo.currentData() == "contrast":
            self.algorithm_result_stack.setCurrentIndex(2)
        else:
            self.algorithm_result_stack.setCurrentIndex(index)
        uses_reference_database = self.algorithm_combo.currentData() == "scd_icd_peak_v1"
        self.show_reference_points_check.setEnabled(uses_reference_database)
        self._set_playback_ui_state(self.playback_state)
        self._refresh_analysis_summary()

    def _sync_iud_window_limits(self) -> None:
        self.iud_baseline_window_count_spin.setMaximum(self.iud_window_count_spin.value())

    def _connect_analysis_summary_signals(self) -> None:
        self.algorithm_combo.currentTextChanged.connect(self._refresh_analysis_summary)
        self.spectrum_mode_combo.currentTextChanged.connect(self._refresh_analysis_summary)
        self.segment_count_spin.valueChanged.connect(self._refresh_analysis_summary)
        self.peak_half_width_spin.valueChanged.connect(self._refresh_analysis_summary)
        self.noise_half_width_spin.valueChanged.connect(self._refresh_analysis_summary)
        self.broadband_half_width_spin.valueChanged.connect(self._refresh_analysis_summary)
        self.order_range_low_spin.valueChanged.connect(self._refresh_analysis_summary)
        self.order_range_high_spin.valueChanged.connect(self._refresh_analysis_summary)
        self.peak_prominence_spin.valueChanged.connect(self._refresh_analysis_summary)
        self.iud_window_count_spin.valueChanged.connect(self._sync_iud_window_limits)
        self.iud_window_count_spin.valueChanged.connect(self._refresh_analysis_summary)
        self.iud_orders_edit.textChanged.connect(self._refresh_analysis_summary)
        self.iud_auc_half_width_spin.valueChanged.connect(self._refresh_analysis_summary)
        self.iud_noise_half_width_spin.valueChanged.connect(self._refresh_analysis_summary)
        self.iud_subtract_noise_check.toggled.connect(self._refresh_analysis_summary)
        self.iud_baseline_window_count_spin.valueChanged.connect(self._refresh_analysis_summary)
        self.iud_threshold_spin.valueChanged.connect(self._refresh_analysis_summary)
        self.iud_normal_reference_spin.valueChanged.connect(self._refresh_analysis_summary)
        self.iud_fixed_f0_spin.valueChanged.connect(self._refresh_analysis_summary)

    def _toggle_analysis_section(self) -> None:
        self._set_analysis_section_expanded(not self.analysis_section_expanded)

    def _set_analysis_section_expanded(self, expanded: bool) -> None:
        self.analysis_section_expanded = bool(expanded)
        self.analysis_content_widget.setVisible(self.analysis_section_expanded)
        self.analysis_toggle_button.setText("收起" if self.analysis_section_expanded else "展开")
        self._refresh_analysis_summary()

    def _toggle_hardware_section(self) -> None:
        self._set_hardware_section_expanded(not self.hardware_section_expanded)

    def _set_hardware_section_expanded(self, expanded: bool) -> None:
        self.hardware_section_expanded = bool(expanded)
        self.hardware_content_widget.setVisible(self.hardware_section_expanded)
        self.hardware_toggle_button.setText("收起" if self.hardware_section_expanded else "展开")

    def _toggle_signal_generator_section(self) -> None:
        self._set_signal_generator_section_expanded(not self.signal_generator_section_expanded)

    def _set_signal_generator_section_expanded(self, expanded: bool) -> None:
        self.signal_generator_section_expanded = bool(expanded)
        self.signal_generator_content_widget.setVisible(self.signal_generator_section_expanded)
        self.signal_generator_toggle_button.setText("收起" if self.signal_generator_section_expanded else "展开")

    def _refresh_analysis_summary(self) -> None:
        self.analysis_summary_label.setText(self.algorithm_combo.currentText())

    def _apply_settings_to_ui(self, settings: AppSettings) -> None:
        mode_index = self.mode_combo.findData(settings.ui.last_mode)
        self.mode_combo.setCurrentIndex(max(mode_index, 0))
        self.playback_source_edit.setText(join_patterns(settings.playback.source_patterns))
        self.playback_interval_spin.setValue(settings.playback.interval_ms)
        self.playback_loop_check.setChecked(settings.playback.loop_playback)
        if settings.contrast.last_browse_dir:
            self.last_contrast_browse_dir = resolve_workspace_path(settings.contrast.last_browse_dir)
        self._set_contrast_source_groups(settings.contrast.source_patterns)
        self.contrast_recursive_check.setChecked(settings.contrast.recursive)
        self._refresh_contrast_source_rows()
        self.contrast_reference_background_check.setChecked(settings.contrast.show_reference_background)
        self.contrast_max_files_spin.setValue(settings.contrast.max_files_per_group)
        self.contrast_chart_widget.set_show_reference_background(settings.contrast.show_reference_background)
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
        self.signal_resource_combo.setCurrentText(settings.signal_generator.resource_name)
        self.signal_timeout_spin.setValue(settings.signal_generator.timeout_ms)
        channel_index = self.signal_channel_combo.findData(settings.signal_generator.channel)
        self.signal_channel_combo.setCurrentIndex(max(channel_index, 0))
        self.signal_frequency_spin.setValue(settings.signal_generator.frequency_hz)
        self.signal_amplitude_spin.setValue(settings.signal_generator.amplitude_vpp)
        load_index = self.signal_load_combo.findData(settings.signal_generator.load)
        self.signal_load_combo.setCurrentIndex(max(load_index, 0))
        self.signal_prf_spin.setValue(settings.signal_generator.prf_hz)
        self.signal_cycles_spin.setValue(settings.signal_generator.burst_cycles)
        self._refresh_signal_generator_summary()
        algorithm_index = self.algorithm_combo.findData(settings.analysis.algorithm_id)
        self.algorithm_combo.setCurrentIndex(max(algorithm_index, 0))
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
        self.iud_window_count_spin.setValue(settings.iud.window_count)
        self._sync_iud_window_limits()
        self.iud_orders_edit.setText(", ".join(f"{order:g}" for order in settings.iud.ultraharmonic_orders))
        self.iud_auc_half_width_spin.setValue(settings.iud.auc_half_width_hz / 1e3)
        self.iud_noise_half_width_spin.setValue(settings.iud.noise_half_width_hz / 1e3)
        self.iud_subtract_noise_check.setChecked(settings.iud.subtract_local_noise)
        self.iud_baseline_window_count_spin.setValue(settings.iud.baseline_window_count)
        self.iud_threshold_spin.setValue(settings.iud.instability_threshold_db)
        self.iud_normal_reference_spin.setValue(settings.iud.normal_reference_db)
        self.iud_fixed_f0_spin.setValue(settings.iud.fixed_f0_hz / 1e6)
        self.max_live_points_spin.setValue(settings.ui.max_live_points)
        self.show_reference_points_check.setChecked(settings.ui.show_reference_points)
        self.scatter_widget.set_show_reference_points(settings.ui.show_reference_points)
        self._set_hardware_section_expanded(settings.ui.hardware_section_expanded)
        self._set_signal_generator_section_expanded(settings.ui.signal_generator_section_expanded)
        self._set_analysis_section_expanded(settings.ui.analysis_section_expanded)
        self._set_playback_ui_state(PlaybackUiState())

    def _collect_settings_from_ui(self) -> AppSettings:
        analysis_settings = AnalysisSettings.from_dict(asdict(self.settings.analysis))
        analysis_settings.algorithm_id = self.algorithm_combo.currentData()
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
        iud_settings = IudSettings(
            window_count=self.iud_window_count_spin.value(),
            ultraharmonic_orders=parse_float_list(self.iud_orders_edit.text()),
            auc_half_width_hz=self.iud_auc_half_width_spin.value() * 1e3,
            noise_half_width_hz=self.iud_noise_half_width_spin.value() * 1e3,
            subtract_local_noise=self.iud_subtract_noise_check.isChecked(),
            baseline_window_count=self.iud_baseline_window_count_spin.value(),
            instability_threshold_db=self.iud_threshold_spin.value(),
            normal_reference_db=self.iud_normal_reference_spin.value(),
            fixed_f0_hz=self.iud_fixed_f0_spin.value() * 1e6,
        )
        iud_settings.validate(self.points_spin.value())

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
            signal_generator=SignalGeneratorSettings(
                resource_name=self.signal_resource_combo.currentText().strip(),
                timeout_ms=self.signal_timeout_spin.value(),
                channel=self.signal_channel_combo.currentData(),
                frequency_hz=self.signal_frequency_spin.value(),
                amplitude_vpp=self.signal_amplitude_spin.value(),
                offset_v=0.0,
                load=self.signal_load_combo.currentData(),
                prf_hz=self.signal_prf_spin.value(),
                burst_cycles=self.signal_cycles_spin.value(),
            ),
            playback=PlaybackSettings(
                source_patterns=split_patterns(self.playback_source_edit.text()),
                interval_ms=self.playback_interval_spin.value(),
                loop_playback=self.playback_loop_check.isChecked(),
            ),
            contrast=ContrastSettings(
                source_patterns=self._contrast_source_patterns_from_rows(),
                recursive=self.contrast_recursive_check.isChecked(),
                max_files_per_group=self.contrast_max_files_spin.value(),
                show_reference_background=self.contrast_reference_background_check.isChecked(),
                last_browse_dir=make_portable_path(self.last_contrast_browse_dir) if self.last_contrast_browse_dir else "",
            ),
            reference=ReferenceSettings(
                no_cavitation_patterns=split_patterns(self.reference_no_edit.text()),
                cavitation_patterns=split_patterns(self.reference_cav_edit.text()),
            ),
            analysis=analysis_settings,
            iud=iud_settings,
            ui=UiSettings(
                last_mode=self.mode_combo.currentData(),
                max_live_points=self.max_live_points_spin.value(),
                show_reference_points=self.show_reference_points_check.isChecked(),
                hardware_section_expanded=self.hardware_section_expanded,
                signal_generator_section_expanded=self.signal_generator_section_expanded,
                analysis_section_expanded=self.analysis_section_expanded,
            ),
        )

    def _update_mode_visibility(self) -> None:
        current_mode = self.mode_combo.currentData()
        is_hardware = current_mode == "hardware"
        is_playback = current_mode == "playback"
        is_contrast = current_mode == "contrast"
        self.playback_group.setEnabled(is_playback)
        self.hardware_group.setEnabled(is_hardware)
        self.signal_generator_group.setEnabled(is_hardware)
        self.contrast_group.setEnabled(is_contrast)
        self.contrast_progress_bar.setVisible(is_contrast)
        self.contrast_progress_label.setVisible(is_contrast)
        if is_contrast:
            self.algorithm_result_stack.setCurrentIndex(2)
        else:
            self.algorithm_result_stack.setCurrentIndex(max(self.algorithm_combo.currentIndex(), 0))
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
        can_edit_reference = can_step and self.algorithm_combo.currentData() == "scd_icd_peak_v1"
        self.playback_pause_button.setEnabled(can_pause)
        self.playback_pause_button.setText("继续" if state.is_paused and can_pause else "暂停")
        self.playback_back10_button.setEnabled(can_step)
        self.playback_back1_button.setEnabled(can_step)
        self.playback_forward1_button.setEnabled(can_step)
        self.playback_forward10_button.setEnabled(can_step)
        self.playback_mark_no_button.setEnabled(can_edit_reference)
        self.playback_mark_cav_button.setEnabled(can_edit_reference)
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

    def _copy_playback_frame_to_reference(self, reference_group: str) -> None:
        if self.worker is None or self.mode_combo.currentData() != "playback" or not self.playback_state.current_file:
            return
        target_name = "无空化参考" if reference_group == "no_cavitation" else "空化参考"
        answer = QMessageBox.question(
            self,
            "加入参考组",
            f"复制当前帧到 {target_name}？\n\n{self.playback_state.current_file}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self.worker.copy_current_playback_to_reference(reference_group)

    def _browse_playback_source(self) -> None:
        selected_dir = QFileDialog.getExistingDirectory(
            self,
            "选择包含回放 CSV 的目录",
            str(resolve_workspace_path(self.playback_source_edit.text() or "data")),
        )
        if selected_dir:
            portable = make_portable_path(Path(selected_dir))
            self.playback_source_edit.setText(f"{portable}/*.csv")

    def _set_contrast_source_groups(self, source_patterns: list[str]) -> None:
        self._clear_contrast_source_groups(add_default_if_empty=False)
        for raw_pattern in source_patterns:
            raw_pattern = raw_pattern.strip()
            if not raw_pattern:
                continue
            try:
                spec = parse_contrast_source_spec(raw_pattern)
            except Exception:
                spec = ContrastSourceSpec(pattern=raw_pattern)
            self._add_contrast_source_row(spec)
        if not self.contrast_rows:
            self._add_contrast_source_row(ContrastSourceSpec(pattern="data/playback"))

    def _contrast_source_patterns_from_rows(self) -> list[str]:
        patterns: list[str] = []
        for row in self.contrast_rows:
            spec = row.to_spec()
            if spec.pattern:
                patterns.append(format_contrast_source_spec(spec))
        return patterns

    def _add_contrast_source_group(self) -> None:
        self._add_contrast_source_row(ContrastSourceSpec(pattern="", group_name=f"Group {len(self.contrast_rows) + 1}"))

    def _add_contrast_source_row(self, spec: ContrastSourceSpec) -> ContrastSourceRow:
        row = ContrastSourceRow(
            spec,
            recursive_provider=lambda: self.contrast_recursive_check.isChecked(),
            browse_start_provider=self._contrast_browse_start_dir,
            browse_memory_updater=self._remember_contrast_browse_dir,
        )
        row.removed.connect(self._remove_contrast_source_row)
        row.changed.connect(lambda: self.statusBar().showMessage("Contrast source group updated."))
        self.contrast_rows_layout.addWidget(row)
        self.contrast_rows.append(row)
        self._update_contrast_rows_scroll_height()
        return row

    def _contrast_browse_start_dir(self, current_path_text: str = "") -> Path:
        current_path_text = current_path_text.strip()
        if current_path_text:
            try:
                current_path = resolve_workspace_path(current_path_text)
                if current_path.exists():
                    return current_path if current_path.is_dir() else current_path.parent
                parent = current_path.parent
                if parent.exists():
                    return parent
            except Exception:
                pass

        if self.last_contrast_browse_dir is not None and self.last_contrast_browse_dir.exists():
            return self.last_contrast_browse_dir

        for row in reversed(getattr(self, "contrast_rows", [])):
            row_path_text = row.path_edit.text().strip()
            if not row_path_text:
                continue
            try:
                row_path = resolve_workspace_path(row_path_text)
                if row_path.exists():
                    return row_path if row_path.is_dir() else row_path.parent
                parent = row_path.parent
                if parent.exists():
                    return parent
            except Exception:
                continue
        return resolve_workspace_path("data")

    def _remember_contrast_browse_dir(self, selected_path: Path) -> None:
        selected_path = selected_path.resolve()
        self.last_contrast_browse_dir = selected_path if selected_path.is_dir() else selected_path.parent

    def _remove_contrast_source_row(self, row: ContrastSourceRow) -> None:
        if row in self.contrast_rows:
            self.contrast_rows.remove(row)
        self.contrast_rows_layout.removeWidget(row)
        row.deleteLater()
        self._update_contrast_rows_scroll_height()
        if not self.contrast_rows:
            self.contrast_summary_label.setText("尚未添加对比数据组。")

    def _clear_contrast_source_groups(self, add_default_if_empty: bool = True) -> None:
        for row in list(getattr(self, "contrast_rows", [])):
            self._remove_contrast_source_row(row)
        if add_default_if_empty:
            self._add_contrast_source_row(ContrastSourceSpec(pattern="data/playback"))

    def _refresh_contrast_source_rows(self) -> None:
        for row in getattr(self, "contrast_rows", []):
            row.refresh_file_count()
        self._update_contrast_rows_scroll_height()

    def _update_contrast_rows_scroll_height(self) -> None:
        if not hasattr(self, "contrast_rows_scroll"):
            return
        row_count = len(getattr(self, "contrast_rows", []))
        if row_count <= 0:
            self.contrast_rows_scroll.setFixedHeight(54)
            return
        height = min(220, max(92, row_count * 96 + (row_count - 1) * 5))
        self.contrast_rows_scroll.setFixedHeight(height)

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

    def _refresh_signal_generator_summary(self) -> None:
        frequency_hz = self.signal_frequency_spin.value()
        prf_hz = self.signal_prf_spin.value()
        cycles = self.signal_cycles_spin.value()
        if frequency_hz <= 0:
            self.signal_duty_label.setText("-")
            return
        pulse_seconds = cycles / frequency_hz
        duty_percent = pulse_seconds * prf_hz * 100.0
        self.signal_duty_label.setText(
            f"{duty_percent:.3g}%（脉冲 {pulse_seconds * 1e3:.3g} ms，周期 {1.0 / prf_hz:.3g} s）"
        )

    def _scan_signal_generator_resources(self) -> None:
        try:
            resources = self.signal_generator_client.list_resources()
        except Exception as exc:
            QMessageBox.critical(self, "信号发生器扫描失败", str(exc))
            self.append_log(f"Signal generator scan failed: {exc}")
            return

        current_text = self.signal_resource_combo.currentText().strip()
        self.signal_resource_combo.clear()
        self.signal_resource_combo.addItems(resources)
        preferred = next((item for item in resources if item.startswith("USB")), "")
        target = current_text if current_text in resources else preferred
        if target:
            self.signal_resource_combo.setCurrentText(target)
        self.append_log(f"Signal generator resources: {', '.join(resources) if resources else 'none'}")

    def _connect_signal_generator(self) -> None:
        try:
            identity = self.signal_generator_client.connect(
                self.signal_resource_combo.currentText(),
                self.signal_timeout_spin.value(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "信号发生器连接失败", str(exc))
            self.append_log(f"Signal generator connect failed: {exc}")
            return
        self.signal_identity_label.setText(identity)
        self._set_signal_connection_visual(True)
        self.append_log(f"Signal generator connected: {identity}")
        self._refresh_signal_generator_output_state()
        if "DG1062" not in identity.upper():
            QMessageBox.warning(
                self,
                "型号提醒",
                "已经连接到 VISA 仪器，但身份返回里没有 DG1062。请确认选中的是 RIGOL 信号发生器。",
            )

    def _disconnect_signal_generator(self) -> None:
        self.signal_generator_client.disconnect()
        self._set_signal_connection_visual(False)
        self.signal_generator_output_is_on = None
        self._update_signal_generator_output_buttons()
        self.signal_identity_label.setText("未连接")
        self.append_log("Signal generator disconnected.")

    def _signal_generator_channel(self) -> int:
        return int(self.signal_channel_combo.currentData())

    def _signal_generator_output_query(self, channel: int) -> str:
        return "OUTP?" if channel == 1 else "OUTP:CH2?"

    def _parse_signal_generator_output_state(self, raw_state: str) -> bool | None:
        normalized = raw_state.strip().upper()
        if normalized in {"1", "ON"}:
            return True
        if normalized in {"0", "OFF"}:
            return False
        return None

    def _update_signal_generator_output_buttons(self) -> None:
        is_connected = self.signal_generator_client.connected
        if not is_connected or self.signal_generator_output_is_on is None:
            self._set_signal_output_visual(None)
            self.signal_output_on_button.setEnabled(False)
            self.signal_output_off_button.setEnabled(False)
            return
        self._set_signal_output_visual(self.signal_generator_output_is_on)
        self.signal_output_on_button.setEnabled(not self.signal_generator_output_is_on)
        self.signal_output_off_button.setEnabled(self.signal_generator_output_is_on)

    def _refresh_signal_generator_output_state(self, *args) -> bool | None:
        if not self.signal_generator_client.connected:
            self.signal_generator_output_is_on = None
            self._update_signal_generator_output_buttons()
            return None
        channel = self._signal_generator_channel()
        try:
            raw_state = self.signal_generator_client.query(self._signal_generator_output_query(channel))
        except Exception as exc:
            self.signal_generator_output_is_on = None
            self._update_signal_generator_output_buttons()
            self.append_log(f"Signal generator output state query failed: {exc}")
            return None
        self.signal_generator_output_is_on = self._parse_signal_generator_output_state(raw_state)
        self._update_signal_generator_output_buttons()
        return self.signal_generator_output_is_on

    def _write_signal_generator_settings(self) -> str | None:
        try:
            self.signal_generator_client.configure_burst_sine(
                channel=self._signal_generator_channel(),
                frequency_hz=self.signal_frequency_spin.value(),
                amplitude_vpp=self.signal_amplitude_spin.value(),
                offset_v=0.0,
                load=self.signal_load_combo.currentData(),
                prf_hz=self.signal_prf_spin.value(),
                burst_cycles=self.signal_cycles_spin.value(),
            )
            error_text = self.signal_generator_client.query_error()
        except Exception as exc:
            self.append_log(f"Signal generator configure failed: {exc}")
            self.statusBar().showMessage("Signal generator configure failed")
            return None

        if error_text.startswith("0"):
            result_text = f"SYST:ERR? {error_text}"
        else:
            result_text = f"SYST:ERR? {error_text}"
            self.statusBar().showMessage("Signal generator reported an SCPI error")
        return result_text

    def _apply_signal_generator_settings(self) -> None:
        result_text = self._write_signal_generator_settings()
        if result_text is None:
            return

        self.append_log(
            "Signal generator configured: "
            f"CH{self._signal_generator_channel()}, "
            f"{self.signal_frequency_spin.value():.12g} Hz, "
            f"{self.signal_amplitude_spin.value():.12g} Vpp, "
            f"PRF {self.signal_prf_spin.value():.12g} Hz, "
            f"{self.signal_cycles_spin.value()} cycles. "
            f"Output state unchanged. {result_text}"
        )
        self._refresh_signal_generator_output_state()

    def _set_signal_generator_output(self, enabled: bool) -> bool:
        channel = self._signal_generator_channel()
        try:
            self.signal_generator_client.set_output(channel, enabled)
            state = self.signal_generator_client.query(self._signal_generator_output_query(channel))
        except Exception as exc:
            self.append_log(f"Signal generator output control failed: {exc}")
            self.signal_generator_output_is_on = None
            self._update_signal_generator_output_buttons()
            return False
        self.signal_generator_output_is_on = self._parse_signal_generator_output_state(state)
        self._update_signal_generator_output_buttons()
        self.append_log(f"Signal generator CH{channel} output {'ON' if enabled else 'OFF'}; query returned {state}.")
        return True

    def _open_scpi_console(self) -> None:
        if not self.signal_generator_client.connected:
            QMessageBox.warning(self, "尚未连接", "请先连接信号发生器，再打开 SCPI 控制台。")
            return
        self.scpi_console_dialog = ScpiConsoleDialog(self.signal_generator_client, self)
        self.scpi_console_dialog.show()

    def _clear_live_points(self) -> None:
        self.scatter_widget.clear_live_results()
        self.spectrum_widget.clear_data()
        self.waveform_widget.clear_data()
        self.iud_curve_widget.clear_data()
        self.iud_treatment_trend_widget.clear_data()
        self.contrast_chart_widget.clear_results()
        self.contrast_summary_label.setText("尚未生成对比结果。")
        self.contrast_progress_bar.setValue(0)
        self.contrast_progress_label.setText("对比进度：-")
        self.append_log("Live points cleared.")

    def _export_settings_profile(self) -> None:
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.warning(self, "正在运行", "请先停止当前运行，再导出设置。")
            return
        try:
            settings = self._collect_settings_from_ui()
        except Exception as exc:
            QMessageBox.critical(self, "配置错误", f"当前设置无法导出：\n{exc}")
            return

        default_name = f"PCD设置方案_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        default_path = settings_path().parent / default_name
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出设置方案",
            str(default_path),
            "JSON 设置文件 (*.json);;所有文件 (*)",
        )
        if not file_path:
            return

        export_path = Path(file_path)
        if not export_path.suffix:
            export_path = export_path.with_suffix(".json")
        try:
            save_app_settings_to_path(settings, export_path)
            save_app_settings(settings)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.settings = settings
        self.append_log(f"Settings profile exported: {export_path}")
        QMessageBox.information(self, "导出完成", f"设置方案已导出：\n{export_path}")

    def _import_settings_profile(self) -> None:
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.warning(self, "正在运行", "请先停止当前运行，再导入设置。")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入设置方案",
            str(settings_path().parent),
            "JSON 设置文件 (*.json);;所有文件 (*)",
        )
        if not file_path:
            return

        try:
            imported_settings = load_app_settings_from_path(Path(file_path))
            imported_settings.analysis.validate()
            imported_settings.iud.validate(imported_settings.analysis.target_sample_count)
            self.settings = imported_settings
            self._apply_settings_to_ui(imported_settings)
            self._update_mode_visibility()
            save_app_settings(imported_settings)
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return

        self.append_log(f"Settings profile imported: {file_path}")
        QMessageBox.information(self, "导入完成", "设置方案已导入并应用。")

    def start_processing(self) -> None:
        if self.worker_thread and self.worker_thread.isRunning():
            return
        try:
            self.settings = self._collect_settings_from_ui()
            save_app_settings(self.settings)
        except Exception as exc:
            QMessageBox.critical(self, "配置错误", str(exc))
            return

        if self.settings.ui.last_mode == "hardware":
            if not self.signal_generator_client.connected:
                self.append_log(
                    "Signal generator is not connected. Skipping automatic configuration; "
                    "hardware acquisition will start with manual signal-generator control."
                )
                self.statusBar().showMessage("Signal generator manual mode")
            else:
                result_text = self._write_signal_generator_settings()
                if result_text is None:
                    answer = QMessageBox.question(
                        self,
                        "信号发生器参数写入失败",
                        "无法自动写入当前信号发生器参数。\n\n是否继续开始采集？\n"
                        "如果继续，请确认信号发生器已经手动设置好。",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if answer != QMessageBox.Yes:
                        self.append_log("Hardware start cancelled: failed to configure signal generator.")
                        return
                    self.append_log("Signal generator auto-configuration failed; continuing with manual control.")
                else:
                    self.append_log(f"Signal generator configured before hardware start. {result_text}")
                    if not self._set_signal_generator_output(True):
                        answer = QMessageBox.question(
                            self,
                            "信号发生器输出失败",
                            "无法自动打开信号发生器输出。\n\n是否继续开始采集？\n"
                            "如果继续，请确认信号发生器输出已经手动打开。",
                            QMessageBox.Yes | QMessageBox.No,
                            QMessageBox.No,
                        )
                        if answer != QMessageBox.Yes:
                            self.append_log("Hardware start cancelled: failed to turn signal generator output ON.")
                            return
                        self.append_log("Failed to turn signal generator output ON automatically; continuing with manual control.")

        if self.settings.ui.last_mode == "contrast" and self.settings.analysis.algorithm_id != "scd_icd_peak_v1":
            QMessageBox.warning(self, "对比模式暂不支持当前算法", "Contrast 对比模式初版仅支持经典峰值法（SCD–ICD）。")
            self.append_log("Contrast start blocked: active algorithm is not SCD–ICD.")
            return

        self.scatter_widget.clear_live_results()
        self.spectrum_widget.clear_data()
        self.waveform_widget.clear_data()
        self.iud_curve_widget.clear_data()
        self.iud_treatment_trend_widget.clear_data()
        if self.settings.ui.last_mode == "contrast":
            self.contrast_chart_widget.clear_results()
            self.contrast_summary_label.setText("正在计算对比结果...")
            self.contrast_progress_bar.setValue(0)
            self.contrast_progress_label.setText("对比进度：准备中")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.algorithm_combo.setEnabled(False)
        self.statusBar().showMessage("Running")
        self.append_log("Processing started.")

        self.worker_thread = QThread(self)
        self.worker = AcquisitionWorker(self.settings)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.log_message.connect(self.append_log)
        self.worker.reference_ready.connect(self._on_reference_ready)
        self.worker.frame_ready.connect(self._on_frame_ready)
        self.worker.contrast_point_ready.connect(self._on_contrast_point_ready)
        self.worker.contrast_progress_changed.connect(self._on_contrast_progress_changed)
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
            if self.settings.ui.last_mode == "hardware":
                if self.signal_generator_client.connected:
                    self.append_log(
                        "Stop requested. Signal generator will stay ON until the current acquisition finishes."
                    )
                else:
                    self.append_log("Stop requested. Manual signal-generator control is active.")
            else:
                self.append_log("Stop requested. Waiting for the current acquisition to finish...")
            self.statusBar().showMessage("Stopping...")

    def _on_reference_ready(self, reference_stats: ReferenceStats) -> None:
        self.scatter_widget.set_reference_results(reference_stats.no_results, reference_stats.cav_results)
        self.contrast_chart_widget.set_reference_results(reference_stats.no_results, reference_stats.cav_results)

    def _on_playback_state_changed(self, state: PlaybackUiState) -> None:
        self._set_playback_ui_state(state)
        if self.settings.analysis.algorithm_id == "iud_intrapulse_v1":
            self.iud_treatment_trend_widget.set_current_file(state.current_file)

    def _on_contrast_point_ready(self, point: ContrastPoint) -> None:
        self.contrast_chart_widget.add_point(point)
        self.contrast_summary_label.setText(self.contrast_chart_widget.summary_text())
        self.statusBar().showMessage(f"Contrast point {point.sequence_index}: {point.group_name} / {point.file_name}")

    def _on_contrast_progress_changed(self, progress: ContrastProgress) -> None:
        if progress.total > 0:
            self.contrast_progress_bar.setRange(0, progress.total)
            self.contrast_progress_bar.setValue(progress.current)
            percent = 100.0 * progress.current / max(progress.total, 1)
            current_text = f"{progress.current_group} / {progress.current_file}" if progress.current_file else "准备中"
            self.contrast_progress_label.setText(
                f"对比进度：{progress.current}/{progress.total} ({percent:.1f}%)，跳过 {progress.skipped}；{current_text}"
            )
        else:
            self.contrast_progress_bar.setRange(0, 100)
            self.contrast_progress_bar.setValue(0)
            self.contrast_progress_label.setText("对比进度：准备中")

    def _on_frame_ready(self, frame: AnalysisFrame) -> None:
        if isinstance(frame.metrics, IudMetrics):
            self.iud_curve_widget.set_curve(
                frame.metrics.window_indices,
                frame.metrics.iud_db,
                self.settings.iud.normal_reference_db,
                self.settings.iud.instability_threshold_db,
            )
            mean_iud_db = float(np.mean(frame.metrics.iud_db))
            if self.settings.ui.last_mode == "hardware":
                self.iud_treatment_trend_widget.add_hardware_point(
                    frame.captured_at,
                    mean_iud_db,
                    frame.source_label,
                    self.max_live_points_spin.value(),
                )
            else:
                self.iud_treatment_trend_widget.add_playback_point(
                    frame.source_label,
                    mean_iud_db,
                    self.playback_state.current_position,
                )
            self.iud_latest_source_label.setText(frame.source_label or "-")
            self.iud_latest_time_label.setText(frame.captured_at.strftime("%Y-%m-%d %H:%M:%S"))
            self.iud_latest_rate_label.setText(f"{frame.sample_rate_hz / 1e6:.3f} MHz")
            self.iud_latest_f0_label.setText(f"{frame.metrics.f0_hz / 1e6:.3f} MHz")
            self.iud_latest_max_label.setText(f"{frame.metrics.max_iud_db:.3f} dB")
            self.iud_latest_mean_label.setText(f"{mean_iud_db:.3f} dB")
            self.iud_latest_max_window_label.setText(str(frame.metrics.max_window_index))
            self.iud_latest_crossing_label.setText(
                str(frame.metrics.first_crossing_window) if frame.metrics.first_crossing_window else "未越阈"
            )
            self.iud_latest_window_duration_label.setText(f"{frame.metrics.window_duration_us:.3f} us")
            self.iud_latest_resolution_label.setText(f"{frame.metrics.frequency_resolution_hz / 1e3:.3f} kHz")
            self.iud_latest_status_label.setText(frame.metrics.conclusion or "-")
            self.statusBar().showMessage(f"Latest IUD frame {frame.sequence_index}: {frame.source_label}")
            return

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
        if self.settings.ui.last_mode == "hardware" and self.signal_generator_client.connected:
            if self._set_signal_generator_output(False):
                self.append_log("Signal generator output turned OFF after acquisition finished.")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.algorithm_combo.setEnabled(True)
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
        line = f"[{timestamp}] {message}"
        for log_output in self.log_outputs:
            log_output.appendPlainText(line)

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
        if self.signal_generator_client.connected:
            self._set_signal_generator_output(False)
        self.signal_generator_client.close()
        event.accept()


def load_app_settings() -> AppSettings:
    config_path = settings_path()
    if not config_path.exists():
        settings = AppSettings()
        save_app_settings(settings)
        return settings
    return load_app_settings_from_path(config_path)


def save_app_settings(settings: AppSettings) -> None:
    save_app_settings_to_path(settings, settings_path())


def load_app_settings_from_path(config_path: Path) -> AppSettings:
    with config_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("设置文件格式不正确：顶层内容不是 JSON 对象。")
    return AppSettings.from_dict(data)


def save_app_settings_to_path(settings: AppSettings, config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(settings.to_dict(), handle, indent=2, ensure_ascii=False)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())







