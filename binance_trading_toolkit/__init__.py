"""共用 Binance USDⓈ-M 合約私有 API 客戶端。"""

from .client import (
    BinanceAPIError,
    BinanceConfig,
    BinanceFuturesClient,
    OrderResult,
    confirmed_fill,
    extract_filters,
    round_to_step,
)
from .gate1_validation import Gate1ValidationAborted, run_gate1_validation
from .ledger import TradeLedger
from .report import generate_report, load_events
from .smoke_test import SmokeTestAborted, quantity_for_notional, run_smoke_test

__version__ = "0.4.0"

__all__ = [
    "BinanceFuturesClient", "BinanceConfig", "BinanceAPIError", "OrderResult",
    "extract_filters", "round_to_step", "confirmed_fill",
    "TradeLedger",
    "generate_report", "load_events",
    "run_smoke_test", "SmokeTestAborted", "quantity_for_notional",
    "run_gate1_validation", "Gate1ValidationAborted",
]
