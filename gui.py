"""Cohesive PySide6 GUI for the elevated, read-only iPod dumper."""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QAbstractItemView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QProgressBar, QPlainTextEdit, QSizePolicy,
    QStatusBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .device_scanner import scan_drives, validated_candidates
from .dump_engine import DumpEngine
from .models import DriveScanRecord, FirmwareLayout
from .version import APP_NAME, VERSION
from .win32_raw_reader import Win32Reader


def _is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _resource_path(filename: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / filename

    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _format_size(value: int | None) -> str:
    if not value:
        return "—"
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def _hex(value: int | None) -> str:
    return "—" if value is None else f"0x{value:X}"


class ScanWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.completed.emit(scan_drives())
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class DumpWorker(QThread):
    progress = Signal(int, int, int)
    status = Signal(str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, path: str, layout: FirmwareLayout, output: Path,
                 extract_sections: bool, full_partition: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.path = path
        self.layout = layout
        self.output = output
        self.extract_sections = extract_sections
        self.full_partition = full_partition
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.status.emit("Cancellation requested; finishing the current read…")

    def run(self) -> None:
        reader = None
        try:
            self.status.emit(f"Opening {self.path} read-only…")
            reader = Win32Reader.open(self.path)

            def on_progress(completed: int, requested: int) -> None:
                percent = int((completed / requested) * 100) if requested else 0
                self.progress.emit(min(max(percent, 0), 100), completed, requested)
                self.status.emit(f"Reading {_format_size(completed)} / {_format_size(requested)}")

            result = DumpEngine().run(
                reader, self.layout, self.output,
                extract_sections=self.extract_sections,
                cancel=self.cancel_event,
                command="gui_dump",
                full_partition=self.full_partition,
                progress=on_progress,
            )
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            if reader is not None and getattr(reader, "handle", None) is not None:
                try:
                    reader.close()
                except Exception:
                    pass


class DumperWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        icon_path = _resource_path("dumper.png")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(760, 520)
        self.resize(900, 610)
        self.records: list[DriveScanRecord] = []
        self.matches: list[tuple[DriveScanRecord, Any]] = []
        self.scan_worker: ScanWorker | None = None
        self.dump_worker: DumpWorker | None = None
        self.last_output: Path | None = None
        self._build_ui()
        self._show_safety_status()
        self._refresh_scan()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 4)

        setup_box = QGroupBox("Dump setup")
        setup = QGridLayout(setup_box)
        setup.setContentsMargins(8, 6, 8, 6)
        setup.setHorizontalSpacing(6)
        setup.setVerticalSpacing(5)
        setup.addWidget(QLabel("iPod:"), 0, 0)
        self.ipod_combo = QComboBox()
        self.ipod_combo.setMinimumWidth(390)
        self.ipod_combo.currentIndexChanged.connect(self._candidate_selected)
        setup.addWidget(self.ipod_combo, 0, 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self._refresh_scan)
        setup.addWidget(self.refresh_button, 0, 2)
        self.admin_label = QLabel()
        self.admin_label.setStyleSheet("color: #2e7d32; font-size: 11px;")
        setup.addWidget(self.admin_label, 0, 3)

        setup.addWidget(QLabel("Files output:"), 1, 0)
        self.output_edit = QLineEdit(str(Path.home() / "iPodDumperOutput"))
        self.output_edit.setPlaceholderText("Host-side folder; never select the iPod volume")
        setup.addWidget(self.output_edit, 1, 1)
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self._browse_output)
        setup.addWidget(self.browse_button, 1, 2)
        options = QHBoxLayout()
        options.setContentsMargins(0, 0, 0, 0)
        self.extract_checkbox = QCheckBox("Extract sections")
        self.extract_checkbox.setChecked(True)
        options.addWidget(self.extract_checkbox)
        self.full_partition_checkbox = QCheckBox("Full partition")
        self.full_partition_checkbox.setToolTip(
            "Dump the entire MBR-declared firmware partition, not just to the last !ATA section.")
        options.addWidget(self.full_partition_checkbox)
        options.addStretch()
        options_container = QWidget()
        options_container.setLayout(options)
        setup.addWidget(options_container, 1, 3)
        setup.setColumnStretch(1, 1)
        root.addWidget(setup_box)

        self.safety_label = QLabel()
        self.safety_label.setWordWrap(True)
        self.safety_label.setStyleSheet("color: #555; font-size: 11px; padding: 1px 3px;")
        root.addWidget(self.safety_label)

        self.candidate_box = QGroupBox("Detected validated iPod candidates")
        self.candidate_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        candidate_box = self.candidate_box
        candidate_layout = QVBoxLayout(candidate_box)
        candidate_layout.setContentsMargins(6, 5, 6, 6)
        self.candidate_table = QTableWidget(0, 4)
        self.candidate_table.setHorizontalHeaderLabels(["Physical disk", "Sector", "Disk size", "Status"])
        self.candidate_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            self.candidate_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.candidate_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.candidate_table.setAlternatingRowColors(True)
        self.candidate_table.verticalHeader().setVisible(False)
        self.candidate_table.itemSelectionChanged.connect(self._table_selection_changed)
        candidate_layout.addWidget(self.candidate_table)

        self.layout_box = QGroupBox("Firmware layout")
        self.layout_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout_box = self.layout_box
        layout_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout_layout = QVBoxLayout(layout_box)
        layout_layout.setContentsMargins(8, 4, 8, 6)
        layout_layout.setSpacing(4)
        self.layout_summary = QLabel("Scan for a validated iPod to inspect its layout.")
        self.layout_summary.setWordWrap(True)
        self.layout_summary.setContentsMargins(0, 0, 0, 0)
        self.layout_summary.setStyleSheet("color: #333; font-size: 11px;")
        layout_layout.addWidget(self.layout_summary)
        self.sections_table = QTableWidget(0, 4)
        self.sections_table.setHorizontalHeaderLabels(["Section", "Offset", "Length", "Bounds"])
        self.sections_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.sections_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.sections_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.sections_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.sections_table.setAlternatingRowColors(True)
        self.sections_table.verticalHeader().setVisible(False)
        layout_layout.addWidget(self.sections_table)
        root.addWidget(layout_box)
        root.addWidget(candidate_box)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(6)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(22)
        self.progress.setFormat("%p%")
        self.progress.setStyleSheet("QProgressBar { border: 1px solid #bbb; border-radius: 4px; background: #eee; text-align: center; } QProgressBar::chunk { background: #4285f4; border-radius: 3px; }")
        progress_row.addWidget(self.progress, 1)
        self.start_button = QPushButton("Start Read-Only Dump")
        self.start_button.clicked.connect(self._start_dump)
        progress_row.addWidget(self.start_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_dump)
        progress_row.addWidget(self.cancel_button)
        root.addLayout(progress_row)

        self.result_box = QGroupBox("Log")
        self.result_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        result_box = self.result_box
        result_layout = QHBoxLayout(result_box)
        result_layout.setContentsMargins(8, 6, 8, 6)
        result_layout.setSpacing(6)
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("Select a row to begin. Status, hashes, and next action will appear here.")
        self.result_text.setFixedHeight(96)
        self.result_text.setStyleSheet(
            "QPlainTextEdit { background: #ffffff; color: #1e1e1e; border: 1px solid #b5b5b5;"
            " font-family: Consolas, 'Courier New', monospace; font-size: 11px; }"
        )
        result_layout.addWidget(self.result_text, 1)
        result_buttons = QVBoxLayout()
        result_buttons.setSpacing(4)
        self.open_output_button = QPushButton("Open Output")
        self.open_output_button.setEnabled(False)
        self.open_output_button.clicked.connect(self._open_output)
        result_buttons.addWidget(self.open_output_button)
        self.open_log_button = QPushButton("Open Read Log")
        self.open_log_button.setEnabled(False)
        self.open_log_button.clicked.connect(self._open_log)
        result_buttons.addWidget(self.open_log_button)
        result_buttons.addStretch()
        result_layout.addLayout(result_buttons)
        root.addWidget(result_box)

        status = QStatusBar()
        self.setStatusBar(status)
        self.status_label = QLabel("Starting scan…")
        self.status_label.setStyleSheet("color: #555; font-size: 11px;")
        status.addWidget(QLabel(f"{APP_NAME} v{VERSION}"))
        status.addWidget(self.status_label, 1)
        credit = QLabel("2026 Created by Ricardo de Koning")
        credit.setStyleSheet("color: #888; font-size: 10px;")
        status.addPermanentWidget(credit)

    def _show_safety_status(self) -> None:
        admin = _is_admin()
        self.admin_label.setText("Administrator: yes" if admin else "Administrator: no — raw access may be denied")
        if not admin:
            self.admin_label.setStyleSheet("color: #c62828; font-size: 11px;")
        self.safety_label.setText(
            "Read-only operation: the selected physical disk is opened with read access only. "
            "No disk sectors are modified. Keep the iPod in disk mode and connected until the operation finishes."
        )

    def _refresh_scan(self) -> None:
        if self.scan_worker and self.scan_worker.isRunning():
            return
        self._set_busy(True, scanning=True)
        self.status_label.setText("Scanning PhysicalDrive0–15 read-only…")
        self.ipod_combo.clear()
        self.candidate_table.setRowCount(0)
        self.start_button.setVisible(True)
        self.layout_summary.setText("Scanning physical disks and validating iPod structure…")
        self.sections_table.setRowCount(0)
        self._fit_table(self.candidate_table, minimum=58, maximum=76)
        self._fit_table(self.sections_table, minimum=58, maximum=76)
        self.scan_worker = ScanWorker(self)
        self.scan_worker.completed.connect(self._scan_completed)
        self.scan_worker.failed.connect(self._scan_failed)
        self.scan_worker.finished.connect(self._scan_thread_finished)
        self.scan_worker.start()

    def _scan_thread_finished(self) -> None:
        if self.scan_worker:
            self.scan_worker.deleteLater()
        self.scan_worker = None

    def _scan_failed(self, message: str) -> None:
        self.status_label.setText(f"Scan failed: {message}")
        self.layout_summary.setText("No scan result is available. Run as Administrator for physical-drive access.")
        self._set_busy(False)

    def _scan_completed(self, records: object) -> None:
        self.records = list(records)
        self.matches = validated_candidates(self.records)
        self.ipod_combo.blockSignals(True)
        self.ipod_combo.clear()
        self.candidate_table.setRowCount(0)
        # Count accepted candidates per physical drive so multiple firmware images on one
        # device are labelled as primary / mirror instead of appearing as duplicate drives.
        per_drive_seen: dict[str, int] = {}
        for record, candidate in self.matches:
            layout = candidate.layout
            if layout is None or record.geometry is None:
                continue
            seen = per_drive_seen.get(record.path, 0)
            per_drive_seen[record.path] = seen + 1
            role = "primary firmware" if seen == 0 else f"mirror/backup #{seen}"
            image = f"{role} @ 0x{layout.marker_offset:X}"
            display = (f"{record.path} — {layout.family} — {role} @ 0x{layout.marker_offset:X} "
                       f"— {_format_size(record.geometry.disk_length)}")
            self.ipod_combo.addItem(display, (record, candidate))
            row = self.candidate_table.rowCount()
            self.candidate_table.insertRow(row)
            values = (f"{record.path}  ({image})", f"{record.geometry.logical_sector_size} B",
                      _format_size(record.geometry.disk_length), "Validated")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if column == 3:
                    item.setForeground(QColor("#2e7d32"))
                self.candidate_table.setItem(row, column, item)
        if not self.matches:
            self.ipod_combo.addItem("No validated iPod candidate found")
            note = next((r.note for r in self.records if r.note), None)
            if note:
                self.status_label.setText("iPod data partition detected — firmware not exposed in disk mode")
                self.layout_summary.setText(note)
            else:
                self.status_label.setText("No validated iPod found; check disk mode and Administrator access.")
                self.layout_summary.setText("No candidate passed the required marker, header, directory, OSOS, and bounds checks.")
        else:
            self.ipod_combo.setCurrentIndex(0)
            self.status_label.setText(f"{len(self.matches)} validated candidate(s) found")
        self.ipod_combo.blockSignals(False)
        self._fit_table(self.candidate_table, minimum=58, maximum=76)
        self._set_busy(False)
        self._candidate_selected(self.ipod_combo.currentIndex())
        self._resize_cards()

    def _candidate_selected(self, index: int) -> None:
        if index < 0 or index >= len(self.matches):
            self.layout_summary.setText("Select a validated iPod candidate after scanning.")
            self.sections_table.setRowCount(0)
            self._fit_table(self.sections_table, minimum=58, maximum=76)
            self._resize_cards()
            self.start_button.setEnabled(False)
            return
        record, candidate = self.matches[index]
        self._render_layout(record, candidate.layout)
        if self.candidate_table.currentRow() != index:
            self.candidate_table.selectRow(index)

    def _table_selection_changed(self) -> None:
        row = self.candidate_table.currentRow()
        if 0 <= row < self.ipod_combo.count():
            self.ipod_combo.setCurrentIndex(row)

    def _render_layout(self, record: DriveScanRecord, layout: FirmwareLayout | None) -> None:
        if layout is None or record.geometry is None:
            return
        try:
            marker_text = layout.marker.decode("ascii")
        except Exception:
            marker_text = layout.marker.hex()
        is_classic = layout.marker == b"8702"
        family = layout.family
        first_line = (
            f"Device: {family}    Marker: {_hex(layout.marker_offset)} ({marker_text})    "
            f"Body: {_hex(layout.firmware_body_offset)}"
        )
        if is_classic:
            second_line = f"Range: 0x00000000–{_hex(layout.requested_end)}    Container: IMG1"
        else:
            second_line = (
                f"Range: 0x00000000–{_hex(layout.requested_end)}    "
                f"OSOS first word: {_hex(layout.osos_first_word)}    retailOS: {layout.retail_os_found}"
            )
        self.layout_summary.setText(
            f"{first_line}\n{second_line}\n"
            f"Complete-safe: {layout.complete_safe}    Partition ambiguity: {layout.boundary_ambiguous}"
        )
        self.sections_table.setRowCount(0)
        for section in layout.sections:
            row = self.sections_table.rowCount()
            self.sections_table.insertRow(row)
            values = (section.name, _hex(section.absolute_start), _format_size(section.declared_length), section.bounds_status)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if column == 3:
                    item.setForeground(QColor("#2e7d32" if section.bounds_status == "IN_BOUNDS" else "#c62828"))
                self.sections_table.setItem(row, column, item)
        self.start_button.setEnabled(layout.complete_safe)
        self._fit_table(self.sections_table, minimum=94, maximum=116)
        self._resize_cards()

    def _fit_table(self, table: QTableWidget, *, minimum: int, maximum: int) -> None:
        header_height = table.horizontalHeader().height()
        rows_height = sum(table.rowHeight(row) for row in range(table.rowCount()))
        desired = header_height + rows_height + 12
        table.setFixedHeight(max(minimum, min(maximum, desired)))

    def _resize_cards(self) -> None:
        self.candidate_box.setFixedHeight(self.candidate_table.height() + 30)
        self.layout_box.setFixedHeight(
            self.layout_summary.heightForWidth(self.layout_summary.width() or 760)
            + self.sections_table.height() + 26
        )
        self.result_box.setFixedHeight(126)
        self.centralWidget().layout().activate()
        self.adjustSize()
        target = self.sizeHint()
        self.resize(max(self.width(), target.width()), max(self.height(), target.height()))

    def _browse_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose host-side output folder", self.output_edit.text())
        if selected:
            self.output_edit.setText(selected)

    def _start_dump(self) -> None:
        index = self.ipod_combo.currentIndex()
        if index < 0 or index >= len(self.matches):
            QMessageBox.warning(self, "No iPod selected", "Select a validated iPod candidate first.")
            return
        output_text = self.output_edit.text().strip()
        if not output_text:
            QMessageBox.warning(self, "Output folder required", "Choose a host-side output folder first.")
            return
        record, candidate = self.matches[index]
        layout = candidate.layout
        if layout is None or not layout.complete_safe:
            QMessageBox.warning(self, "Unsafe layout", "This layout is not eligible for a complete dump.")
            return
        output = Path(output_text)
        full_partition = self.full_partition_checkbox.isChecked()
        if full_partition and layout.firmware_partition_end:
            end_label = _hex(max(layout.firmware_partition_end, layout.section_end))
            range_label = f"0x00000000–{end_label} (full firmware partition)"
        else:
            range_label = f"0x00000000–{_hex(layout.requested_end)}"
        confirmation = (
            f"Device: {record.path}\n"
            f"Family: {layout.family}\n"
            f"Sector size: {record.geometry.logical_sector_size if record.geometry else 'unknown'} bytes\n"
            f"Read range: {range_label}\n"
            f"Output: {output}\n\n"
            "This operation opens the selected physical disk read-only and copies bytes from it. "
            "No write access will be requested and no disk sectors will be modified.\n\n"
            "Confirm that this is the intended iPod."
        )
        if QMessageBox.question(self, "Confirm read-only dump", confirmation, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.last_output = output
        self.start_button.setVisible(False)
        self.result_text.setPlainText("Dump in progress…")
        self.progress.setValue(0)
        self._set_busy(True, dumping=True)
        self.status_label.setText("Starting read-only dump…")
        self.dump_worker = DumpWorker(record.path, layout, output, self.extract_checkbox.isChecked(),
                                      full_partition, self)
        self.dump_worker.progress.connect(self._dump_progress)
        self.dump_worker.status.connect(self.status_label.setText)
        self.dump_worker.completed.connect(self._dump_completed)
        self.dump_worker.failed.connect(self._dump_failed)
        self.dump_worker.finished.connect(self._dump_thread_finished)
        self.dump_worker.start()

    def _dump_progress(self, percent: int, completed: int, total: int) -> None:
        self.progress.setValue(percent)
        self.status_label.setText(f"Reading {_format_size(completed)} / {_format_size(total)}")

    def _cancel_dump(self) -> None:
        if self.dump_worker and self.dump_worker.isRunning():
            self.dump_worker.cancel()
            self.cancel_button.setEnabled(False)

    def _dump_failed(self, message: str) -> None:
        self._set_busy(False)
        self.status_label.setText(f"Dump failed: {message}")
        self.result_text.setPlainText(f"Dump failed: {message}")
        QMessageBox.critical(self, "Dump failed", message)

    def _dump_completed(self, manifest: object) -> None:
        result = dict(manifest)
        session = result.get("session", {})
        dump = result.get("dump", {})
        verification = result.get("verification", {})
        status = session.get("status", "UNKNOWN")
        self.progress.setValue(100 if status == "COMPLETE" else self.progress.value())
        self._set_busy(False)
        self.status_label.setText(f"Final status: {status}")
        self.result_text.setPlainText(
            f"Status: {status}  •  Written: {dump.get('written_bytes', 0):,} / {dump.get('requested_bytes', 0):,} bytes\n"
            f"SHA-256: {verification.get('raw_sha256', 'unavailable')}\n"
            f"OSOS first word: {_hex(verification.get('osos_first_word'))}  •  Output: {self.last_output}\n"
            "Next action: review metadata.json and read.log before further analysis."
        )
        self.open_output_button.setEnabled(self.last_output is not None and self.last_output.exists())
        self.open_log_button.setEnabled(self.last_output is not None and (self.last_output / "read.log").exists())
        self._resize_cards()
        if status != "COMPLETE":
            QMessageBox.warning(self, "Dump result", f"The operation finished with status {status}. Review read.log and metadata.json.")

    def _dump_thread_finished(self) -> None:
        if self.dump_worker:
            self.dump_worker.deleteLater()
        self.dump_worker = None

    def _set_busy(self, busy: bool, *, scanning: bool = False, dumping: bool = False) -> None:
        self.refresh_button.setEnabled(not busy)
        self.ipod_combo.setEnabled(not busy)
        self.browse_button.setEnabled(not busy)
        self.output_edit.setEnabled(not busy)
        self.extract_checkbox.setEnabled(not busy)
        self.start_button.setEnabled(not busy and bool(self.matches) and not scanning)
        self.cancel_button.setEnabled(dumping)

    def _open_output(self) -> None:
        if self.last_output and self.last_output.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_output)))

    def _open_log(self) -> None:
        if self.last_output:
            path = self.last_output / "read.log"
            if path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def closeEvent(self, event: Any) -> None:
        if self.dump_worker and self.dump_worker.isRunning():
            answer = QMessageBox.question(self, "Dump in progress", "Cancel the read-only dump and close?", QMessageBox.Yes | QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.dump_worker.cancel()
            self.dump_worker.wait(5000)
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.wait(3000)
        event.accept()


def launch_gui() -> None:
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(245, 245, 245))
    palette.setColor(QPalette.WindowText, QColor(30, 30, 30))
    palette.setColor(QPalette.Base, QColor(255, 255, 255))
    palette.setColor(QPalette.AlternateBase, QColor(240, 240, 240))
    palette.setColor(QPalette.Highlight, QColor(66, 133, 244))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)
    window = DumperWindow()
    window.show()
    app.exec()
