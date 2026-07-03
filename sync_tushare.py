"""
Tushare -> vnpy 行情数据同步工具

功能：
  1. 首次导入：将指定日期范围的 A 股全市场日线写入 vnpy 的 dbbardata 表
  2. 增量同步：自动检测数据库已有最新日期，只拉取增量数据
  3. 自动更新 dbbaroverview 汇总表

用法：
  激活 venv 后运行：
    python sync_tushare.py                     # 增量同步（从已有最新日到今天）
    python sync_tushare.py 20260601            # 指定起始日期
    python sync_tushare.py 20260601 20260703   # 指定起始和结束日期
"""

import sys
import time
from datetime import datetime, date

import pymysql
import tushare as ts
from tqdm import tqdm

# =============================================================================
# 配置（请勿将此文件提交到公开仓库，token 属于个人隐私）
# =============================================================================
TUSHARE_TOKEN = "6ed90dfb682ab72d151686e6c1c0efe029270f732db6da9916c2d6d5"

MYSQL_HOST = "127.0.0.1"
MYSQL_PORT = 3306
MYSQL_USER = "root"
MYSQL_PASSWORD = "root"
MYSQL_DB = "vnpy"

# 交易所映射：Tushare -> vnpy Exchange 枚举值
EXCHANGE_MAP = {
    "SH": "SSE",      # 上海证券交易所
    "SZ": "SZSE",     # 深圳证券交易所
    "BJ": "BSE",      # 北京证券交易所
}

# 每次 insert 的批次大小
BATCH_SIZE = 500


# =============================================================================
# 数据库操作
# =============================================================================
def get_connection():
    """获取 MySQL 连接"""
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        charset="utf8mb4",
    )


def ensure_tables(conn):
    """确保 vnpy 所需的数据表存在"""
    with conn.cursor() as cursor:
        # dbbardata - K线数据
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

        # dbtickdata - 逐笔行情
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
                bid_price_2 DOUBLE,
                bid_price_3 DOUBLE,
                bid_price_4 DOUBLE,
                bid_price_5 DOUBLE,
                ask_price_1 DOUBLE NOT NULL,
                ask_price_2 DOUBLE,
                ask_price_3 DOUBLE,
                ask_price_4 DOUBLE,
                ask_price_5 DOUBLE,
                bid_volume_1 DOUBLE NOT NULL,
                bid_volume_2 DOUBLE,
                bid_volume_3 DOUBLE,
                bid_volume_4 DOUBLE,
                bid_volume_5 DOUBLE,
                ask_volume_1 DOUBLE NOT NULL,
                ask_volume_2 DOUBLE,
                ask_volume_3 DOUBLE,
                ask_volume_4 DOUBLE,
                ask_volume_5 DOUBLE,
                `localtime` DATETIME(3),
                UNIQUE KEY uk_tick (symbol, exchange, datetime)
            )
        """)

        # dbbaroverview - K线概览
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

        # dbtickoverview - Tick 概览
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

    conn.commit()


def get_latest_date(conn):
    """查询 dbbardata 中日线的最新日期（用于增量同步）"""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT MAX(datetime) FROM dbbardata WHERE `interval` = 'd'"
        )
        result = cursor.fetchone()
        if result and result[0]:
            return result[0].strftime("%Y%m%d")
    return None


def insert_bars(conn, data_list):
    """批量写入 K 线数据（REPLACE INTO 实现 upsert）"""
    if not data_list:
        return

    sql = """
        REPLACE INTO dbbardata
            (symbol, exchange, datetime, `interval`,
             open_price, high_price, low_price, close_price,
             volume, turnover, open_interest)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    with conn.cursor() as cursor:
        for i in range(0, len(data_list), BATCH_SIZE):
            batch = data_list[i : i + BATCH_SIZE]
            cursor.executemany(sql, batch)
    conn.commit()


def update_bar_overview(conn, symbol, exchange, start_dt, end_dt, count):
    """更新单个股票的 dbbaroverview 汇总"""
    sql = """
        INSERT INTO dbbaroverview (symbol, exchange, `interval`, count, start, end)
        VALUES (%s, %s, 'd', %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            count = VALUES(count),
            start = VALUES(start),
            end = VALUES(end)
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (symbol, exchange, count, start_dt, end_dt))
    conn.commit()


# =============================================================================
# Tushare 数据获取
# =============================================================================
def get_stock_list(pro):
    """获取 A 股全部股票列表"""
    df = pro.stock_basic(
        exchange="",
        list_status="L",
        fields="ts_code,symbol,exchange",
    )
    # 只保留沪深北三市的 A 股
    df = df[df["exchange"].isin(EXCHANGE_MAP.keys())]
    return df


def fetch_daily_by_date(pro, trade_date):
    """按日期获取全市场日线数据"""
    df = pro.daily(trade_date=trade_date)
    return df


# =============================================================================
# 主流程
# =============================================================================
def sync(start_date, end_date=None):
    """
    同步入口

    Args:
        start_date: 起始日期，格式 YYYYMMDD
        end_date:   结束日期，格式 YYYYMMDD，默认今天
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    start_dt = datetime.strptime(start_date, "%Y%m%d")
    end_dt = datetime.strptime(end_date, "%Y%m%d")

    print(f"=== Tushare -> vnpy 日线数据同步 ===")
    print(f"数据范围: {start_date} ~ {end_date}")

    # 初始化
    conn = get_connection()
    ensure_tables(conn)
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()

    # 获取股票列表
    print("获取 A 股股票列表...")
    stock_list = get_stock_list(pro)
    print(f"共 {len(stock_list)} 只股票")

    # 获取交易日列表（只处理区间内的交易日）
    print("获取交易日历...")
    cal = pro.trade_cal(
        exchange="SSE",
        start_date=start_date,
        end_date=end_date,
        is_open="1",
    )
    trade_dates = sorted(cal["cal_date"].tolist())

    if not trade_dates:
        print("该日期范围内没有交易日")
        conn.close()
        return

    print(f"共 {len(trade_dates)} 个交易日需要处理\n")

    total_bars = 0
    failed_dates = []
    t0 = time.time()

    for trade_date in tqdm(trade_dates, desc="同步进度"):
        try:
            df = fetch_daily_by_date(pro, trade_date)

            if df is None or df.empty:
                tqdm.write(f"  {trade_date}: 无数据")
                time.sleep(0.3)
                continue

            dt = datetime.strptime(trade_date, "%Y%m%d")

            # 构建写入数据
            data_list = []
            for _, row in df.iterrows():
                ts_code = row["ts_code"]
                symbol = ts_code[:6]
                exchange_key = ts_code[-2:]
                exchange = EXCHANGE_MAP.get(exchange_key)

                if exchange is None:
                    continue

                data_list.append((
                    symbol,                                     # symbol
                    exchange,                                   # exchange
                    dt,                                         # datetime
                    "d",                                        # interval = 日线
                    float(row["open"]),                         # open_price
                    float(row["high"]),                         # high_price
                    float(row["low"]),                          # low_price
                    float(row["close"]),                        # close_price
                    float(row["vol"]),                          # volume（手）
                    float(row["amount"]) * 1000,                # turnover（元，tushare 返回千元）
                    0.0,                                        # open_interest（股票无持仓量）
                ))

            if data_list:
                insert_bars(conn, data_list)
                total_bars += len(data_list)

            tqdm.write(f"  {trade_date}: {len(data_list)} 条")

            # Tushare 频率控制
            time.sleep(0.35)

        except Exception as e:
            tqdm.write(f"  {trade_date}: 错误 - {e}")
            failed_dates.append(trade_date)
            time.sleep(1)

    # 更新 dbbaroverview 汇总
    print("\n更新 K 线概览表...")
    overview_sql = """
        SELECT symbol, exchange, COUNT(*) as cnt, MIN(datetime) as s, MAX(datetime) as e
        FROM dbbardata
        WHERE `interval` = 'd'
        GROUP BY symbol, exchange
    """
    with conn.cursor() as cursor:
        cursor.execute(overview_sql)
        rows = cursor.fetchall()
        for row in rows:
            update_bar_overview(conn, row[0], row[1], row[3], row[4], row[2])
    print(f"已更新 {len(rows)} 只股票的概览数据")

    elapsed = time.time() - t0

    print(f"\n=== 同步完成 ===")
    print(f"写入 K 线: {total_bars} 条")
    print(f"耗时: {elapsed:.1f} 秒")
    if failed_dates:
        print(f"失败日期 ({len(failed_dates)} 个): {', '.join(failed_dates)}")
        print("可重新运行脚本重试失败日期（增量模式会自动跳过已有数据）")

    conn.close()


if __name__ == "__main__":
    start = None
    end = None

    if len(sys.argv) >= 2:
        start = sys.argv[1]
    if len(sys.argv) >= 3:
        end = sys.argv[2]

    # 增量同步：没有指定起始日期时，自动从数据库最新日期开始
    if start is None:
        conn = get_connection()
        ensure_tables(conn)
        latest = get_latest_date(conn)
        conn.close()

        if latest:
            start = latest
            print(f"[增量模式] 数据库最新日期: {latest}，从此日期开始同步")
        else:
            start = "20260101"
            print(f"[首次导入] 数据库为空，从 {start} 开始同步")

    sync(start, end)
