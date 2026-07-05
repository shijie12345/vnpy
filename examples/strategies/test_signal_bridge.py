"""
SignalBridge 独立测试脚本

不依赖 vnpy 完整环境，直接测试 MySQL 连接和信号写入功能。

启动方式：
    cd F:\system\vnpy
    python examples\strategies\test_signal_bridge.py
"""

import sys
import os

# 将 vnpy 根目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 模拟 vnpy 最小运行环境
from unittest.mock import MagicMock


def test():
    """测试信号桥接引擎"""
    from vnpy.trader.signal_bridge import SignalBridgeEngine, _vt_symbol_to_ts_code

    # —— 1. 测试符号转换 ——
    print("=" * 50)
    print("1. 测试 vt_symbol → ts_code 转换")
    print("-" * 50)
    cases = {
        "600000.SSE": "600000.SH",
        "000001.SZSE": "000001.SZ",
        "830799.BSE": "830799.BJ",
        "510050.SSE": "510050.SH",
    }
    for vt, expected in cases.items():
        result = _vt_symbol_to_ts_code(vt)
        status = "✓" if result == expected else "✗"
        print(f"  {status} {vt} → {result} (期望: {expected})")

    # —— 2. 测试 MySQL 连接 ——
    print()
    print("=" * 50)
    print("2. 测试 MySQL 连接")
    print("-" * 50)
    mock_main = MagicMock()
    mock_event = MagicMock()
    try:
        bridge = SignalBridgeEngine(mock_main, mock_event)
        print("  ✓ MySQL 连接成功")
    except Exception as e:
        print(f"  ✗ MySQL 连接失败: {e}")
        print("  请确�?")
        print("    - MySQL 服务已启动 (192.168.77.130:3306)")
        print("    - 数据库 thewolfofwallstreet 已创建")
        print("    - trading_signals 表已创建")
        return

    # —— 3. 测试信号写入 ——
    print()
    print("=" * 50)
    print("3. 测试信号写入")
    print("-" * 50)

    test_signals = [
        {
            "vt_symbol": "600000.SSE",
            "direction": "BUY",
            "signal_type": "MA_CROSS_GOLD",
            "price": 10.50,
            "volume": 100,
            "strategy_name": "TestStrategy",
        },
        {
            "vt_symbol": "000001.SZSE",
            "direction": "SELL",
            "signal_type": "MA_CROSS_DEAD",
            "price": 15.80,
            "volume": 200,
            "strategy_name": "TestStrategy",
        },
    ]

    for i, sig in enumerate(test_signals):
        signal_id = bridge.write_signal(**sig)
        if signal_id:
            print(f"  ✓ 信号{i+1} 写入成功, id={signal_id} "
                  f"{sig['vt_symbol']} {sig['direction']} {sig['signal_type']}")
        else:
            print(f"  ✗ 信号{i+1} 写入失败")

    # —— 4. 验证数据库 ——
    print()
    print("=" * 50)
    print("4. 验证: 查询最新 5 条信号")
    print("-" * 50)
    conn = bridge._ensure_conn()
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT id, ts_code, direction, signal_type, price, remark, created_at "
            "FROM trading_signals ORDER BY id DESC LIMIT 5"
        )
        rows = cursor.fetchall()
        if rows:
            for row in rows:
                print(f"  id={row['id']:>4} | {row['ts_code']:<12} | "
                      f"{row['direction']:<4} | {row['signal_type']:<20} | "
                      f"价={row['price']} | {row['remark']} | {row['created_at']}")
        else:
            print("  (无记�?)")

    bridge.close()
    print()
    print("=" * 50)
    print("测试完成！")


if __name__ == "__main__":
    test()
