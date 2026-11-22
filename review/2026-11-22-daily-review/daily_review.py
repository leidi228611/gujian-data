# -*- coding: utf-8 -*-
"""每日个股复盘 v2（2026-11-22，按 superagent 审阅修正）。

三块内容：
  1. 今日买入（=今日推荐）逐票：买入价 / 当日收盘 / 收益率 / 状态
  2. 上一交易日推荐验证：买入价 / **最新收盘** / 收益率（不再把买入日收盘当"最新"）
  3. 当前持仓：成本 / 最新价 / 浮盈亏 / 到期 / 逾期

审查修正（issue #73）：
  - F1：上一交易日验证改用 latest_close/latest_ret，并显示价格日期；
  - F2：所有收益率/浮盈亏格式化前判空，None 显示「—」，不再整段消失；
  - F3：build(save=False) 可关闭写盘；写盘用 tmp + os.replace 原子替换；
  - F8：_bars 缓存带 TTL，空结果不缓存；
  - F12：卖出状态按股数累计，支持「部分卖出」；
  - F17：日期参数校验（由调用方或 build 抛 ValueError）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from .config import ROOT
from .utils import now_str

OUT = ROOT / "_data" / "daily_review.json"
INSTS = ("A", "B", "C")
BARS_TTL = 300  # 秒；长驻进程 5 分钟后重新读 TDX

_bars_cache: dict[str, tuple[float, dict[int, float]]] = {}


def _bars(code: str) -> dict[int, float]:
    """通达信本地日线：{YYYYMMDD: 收盘价(元)}；带 TTL，失败不缓存。"""
    code = str(code or "")
    now = time.time()
    hit = _bars_cache.get(code)
    if hit and now - hit[0] < BARS_TTL:
        return hit[1]
    try:
        from . import tdx
        arr = tdx.read_stock(code)
        out = {int(d): float(c) for d, c in zip(arr["date"], arr["close"]) if float(c) > 0}
    except Exception:  # noqa: BLE001
        out = {}
    if out:  # 空结果不缓存，避免一次失败永久空表
        _bars_cache[code] = (now, out)
    return out


def _ledgers() -> tuple[dict[str, dict[str, Any]], list[str]]:
    from . import market_view
    out: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for inst in INSTS:
        try:
            led = market_view.read_ledger(inst) or {}
            out[inst] = led
            if not led:
                missing.append(inst)
        except Exception:  # noqa: BLE001
            out[inst] = {}
            missing.append(inst)
    return out, missing


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


def _safe_pct(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _status_for(t: dict, led: dict) -> str:
    """持仓/已卖出/部分卖出：按股数累计判断（F12）。"""
    code = str(t.get("code") or "")
    try:
        bought = float(t.get("shares") or 0)
    except (TypeError, ValueError):
        bought = 0.0
    slot_shares = 0.0
    for s in (led.get("slots") or []):
        if str(s.get("code")) == code:
            try:
                slot_shares += float(s.get("shares") or 0)
            except (TypeError, ValueError):
                continue
    if slot_shares > 0:
        return "持仓中"
    buy_at = str(t.get("at") or "")
    sold = 0.0
    for x in (led.get("trades") or []):
        if "卖出" not in str(x.get("action") or "") or str(x.get("code")) != code:
            continue
        if str(x.get("at") or "") >= buy_at:
            try:
                sold += float(x.get("shares") or 0)
            except (TypeError, ValueError):
                continue
    if sold <= 0:
        return "未成交/未知"
    if bought and sold < bought:
        return "部分卖出"
    return "已卖出"


def _review_one(t: dict, view_day: int, dates: list[int],
                bars_fn: Callable[[str], dict[int, float]], led: dict) -> dict[str, Any]:
    code = str(t.get("code") or "")
    try:
        buy_price = float(t.get("price") or 0)
        shares = float(t.get("shares") or 0)
    except (TypeError, ValueError):
        buy_price, shares = 0.0, 0.0
    bars = bars_fn(code)
    buy_day = int(str(t.get("at") or "")[:10].replace("-", "") or view_day)

    # 买入日/查看日收盘
    close_view = _latest_close(bars, view_day)
    close_ret = None
    if close_view and buy_price:
        close_ret = round((close_view[1] / buy_price - 1) * 100, 2)
    # 最新收盘（F1：推荐验证用这个）
    latest = _latest_close(bars)
    latest_ret = None
    if latest and buy_price:
        latest_ret = round((latest[1] / buy_price - 1) * 100, 2)
    # 买入次一交易日收盘
    nxt = _next_trading_day(dates, buy_day)
    next_ret = None
    if nxt and nxt in bars and buy_price:
        next_ret = round((bars[nxt] / buy_price - 1) * 100, 2)

    return {
        "name": t.get("name") or code, "code": code, "shares": shares,
        "buy_price": buy_price, "buy_at": t.get("at"), "buy_day": buy_day,
        "close": close_view[1] if close_view else None,
        "close_date": close_view[0] if close_view else None,
        "close_ret_pct": close_ret,
        "next_close": bars.get(nxt) if nxt else None,
        "next_ret_pct": next_ret,
        "latest_close": latest[1] if latest else None,
        "latest_date": latest[0] if latest else None,
        "latest_ret_pct": latest_ret,
        "status": _status_for(t, led),
    }


def build(day: str | None = None,
          ledgers: dict[str, dict[str, Any]] | None = None,
          bars_fn: Callable[[str], dict[int, float]] | None = None,
          save: bool = True) -> dict[str, Any]:
    """生成某日的个股复盘；ledgers/bars_fn 可注入（测试用）；save=False 不写盘。"""
    day = day or now_str()[:10].replace("-", "")
    day_s = str(day).replace("-", "")
    if not day_s.isdigit() or len(day_s) != 8:
        raise ValueError("day 必须是 YYYYMMDD（8 位数字）")
    day_i = int(day_s)
    if ledgers is not None:
        leds = ledgers
        missing: list[str] = []
    else:
        leds, missing = _ledgers()
    bf = bars_fn or _bars
    dates = _trading_dates()

    res: dict[str, Any] = {"at": now_str(), "date": day_s, "instances": {},
                           "prev_trading_day": _prev_trading_day(dates, day_i),
                           "missing_instances": missing}
    for inst in INSTS:
        led = leds.get(inst) or {}
        buys_today = [_review_one(t, day_i, dates, bf, led)
                      for t in _day_trades(led, day_s, "买入")]
        sells_today = _day_trades(led, day_s, "卖出")
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
            try:
                cost = float(s.get("cost") or 0)
            except (TypeError, ValueError):
                cost = 0.0
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
                "cost": cost, "last": last, "last_date": latest[0] if latest else None,
                "pnl_pct": pnl_pct,
                "buy_date": s.get("buy_date"), "sell_due": due,
                "overdue": bool(due and due < day_s),
            })
        prev = res["prev_trading_day"]
        prev_buys = []
        if prev:
            prev_buys = [_review_one(t, prev, dates, bf, led)
                         for t in _day_trades(led, str(prev), "买入")]
        res["instances"][inst] = {
            "buys_today": buys_today, "sells_today": len(sells_today),
            "holdings": holdings, "prev_buys": prev_buys,
        }

    res["buy_count"] = sum(len(v["buys_today"]) for v in res["instances"].values())
    res["prev_buy_count"] = sum(len(v["prev_buys"]) for v in res["instances"].values())
    if save:
        _write_json(OUT, res)
    return res


def _write_json(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def summary_text(res: dict[str, Any]) -> str:
    lines = [f"个股复盘（{res.get('date')}）"]

    lines.append("一、今日买入/推荐：")
    any_today = False
    for inst in INSTS:
        for b in (res["instances"].get(inst) or {}).get("buys_today") or []:
            any_today = True
            close = b.get("close")
            close_txt = f"{close}（{b.get('close_date') or '—'}）" if close is not None else "无当日K线"
            lines.append(
                f"  {inst} {b['name']} {_fmt_shares(b.get('shares'))}股 买入 {b['buy_price']}，"
                f"收盘 {close_txt}（{_safe_pct(b.get('close_ret_pct'))}），状态 {b['status']}"
            )
    if not any_today:
        lines.append("  无（非交易日/无数据/今日未买入）")

    lines.append(f"二、上一交易日（{res.get('prev_trading_day') or '—'}）推荐验证：")
    any_prev = False
    for inst in INSTS:
        for b in (res["instances"].get(inst) or {}).get("prev_buys") or []:
            any_prev = True
            latest = b.get("latest_close")
            latest_txt = (f"{latest}（{b.get('latest_date') or '—'}）"
                          if latest is not None else "无K线")
            lines.append(
                f"  {inst} {b['name']} 买 {b['buy_price']} → 最新 {latest_txt}"
                f"（{_safe_pct(b.get('latest_ret_pct'))}），状态 {b['status']}"
            )
    if not any_prev:
        lines.append("  无")

    lines.append("三、当前持仓：")
    any_hold = False
    for inst in INSTS:
        for h in (res["instances"].get(inst) or {}).get("holdings") or []:
            any_hold = True
            flag = " ⚠️逾期" if h["overdue"] else ""
            lines.append(
                f"  {inst} {h['name']} {_fmt_shares(h.get('shares'))}股 成本 {h['cost']} "
                f"现价 {h['last']}（{_safe_pct(h.get('pnl_pct'))}）"
                f"到期 {h['sell_due'] or '-'}{flag}"
            )
    if not any_hold:
        lines.append("  空仓")
    missing = res.get("missing_instances") or []
    if missing:
        lines.append(f"  ⚠️ 账本读取失败：{'、'.join(missing)}（数据不可用，不代表空仓）")
    return "\n".join(lines)


def _fmt_shares(v: Any) -> str:
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return "—"


def main() -> int:
    print(summary_text(build()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
