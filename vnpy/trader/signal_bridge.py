"""
Signal Bridge Engine — 信号桥接引擎

将 vnpy 策略产生的交易信号写入 MySQL trading_signals 表，
供 Java 交易系统读取并执行（风控 → 下单 → 持仓管理）。

两种模式（同时生效）：

模式B'（推荐，无需网关）— 策略直接调用 write_signal()：
    在策略的 on_bar() 中加 3 行：
        bridge = self.cta_engine.main_engine.get_engine("signal_bridge")
        bridge.write_signal(vt_symbol=..., direction=..., signal_type=..., price=..., volume=...)
    策略代码仅需微调，不依赖券商网关。

模式B（可选，需要网关）— 自动监听 EVENT_ORDER：
    vnpy 策略调用 self.buy()/self.sell() → 网关回报 → 事件引擎广播 → 自动写入 MySQL
    策略代码完全零改动，但必须连接实盘/模拟盘网关。
"""

import logging
from datetime import date

import pymysql
from pymysql.cursors import DictCursor

from vnpy.trader.engine import BaseEngine
from vnpy.trader.event import EVENT_ORDER
from vnpy.trader.constant import Direction, Offset, Status

logger = logging.getLogger(__name__)


# ——————————————————————————————————————————————
# 交易所代码映射：vnpy → Java 系统 ts_code 后缀
# ——————————————————————————————————————————————
_EXCHANGE_MAP: dict[str, str] = {
    "SSE": "SH",      # 上海证券交易所
    "SZSE": "SZ",     # 深圳证券交易所
    "BSE": "BJ",      # 北京证券交易所
}

# vnpy Direction → Java 系统 direction
_DIRECTION_MAP: dict[str, str] = {
    "LONG": "BUY",
    "SHORT": "SELL",
}


def _vt_symbol_to_ts_code(vt_symbol: str) -> str:
    """将 '600000.SSE' 转为 '600000.SH'"""
    if "." not in vt_symbol:
        return vt_symbol
    code, exchange = vt_symbol.rsplit(".", 1)
    mapped = _EXCHANGE_MAP.get(exchange, exchange)
    return f"{code}.{mapped}"


# ——————————————————————————————————————————————
# SignalBridgeEngine
# ——————————————————————————————————————————————


class SignalBridgeEngine(BaseEngine):
    """
    信号桥接引擎。

    模式B'（推荐，无需网关）：
        策略直接调用 bridge.write_signal() 写入 MySQL。
        示例见 dual_ma_signal_strategy.py。

    模式B（可选，需要网关）：
        监听 EVENT_ORDER，自动捕获策略 self.buy()/self.sell() 的订单信号。
        策略代码零改动，但必须连接券商网关。
    """

    # MySQL 连接配置（与 Java 系统 application.yml 一致）
    DB_HOST: str = "192.168.77.130"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = "root"
    DB_NAME: str = "thewolfofwallstreet"

    def __init__(self, main_engine, event_engine):
        super().__init__(main_engine, event_engine, "signal_bridge")
        self._conn: pymysql.Connection | None = None
        self._seen_orderids: set[str] = set()    # 已处理的 orderid（防重）
        self._connect()
        self._register_events()
        logger.info("[SignalBridge] 引擎初始化完成（事件监听已注册）")

    # ——————————————————————————————————————————
    # 数据库连接管理
    # ——————————————————————————————————————————

    def _connect(self) -> pymysql.Connection:
        """连接 MySQL，失败则抛出异常让调用方感知"""
        self._conn = pymysql.connect(
            host=self.DB_HOST,
            port=self.DB_PORT,
            user=self.DB_USER,
            password=self.DB_PASSWORD,
            database=self.DB_NAME,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=True,
        )
        logger.info("[SignalBridge] MySQL 连接成功")
        return self._conn

    def _ensure_conn(self) -> pymysql.Connection:
        """确保连接可用，断线自动重连"""
        if self._conn is None:
            return self._connect()
        try:
            self._conn.ping(reconnect=True)
        except pymysql.Error:
            logger.warning("[SignalBridge] MySQL 连接断开，尝试重连...")
            self._connect()
        return self._conn

    # ——————————————————————————————————————————
    # 模式B：事件监听（自动捕获所有策略订单）
    # ——————————————————————————————————————————

    def _register_events(self) -> None:
        """注册 EVENT_ORDER 监听"""
        self.event_engine.register(EVENT_ORDER, self._on_order_event)
        logger.info("[SignalBridge] 已注册 EVENT_ORDER 监听器")

    def _on_order_event(self, event) -> None:
        """
        EVENT_ORDER 回调：任何策略调用 self.buy()/self.sell() 时触发。
        将订单信息翻译为 trading_signals 记录写入 MySQL。
        """
        order = event.data

        # 仅捕获开仓订单（忽略平仓/撤单）
        if order.offset != Offset.OPEN:
            return

        # 仅捕获新订单（状态为 SUBMITTING），避免重复写入
        if order.status != Status.SUBMITTING:
            return

        # 防重：同一 orderid 只处理一次
        if order.vt_orderid in self._seen_orderids:
            return
        self._seen_orderids.add(order.vt_orderid)

        # 提取方向
        direction: str = (
            order.direction.value if order.direction else ""
        )

        # 信号类型：策略名（如有）| 订单类型
        signal_type: str = order.reference or f"VN_{order.type.value}"

        self.write_signal(
            vt_symbol=order.vt_symbol,
            direction=direction,
            signal_type=signal_type,
            price=order.price,
            volume=order.volume,
            strategy_name=order.reference or "vnpy_auto",
        )

    # ——————————————————————————————————————————
    # 模式A / 底层实现：写出信号
    # ——————————————————————————————————————————

    def write_signal(
        self,
        *,
        vt_symbol: str,
        direction: str,
        signal_type: str,
        price: float = 0.0,
        volume: float = 0,
        strategy_name: str = "",
        freq: str = "D",
        signal_strength: float = 0.0,
        confidence: float = 0.0,
        stop_loss_price: float = 0.0,
        target_price: float = 0.0,
    ) -> int | None:
        """
        将一条交易信号写入 MySQL trading_signals 表。

        参数
        ----
        vt_symbol : str
            vnpy 格式合约代码，如 "600000.SSE"、"000001.SZSE"
        direction : str
            "BUY" / "SELL"，或 vnpy 的 "LONG" / "SHORT"（会自动转换）
        signal_type : str
            信号类型标识，如 "MA_CROSS_GOLD"、"CHANLUN_BUY1"
        price : float
            触发信号时的价格
        volume : float
            建议交易数量（股/手）
        strategy_name : str
            产生信号的策略名称（用于审计追溯）
        freq : str
            信号周期，默认 "D"（日线），可选 "W"/"M"/"60min" 等
        signal_strength : float
            信号强度 0-100
        confidence : float
            置信度 0-1
        stop_loss_price : float
            止损价格
        target_price : float
            目标价格

        返回
        ----
        int or None
            插入行的自增ID；失败返回 None
        """
        ts_code: str = _vt_symbol_to_ts_code(vt_symbol)
        direction_mapped: str = _DIRECTION_MAP.get(direction, direction)

        # 构建备注
        remark_parts: list[str] = [f"strategy={strategy_name}"] if strategy_name else []
        remark_parts.append(f"source=vnpy_signal_bridge")
        remark: str = "; ".join(remark_parts)

        sql: str = """
            INSERT INTO trading_signals
                (ts_code, trade_date, freq, signal_type, direction,
                 is_valid, signal_strength, confidence, price,
                 stop_loss_price, target_price,
                 is_executed, is_cancelled, remark)
            VALUES
                (%s, %s, %s, %s, %s,
                 1, %s, %s, %s,
                 %s, %s,
                 0, 0, %s)
        """

        params = (
            ts_code,
            date.today(),
            freq,
            signal_type,
            direction_mapped,
            signal_strength,
            confidence,
            price,
            stop_loss_price,
            target_price,
            remark,
        )

        try:
            conn = self._ensure_conn()
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                signal_id: int = cursor.lastrowid
            logger.info(
                "[SignalBridge] 信号已写入 id=%s %s %s %s 价=%s 量=%s",
                signal_id, ts_code, direction_mapped, signal_type, price, volume,
            )
            return signal_id
        except pymysql.Error as e:
            logger.error("[SignalBridge] 写入信号失败: %s", e)
            return None

    def close(self) -> None:
        """关闭引擎"""
        self.event_engine.unregister(EVENT_ORDER, self._on_order_event)
        if self._conn:
            try:
                self._conn.close()
            except pymysql.Error:
                pass
            self._conn = None
        logger.info("[SignalBridge] 引擎已关闭")
