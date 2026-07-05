"""
双均线日线策略 + SignalBridge 信号输出

依赖：
    pip install pymysql

文件位置：
    F:/system/vnpy/strategies/dual_ma_signal_strategy.py
"""

from vnpy.trader.constant import Interval
from vnpy_ctastrategy import CtaTemplate, BarData, ArrayManager


class DualMaSignalStrategy(CtaTemplate):
    """双均线日线策略 — 信号写 MySQL，Java 系统执行"""

    author = "SignalBridge Demo"
    fast_window: int = 10
    slow_window: int = 20
    fast_ma: float = 0.0
    slow_ma: float = 0.0
    parameters = ["fast_window", "slow_window"]
    variables = ["fast_ma", "slow_ma"]

    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.am = ArrayManager()
        self.bridge = cta_engine.main_engine.get_engine("signal_bridge")

    def on_init(self):
        self.write_log("策略初始化，从数据库加载日线...")
        self.load_bar(50, interval=Interval.DAILY, use_database=True)  # 加载 50 天日 K

    def on_start(self):
        self.write_log("策略启动")

    def on_stop(self):
        self.write_log("策略停止")

    def on_bar(self, bar: BarData):
        self.am.update_bar(bar)
        if not self.am.inited:
            return

        fast_ma = self.am.sma(self.fast_window, array=True)
        slow_ma = self.am.sma(self.slow_window, array=True)
        if fast_ma is None or slow_ma is None:
            return

        fast_ma0, fast_ma1 = float(fast_ma[-1]), float(fast_ma[-2])
        slow_ma0, slow_ma1 = float(slow_ma[-1]), float(slow_ma[-2])

        cross_above = fast_ma0 > slow_ma0 and fast_ma1 <= slow_ma1
        cross_below = fast_ma0 < slow_ma0 and fast_ma1 >= slow_ma1

        if cross_above:
            self._emit("BUY", "MA_CROSS_GOLD", bar)
        elif cross_below:
            self._emit("SELL", "MA_CROSS_DEAD", bar)

        self.fast_ma = fast_ma0
        self.slow_ma = slow_ma0
        self.put_event()

    def _emit(self, direction: str, signal_type: str, bar: BarData):
        if self.bridge is None:
            self.write_log("SignalBridge 未加载，跳过")
            return
        signal_id = self.bridge.write_signal(
            vt_symbol=self.vt_symbol,
            direction=direction,
            signal_type=signal_type,
            price=bar.close_price,
            volume=100,
            strategy_name=self.strategy_name,
        )
        if signal_id:
            self.write_log(f"信号已写入 id={signal_id} {direction} {signal_type} 价={bar.close_price}")

    def on_order(self, order): pass
    def on_trade(self, trade): pass
    def on_stop_order(self, stop_order): pass
