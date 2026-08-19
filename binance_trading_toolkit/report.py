"""把 TradeLedger 寫的 JSONL 帳本轉成人看得懂的 Markdown 審計報表。

跟 ``mexc-futures-toolkit`` 的 ``report.py`` 是同一份設計，差別只在少了
「本地重算 vs 交易所回報損益」的差額欄位——Binance 的下單回應目前沒有
觀察到像 MEXC 那樣直接給 ``profit`` 欄位，所以這裡的損益完全是本地帳本
自己算的，沒有交易所端數字可以對照。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_events(path: str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    events = []
    for line_number, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"帳本第 {line_number} 行不是合法 JSON（{p}）: {exc}") from exc
    return events


def fmt(value: Any, digits: int = 6) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def generate_report(events: list[dict[str, Any]], generated_at: str | None = None) -> str:
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    closes = [e for e in events if e.get("event_type") == "trade_close"]
    orders = [e for e in events if e.get("event_type") in {"order_attempt", "order_result"}]
    failures = [e for e in events if e.get("event_type") == "order_result" and e.get("status") == "failed"]
    dry_runs = [e for e in events if e.get("event_type") == "order_result" and e.get("status") == "dry_run_skipped"]
    net_pnl = sum(float(e.get("net_pnl") or 0) for e in closes)
    gross_pnl = sum(float(e.get("gross_pnl") or 0) for e in closes)
    fees = sum(float(e.get("total_fees") or 0) for e in closes)
    wins = sum(1 for e in closes if float(e.get("net_pnl") or 0) > 0)
    losses = sum(1 for e in closes if float(e.get("net_pnl") or 0) < 0)
    status_counts = Counter(str(e.get("status")) for e in failures + dry_runs)

    lines = [
        "# Binance 交易審計報表",
        "",
        f"> 報表產生時間（UTC）：{generated_at}",
        "",
        "## 摘要",
        "",
        "| 指標 | 數值 |",
        "|---|---:|",
        f"| 帳本事件數 | {len(events)} |",
        f"| 訂單審計事件數 | {len(orders)} |",
        f"| 已完成交易數 | {len(closes)} |",
        f"| 獲利交易數 | {wins} |",
        f"| 虧損交易數 | {losses} |",
        f"| 勝率 | {fmt(wins / len(closes) if closes else None, 4)} |",
        f"| 毛損益（USDT） | {fmt(gross_pnl)} |",
        f"| 總手續費（USDT） | {fmt(fees)} |",
        f"| 淨損益（USDT） | **{fmt(net_pnl)}** |",
        f"| 下單失敗事件 | {len(failures)} |",
        f"| dry-run 未送單事件 | {len(dry_runs)} |",
        "",
        "## 每筆已完成交易",
        "",
        "| Trade ID | Symbol | 進場價 | 出場價 | 數量 | 淨損益 | 來源 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for event in closes:
        lines.append(
            "| {trade_id} | {symbol} | {entry_price} | {exit_price} | {volume} | {net_pnl} | {source} |".format(
                trade_id=str(event.get("trade_id", ""))[:12],
                symbol=event.get("symbol", ""),
                entry_price=fmt(event.get("entry_price")),
                exit_price=fmt(event.get("exit_price")),
                volume=fmt(event.get("exit_volume")),
                net_pnl=fmt(event.get("net_pnl")),
                source=event.get("source", ""),
            )
        )
    if not closes:
        lines.append("| — | 尚無已完成交易 | — | — | — | — | — |")

    lines += [
        "",
        "## 訂單失敗與 dry-run 檢查",
        "",
        "| 狀態 | 次數 |",
        "|---|---:|",
    ]
    for status, count in sorted(status_counts.items()):
        lines.append(f"| {status} | {count} |")
    if not status_counts:
        lines.append("| 尚無失敗或 dry-run 訂單事件 | 0 |")

    lines += [
        "",
        "## 審計解讀",
        "",
        "本報表的損益由本地帳本以多單公式 `(出場價 - 進場價) × 成交量 × contract_size - 手續費` 重算。Binance 的下單回應目前沒有觀察到像 MEXC 那樣直接帶回 `profit` 欄位，所以這裡沒有「本地重算 vs 交易所回報」的對照差額——實際成交價/量建議另外用 `open_positions()`/`ticker_price()` 查證，不要完全信任下單回應裡的 `avgPrice`/`executedQty`（測試網上觀察到這兩個欄位常常是空的）。",
        "",
        "帳本採 JSONL append-only 格式，不得把 API Secret、完整帳戶資產或其他敏感資料寫入帳本。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="產生 Binance 交易審計報表")
    parser.add_argument("--ledger", default="audit/trading_ledger.jsonl")
    parser.add_argument("--output", default="audit/trading_report.md")
    parser.add_argument("--print", dest="print_report", action="store_true",
                         help="除了寫檔，也把報表印到終端機")
    args = parser.parse_args()
    report = generate_report(load_events(args.ledger))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report + "\n", encoding="utf-8")
    print(output)
    if args.print_report:
        print()
        print(report)


if __name__ == "__main__":
    main()
