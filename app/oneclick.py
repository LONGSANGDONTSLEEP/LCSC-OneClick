"""Simplified portable desktop entry. SPDX-License-Identifier: AGPL-3.0-or-later."""
import os
import sys
import threading
from pathlib import Path
from PySide6.QtCore import Qt, QThread, Signal, QUrl, QTimer
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QPlainTextEdit, QLineEdit, QComboBox, QFileDialog, QProgressBar, QMessageBox)
from oneclick_export import PRESETS, extract_parts, read_bom, export_bundle
from app_paths import configure_runtime_paths
from app_logging import configure_logging, install_exception_hooks

class Worker(QThread):
    phase = Signal(int, str)
    done = Signal(object)
    error = Signal(str)
    def __init__(self, parts, destination, preset):
        super().__init__()
        self.parts, self.destination, self.preset = parts, destination, preset
        self.cancelled = threading.Event()
    def run(self):
        try:
            result = export_bundle(self.parts, self.destination, self.preset, self.cancelled,
                lambda index, phase, title: self.phase.emit(index, phase + (' · ' + title if title else '')))
            self.done.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))

class OneClickWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.last_root = None
        self.failed = []
        self.advanced = None
        self.setWindowTitle('立创一键导出 · LCSC OneClick 1.1')
        self.resize(920, 730)
        layout = QVBoxLayout(self)
        title = QLabel('立创元件，一键打包')
        title.setObjectName('title')
        layout.addWidget(title)
        layout.addWidget(QLabel('原理图符号 + PCB 封装 + STEP / OBJ 3D ｜ AD 与 KiCad ｜ 无需安装 EDA'))
        layout.addWidget(QLabel('① 粘贴 C 编号、商城链接或 BOM 内容（自动提取编号、去重）'))
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText('例如 C2040, C20197, C23922\n也可导入 CSV / TXT，或直接粘贴表格')
        self.input.setMaximumHeight(180)
        layout.addWidget(self.input)
        row = QHBoxLayout()
        self.bom = QPushButton('导入 BOM / TXT')
        self.bom.clicked.connect(self.import_bom)
        row.addWidget(self.bom)
        self.count = QLabel('0 个器件')
        row.addWidget(self.count)
        row.addStretch()
        advanced = QPushButton('高级：商城搜索 / 预览 / 自定义导出')
        advanced.clicked.connect(self.open_advanced)
        row.addWidget(advanced)
        layout.addLayout(row)
        self.input.textChanged.connect(lambda: self.count.setText(f'{len(extract_parts(self.input.toPlainText()))} 个器件'))
        layout.addWidget(QLabel('② 选择目标软件和保存位置'))
        row = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.addItems(PRESETS)
        self.preset.setCurrentText('AD + KiCad')
        row.addWidget(self.preset)
        self.destination = QLineEdit(str(Path.home() / 'Downloads' / 'LCSC-Exports'))
        row.addWidget(self.destination, 1)
        self.browse = QPushButton('选择目录')
        self.browse.clicked.connect(self.choose_folder)
        row.addWidget(self.browse)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.start = QPushButton('③ 一键导出并生成 ZIP')
        self.start.setObjectName('primary')
        self.start.clicked.connect(self.start_export)
        row.addWidget(self.start, 1)
        self.stop = QPushButton('停止')
        self.stop.setEnabled(False)
        self.stop.clicked.connect(lambda: self.worker.cancelled.set())
        row.addWidget(self.stop)
        self.retry = QPushButton('重试未完成器件')
        self.retry.setEnabled(False)
        self.retry.clicked.connect(self.retry_export)
        row.addWidget(self.retry)
        self.open = QPushButton('打开导出目录')
        self.open.setEnabled(False)
        self.open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_root))))
        row.addWidget(self.open)
        layout.addLayout(row)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)
        note = QLabel('AD 封装库自动内嵌 STEP；KiCad 自动关联模型（将 OneClick_3D 复制到工程根目录）。\n放置参数来自源库，请在 3D 视图核对。每次导出生成独立 ZIP、CSV 和重试列表。')
        note.setWordWrap(True)
        layout.addWidget(note)
    def import_bom(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入 BOM', '', 'BOM (*.csv *.tsv *.txt);;所有文件 (*)')
        if path:
            try:
                parts = read_bom(path)
                if not parts:
                    raise ValueError('文件内没有 C 编号；请导出含立创编号的 CSV')
                self.input.appendPlainText('\n'.join(parts))
            except Exception as exc:
                QMessageBox.warning(self, '导入失败', str(exc))
    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, '选择保存目录', self.destination.text())
        if path:
            self.destination.setText(path)
    def start_export(self):
        parts = extract_parts(self.input.toPlainText())
        if not parts or not self.destination.text().strip():
            QMessageBox.warning(self, '需要输入', '请填入有效 C 编号和保存目录')
            return
        self.log.clear()
        self.failed = []
        self.progress.setRange(0, 0)
        for widget in (self.start, self.retry, self.input, self.preset, self.destination, self.bom, self.browse):
            widget.setEnabled(False)
        self.stop.setEnabled(True)
        self.worker = Worker(parts, self.destination.text().strip(), self.preset.currentText())
        self.worker.phase.connect(lambda index, text: self.log.appendPlainText(f'{parts[index]} · {text}'))
        self.worker.done.connect(self.completed)
        self.worker.error.connect(lambda text: self.log.appendPlainText('导出失败：' + text))
        self.worker.finished.connect(self.finished)
        self.worker.start()
    def completed(self, bundle):
        results, self.last_root, archive, self.failed = bundle
        for r in results:
            self.log.appendPlainText(f'{r.part}：{r.status} · {r.message}')
        self.log.appendPlainText(f'ZIP：{archive}\n未完成全部格式：{len(self.failed)} 个（详情见 results.csv）')
        self.open.setEnabled(True)
    def finished(self):
        for widget in (self.start, self.input, self.preset, self.destination, self.bom, self.browse):
            widget.setEnabled(True)
        self.stop.setEnabled(False)
        self.retry.setEnabled(bool(self.failed))
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
    def retry_export(self):
        self.input.setPlainText('\n'.join(self.failed))
        self.start_export()
    def open_advanced(self):
        if self.advanced is None:
            from main import MainWindow
            self.advanced = MainWindow()
            # Never replace this fork with an upstream executable.
            self.advanced.check_updates = lambda: QDesktopServices.openUrl(QUrl('https://github.com/LONGSANGDONTSLEEP/LCSC-OneClick/releases'))
        self.advanced.show()
        self.advanced.raise_()
    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.cancelled.set()
            self.log.appendPlainText('正在停止任务；完成后可关闭窗口。')
            event.ignore()
            return
        if self.advanced:
            self.advanced.close()
        event.accept()

def main():
    configure_runtime_paths()
    configure_logging('INFO', version='OneClick-1.1')
    install_exception_hooks()
    if '--smoke-export' in sys.argv:
        destination = Path(sys.argv[sys.argv.index('--smoke-export') + 1])
        results, root, archive, failed = export_bundle(['C2040', 'C20197'], destination)
        return 1 if failed else 0
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setFont(QFont('Microsoft YaHei UI', 10))
    app.setStyle('Fusion')
    app.setStyleSheet('QWidget {background:#f5f7fb;color:#172b4d;} QPlainTextEdit,QLineEdit,QComboBox {background:white;border:1px solid #ccd5e3;border-radius:6px;padding:8px;} QPushButton {background:#e6edf8;border:0;border-radius:6px;padding:11px;} QPushButton:disabled {color:#8894a7;} QPushButton#primary {background:#2563eb;color:white;font-weight:bold;} QLabel#title {font-size:28px;font-weight:bold;padding:12px 0;}')
    window = OneClickWindow()
    window.show()
    if '--smoke-advanced' in sys.argv:
        output = Path(sys.argv[sys.argv.index('--smoke-advanced') + 1])
        output.mkdir(parents=True, exist_ok=True)
        window.open_advanced()
        def capture_advanced():
            window.advanced.grab().save(str(output / 'advanced.png'))
            window.close()
            app.quit()
        QTimer.singleShot(4000, capture_advanced)
    if '--smoke-test' in sys.argv:
        output = Path(sys.argv[sys.argv.index('--smoke-test') + 1])
        output.mkdir(parents=True, exist_ok=True)
        def capture():
            window.grab().save(str(output / 'oneclick.png'))
            window.close()
            app.quit()
        QTimer.singleShot(1200, capture)
    return app.exec()

if __name__ == '__main__':
    sys.exit(main())
