"""
Tushare 行情数据同步模块

支持增量同步：自动检测数据库最新日期，只拉取增量数据。
可独立运行：python sync_tushare.py [start_date] [end_date]
也可作为模块导入：from sync_tushare import TushareSyncer, SyncResult
"""

import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import pymysql
import tushare as ts

# ============================================================
# 配置（请勿将此文件提交到公开仓库，token 属于个人隐私）
# ============================================================
TUSHARE_TOKEN = "6ed90dfb682ab72d151686e6c1c0efe029270f732db6da9916c2d6d5"

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "root",
    "database": "vnpy",
    "charset": "utf8mb4",
}

# Tushare 交易所代码 → vnpy Exchange 枚举值
EXCHANGE_MAP = {
    "SSE": "SSE",
    "SZSE": "SZSE",
    "BSE": "BSE",
}

BATCH_SIZE = 500


@dataclass
class SyncResult:
    """同步结果"""
    success: bool
    count: int = 0
    days: int = 0
    start_date: str = ""
    end_date: str = ""
    error: str = ""


class TushareSyncer:
    """Tushare 行情数据同步器"""

    def __init__(self, db_config: dict = None, token: str = None):
        self.db_config = db_config or DB_CONFIG
        self.token = token or TUSHARE_TOKEN
        self.conn = None

    def connect(self):
        self.conn = pymysql.connect(**self.db_config)

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def get_last_sync_date(self) -> str | None:
        """获取 dbbardata 中日线的最新日期"""
        with self.conn.cursor() as cursor:
            cursor.execute(
                "SELECT MAX(datetime) FROM dbbardata WHERE `interval` = 'd'"
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0].strftime("%Y%m%d")
        return None

    def _ensure_tables(self):
        """确保 vnpy 数据表存在"""
        with self.conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS dbbardata (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(255) NOT NULL,
                    exchange VARCHAR(255) NOT NULL,
                    datetime DATETIME NOT NULL,
                    `interval` VARCHAR(255) NOT NULL,
                    volume DOUBLE NOT NULL,
                    turnover DOUBLE NOT NULL,
                    open_interest DOUBLE NOT NULL,
                    open_price DOUBLE NOT NULL,
                    high_price DOUBLE NOT NULL,
                    low_price DOUBLE NOT NULL,
                    close_price DOUBLE NOT NULL,
                    UNIQUE KEY uk_bar (symbol, exchange, `interval`, datetime)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS dbtickdata (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(255) NOT NULL,
                    exchange VARCHAR(255) NOT NULL,
                    datetime DATETIME(3) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    volume DOUBLE NOT NULL,
                    turnover DOUBLE NOT NULL,
                    open_interest DOUBLE NOT NULL,
                    last_price DOUBLE NOT NULL,
                    last_volume DOUBLE NOT NULL,
                    limit_up DOUBLE NOT NULL,
                    limit_down DOUBLE NOT NULL,
                    open_price DOUBLE NOT NULL,
                    high_price DOUBLE NOT NULL,
                    low_price DOUBLE NOT NULL,
                    pre_close DOUBLE NOT NULL,
                    bid_price_1 DOUBLE NOT NULL,
                    bid_price_2 DOUBLE, bid_price_3 DOUBLE,
                    bid_price_4 DOUBLE, bid_price_5 DOUBLE,
                    ask_price_1 DOUBLE NOT NULL,
                    ask_price_2 DOUBLE, ask_price_3 DOUBLE,
                    ask_price_4 DOUBLE, ask_price_5 DOUBLE,
                    bid_volume_1 DOUBLE NOT NULL,
                    bid_volume_2 DOUBLE, bid_volume_3 DOUBLE,
                    bid_volume_4 DOUBLE, bid_volume_5 DOUBLE,
                    ask_volume_1 DOUBLE NOT NULL,
                    ask_volume_2 DOUBLE, ask_volume_3 DOUBLE,
                    ask_volume_4 DOUBLE, ask_volume_5 DOUBLE,
                    `localtime` DATETIME(3),
                    UNIQUE KEY uk_tick (symbol, exchange, datetime)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS dbbaroverview (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(255) NOT NULL,
                    exchange VARCHAR(255) NOT NULL,
                    `interval` VARCHAR(255) NOT NULL,
                    count INT NOT NULL,
                    start DATETIME NOT NULL,
                    end DATETIME NOT NULL,
                    UNIQUE KEY uk_bar_ov (symbol, exchange, `interval`)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS dbtickoverview (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(255) NOT NULL,
                    exchange VARCHAR(255) NOT NULL,
                    count INT NOT NULL,
                    start DATETIME NOT NULL,
                    end DATETIME NOT NULL,
                    UNIQUE KEY uk_tick_ov (symbol, exchange)
                )
            """)
        self.conn.commit()

    def _insert_bars(self, data: list):
        if not data:
            return
        sql = """
            REPLACE INTO dbbardata
                (symbol, exchange, datetime, `interval`,
                 open_price, high_price, low_price, close_price,
                 volume, turnover, open_interest)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        with self.conn.cursor() as cursor:
            for i in range(0, len(data), BATCH_SIZE):
                cursor.executemany(sql, data[i:i + BATCH_SIZE])
        self.conn.commit()

    def _update_overview(self, symbol, exchange, start_dt, end_dt, count):
        sql = """
            INSERT INTO dbbaroverview (symbol, exchange, `interval`, count, start, end)
            VALUES (%s, %s, 'd', %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                count = VALUES(count), start = VALUES(start), end = VALUES(end)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (symbol, exchange, count, start_dt, end_dt))
        self.conn.commit()

    def sync_daily(self, start_date=None, end_date=None, progress_callback=None):
        """同步 A 股日线数据"""
        self._ensure_tables()
        ts.set_token(self.token)
        pro = ts.pro_api()

        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")

        if start_date is None:
            last = self.get_last_sync_date()
            if last:
                start_date = (
                    datetime.strptime(last, "%Y%m%d") + timedelta(days=1)
                ).strftime("%Y%m%d")
            else:
                start_date = "20260101"

        start_dt = datetime.strptime(start_date, "%Y%m%d")
        end_dt = datetime.strptime(end_date, "%Y%m%d")

        if start_dt > end_dt:
            return SyncResult(success=True, count=0, start_date=start_date, end_date=end_date)

        cal = pro.trade_cal(
            exchange="SSE", start_date=start_date, end_date=end_date, is_open="1"
        )
        trade_dates = sorted(cal["cal_date"].tolist())
        if not trade_dates:
            return SyncResult(success=True, count=0, start_date=start_date, end_date=end_date)

        total_bars = 0
        total = len(trade_dates)

        for i, td in enumerate(trade_dates):
            try:
                df = pro.daily(trade_date=td)
                if df is None or df.empty:
                    if progress_callback:
                        progress_callback(i + 1, total, f"{td}: 无数据")
                    continue

                dt = datetime.strptime(td, "%Y%m%d")
                data = []
                for _, row in df.iterrows():
                    ts_code = row["ts_code"]
                    symbol = ts_code[:6]
                    exchange = ts_code.split(".")[-1]
                    if exchange not in EXCHANGE_MAP:
                        continue
                    data.append((
                        symbol, EXCHANGE_MAP[exchange], dt, "d",
                        float(row["open"]), float(row["high"]),
                        float(row["low"]), float(row["close"]),
                        float(row["vol"]), float(row["amount"]) * 1000, 0.0,
                    ))

                self._insert_bars(data)

                for ts_code in df["ts_code"].unique():
                    sym = ts_code[:6]
                    exc = ts_code.split(".")[-1]
                    if exc not in EXCHANGE_MAP:
                        continue
                    sdf = df[df["ts_code"] == ts_code]
                    s_dt = datetime.strptime(sdf["trade_date"].min(), "%Y%m%d")
                    e_dt = datetime.strptime(sdf["trade_date"].max(), "%Y%m%d")
                    self._update_overview(sym, EXCHANGE_MAP[exc], s_dt, e_dt, len(sdf))

                total_bars += len(data)
                if progress_callback:
                    progress_callback(i + 1, total, f"{td}: {len(data)} 条")

                time.sleep(0.35)

            except Exception as e:
                if progress_callback:
                    progress_callback(i + 1, total, f"{td}: 错误 - {e}")
                time.sleep(1)

        return SyncResult(
            success=True, count=total_bars, days=total,
            start_date=start_date, end_date=end_date,
        )


def run_incremental_sync(start_date=None, end_date=None, progress_callback=None):
    """快捷函数"""
    syncer = TushareSyncer()
    syncer.connect()
    try:
        return syncer.sync_daily(start_date, end_date, progress_callback)
    finally:
        syncer.close()


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else None
    end = sys.argv[2] if len(sys.argv) > 2 else None

    syncer = TushareSyncer()
    syncer.connect()
    try:
        last = syncer.get_last_sync_date()
        if start is None and last:
            print(f"[增量模式] 数据库最新日期: {last}")
        elif start is None:
            print("[首次导入] 数据库为空")

        result = syncer.sync_daily(
            start, end,
            progress_callback=lambda c, t, m: print(f"  [{c}/{t}] {m}"),
        )
        print(f"\n完成: {result.count} 条, {result.days} 个交易日")
        print(f"范围: {result.start_date} ~ {result.end_date}")
        if result.error:
            print(f"错误: {result.error}")
    finally:
        syncer.close()
