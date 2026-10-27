import array
import math
import socket
import struct
import sys
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGridLayout, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPushButton, QSpinBox, QSystemTrayIcon, QVBoxLayout, QWidget,
)

try:
    import numpy as np
    import soundcard as sc
except Exception:  # без soundcard доступны только микрофоны
    np = sc = None

MAGIC = 0x4D555331
SAMPLE_RATE = 48_000
FRAME_SAMPLES = 480
FRAME_BYTES = FRAME_SAMPLES * 2
DEFAULT_HOST = "192.168.42.129"  # адрес телефона при USB-модеме (типично для Android)
DEFAULT_PORT = 42042


class LoopbackCapture(QObject):
    """Захват системного звука (WASAPI loopback) в отдельном потоке."""
    pcm = Signal(bytes)
    error = Signal(str)

    def __init__(self, speaker_id):
        super().__init__()
        self.speaker_id = speaker_id
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._run, name="mic-usb-loopback", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        try:
            mic = sc.get_microphone(self.speaker_id, include_loopback=True)
            channels = max(1, min(2, mic.channels))
            with mic.recorder(samplerate=SAMPLE_RATE, channels=channels, blocksize=FRAME_SAMPLES) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=FRAME_SAMPLES)
                    if self._stop.is_set():
                        break
                    mono = data.mean(axis=1) if data.ndim > 1 else data
                    self.pcm.emit((np.clip(mono, -1.0, 1.0) * 32767).astype("<i2").tobytes())
        except Exception as exc:
            if not self._stop.is_set():
                self.error.emit(str(exc))


class MicUsbWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.running = False
        self.audio_source = None
        self.audio_io = None
        self.sock = None
        self.dest = None
        self.buffer = bytearray()
        self.seq = 0
        self.sent_packets = 0
        self.level = 0
        self.sources = []
        self.capture = None
        self.setWindowTitle("Mic USB Bridge")
        self.setFixedSize(470, 430)
        self.setWindowIcon(self._make_icon())
        self._build_ui()
        self._build_tray()
        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self._refresh_stats)
        self.stats_timer.start(200)

    def _make_icon(self):
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor("#2b2b2b"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#a78bfa"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(21, 10, 22, 35, 9, 9)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QColor("white"))
        painter.drawArc(11, 25, 42, 30, 0, 180 * 16)
        painter.drawLine(32, 50, 32, 59)
        painter.drawLine(23, 59, 41, 59)
        painter.end()
        return QIcon(pixmap)

    def _build_ui(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #111111; color: #e0e0e0; }
            QFrame#card { background: #202020; border: 1px solid #2c2c2c; border-radius: 14px; }
            QLabel#title { color: #eeeeee; font-size: 20px; font-weight: 700; }
            QLabel#muted, QLabel#caption { color: #888888; }
            QLabel#caption { font-size: 11px; }
            QLineEdit, QSpinBox, QComboBox { background: #2a2a2a; color: #eeeeee; border: 0; border-radius: 8px; padding: 8px; }
            QComboBox QAbstractItemView { background: #2a2a2a; color: #eeeeee; selection-background-color: #5b466f; }
            QPushButton { background: #a78bfa; color: white; border: 0; border-radius: 9px; padding: 9px 14px; font-weight: 600; }
            QPushButton:hover { background: #8b5cf6; }
            QPushButton#stop { background: #5b466f; }
        """)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.addWidget(QLabel("MIC USB BRIDGE", objectName="title"))
        layout.addWidget(QLabel("ПК → телефон • системный звук / микрофон • PCM 48 kHz", objectName="muted"))

        card = QFrame(objectName="card")
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 14, 16, 16)
        grid.addWidget(QLabel("АДРЕС ТЕЛЕФОНА В USB-СЕТИ", objectName="caption"), 0, 0)
        grid.addWidget(QLabel("UDP ПОРТ", objectName="caption"), 0, 1)
        self.host = QLineEdit(DEFAULT_HOST)
        grid.addWidget(self.host, 1, 0)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(DEFAULT_PORT)
        grid.addWidget(self.port, 1, 1)
        grid.addWidget(QLabel("ИСТОЧНИК ЗВУКА", objectName="caption"), 2, 0, 1, 2)
        self.input_combo = QComboBox()
        self.input_combo.setMinimumWidth(280)
        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.clicked.connect(self.refresh_inputs)
        grid.addWidget(self.input_combo, 3, 0)
        grid.addWidget(self.refresh_button, 3, 1)
        self.toggle_button = QPushButton("НАЧАТЬ ПЕРЕДАЧУ")
        self.toggle_button.clicked.connect(self.toggle)
        grid.addWidget(self.toggle_button, 4, 0, 1, 2)
        layout.addWidget(card)

        self.status = QLabel("Готов к подключению", objectName="muted")
        self.status.setMinimumHeight(44)
        layout.addWidget(self.status)
        layout.addWidget(QLabel(
            "Сначала запустите приём на телефоне, затем передачу здесь.\n"
            "Закрытие окна сворачивает приложение в системный трей.",
            objectName="caption"))
        layout.addStretch()
        self.setCentralWidget(root)
        self.refresh_inputs()

    def refresh_inputs(self):
        if self.running:
            return
        current = self.input_combo.currentText() if self.input_combo.count() else ""
        self.input_combo.clear()
        self.sources = []
        if sc is not None:
            try:
                default_id = sc.default_speaker().id
                speakers = sorted(sc.all_speakers(), key=lambda s: s.id != default_id)
                for spk in speakers:
                    label = f"Системный звук: {spk.name}" + (" (по умолчанию)" if spk.id == default_id else "")
                    self.sources.append(("loopback", spk.id, label))
            except Exception:
                pass
        default_mic = QMediaDevices.defaultAudioInput().id()
        for dev in QMediaDevices.audioInputs():
            label = f"Микрофон: {dev.description()}" + (" (по умолчанию)" if dev.id() == default_mic else "")
            self.sources.append(("mic", dev, label))
        selected = 0
        for index, (_kind, _payload, label) in enumerate(self.sources):
            self.input_combo.addItem(label)
            if label == current:
                selected = index
        if self.sources:
            self.input_combo.setCurrentIndex(selected)
        else:
            self.input_combo.addItem("Источники звука не найдены")

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self._make_icon(), self)
        self.tray.setToolTip("Mic USB Bridge")
        menu = self.tray_menu = QMenu()
        open_action = QAction("Открыть", self)
        open_action.triggered.connect(self.show_window)
        exit_action = QAction("Выход", self)
        exit_action.triggered.connect(self.quit_app)
        menu.addAction(open_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_window() if reason == QSystemTrayIcon.DoubleClick else None)
        self.tray.show()

    def toggle(self):
        self.stop() if self.running else self.start()

    def start(self):
        if not self.sources:
            QMessageBox.warning(self, "Нет источника звука", "Источники не найдены. Нажмите «Обновить».")
            return
        host = self.host.text().strip()
        port = self.port.value()
        try:
            address = socket.gethostbyname(host)
        except OSError:
            QMessageBox.warning(self, "Неверный адрес", f"Не удалось разобрать адрес: {host}")
            return
        self.dest = (address, port)
        self.seq = 0
        self.sent_packets = 0
        self.level = 0
        self.buffer.clear()
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setblocking(False)
            kind, payload, _label = self.sources[self.input_combo.currentIndex()]
            if kind == "loopback":
                self.capture = LoopbackCapture(payload)
                self.capture.pcm.connect(self._feed)
                self.capture.error.connect(self._error)
                self.capture.start()
            else:
                self._start_audio(payload)
        except Exception as exc:
            self.stop()
            QMessageBox.critical(self, "Не удалось открыть микрофон", str(exc))
            return
        self.running = True
        self.host.setEnabled(False)
        self.port.setEnabled(False)
        self.input_combo.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.toggle_button.setText("ОСТАНОВИТЬ")
        self._set_stop_style(True)
        self.status.setStyleSheet("color: #a78bfa")
        self.status.setText(f"● Передача на {host}:{port}")

    def _start_audio(self, device):
        audio_format = QAudioFormat()
        audio_format.setSampleRate(SAMPLE_RATE)
        audio_format.setChannelCount(1)
        audio_format.setSampleFormat(QAudioFormat.Int16)
        self.audio_source = QAudioSource(device, audio_format, self)
        self.audio_source.setBufferSize(FRAME_BYTES * 4)
        self.audio_io = self.audio_source.start()
        if self.audio_io is None:
            raise RuntimeError("Qt не смог открыть микрофон. Проверьте доступ к микрофону в настройках конфиденциальности Windows.")
        self.audio_io.readyRead.connect(self._on_ready)

    def _on_ready(self):
        if not self.audio_io:
            return
        self._feed(bytes(self.audio_io.readAll()))

    def _feed(self, data):
        if not self.sock:
            return
        self.buffer += data
        while len(self.buffer) >= FRAME_BYTES:
            frame = bytes(self.buffer[:FRAME_BYTES])
            del self.buffer[:FRAME_BYTES]
            header = struct.pack(">III", MAGIC, self.seq & 0xFFFFFFFF, FRAME_SAMPLES)
            self.seq += 1
            try:
                self.sock.sendto(header + frame, self.dest)
                self.sent_packets += 1
            except (BlockingIOError, ConnectionResetError):
                pass  # телефон ещё не слушает порт — не страшно, пробуем дальше
            except OSError as exc:
                self._error(str(exc))
                return
            samples = array.array("h", frame)  # Windows — little-endian, как в протоколе
            rms = math.sqrt(sum(s * s for s in samples) / len(samples))
            self.level = min(100, int(rms / 327.68))

    def _refresh_stats(self):
        if self.running:
            self.status.setText(f"●  Уровень сигнала  {self.level}%\nпакетов отправлено: {self.sent_packets}")

    def _error(self, message):
        self.stop()
        self.status.setStyleSheet("color: #f87171")
        self.status.setText(f"Ошибка: {message}")

    def _set_stop_style(self, stop_style):
        self.toggle_button.setObjectName("stop" if stop_style else "")
        self.toggle_button.style().unpolish(self.toggle_button)
        self.toggle_button.style().polish(self.toggle_button)

    def stop(self):
        if self.capture is not None:
            self.capture.stop()
            for signal, slot in ((self.capture.pcm, self._feed), (self.capture.error, self._error)):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            self.capture = None
        if self.audio_io is not None:
            try:
                self.audio_io.readyRead.disconnect(self._on_ready)
            except (RuntimeError, TypeError):
                pass
        if self.audio_source:
            self.audio_source.stop()
            self.audio_source = None
        self.audio_io = None
        if self.sock:
            self.sock.close()
            self.sock = None
        self.buffer.clear()
        self.running = False
        self.host.setEnabled(True)
        self.port.setEnabled(True)
        self.input_combo.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.toggle_button.setText("НАЧАТЬ ПЕРЕДАЧУ")
        self._set_stop_style(False)
        self.status.setStyleSheet("color: #888888")
        self.status.setText("Готов к подключению")

    def show_window(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def quit_app(self):
        self.stop()
        self.tray.hide()
        QApplication.quit()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.warning(None, "Mic USB Bridge", "Системный трей недоступен, приложение всё равно можно использовать.")
    window = MicUsbWindow()
    sys.exit(app.exec())
