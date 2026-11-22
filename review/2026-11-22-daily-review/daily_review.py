# -*- coding: utf-8 -*-
"""每日个股复盘（2026-11-22）：如实复盘「每天买入/推荐的股票」。

覆盖三块：
  1. 今日买入（=今日推荐）逐票复盘：买入价 vs 当日收盘、次日、至今表现
  2. 上一交易日推荐验证：昨天/上个交易日买的票，今天表现如何
  3. 当前持仓复盘：浮盈亏 + 到期日 + 是否逾期

数据：账本 trades（真实成交）+ 通达信本地日线（收盘价，离线）。
产物：_data/daily_review.json + 文本摘要（供 postmortem / 微信推送 / API 使用）
用法：python -X utf8 -m stocklens.daily_review
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .config import ROOT
from .utils import now_str

OUT = ROOT / "_data" / "daily_review.json"
INSTS = ("A", "B", "C")
_bars_cache: dict[str, dict[int, float]] = {}


def _bars(code: str) -> dict[int, float]:
    """通达信本地日线：{YYYYMMDD: 收盘价(元)}。"""
    code = str(code or "")
    if code in _bars_cache:
        return _bars_cache[code]
    try:
        from . import tdx
        arr = tdx.read_stock(code)
        out = {int(d): float(c) for d, c in zip(arr["date"], arr["close"]) if float(c) > 0}
    except Exception:  # noqa: BLE001
        out = {}
    _bars_cache[code] = out
    return out


def _ledgers() -> dict[str, dict[str, Any]]:
    from . import market_view
    out: dict[str, dict[str, Any]] = {}
    for inst in INSTS:
        try:
            out[inst] = market_view.read_ledger(inst) or {}
        except Exception:  # noqa: BLE001
            out[inst] = {}
    return out


def _trading_dates() -> list[int]:
    try:
        from . import tdx
        return sorted(int(x) for x in tdx.index_series("sh000001")["date"])
    except Exception:  # noqa: BLE001
        return []


def _day_trades(led: dict, day: str, action: str) -> list[dict]:
    out = []
    for t in (led.get("trades") or []):
        if action not in str(t.get("action") or ""):
            continue
        if str(t.get("at") or "")[:10].replace("-", "") == day:
            out.append(t)
    return out


def _prev_trading_day(dates: list[int], day: int) -> int | None:
    prev = [d for d in dates if d < day]
    return prev[-1] if prev else None


def _next_trading_day(dates: list[int], day: int) -> int | None:
    nxt = [d for d in dates if d > day]
    return nxt[0] if nxt else None


def _latest_close(bars: dict[int, float], day: int | None = None) -> tuple[int, float] | None:
    if not bars:
        return None
    keys = sorted(bars)
    if day is None:
        k = keys[-1]
        return k, bars[k]
    cand = [k for k in keys if k <= day]
    if not cand:
        return None
    k = cand[-1]
    return k, bars[k]


def _review_one(t: dict, day: int, dates: list[int],
                bars_fn: Callable[[str], dict[int, float]], led: dict) -> dict[str, Any]:
    code = str(t.get("code") or "")
    try:
        buy_price = float(t.get("price") or 0)
        shares = float(t.get("shares") or 0)
    except (TypeError, ValueError):
        buy_price, shares = 0.0, 0.0
    bars = bars_fn(code)
    close_today = _latest_close(bars, day)
    close_ret = None
    if close_today and buy_price:
        close_ret = round((close_today[1] / buy_price - 1) * 100, 2)
    nxt = _next_trading_day(dates, day)
    next_ret = None
    if nxt and nxt in bars and buy_price:
        next_ret = round((bars[nxt] / buy_price - 1) * 100, 2)
    latest = _latest_close(bars)
    latest_ret = None
    if latest and buy_price:
        latest_ret = round((latest[1] / buy_price - 1) * 100, 2)

    status = "持仓中"
    slot = next((s for s in (led.get("slots") or []) if str(s.get("code")) == code), None)
    if not slot:
        sold = [x for x in (led.get("trades") or [])
                if "卖出" in str(x.get("action") or "") and str(x.get("code")) == code
                and str(x.get("at") or "")[:10] >= str(t.get("at") or "")[:10]]
        if sold:
            status = "已卖出"
    return {
        "name": t.get("name") or code, "code": code, "shares": shares,
        "buy_price": buy_price, "buy_at": t.get("at"),
        "close": close_today[1] if close_today else None,
        "close_date": close_today[0] if close_today else None,
        "close_ret_pct": close_ret,
        "next_close": bars.get(nxt) if nxt else None,
        "next_ret_pct": next_ret,
        "latest_close": latest[1] if latest else None,
        "latest_ret_pct": latest_ret,
        "status": status,
    }


def build(day: str | None = None,
          ledgers: dict[str, dict[str, Any]] | None = None,
          bars_fn: Callable[[str], dict[int, float]] | None = None) -> dict[str, Any]:
    """生成某日的个股复盘。ledgers/bars_fn 可注入（测试用）。"""
    day = day or now_str()[:10].replace("-", "")
    day_i = int(day)
    leds = ledgers if ledgers is not None else _ledgers()
    bf = bars_fn or _bars
    dates = _trading_dates()

    res: dict[str, Any] = {"at": now_str(), "date": day, "instances": {},
                           "prev_trading_day": _prev_trading_day(dates, day_i)}
    for inst in INSTS:
        led = leds.get(inst) or {}
        buys_today = [_review_one(t, day_i, dates, bf, led)
                      for t in _day_trades(led, day, "买入")]
        sells_today = _day_trades(led, day, "卖出")
        holdings = []
        try:
            from . import market_view
            cfg = market_view.read_config(inst) or {}
            hold_days = int(cfg.get("hold_days") or 0)
        except Exception:  # noqa: BLE001
            hold_days = 0
        for s in (led.get("slots") or []):
            code = str(s.get("code") or "")
            bars = bf(code)
            latest = _latest_close(bars)
            cost = float(s.get("cost") or 0)
            last = (latest[1] if latest else 0) or float(s.get("last") or 0) or cost
            pnl_pct = round((last / cost - 1) * 100, 2) if cost else None
            due = str(s.get("sell_due") or "")
            if not due and s.get("buy_date"):
                try:
                    from . import market_view as _mv
                    due = _mv._sell_due(str(s.get("buy_date")), hold_days)  # noqa: SLF001
                except Exception:  # noqa: BLE001
                    due = ""
            holdings.append({
                "name": s.get("name"), "code": code, "shares": s.get("shares"),
                "cost": cost, "last": last, "pnl_pct": pnl_pct,
                "buy_date": s.get("buy_date"), "sell_due": due,
                "overdue": bool(due and due < day),
            })
        # 上一交易日推荐验证
        prev = res["prev_trading_day"]
        prev_buys = []
        if prev:
            prev_buys = [_review_one(t, prev, dates, bf, led)
                         for t in _day_trades(led, str(prev), "买入")]
        res["instances"][inst] = {
            "buys_today": buys_today, "sells_today": len(sells_today),
            "holdings": holdings, "prev_buys": prev_buys,
        }

    # 合计（去重同一个 code 在 A/B 都买的情况，保留各实例）
    res["buy_count"] = sum(len(v["buys_today"]) for v in res["instances"].values())
    res["prev_buy_count"] = sum(len(v["prev_buys"]) for v in res["instances"].values())
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return res


def summary_text(res: dict[str, Any]) -> str:
    lines = [f"个股复盘（{res.get('date')}）"]

    # 今日买入/推荐
    lines.append("一、今日买入/推荐：")
    any_today = False
    for inst in INSTS:
        for b in (res["instances"].get(inst) or {}).get("buys_today") or []:
            any_today = True
            lines.append(
                f"  {inst} {b['name']} {b['shares']:g}股 买入 {b['buy_price']}，"
                f"收盘 {b['close']}（{b['close_ret_pct']:+.2f}%），状态 {b['status']}"
            )
    if not any_today:
        lines.append("  无（非交易日或今日未买入）")

    # 上一交易日推荐验证
    lines.append(f"二、上一交易日（{res.get('prev_trading_day') or '—'}）推荐验证：")
    any_prev = False
    for inst in INSTS:
        for b in (res["instances"].get(inst) or {}).get("prev_buys") or []:
            any_prev = True
            lines.append(
                f"  {inst} {b['name']} 买 {b['buy_price']} → 最新 {b['close']} "
                f"（{b['close_ret_pct']:+.2f}%），状态 {b['status']}"
            )
    if not any_prev:
        lines.append("  无")

    # 持仓
    lines.append("三、当前持仓：")
    any_hold = False
    for inst in INSTS:
        for h in (res["instances"].get(inst) or {}).get("holdings") or []:
            any_hold = True
            flag = " ⚠️逾期" if h["overdue"] else ""
            lines.append(
                f"  {inst} {h['name']} {h['shares']:g}股 成本 {h['cost']} 现价 {h['last']} "
                f"（{h['pnl_pct']:+.2f}%）到期 {h['sell_due'] or '-'}{flag}"
            )
    if not any_hold:
        lines.append("  空仓")
    return "\n".join(lines)


def main() -> int:
    print(summary_text(build()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
