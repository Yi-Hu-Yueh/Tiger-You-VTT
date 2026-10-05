"""Native controls for the system-audio caption overlay."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.overlay.api_client import OverlayApiClient, OverlayApiError, OwnedSystemAudioJob
from app.overlay.caption_state import CaptionState
from app.overlay.overlay_window import CaptionOverlayWindow
from app.overlay.settings import OverlaySettings


class _TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(object)


class _ApiTask(QRunnable):
    def __init__(self, callback: Callable[[], Any]) -> None:
        super().__init__()
        self.callback = callback
        self.signals = _TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.succeeded.emit(self.callback())
        except Exception as exc:  # delivered safely to the UI thread
            self.signals.failed.emit(exc)


class OverlayControlWindow(QMainWindow):
    """Small native control window; all network work runs off the UI thread."""

    def __init__(
        self,
        overlay: CaptionOverlayWindow,
        client: OverlayApiClient | None = None,
        settings_store: QSettings | None = None,
        auto_connect: bool = True,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Tiger-You-VTT 即時字幕")
        self.setMinimumWidth(410)
        self.overlay = overlay
        self.client = client or OverlayApiClient()
        self.job = OwnedSystemAudioJob(self.client)
        self.settings_store = settings_store
        self.caption_state = CaptionState(overlay.settings.display_lines)
        self.thread_pool = QThreadPool(self)
        self._task_refs: set[_ApiTask] = set()
        self._poll_in_flight = False
        self._closing = False

        self.status_label = QLabel("Backend 檢查中...")
        self.status_label.setObjectName("backendStatusLabel")
        self.device_combo = QComboBox()
        self.device_combo.setObjectName("deviceCombo")
        self.low_latency_check = QCheckBox("低延遲即時字幕")
        self.low_latency_check.setObjectName("lowLatencyCheck")
        self.low_latency_check.setChecked(False)
        self.low_latency_check.setToolTip("縮短桌面字幕延遲；啟動前需準備模型，並可能增加 GPU 使用率。")
        self.start_button = QPushButton("開始桌面字幕")
        self.start_button.setObjectName("startButton")
        self.start_button.setEnabled(False)
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setEnabled(False)
        self.overlay_button = QPushButton("顯示字幕")
        self.overlay_button.setObjectName("overlayButton")

        self.position_combo = QComboBox()
        self.position_combo.setObjectName("positionCombo")
        self.position_combo.addItem("頂部", "top")
        self.position_combo.addItem("底部", "bottom")
        self.position_combo.addItem("自訂", "custom")
        self.position_combo.setCurrentIndex(
            max(0, self.position_combo.findData(overlay.settings.position))
        )
        self.font_spin = QSpinBox()
        self.font_spin.setObjectName("fontSizeSpin")
        self.font_spin.setRange(18, 72)
        self.font_spin.setValue(overlay.settings.font_size)
        self.opacity_spin = QSpinBox()
        self.opacity_spin.setObjectName("opacitySpin")
        self.opacity_spin.setRange(20, 95)
        self.opacity_spin.setSuffix("%")
        self.opacity_spin.setValue(overlay.settings.background_opacity)
        self.lines_spin = QSpinBox()
        self.lines_spin.setObjectName("displayLinesSpin")
        self.lines_spin.setRange(1, 4)
        self.lines_spin.setValue(overlay.settings.display_lines)
        self.topmost_check = QCheckBox("永遠置頂")
        self.topmost_check.setObjectName("alwaysOnTopCheck")
        self.topmost_check.setChecked(overlay.settings.always_on_top)

        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        buttons.addWidget(self.overlay_button)
        form = QFormLayout()
        form.addRow("Backend", self.status_label)
        form.addRow("系統音訊裝置", self.device_combo)
        form.addRow("", self.low_latency_check)
        form.addRow("字幕位置", self.position_combo)
        form.addRow("字型大小", self.font_spin)
        form.addRow("背景透明度", self.opacity_spin)
        form.addRow("顯示行數", self.lines_spin)
        form.addRow("", self.topmost_check)
        root = QVBoxLayout()
        root.addLayout(form)
        root.addLayout(buttons)
        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(500)
        self.poll_timer.timeout.connect(self.poll_job)
        self.start_button.clicked.connect(self.start_job)
        self.stop_button.clicked.connect(self.stop_job)
        self.overlay_button.clicked.connect(self.toggle_overlay)
        self.position_combo.currentIndexChanged.connect(self.apply_controls)
        self.font_spin.valueChanged.connect(self.apply_controls)
        self.opacity_spin.valueChanged.connect(self.apply_controls)
        self.lines_spin.valueChanged.connect(self.apply_controls)
        self.topmost_check.toggled.connect(self.apply_controls)

        if auto_connect:
            QTimer.singleShot(0, self.refresh_backend)

    def _run_task(
        self,
        callback: Callable[[], Any],
        success: Callable[[Any], None],
        failure: Callable[[Exception], None],
    ) -> None:
        task = _ApiTask(callback)
        self._task_refs.add(task)

        def release_success(value: Any) -> None:
            self._task_refs.discard(task)
            success(value)

        def release_failure(error: Exception) -> None:
            self._task_refs.discard(task)
            failure(error)

        task.signals.succeeded.connect(release_success)
        task.signals.failed.connect(release_failure)
        self.thread_pool.start(task)

    def refresh_backend(self) -> None:
        self.start_button.setEnabled(False)
        self.status_label.setText("Backend 檢查中...")
        self._run_task(self.client.list_system_audio_devices, self._devices_ready, self._backend_error)

    def _devices_ready(self, devices: object) -> None:
        self.device_combo.clear()
        for device in devices if isinstance(devices, list) else []:
            self.device_combo.addItem(device.name, device.id)
        if self.device_combo.count():
            self.status_label.setText("Backend 已連線")
            self.start_button.setEnabled(True)
        else:
            self.status_label.setText("找不到可用的系統音訊裝置")
            self.start_button.setEnabled(False)

    def _backend_error(self, error: Exception) -> None:
        self.status_label.setText("Backend 未啟動")
        self.start_button.setEnabled(False)
        if self.isVisible():
            QMessageBox.warning(self, "Tiger-You-VTT", "請先啟動 Tiger-You-VTT Server。")

    def start_job(self) -> None:
        device_id = self.device_combo.currentData()
        if device_id is None:
            return
        self.start_button.setEnabled(False)
        low_latency = self.low_latency_check.isChecked()
        self.status_label.setText("準備低延遲模型..." if low_latency else "啟動中...")
        self._run_task(
            lambda: self.job.start(int(device_id), low_latency=True) if low_latency else self.job.start(int(device_id)),
            self._job_started,
            self._operation_error,
        )

    def _job_started(self, payload: object) -> None:
        self.caption_state.clear()
        self.overlay.set_caption(self.caption_state.rendered_text())
        self.overlay.show()
        self.overlay.raise_()
        self.overlay_button.setText("隱藏字幕")
        self.stop_button.setEnabled(True)
        status = payload.get("status", "queued") if isinstance(payload, dict) else "queued"
        self.status_label.setText(f"轉錄狀態：{status}")
        self.poll_timer.start()

    def poll_job(self) -> None:
        if self._poll_in_flight or not self.job.active:
            return
        self._poll_in_flight = True
        self._run_task(self.job.poll, self._poll_ready, self._poll_error)

    def _poll_ready(self, payload: object) -> None:
        self._poll_in_flight = False
        if not isinstance(payload, dict):
            return
        self.caption_state.display_lines = self.lines_spin.value()
        self.caption_state.update_from_job(payload)
        self.overlay.set_caption(self.caption_state.rendered_text())
        status = str(payload.get("status", "unknown"))
        if status == "failed":
            error = payload.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            status_text = (
                f"轉錄狀態：failed — {message}"
                if isinstance(message, str)
                else "轉錄狀態：failed"
            )
            self.status_label.setText(status_text)
        else:
            result = payload.get("result") or {}
            phases = {"preparing_model": "準備低延遲模型...", "capturing": "低延遲字幕收音中", "stopping": "停止中..."}
            phase = phases.get(result.get("live_phase")) if result.get("low_latency") else None
            self.status_label.setText(phase if phase and self.job.active else f"轉錄狀態：{status}")
        if not self.job.active:
            self.poll_timer.stop()
            self.stop_button.setEnabled(False)
            self.start_button.setEnabled(self.device_combo.count() > 0)
            if self._closing:
                self.close()

    def _poll_error(self, error: Exception) -> None:
        self._poll_in_flight = False
        self._operation_error(error)

    def stop_job(self) -> None:
        if not self.job.active:
            return
        self.stop_button.setEnabled(False)
        self.status_label.setText("停止中...")
        self._run_task(self.job.stop, self._stop_requested, self._operation_error)

    def _stop_requested(self, payload: object) -> None:
        self.poll_timer.start()
        self.poll_job()

    def _operation_error(self, error: Exception) -> None:
        message = error.message if isinstance(error, OverlayApiError) else "操作失敗"
        self.status_label.setText(message)
        self.stop_button.setEnabled(self.job.active)
        self.start_button.setEnabled(not self.job.active and self.device_combo.count() > 0)

    def toggle_overlay(self) -> None:
        if self.overlay.isVisible():
            self.overlay.hide()
            self.overlay_button.setText("顯示字幕")
        else:
            self.overlay.show()
            self.overlay.raise_()
            self.overlay_button.setText("隱藏字幕")

    def apply_controls(self, *args: object) -> None:
        del args
        settings = OverlaySettings(
            position=str(self.position_combo.currentData()),
            font_size=self.font_spin.value(),
            background_opacity=self.opacity_spin.value(),
            display_lines=self.lines_spin.value(),
            always_on_top=self.topmost_check.isChecked(),
        )
        self.caption_state.display_lines = settings.display_lines
        self.overlay.apply_settings(settings)
        self.overlay.set_caption(self.caption_state.rendered_text())
        if self.settings_store is not None:
            settings.save(self.settings_store)

    def closeEvent(self, event: object) -> None:  # noqa: N802
        if self.job.active and not self._closing:
            self._closing = True
            event.ignore()
            self.status_label.setText("關閉前停止轉錄...")
            self.stop_job()
            QTimer.singleShot(15000, self.close)
            return
        self.poll_timer.stop()
        self.overlay.close()
        event.accept()
