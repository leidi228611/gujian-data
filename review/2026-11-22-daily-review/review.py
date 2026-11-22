# -*- coding: utf-8 -*-
"""每日复盘「交易质量」统计 v2（2026-11-22，按 superagent 审阅修正）。

指标口径（见 docs/复盘体系-升级-20261122.md）：
  - 平仓笔数/胜率/平均盈利/平均亏损/利润因子/单笔期望
  - 最大回撤（equity 峰谷，起点含本金；允许跌到 0）
  - 累计已实现盈亏 / 费用 / 总权益收益率（两者分开标注，F4）
  - 今日买卖/盈亏、逾期未卖、基准（上证近 10 日，按 as-of 日期，F9）
  - 合计补齐 avg/PF/expectancy/fees/MDD/overdue（F5）

审查修正：issue #73（F4/F5/F6/F7/F9/F11/F13）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import ROOT
from .utils import now_str

OUT = ROOT / "_data" / "review_stats.json"
INSTS = ("A", "B", "C")


def _read_ledgers() -> tuple[dict[str, dict[str, Any]], list[str]]:
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


def _closed_trades(led: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in (led.get("trades") or [])
            if "卖出" in str(t.get("action") or "") and t.get("pnl") is not None]


def _trades_today(led: dict[str, Any], day: str) -> list[dict[str, Any]]:
    return [t for t in (led.get("trades") or [])
            if str(t.get("at") or "")[:10].replace("-", "") == day]


def _clean_equity_points(points: list[tuple[str, float]], start: float) -> list[tuple[str, float]]:
    """剔除明显异常的历史 equity 点（如资金口径变更前的旧点）。

    规则：某点 < 50% 本金，且之后存在 >= 80% 本金的点 → 视为早期旧口径，
    避免把口径切换前的旧点算成 -80% 回撤（review issue #73 F7 衍生）。
    """
    if start <= 0 or not points:
        return points
    bad: set[int] = set()
    for i, (_d, v) in enumerate(points):
        if v < start * 0.5 and any(v2 >= start * 0.8 for _d2, v2 in points[i + 1:]):
            bad.add(i)
    return [p for i, p in enumerate(points) if i not in bad]


def _max_drawdown(equity: list[dict[str, Any]], start: float = 0.0) -> tuple[float, str]:
    """最大回撤%（峰谷）；起点含本金；允许跌到 0；异常旧点剔除。"""
    peak = start if start > 0 else 0.0
    peak_date = ""
    worst, worst_date = 0.0, ""
    points: list[tuple[str, float]] = []
    for e in equity:
        try:
            v = float(e.get("equity") or 0)
        except (TypeError, ValueError):
            continue
        points.append((str(e.get("date") or ""), v))
    points = _clean_equity_points(points, start)
    for date, v in points:
        if peak <= 0 or v > peak:
            peak, peak_date = max(peak, v), date
        if peak > 0:
            dd = (v / peak - 1) * 100
            if dd < worst:
                worst, worst_date = dd, f"{peak_date}→{date}"
    return (round(worst, 2), worst_date)


def _combined_equity(ledgers: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """按日期合并各实例 equity（仅用于合计回撤；缺失日期跳过）。"""
    by_date: dict[str, float] = {}
    for led in ledgers.values():
        for e in (led.get("equity") or []):
            try:
                v = float(e.get("equity") or 0)
            except (TypeError, ValueError):
                continue
            d = str(e.get("date") or "")
            if d:
                by_date[d] = by_date.get(d, 0.0) + v
    return [{"date": d, "equity": v} for d, v in sorted(by_date.items())]


def _inst_stats(led: dict[str, Any], day: str) -> dict[str, Any]:
    closed = _closed_trades(led)
    pnls = [float(t.get("pnl") or 0) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    fees = round(sum(float(t.get("fee") or 0) for t in (led.get("trades") or [])), 2)

    equity = led.get("equity") or []
    eq_vals = [float(e.get("equity") or 0) for e in equity if e.get("equity") is not None]
    start = float(led.get("start_capital") or 0) or (eq_vals[0] if eq_vals else 0)
    if eq_vals:
        last = eq_vals[-1]
    else:
        last = float(led.get("cash") or 0)
        for s in (led.get("slots") or []):
            try:
                last += float(s.get("shares") or 0) * (float(s.get("last") or 0) or float(s.get("cost") or 0))
            except (TypeError, ValueError):
                continue
    ret = (last / start - 1) * 100 if start else 0.0
    mdd, mdd_at = _max_drawdown(equity, start)
    _eq_points = len([e for e in equity if e.get("equity") is not None])
    _eq_used = len(_clean_equity_points(
        [(str(e.get("date") or ""), float(e.get("equity") or 0)) for e in equity
         if e.get("equity") is not None], start))

    today_trades = _trades_today(led, day)
    today_sells = [t for t in today_trades if "卖出" in str(t.get("action") or "")]
    today_buys = [t for t in today_trades if "买入" in str(t.get("action") or "")]
    today_pnl = round(sum(float(t.get("pnl") or 0) for t in today_sells), 2)

    overdue = []
    for s in (led.get("slots") or []):
        due = str(s.get("sell_due") or "")
        if due and due < day:
            overdue.append({"name": s.get("name"), "code": s.get("code"),
                            "due": due, "shares": s.get("shares")})

    return {
        "closed_trades": len(closed),
        "win_trades": len(wins),
        "loss_trades": len(losses),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
        "expectancy": round(sum(pnls) / len(pnls), 2) if pnls else None,
        "pnl_total": round(sum(pnls), 2),
        "fees_total": fees,
        "start_capital": round(start, 2),
        "equity": round(last, 2),
        "ret_pct": round(ret, 2),
        "max_drawdown_pct": mdd,
        "max_drawdown_at": mdd_at,
        "today": {"buys": len(today_buys), "sells": len(today_sells),
                  "pnl": today_pnl, "turnover": len(today_trades)},
        "overdue": overdue,
        "daily_equity_points": _eq_points,
        "equity_points_used": _eq_used,
        "equity_points_excluded": max(0, _eq_points - _eq_used),
    }


def _combined(per: dict[str, dict[str, Any]], ledgers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    closed = sum(v["closed_trades"] for v in per.values())
    wins = sum(v["win_trades"] for v in per.values())
    losses = sum(v["loss_trades"] for v in per.values())
    pnls = [v["pnl_total"] for v in per.values()]
    eq = round(sum(v["equity"] for v in per.values()), 2)
    start = round(sum(v["start_capital"] for v in per.values()), 2)
    tot_win = sum(v["avg_win"] * v["win_trades"] for v in per.values() if v["avg_win"] is not None)
    tot_loss = sum(v["avg_loss"] * v["loss_trades"] for v in per.values() if v["avg_loss"] is not None)
    combined_eq = _combined_equity(ledgers)
    mdd, mdd_at = _max_drawdown(combined_eq, start) if combined_eq else (0.0, "")
    return {
        "closed_trades": closed,
        "win_trades": wins,
        "loss_trades": losses,
        "win_rate_pct": round(wins / closed * 100, 1) if closed else None,
        "avg_win": round(tot_win / wins, 2) if wins else None,
        "avg_loss": round(tot_loss / losses, 2) if losses else None,
        "profit_factor": round(tot_win / abs(tot_loss), 2) if tot_loss else None,
        "expectancy": round(sum(pnls) / closed, 2) if closed else None,
        "pnl_total": round(sum(pnls), 2),
        "fees_total": round(sum(v["fees_total"] for v in per.values()), 2),
        "equity": eq,
        "start_capital": start,
        "ret_pct": round((eq / start - 1) * 100, 2) if start else 0.0,
        "max_drawdown_pct": mdd,
        "max_drawdown_at": mdd_at,
        "today_pnl": round(sum(v["today"]["pnl"] for v in per.values()), 2),
        "today_trades": sum(v["today"]["turnover"] for v in per.values()),
        "overdue": sum(len(v["overdue"]) for v in per.values()),
    }


def _benchmark(day: str | None = None) -> dict[str, Any] | None:
    """上证指数近 10 日涨跌；按 as-of day（F9）；短序列返回 None。"""
    try:
        from . import tdx
        series = tdx.index_series("sh000001")
        pairs = [(int(d), float(c)) for d, c in zip(series["date"], series["close"]) if float(c) > 0]
    except Exception:  # noqa: BLE001
        return None
    if day:
        try:
            day_i = int(str(day).replace("-", ""))
            pairs = [(d, c) for d, c in pairs if d <= day_i]
        except (TypeError, ValueError):
            pass
    if len(pairs) < 2:
        return None
    recent = max(1, min(10, len(pairs) - 1))
    last_d, last_c = pairs[-1]
    prev_c = pairs[-1 - recent][1]
    if prev_c <= 0:
        return None
    return {"last_date": str(last_d), "close": round(last_c, 2),
            "recent_days": recent,
            "recent_ret_pct": round((last_c / prev_c - 1) * 100, 2)}


def compute(day: str | None = None,
            ledgers: dict[str, dict[str, Any]] | None = None,
            save: bool = True) -> dict[str, Any]:
    """计算复盘统计；ledgers 可注入（测试用）；save=False 不覆盖生产 json。"""
    day = day or now_str()[:10].replace("-", "")
    day = str(day).replace("-", "")
    if ledgers is not None:
        leds = ledgers
        missing: list[str] = []
    else:
        leds, missing = _read_ledgers()
    per = {inst: _inst_stats(leds.get(inst) or {}, day) for inst in INSTS}
    res = {"at": now_str(), "date": day, "instances": per,
           "combined": _combined(per, leds), "benchmark": _benchmark(day),
           "missing_instances": missing}
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


def _fmt(v: Any, suffix: str = "", dash: str = "—") -> str:
    if v is None:
        return dash
    return f"{v}{suffix}"


def summary_text(res: dict[str, Any]) -> str:
    c = res.get("combined") or {}
    lines = [f"复盘统计（{res.get('date')}）"]
    for inst in INSTS:
        v = (res.get("instances") or {}).get(inst) or {}
        if not v.get("closed_trades"):
            lines.append(f"{inst}：暂无平仓样本")
            continue
        lines.append(
            f"{inst}：平仓 {v['closed_trades']} 笔，胜率 {_fmt(v['win_rate_pct'], '%')}，"
            f"期望 {_fmt(v['expectancy'])} 元/笔，利润因子 {_fmt(v['profit_factor'])}，"
            f"已实现 {_fmt(v['pnl_total'], dash='0')} 元，总权益 {_fmt(v['ret_pct'], '%')}（含浮亏）"
            f"，最大回撤 {_fmt(v['max_drawdown_pct'], '%', '0%')}"
        )
        if v.get("overdue"):
            names = "、".join(o.get("name") or "" for o in v["overdue"])
            lines.append(f"  ⚠️ 逾期未卖：{names}")
    lines.append(
        f"合计：平仓 {c.get('closed_trades')} 笔，胜率 {_fmt(c.get('win_rate_pct'), '%')}，"
        f"已实现 {_fmt(c.get('pnl_total'), dash='0')} 元，"
        f"总权益 {_fmt(c.get('ret_pct'), '%')}（含浮亏），"
        f"利润因子 {_fmt(c.get('profit_factor'))}，"
        f"最大回撤 {_fmt(c.get('max_drawdown_pct'), '%', '0%')}"
    )
    bm = res.get("benchmark") or {}
    if bm:
        lines.append(f"基准：上证指数近 {bm.get('recent_days')} 日 {bm.get('recent_ret_pct')}%")
    missing = res.get("missing_instances") or []
    if missing:
        lines.append(f"⚠️ 账本读取失败：{'、'.join(missing)}（数据不可用）")
    return "\n".join(lines)


def main() -> int:
    print(summary_text(compute()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
