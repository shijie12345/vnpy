"""
vnpy 启动脚本
用法：先激活 venv，然后运行 python start.py
"""

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp

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
# from vnpy_ctastrategy import CtaStrategyApp       # CTA 策略
# from vnpy_ctabacktester import CtaBacktesterApp   # CTA 回测
# from vnpy_datamanager import DataManagerApp        # 数据管理
# from vnpy_spreadtrading import SpreadTradingApp    # 价差交易
# from vnpy_algotrading import AlgoTradingApp        # 算法交易
# from vnpy_riskmanager import RiskManagerApp        # 风险管理
# from vnpy_datarecorder import DataRecorderApp      # 行情记录


def main():
    qapp = create_qapp()
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)

    # ---- 添加交易网关 ----
    # main_engine.add_gateway(CtpGateway)
    # main_engine.add_gateway(XtpGateway)

    # ---- 添加应用模块 ----
    # main_engine.add_app(CtaStrategyApp)
    # main_engine.add_app(CtaBacktesterApp)
    # main_engine.add_app(DataManagerApp)

    main_window = MainWindow(main_engine, event_engine)
    main_window.showMaximized()
    qapp.exec()


if __name__ == "__main__":
    main()
