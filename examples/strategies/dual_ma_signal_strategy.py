"""
示范策略：双均线交叉 + SignalBridge 直接调用模式

不依赖券商网关，策略检测到信号后直接调用 bridge.write_signal()
写入 MySQL trading_signals 表，供 Java 交易系统读取执行。

用法：
    将此文件放入 F:\system\vnpy\strategies\
    vnpy 启动时会自动扫描并注册此策略。
"""

from vnpy_ctastrategy import (
    CtaTemplate,
    BarData,
    BarGenerator,
    ArrayManager,
)


class DualMaSignalStrategy(CtaTemplate):
    """双均线交叉策略 — 信号→MySQL→Java 系统执行"""

    author = "SignalBridge Demo"

    fast_window: int = 10
    slow_window: int = 20

    fast_ma: float = 0.0
    slow_ma: float = 0.0

    parameters = ["fast_window", "slow_window"]
    variables = ["fast_ma", "slow_ma"]

    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.bg = BarGenerator(self.on_bar)
        self.am = ArrayManager()
        self.bridge = cta_engine.main_engine.get_engine("signal_bridge")

    def on_init(self):
        self.write_log("策略初始化")
        self.load_bar(10)

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

        fast_ma0, fast_ma1 = fast_ma[-1], fast_ma[-2]
        slow_ma0, slow_ma1 = slow_ma[-1], slow_ma[-2]

        cross_above = (fast_ma0 > slow_ma0) and (fast_ma1 <= slow_ma1)
        cross_below = (fast_ma0 < slow_ma0) and (fast_ma1 >= slow_ma1)

        if cross_above:
            self._emit("BUY", "MA_CROSS_GOLD", bar)
        elif cross_below:
            self._emit("SELL", "MA_CROSS_DEAD", bar)

        self.fast_ma = fast_ma0
        self.slow_ma = slow_ma0
        self.put_event()

    def _emit(self, direction: str, signal_type: str, bar: BarData):
        """写信号到 MySQL"""
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
            self.write_log(f"信号已发出 id={signal_id} {direction} 价={bar.close_price}")

    # ———— 占位方法 ————
    def on_order(self, order): pass
    def on_trade(self, trade): pass
    def on_stop_order(self, stop_order): pass
