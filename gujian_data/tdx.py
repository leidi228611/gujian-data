# -*- coding: utf-8 -*-
"""通达信全量日线数据层（2026-11-19 接入股鉴）。

数据位置（决策官 2026-11-19 交付）：
    · 目录结构：`<TDX_HOME>/{sh,sz,bj}/lday/*.day`
  · 环境变量 `GUJIAN_TDX_HOME` 可覆盖位置

文件格式（32 字节/条，小端）：
  date(int32, YYYYMMDD) open/high/low/close(int32, 分) amount(float32, 元) volume(int32, 股) reserved

特点：
  · **含全量退市/停牌股**（2,412 只）→ 修幸存者偏差用
  · 价格**不复权** → 算长期收益要用 `adj_close()`（除权日按 0 收益处理）
  · 覆盖 1990s ~ 2026，2,989 万行

用法：
  from stocklens import tdx
  codes = tdx.list_codes()                     # 主板股票代码
  arrays = tdx.read_stock("600519")            # 日线数组
  px = tdx.adjusted_close(arrays["close"])     # 复权收益序列
  idx = tdx.index_series("sh000001")           # 指数
  st = tdx.health()                            # 体检项用
"""
from __future__ import annotations

import json
import os
import struct
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from .config import ROOT

REC = np.dtype([("date", "<i4"), ("open", "<i4"), ("high", "<i4"), ("low", "<i4"),
                ("close", "<i4"), ("amount", "<f4"), ("volume", "<i4"), ("_", "<i4")])
REC_BYTES = 32
UNIVERSE = ROOT / "_data" / "tdx_universe.json"
DIV_FACTORS = ROOT / "_data" / "tdx_div_factors.json"
_DIV_CACHE: dict[str, Any] | None = None
DEFAULT_HOME = Path(os.environ.get("GUJIAN_TDX_HOME") or (Path.home() / "hsjday"))


def home() -> Path:
    """通达信数据根目录（环境变量优先）。"""
    env = os.environ.get("GUJIAN_TDX_HOME")
    return Path(env) if env else DEFAULT_HOME


def available() -> bool:
    return (home() / "sh" / "lday").is_dir()


def _market_of(code: str) -> str:
    return "sh" if code.startswith(("60", "68")) else "sz"



def is_main_board_file(path) -> bool:
    """是否主板股票文件（sh60x / sz00x），排除上证指数 sh000xxx 等。"""
    stem = Path(path).stem
    mk, code = stem[:2], stem[2:]
    return (mk == "sh" and code.startswith("60")) or (mk == "sz" and code.startswith("00"))


def is_stock_file(path) -> bool:
    """是否 A 股个股文件（sh60/68、sz00/30、bj），排除指数/ETF/债券。"""
    stem = Path(path).stem
    mk, code = stem[:2], stem[2:]
    if mk == "sh":
        return code.startswith(("60", "68"))
    if mk == "sz":
        return code.startswith(("00", "30"))
    return mk == "bj"


def list_codes(include_star: bool = False) -> list[str]:
    """全部代码（默认只要主板 60/00；include_star=True 含科创/创业板之外的全部）。"""
    root = home()
    out = []
    for mk in ("sh", "sz", "bj"):
        d = root / mk / "lday"
        if not d.is_dir():
            continue
        for f in d.glob("*.day"):
            if not is_main_board_file(f):
                continue
            out.append(f.stem[2:])
    return sorted(out)


def read_stock(code: str) -> dict[str, np.ndarray]:
    """读单只股票的日线数组；找不到抛 FileNotFoundError。"""
    mk = _market_of(code)
    for prefix, m in ((code, mk), (code, "sz" if mk == "sh" else "sh")):
        p = home() / m / "lday" / f"{m}{prefix}.day"
        if p.exists():
            arr = np.fromfile(p, dtype=REC)
            return {"date": arr["date"].astype(np.int64),
                    "open": arr["open"].astype(np.float64) / 100.0,
                    "high": arr["high"].astype(np.float64) / 100.0,
                    "low": arr["low"].astype(np.float64) / 100.0,
                    "close": arr["close"].astype(np.float64) / 100.0,
                    "amount": arr["amount"].astype(np.float64),
                    "volume": arr["volume"].astype(np.float64)}
    raise FileNotFoundError(f"通达信里没有 {code}")


def read_file(path: Path) -> dict[str, np.ndarray]:
    """按完整路径读 .day 文件（批量扫描用，性能与 read_stock 相同）。"""
    arr = np.fromfile(path, dtype=REC)
    return {"date": arr["date"].astype(np.int64),
            "open": arr["open"].astype(np.float64) / 100.0,
            "high": arr["high"].astype(np.float64) / 100.0,
            "low": arr["low"].astype(np.float64) / 100.0,
            "close": arr["close"].astype(np.float64) / 100.0,
            "amount": arr["amount"].astype(np.float64),
            "volume": arr["volume"].astype(np.float64)}



def div_factors(code: str | None = None) -> dict[str, list[list[float]]] | list[list[float]]:
    """除权除息记录缓存（2026-11-19 由 _tools/build_div_factors.py 生成）。

    每条纹： [除权日 YYYYMMDD, 派息/10股, 配股价, 送转股/10股, 配股/10股]
    没有缓存返回空；给了 code 返回该股列表。
    """
    global _DIV_CACHE
    if _DIV_CACHE is None:
        try:
            _DIV_CACHE = json.loads(DIV_FACTORS.read_text(encoding="utf-8")).get("stocks") or {}
        except (OSError, ValueError):
            _DIV_CACHE = {}
    return _DIV_CACHE.get(code, []) if code else _DIV_CACHE


def exact_adjusted_close(raw: np.ndarray, dates: np.ndarray,
                         events: list[list[float]]) -> np.ndarray:
    """用真实除权记录算前复权价（纯函数）。

    每个除权日之前的价格乘以：
      f = (前收 - 派息/10 + 配股价×配股/10) / (前收 × (1 + (送转+配股)/10))
    """
    adj = raw.astype(np.float64).copy()
    for ev in sorted(events, key=lambda x: int(x[0])):
        try:
            d, bonus, allot_price, share_bonus, allotment = int(ev[0]), float(ev[1]), float(ev[2]), float(ev[3]), float(ev[4])
        except (TypeError, ValueError, IndexError):
            continue
        idx = int(np.searchsorted(dates, d))
        if idx <= 0 or idx >= len(dates):
            continue
        prev = float(raw[idx - 1])
        if prev <= 0:
            continue
        ratio = (share_bonus + allotment) / 10.0
        f = (prev - bonus / 10.0 + allot_price * allotment / 10.0) / (prev * (1 + ratio))
        if f > 0:
            adj[:idx] *= f
    return adj


def adjusted_close(raw: np.ndarray, dates: np.ndarray | None = None,
                   code: str | None = None) -> np.ndarray:
    """不复权 → 复权收益序列。

    优先用真实除权记录（需要 dates + code）；没有记录时退回启发式
    （|单日涨跌| > 11.5% 视为除权、按 0 收益处理）。
    """
    if code and dates is not None:
        events = div_factors(code)
        if events:
            return exact_adjusted_close(raw, dates, events)
    return _heuristic_adjusted_close(raw)

def _heuristic_adjusted_close(raw: np.ndarray) -> np.ndarray:
    """把不复权收盘价转成「复权收益序列」。

    主板涨跌停 ±10%（ST ±5%），所以 |单日涨跌| > 11.5% 的跳变视为除权除息；
    送股/拆股对总收益≈0，因此把这类跳变的当日收益按 0 处理。
    """
    n = len(raw)
    if n == 0:
        return raw
    chg = np.zeros(n)
    chg[1:] = raw[1:] / np.where(raw[:-1] > 0, raw[:-1], 1) - 1
    eff = np.where(np.abs(chg) > 0.115, 0.0, chg)
    adj = np.empty(n)
    adj[0] = raw[0] if raw[0] > 0 else 1.0
    for i in range(1, n):
        adj[i] = adj[i - 1] * (1 + eff[i])
    return adj


def adjusted_arrays(arrays: dict[str, np.ndarray], code: str | None = None) -> dict[str, np.ndarray]:
    """对 OHLC 一起复权（用收盘价算出的比例缩放高/低）。"""
    raw_close = arrays["close"]
    adj = adjusted_close(raw_close, arrays.get("date"), code)
    ratio = np.where(raw_close > 0, adj / raw_close, 1.0)
    out = dict(arrays)
    out["close"] = adj
    out["high"] = arrays["high"] * ratio
    out["low"] = arrays["low"] * ratio
    out["open"] = arrays["open"] * ratio
    return out


def index_series(name: str = "sh000001") -> dict[str, np.ndarray]:
    """指数日线（上证 sh000001 等）。"""
    p = home() / name[:2] / "lday" / f"{name}.day"
    if not p.exists():
        raise FileNotFoundError(f"通达信里没有指数 {name}")
    arr = np.fromfile(p, dtype=REC)
    return {"date": arr["date"].astype(np.int64),
            "close": arr["close"].astype(np.float64) / 100.0}


def index_bull_map(name: str = "sh000001", window: int = 200) -> dict[int, bool]:
    """每日「指数是否在 N 日线上方」（牛熊开关）。"""
    idx = index_series(name)
    dates, closes = idx["date"], idx["close"]
    out: dict[int, bool] = {}
    for i in range(window - 1, len(closes)):
        out[int(dates[i])] = bool(closes[i] > closes[i - window + 1:i + 1].mean())
    return out


def universe(refresh: bool = False) -> dict[str, Any]:
    """股票目录缓存：{code: {market, first, last, rows, delisted}}。

    首次调用会扫描 12k 个文件（约 10-20 秒）并写 `_data/tdx_universe.json`；
    之后直接读缓存。refresh=True 强制重建（数据更新后调用）。
    """
    if UNIVERSE.exists() and not refresh:
        try:
            return json.loads(UNIVERSE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    root = home()
    data: dict[str, Any] = {}
    latest = 0
    for mk in ("sh", "sz", "bj"):
        d = root / mk / "lday"
        if not d.is_dir():
            continue
        for f in d.glob("*.day"):
            if not is_stock_file(f):
                continue
            code = f.stem[2:]
            size = f.stat().st_size
            if size < REC_BYTES:
                continue
            with f.open("rb") as fh:
                fh.seek(0)
                first = struct.unpack("<i", fh.read(4))[0]
                fh.seek(-REC_BYTES, 2)
                rec = fh.read(REC_BYTES)
                last = struct.unpack("<i", rec[:4])[0]
            latest = max(latest, last)
            data[code] = {"market": mk, "first": int(first), "last": int(last),
                          "rows": size // REC_BYTES}
    for code, v in data.items():
        v["delisted"] = v["last"] < latest or v["last"] < 20260000
    payload = {"built": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
               "latest": latest, "count": len(data), "stocks": data}
    try:
        UNIVERSE.parent.mkdir(parents=True, exist_ok=True)
        UNIVERSE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return payload


def health(latest_db: str | None = None) -> dict[str, Any]:
    """体检用：数据在不在、有多少只、最新到哪天、退市股多少。"""
    if not available():
        return {"ok": False, "detail": f"通达信数据目录不存在：{home()}"}
    u = universe()
    stocks = u.get("stocks") or {}
    main = [c for c in stocks if c.startswith(("60", "00"))]
    delisted = sum(1 for c in main if stocks[c].get("delisted"))
    latest = int(u.get("latest") or 0)
    detail = (f"{len(stocks)} 只（主板 {len(main)}，退市/停牌 {delisted}）｜"
              f"最新 {latest}｜目录 {home()}")
    return {"ok": latest >= 20250101, "latest": latest, "count": len(stocks),
            "main": len(main), "delisted": delisted, "detail": detail}


def iter_main_board(adjusted: bool = True) -> Iterator[tuple[str, dict[str, np.ndarray]]]:
    """遍历全部主板股票（回测用）：(code, arrays)。"""
    root = home()
    for mk in ("sh", "sz"):
        d = root / mk / "lday"
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.day")):
            if not is_main_board_file(f):
                continue
            code = f.stem[2:]
            arr = read_file(f)
            if adjusted:
                arr = adjusted_arrays(arr, code)
            yield code, arr
