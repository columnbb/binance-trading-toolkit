"""共用 Binance USDⓈ-M 合約私有 API 客戶端。"""

from .client import (
    BinanceAPIError,
    BinanceConfig,
    BinanceFuturesClient,
    OrderResult,
    extract_filters,
    round_to_step,
)
from .ledger import TradeLedger
from .report import generate_report, load_events
from .smoke_test import SmokeTestAborted, quantity_for_notional, run_smoke_test

__version__ = "0.2.0"

__all__ = [
    "BinanceFuturesClient", "BinanceConfig", "BinanceAPIError", "OrderResult",
    "extract_filters", "round_to_step",
    "TradeLedger",
    "generate_report", "load_events",
    "run_smoke_test", "SmokeTestAborted", "quantity_for_notional",
]
