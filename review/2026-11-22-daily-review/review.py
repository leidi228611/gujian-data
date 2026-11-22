# -*- coding: utf-8 -*-
"""每日复盘的「交易质量」统计（2026-11-22 升级）。

参考 GitHub 上 trading-journal / postmortem 类项目（simonro/Trading-Journal-AI、
Cursivez/journalit、francisx1999/crypto-trading-bot-postmortem 等）的指标口径，
把「只看今天赚亏」升级为「看方法是否可持续」：

  - 胜率 / 盈亏比 / 利润因子 / 单笔期望值
  - 最大回撤（基于 equity 序列）
  - 买入执行率、逾期未卖、交易频率
  - 与上证指数基准对比（策略贡献 vs 市场）
  - 今日 + 累计，三个实例分别统计

产物：data/review_stats.json + 供 postmortem / 微信推送调用的摘要
用法：python -X utf8 -m stocklens.review
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import ROOT
from .utils import now_str

OUT = ROOT / "_data" / "review_stats.json"

INSTS = ("A", "B", "C")


# ---------------------------------------------------------------- 数据读取
def _read_ledgers() -> dict[str, dict[str, Any]]:
    """读取 A/B/C 三个实例账本（复用 market_view 的路径解析）。"""
    from . import market_view
    out: dict[str, dict[str, Any]] = {}
    for inst in INSTS:
        try:
            out[inst] = market_view.read_ledger(inst) or {}
        except Exception:  # noqa: BLE001
            out[inst] = {}
    return out


def _closed_trades(led: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in (led.get("trades") or [])
            if "卖出" in str(t.get("action") or "") and t.get("pnl") is not None]


def _trades_today(led: dict[str, Any], day: str) -> list[dict[str, Any]]:
    out = []
    for t in (led.get("trades") or []):
        if str(t.get("at") or "")[:10].replace("-", "") == day:
            out.append(t)
    return out


def _max_drawdown(equity: list[dict[str, Any]]) -> tuple[float, str]:
    """返回 (最大回撤%, 发生日期)。"""
    peak, peak_date, worst, worst_date = 0.0, "", 0.0, ""
    for e in equity:
        try:
            v = float(e.get("equity") or 0)
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        date = str(e.get("date") or "")
        if v > peak:
            peak, peak_date = v, date
        if peak:
            dd = (v / peak - 1) * 100
            if dd < worst:
                worst, worst_date = dd, f"{peak_date}→{date}"
    return (round(worst, 2), worst_date)


# ---------------------------------------------------------------- 指标计算
def _inst_stats(led: dict[str, Any], day: str) -> dict[str, Any]:
    closed = _closed_trades(led)
    pnls = [float(t.get("pnl") or 0) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    fees = round(sum(float(t.get("fee") or 0) for t in (led.get("trades") or [])), 2)

    equity = led.get("equity") or []
    eq_vals = [float(e.get("equity") or 0) for e in equity if e.get("equity")]
    start = float(led.get("start_capital") or 0) or (eq_vals[0] if eq_vals else 0)
    if eq_vals:
        last = eq_vals[-1]
    else:
        # 无 equity 记录（如 C 空仓）：用 现金 + 持仓市值 兜底
        last = float(led.get("cash") or 0)
        for s in (led.get("slots") or []):
            try:
                shares = float(s.get("shares") or 0)
                px = float(s.get("last") or 0) or float(s.get("cost") or 0)
                last += shares * px
            except (TypeError, ValueError):
                continue
    ret = (last / start - 1) * 100 if start else 0.0
    mdd, mdd_at = _max_drawdown(equity)

    # 今日
    today_trades = _trades_today(led, day)
    today_sells = [t for t in today_trades if "卖出" in str(t.get("action") or "")]
    today_buys = [t for t in today_trades if "买入" in str(t.get("action") or "")]
    today_pnl = round(sum(float(t.get("pnl") or 0) for t in today_sells), 2)

    # 逾期未卖（当前持仓里 sell_due 已过）
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
        "daily_equity_points": len(eq_vals),
    }


def _combined(per: dict[str, dict[str, Any]]) -> dict[str, Any]:
    closed = sum(v["closed_trades"] for v in per.values())
    wins = sum(v["win_trades"] for v in per.values())
    pnl = round(sum(v["pnl_total"] for v in per.values()), 2)
    eq = round(sum(v["equity"] for v in per.values()), 2)
    start = round(sum(v["start_capital"] for v in per.values()), 2)
    return {
        "closed_trades": closed,
        "win_rate_pct": round(wins / closed * 100, 1) if closed else None,
        "pnl_total": pnl,
        "equity": eq,
        "start_capital": start,
        "ret_pct": round((eq / start - 1) * 100, 2) if start else 0.0,
        "today_pnl": round(sum(v["today"]["pnl"] for v in per.values()), 2),
        "today_trades": sum(v["today"]["turnover"] for v in per.values()),
    }


def _benchmark() -> dict[str, Any] | None:
    """上证指数近期涨跌（%）；无 tdx/numpy 或数据缺失时返回 None（CI 友好）。"""
    try:
        from . import tdx
        series = tdx.index_series("sh000001")
        dates = [str(d) for d in series["date"]]
        closes = [float(c) for c in series["close"]]
    except Exception:  # noqa: BLE001
        return None
    if not dates:
        return None
    # 取最近 10 个交易日内的涨跌，用于「今日/近期」参考
    recent = max(1, min(10, len(dates) - 1))
    ret = (closes[-1] / closes[-1 - recent] - 1) * 100 if closes[-1 - recent] else None
    return {"last_date": dates[-1], "close": round(closes[-1], 2),
            "recent_days": recent,
            "recent_ret_pct": round(ret, 2) if ret is not None else None}


def compute(day: str | None = None, ledgers: dict[str, dict[str, Any]] | None = None,
            save: bool = True) -> dict[str, Any]:
    """计算复盘统计。ledgers 可注入（测试用）；save=False 时不覆盖生产 json。"""
    day = day or now_str()[:10].replace("-", "")
    leds = ledgers if ledgers is not None else _read_ledgers()
    per = {inst: _inst_stats(leds.get(inst) or {}, day) for inst in INSTS}
    res = {"at": now_str(), "date": day, "instances": per,
           "combined": _combined(per), "benchmark": _benchmark()}
    if save:
        try:
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
    return res


# ---------------------------------------------------------------- 文本摘要
def summary_text(res: dict[str, Any]) -> str:
    c = res.get("combined") or {}
    lines = [f"复盘统计（{res.get('date')}）"]
    for inst in INSTS:
        v = (res.get("instances") or {}).get(inst) or {}
        if not v.get("closed_trades"):
            lines.append(f"{inst}：暂无平仓样本")
            continue
        lines.append(
            f"{inst}：平仓 {v['closed_trades']} 笔，胜率 {v['win_rate_pct']}%，"
            f"期望 {v['expectancy']} 元/笔，盈亏比 {v['profit_factor']}，"
            f"累计 {v['pnl_total']} 元，最大回撤 {v['max_drawdown_pct']}%"
        )
        if v.get("overdue"):
            names = "、".join(o.get("name") or "" for o in v["overdue"])
            lines.append(f"  ⚠️ 逾期未卖：{names}")
    lines.append(
        f"合计：平仓 {c.get('closed_trades')} 笔，胜率 {c.get('win_rate_pct')}%，"
        f"累计 {c.get('pnl_total')} 元（{c.get('ret_pct')}%），今日 {c.get('today_pnl')} 元"
    )
    bm = res.get("benchmark") or {}
    if bm:
        lines.append(f"基准：上证指数近 {bm.get('recent_days')} 日 {bm.get('recent_ret_pct')}%")
    return "\n".join(lines)


def main() -> int:
    print(summary_text(compute()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
