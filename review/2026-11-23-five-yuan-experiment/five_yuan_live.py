# -*- coding: utf-8 -*-
"""D 研究臂（实盘模拟）：5 元以内、涨 5% 卖、卖出立刻买下一只。

2026-11-23 决策官：ABC 都做——A 选股对比、C 扩大样本已完成；这是 B：直接开 D 实例。
D 独立账本 `_data/five_yuan_live.json`，不碰 A/B/C 的 sim.json；计划任务每 5 分钟 tick 一次。

规则：
  · 空仓且无当日买入：14:52–14:57 买一只收盘价/现价 ≤5 元的股票（随机，日成交额≥1000万）；
  · 持仓：盘中现价 ≥ 成本×1.05 且 T+1（买入日不是今天）→ 卖出；
  · 卖出后立即（当日 14:57 前）买下一只；否则次日 14:52 再买；
  · 每笔预算 20,000 元；单槽位；费用同 A/B（佣金万2.5最低5元、印花税卖出万5、过户费万0.1）。
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\自动炒股")
sys.path.insert(0, str(ROOT))

STATE = ROOT / "data_d" / "data" / "sim.json"
LOG = ROOT / "_data" / "five_yuan_live.log"
TARGET = 0.05
MAX_PRICE = 5.0
BUDGET = 20000.0
BUY_START, BUY_END = "14:50", "14:57"
MAX_HOLD_DAYS = 40  # 2026-11-23 回测最优：N=40（盘中触+5%口径）


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _load() -> dict:
    try:
        d = json.loads(STATE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return d
    except Exception:  # noqa: BLE001
        pass
    return {"start_capital": BUDGET, "cash": BUDGET, "slots": [], "trades": [],
            "equity": [], "created": time.strftime("%Y-%m-%d %H:%M:%S")}


def _save(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def _fee_buy(amount: float) -> float:
    from stocklens import simulate as sim
    return sim._fee_buy(amount)  # noqa: SLF001


def _fee_sell(amount: float) -> float:
    from stocklens import simulate as sim
    return sim._fee_sell(amount)  # noqa: SLF001


def _today() -> str:
    return time.strftime("%Y%m%d")


def _max_hold_days() -> int:
    """读实盘执行口径 max_hold_days；与看板 hold_days 不一致时告警（两者应同步）。"""
    try:
        import json as _json
        cfg = _json.loads((ROOT / "data_d" / "data" / "strategy_config.json").read_text(encoding="utf-8"))
        exec_days = int(cfg.get("max_hold_days") or MAX_HOLD_DAYS)
        view_days = cfg.get("hold_days")
        if view_days not in (None, "") and int(view_days) != exec_days:
            log(f"配置告警：hold_days={view_days} 与 max_hold_days={exec_days} 不一致，"
                f"实盘以 max_hold_days={exec_days} 为准，请同步两者")
        return max(1, exec_days)
    except Exception:  # noqa: BLE001
        return MAX_HOLD_DAYS


def _held_days(buy_date: str, hub) -> int:
    """买入日到今天的交易日数（不含买入日，含今天）；取不到日历退回工作日。"""
    try:
        from stocklens.trading import trading_days_between
        r = trading_days_between(str(buy_date), _today(), hub)
        return int(r.get("days") or 0)
    except Exception:  # noqa: BLE001
        from datetime import date, timedelta
        try:
            d1 = date(int(str(buy_date)[:4]), int(str(buy_date)[4:6]), int(str(buy_date)[6:8]))
            d2 = date.today()
            n = 0; cur = d1
            while cur < d2:
                cur += timedelta(days=1)
                if cur.weekday() < 5:
                    n += 1
            return n
        except Exception:  # noqa: BLE001
            return 0


def _quote(hub, code: str) -> float | None:
    from stocklens.datasource import normalize
    try:
        q = hub.quote(normalize(str(code)))
        p = float((q or {}).get("price") or 0)
        return p if p > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _limit_pct(code: str) -> float:
    """按板块估涨跌停幅度（无涨跌停字段时兜底）。"""
    c = str(code)
    if c.startswith(("688", "689", "300", "301")):
        return 0.20
    if c.startswith(("8", "4")):
        return 0.30
    return 0.10


def _limits(hub, code: str) -> tuple[float | None, float | None]:
    """返回 (涨停价, 跌停价)；优先行情字段，缺失时用昨收×板幅推导。"""
    try:
        from stocklens.datasource import normalize
        q = hub.quote(normalize(str(code))) or {}
        up = float(q.get("limit_up") or 0)
        down = float(q.get("limit_down") or 0)
        if up > 0 and down > 0:
            return (up, down)
        prev = float(q.get("prev_close") or 0)
        if prev > 0:
            pct = _limit_pct(code)
            return (round(prev * (1 + pct), 2), round(prev * (1 - pct), 2))
        return (None, None)
    except Exception:  # noqa: BLE001
        return (None, None)


def _tradeable_meta(meta) -> bool:
    """排除退市/长期停牌（universe 里的 delisted 标记）。"""
    if not isinstance(meta, dict):
        return False
    if meta.get("delisted"):
        return False
    try:
        return int(meta.get("last") or 0) >= 20260101
    except (TypeError, ValueError):
        return False


def _candidates() -> list[str]:
    """≤5 元、近 20 日均额≥1000 万、非退市（主板/创业板/科创板均可）。"""
    from stocklens import tdx
    import numpy as np
    out = []
    u = tdx.universe()
    for code, meta in (u.get("stocks") or {}).items():
        if not _tradeable_meta(meta):
            continue
        try:
            a = tdx.read_stock(code)
            if len(a["close"]) < 21:
                continue
            close = float(a["close"][-1])
            amt = float(np.mean(a["amount"][-20:]))
            if 0 < close <= MAX_PRICE and amt >= 1e7:
                out.append(str(code))
        except Exception:  # noqa: BLE001
            continue
    return out


def _lock_path() -> Path:
    return STATE.with_suffix(".lock")


def _acquire_lock() -> str | None:
    """单实例互斥（文件锁）；成功返回 token，失败返回 None。"""
    p = _lock_path()
    token = f"{os.getpid()}-{time.time_ns()}"
    for _ in range(2):
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, token.encode())
            finally:
                os.close(fd)
            return token
        except FileExistsError:
            try:
                age = time.time() - p.stat().st_mtime
            except OSError:
                age = 0
            if age > 600:                     # 陈旧锁（>10 分钟）自动清理
                try:
                    p.unlink()
                except OSError:
                    pass
                continue
            return None
        except OSError:
            return None
    return None


def _release_lock(token: str | None = None) -> None:
    """释放锁；带 token 时只删自己的锁（避免 ABA 误删新锁）。"""
    p = _lock_path()
    try:
        if token is not None:
            try:
                if p.read_text(encoding="utf-8") != token:
                    return
            except OSError:
                return
        p.unlink()
    except OSError:
        pass


def tick(now: str | None = None, hub=None, candidates=None) -> dict:
    """执行一次 tick（带文件锁）；返回结果（便于测试/日志）。"""
    token = _acquire_lock()
    if not token:
        return {"ok": True, "actions": [], "skipped": "locked"}
    try:
        return _tick_impl(now=now, hub=hub, candidates=candidates)
    finally:
        _release_lock(token)


def _tick_impl(now: str | None = None, hub=None, candidates=None) -> dict:
    """真正执行 tick 的内部实现。"""
    now = now or time.strftime("%H:%M")
    if time.localtime().tm_wday >= 5:      # 周末不交易
        return {"ok": True, "actions": [], "skipped": "weekend"}
    if hub is None:
        from stocklens.config import get_config
        from stocklens.datasource import DataHub
        from stocklens.utils import Log
        hub = DataHub(get_config(), Log(verbose=False))
    # 节假日不交易
    try:
        from stocklens.trading import is_trading_day
        _td = is_trading_day(hub)
        if isinstance(_td, dict) and not _td.get("trading"):
            return {"ok": True, "actions": [], "skipped": "holiday"}
    except Exception as exc:  # noqa: BLE001
        log(f"交易日判断失败，跳过本次 tick（fail-closed）: {type(exc).__name__}")
        return {"ok": True, "actions": [], "skipped": "trading_day_unknown"}
    today = _today()
    actions = []
    sold_codes: set[str] = set()
    d = _load()

    # 1) 卖出：T+1 且现价 ≥ 成本×1.05
    for s in list(d.get("slots") or []):
        if str(s.get("buy_date") or "") == today:
            continue
        _up, _down = _limits(hub, s.get("code"))
        price = _quote(hub, s.get("code"))
        cost = float(s.get("cost") or 0)
        if price is None or cost <= 0:
            continue
        if _down and price <= _down * 0.999:
            log(f"跌停无法卖出，跳过：{s.get('name')} {price}（跌停 {_down}）")
            continue
        held = _held_days(str(s.get("buy_date") or ""), hub)
        max_hold = _max_hold_days()
        # 时间止损只在尾盘（14:50 后）执行，避免盘中提前砍仓
        time_stop = held >= max_hold and now >= "14:50"
        if price >= cost * (1 + TARGET) or time_stop:
            shares = float(s.get("shares") or 0)
            amount = round(price * shares, 2)
            fee = _fee_sell(amount)
            pnl = round((price - cost) * shares - fee - float(s.get("fee") or 0), 2)
            d["cash"] = round(float(d.get("cash") or 0) + amount - fee, 2)
            d["slots"].remove(s)
            d["trades"].append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "action": "卖出",
                                "code": s.get("code"), "name": s.get("name"),
                                "shares": shares, "price": price, "fee": fee,
                                "pnl": pnl, "reason": (f"涨到+5%（成本{cost}）" if price >= cost * (1 + TARGET)
                                                        else f"持有满{max_hold}个交易日时间止损")})
            sold_codes.add(str(s.get("code") or ""))
            reason = ("涨到+5%" if price >= cost * (1 + TARGET)
                      else f"持有满{max_hold}个交易日时间止损")
            actions.append(f"卖出 {s.get('name')} {shares:g}股 @{price}（{reason}）")
            log(actions[-1])

    # 2) 买入：空仓 + 无当日买入；14:52-14:57 或刚卖出后（允许当日补买）
    held = d.get("slots") or []
    # 单槽位：空仓就能买（卖出后当日立即换下一只）；不需要“当日已买”闸门
    can_buy = not held
    just_sold = bool(actions)
    if can_buy and (BUY_START <= now <= BUY_END or (just_sold and now <= BUY_END)):
        cands = list(candidates) if candidates is not None else _candidates()
        # 卖出即换“下一只”：排除本 tick 刚卖出的标的（BUG-1）
        cands = [c for c in cands if c not in sold_codes]
        seed = int(time.time()) % 100000
        random.Random(seed).shuffle(cands)
        for code in cands[:30]:
            _up, _down = _limits(hub, code)
            price = _quote(hub, code)
            if not price or price > MAX_PRICE:
                continue
            if _up and price >= _up * 0.999:
                log(f"涨停无法买入，跳过：{code} {price}（涨停 {_up}）")
                continue
            # 留出费用空间：预算 20000 不能把现金全花掉（否则 amount+fee 超现金）
            available = float(d.get("cash") or 0) - 20.0
            budget = min(BUDGET, max(0.0, available))
            lot = int(budget / price / 100) * 100
            if lot < 100:
                continue
            amount = round(price * lot, 2)
            fee = _fee_buy(amount)
            if amount + fee > float(d.get("cash") or 0):
                continue
            d["cash"] = round(float(d.get("cash") or 0) - amount - fee, 2)
            name = ""  # 名称从行情/代码简单回填
            try:
                q = hub.quote(__import__("stocklens.datasource", fromlist=["normalize"]).normalize(code))
                name = (q or {}).get("name") or ""
            except Exception:  # noqa: BLE001
                pass
            if "退" in name or "ST" in name.upper():
                log(f"排除退市/ST：{code} {name}")
                continue
            d["slots"].append({"code": code, "name": name, "shares": lot, "cost": price,
                               "fee": fee, "buy_date": today,
                               "buy_at": time.strftime("%Y-%m-%d %H:%M:%S")})
            d["trades"].append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "action": "买入",
                                "code": code, "name": name, "shares": lot, "price": price,
                                "fee": fee, "reason": "5元股·涨5%卖出策略", "seed": seed})
            actions.append(f"买入 {name} {lot}股 @{price}")
            log(actions[-1])
            break
    try:
        from stocklens.src_equity import mark_equity  # type: ignore
    except Exception:  # noqa: BLE001
        mark_equity = None
    # 记录一条权益（现金 + 持仓成本/现价粗略）
    eq = float(d.get("cash") or 0)
    for s in d.get("slots") or []:
        try:
            eq += float(s.get("shares") or 0) * float(s.get("cost") or 0)
        except Exception:  # noqa: BLE001
            pass
    if actions:
        d.setdefault("equity", []).append({"date": today, "equity": round(eq, 2),
                                           "note": "five_yuan_live"})
    _save(d)
    return {"ok": True, "actions": actions, "cash": d.get("cash"), "slots": d.get("slots")}


def status() -> dict:
    d = _load()
    return {"cash": d.get("cash"), "slots": d.get("slots"), "trades": d.get("trades") or []}


if __name__ == "__main__":
    print(tick())
