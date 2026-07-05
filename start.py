"""
vnpy 启动脚本
用法：先激活 venv，然后运行 python start.py

功能：
  - 启动时自动增量同步 Tushare 行情数据
  - 菜单栏「系统 → 同步行情数据」可手动触发同步
"""

import importlib
import os


# ============================================================
# 兼容性补丁（自动修复已安装包中的已知兼容性问题）
# ============================================================
def _apply_patches():
    """启动时自动修补 venv 中已安装包的兼容性问题"""

    # 补丁 1: pandas 3.x + pyqtgraph 兼容
    # pyqtgraph 用整数索引访问 pandas Series，当索引是 datetime 类型时会报 KeyError
    try:
        mod = importlib.import_module("vnpy_ctabacktester.ui.widget")
        filepath = mod.__file__
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        old = 'self.balance_curve.setData(df["balance"])'
        new = 'self.balance_curve.setData(df["balance"].values)'

        if old in content:
            content = content.replace(old, new)
            content = content.replace(
                'self.drawdown_curve.setData(df["drawdown"])',
                'self.drawdown_curve.setData(df["drawdown"].values)',
            )
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"[补丁] 已修复 pandas 3.x 兼容问题: {os.path.basename(filepath)}")
    except Exception:
        pass


_apply_patches()


from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp

from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar, QMessageBox
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, QThread, Signal
from sync_tushare import TushareSyncer, SyncResult
from vnpy.trader.signal_bridge import SignalBridgeEngine

# ============================================================
# 交易网关（按需取消注释，需先 pip install 对应插件）
# ============================================================
# from vnpy_ctp import CtpGateway           # 期货 CTP
# from vnpy_xtp import XtpGateway           # 中泰证券 XTP
# from vnpy_ib import IbGateway             # 盈透证券
# from vnpy_tts import TtsGateway           # TTS 模拟

# ============================================================
# 应用模块（按需取消注释，需先 pip install 对应插件）
# ============================================================
from vnpy_ctastrategy import CtaStrategyApp       # CTA 策略
from vnpy_ctabacktester import CtaBacktesterApp   # CTA 回测
# from vnpy_datamanager import DataManagerApp        # 数据管理
# from vnpy_spreadtrading import SpreadTradingApp    # 价差交易
# from vnpy_algotrading import AlgoTradingApp        # 算法交易
# from vnpy_riskmanager import RiskManagerApp        # 风险管理
# from vnpy_datarecorder import DataRecorderApp      # 行情记录


# ============================================================
# 后台同步线程
# ============================================================
class SyncWorker(QThread):
    """后台线程：执行 Tushare 增量同步"""
    progress = Signal(int, int, str)     # (current, total, message)
    finished = Signal(object)            # SyncResult
    error = Signal(str)

    def __init__(self, start_date=None, end_date=None):
        super().__init__()
        self.start_date = start_date
        self.end_date = end_date
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            syncer = TushareSyncer()
            syncer.connect()
            try:
                result = syncer.sync_daily(
                    start_date=self.start_date,
                    end_date=self.end_date,
                    progress_callback=self._on_progress,
                )
                self.finished.emit(result)
            finally:
                syncer.close()
        except Exception as e:
            self.error.emit(str(e))

    def _on_progress(self, current, total, message):
        if not self._is_cancelled:
            self.progress.emit(current, total, message)


# ============================================================
# 同步进度对话框
# ============================================================
class SyncProgressDialog(QDialog):
    """同步进度对话框"""

    def __init__(self, parent=None, cancellable=True):
        super().__init__(parent)
        self.setWindowTitle("行情数据同步")
        self.setMinimumWidth(420)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        self.label = QLabel("正在同步行情数据...")
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)

        layout.addWidget(self.label)
        layout.addWidget(self.progress_bar)

        if cancellable:
            from PySide6.QtWidgets import QPushButton
            self.cancel_btn = QPushButton("取消")
            layout.addWidget(self.cancel_btn, alignment=Qt.AlignRight)
            self.cancel_btn.clicked.connect(self.reject)
        else:
            self.setModal(True)

    def update_progress(self, current, total, message):
        self.label.setText(message)
        if total > 0:
            self.progress_bar.setValue(int(current / total * 100))

    def closeEvent(self, event):
        event.accept()


# ============================================================
# 主窗口扩展：添加同步菜单
# ============================================================
class VnpyMainWindow(MainWindow):
    """扩展 MainWindow，增加行情同步菜单"""

    def __init__(self, main_engine, event_engine):
        super().__init__(main_engine, event_engine)
        self._sync_worker = None
        self._sync_dialog = None
        self._add_sync_menu()

    def _add_sync_menu(self):
        menubar = self.menuBar()
        sync_menu = menubar.addMenu("系统(&S)")
        sync_action = QAction("同步行情数据", self)
        sync_action.setShortcut("Ctrl+Shift+S")
        sync_action.triggered.connect(self._start_manual_sync)
        sync_menu.addAction(sync_action)

    # ---------- 手动同步（菜单触发） ----------
    def _start_manual_sync(self):
        if self._sync_worker and self._sync_worker.isRunning():
            QMessageBox.warning(self, "提示", "同步正在进行中，请稍后再试。")
            return

        self._sync_dialog = SyncProgressDialog(self, cancellable=True)
        self._sync_worker = SyncWorker()
        self._sync_worker.progress.connect(self._sync_dialog.update_progress)
        self._sync_worker.finished.connect(self._on_manual_sync_finished)
        self._sync_worker.error.connect(self._on_sync_error)
        self._sync_dialog.cancel_btn.clicked.disconnect()
        self._sync_dialog.cancel_btn.clicked.connect(self._cancel_sync)
        self._sync_dialog.show()
        self._sync_worker.start()

    def _on_manual_sync_finished(self, result: SyncResult):
        if self._sync_dialog:
            self._sync_dialog.close()
            self._sync_dialog = None
        if result.success and result.count > 0:
            QMessageBox.information(
                self, "同步完成",
                f"同步完成！\n"
                f"写入 {result.count} 条记录\n"
                f"范围：{result.start_date} ~ {result.end_date}",
            )
        elif result.count == 0:
            QMessageBox.information(self, "同步完成", "数据已是最新，无需同步。")

    def _on_sync_error(self, error_msg):
        if self._sync_dialog:
            self._sync_dialog.close()
            self._sync_dialog = None
        QMessageBox.critical(self, "同步失败", f"同步出错：\n{error_msg}")

    def _cancel_sync(self):
        if self._sync_worker and self._sync_worker.isRunning():
            self._sync_worker.cancel()
            self._sync_worker.terminate()
        if self._sync_dialog:
            self._sync_dialog.close()
            self._sync_dialog = None


# ============================================================
# 启动时自动增量同步
# ============================================================
def _startup_sync(qapp):
    """在显示主窗口前执行增量同步（带进度对话框）"""
    dialog = SyncProgressDialog(cancellable=False)
    dialog.show()

    worker = SyncWorker()  # start_date=None → 自动增量
    worker.progress.connect(dialog.update_progress)

    result_holder = [None]

    def on_finished(result):
        result_holder[0] = result
        dialog.close()

    def on_error(msg):
        result_holder[0] = SyncResult(success=False, error=msg)
        dialog.close()

    worker.finished.connect(on_finished)
    worker.error.connect(on_error)
    worker.start()

    qapp.exec()  # 阻塞直到 dialog.close()
    return result_holder[0]


# ============================================================
# 主入口
# ============================================================
def main():
    qapp = create_qapp()
    qapp.setStyle("Fusion")

    # 确保 F:\system\vnpy 在 sys.path，自定义策略目录才能被 import
    import sys
    from pathlib import Path
    _script_dir = str(Path(__file__).resolve().parent)
    if _script_dir not in sys.path:
        sys.path.insert(0, _script_dir)

    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)

    # ---- 添加交易网关 ----
    # main_engine.add_gateway(CtpGateway)
    # main_engine.add_gateway(XtpGateway)

    # ---- 添加应用模块 ----
    cta_engine = main_engine.add_app(CtaStrategyApp)
    backtester_engine = main_engine.add_app(CtaBacktesterApp)
    # main_engine.add_app(DataManagerApp)

    # 加载信号桥接引擎
    main_engine.add_engine(SignalBridgeEngine)

    # 显式加载自定义策略目录（解决 Path.cwd() 与脚本目录不一致的问题）
    from pathlib import Path
    custom_strategy_path = Path(__file__).parent.joinpath("strategies")
    cta_engine.load_strategy_class_from_folder(custom_strategy_path, "strategies")
    backtester_engine.load_strategy_class_from_folder(custom_strategy_path, "strategies")

    # 启动时自动增量同步（后台线程 + 进度对话框）
    print("正在检查行情数据更新...")
    sync_result = _startup_sync(qapp)
    if sync_result and sync_result.success and sync_result.count > 0:
        print(f"行情同步完成：{sync_result.count} 条 ({sync_result.start_date} ~ {sync_result.end_date})")

    # 显示主窗口
    main_window = VnpyMainWindow(main_engine, event_engine)
    main_window.showMaximized()
    qapp.exec()


if __name__ == "__main__":
    main()
