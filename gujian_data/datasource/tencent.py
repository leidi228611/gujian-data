# -*- coding: utf-8 -*-
"""腾讯行情源（qt.gtimg.cn / web.ifzq.gtimg.cn）——最稳定，作为首选。

实时行情字段（v_sh600519=... 共 88 段，1 起算）：
 3 现价 4 昨收 5 今开 6 成交量(手) 30 时间 31 涨跌额 32 涨跌幅
 33 最高 34 最低 36 成交量(手) 37 成交额(万) 38 换手率 39 市盈率TTM
 43 振幅 44 流通市值(亿) 45 总市值(亿) 46 市净率 47 涨停价 48 跌停价
 49 量比 51 均价 52 市盈率(动) 53 市盈率(静) 67 52周最高 68 52周最低
 72 总股本 73 流通股本
"""
from __future__ import annotations

from typing import Any

from ..utils import to_float
from .net import HttpClient
from .symbols import Symbol

QT_URL = "https://qt.gtimg.cn/q="
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"

INDEX_FIELDS = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
                "sh000300": "沪深300", "sh000688": "科创50", "sh000905": "中证500",
                "sz399905": "中证500", "sh000016": "上证50", "sz399005": "中小100"}


class TencentSource:
    name = "tencent"

    def __init__(self, http: HttpClient):
        self.http = http

    # ---------------- 实时行情 ----------------
    def _parse_qt(self, line: str) -> dict[str, Any] | None:
        head, _, body = line.partition("=")
        body = body.strip().rstrip(";").strip('"')
        if not body or "~" not in body:
            return None
        p = body.split("~")
        if len(p) < 50:
            return None

        def f(i: int) -> float | None:
            return to_float(p[i]) if i < len(p) else None

        price = f(3)
        prev = f(4)
        code6 = (p[2] or "").strip()
        exch = "sh" if head.strip().replace("v_", "").startswith("sh") else (
            "sz" if head.strip().startswith("v_sz") else "bj")
        vol_hand = f(36) or f(6)
        return {
            "code": code6,
            "name": p[1].strip(),
            "exchange": exch,
            "price": price,
            "prev_close": prev,
            "open": f(5),
            "high": f(33),
            "low": f(34),
            "change": f(31),
            "change_pct": f(32),
            "volume": vol_hand,                    # 手
            "amount": (f(37) or 0) * 1e4 if f(37) is not None else None,  # 元
            "turnover_rate": f(38),
            "pe_ttm": f(39),
            "amplitude": f(43),
            "circ_mv": (f(44) or 0) * 1e8 if f(44) is not None else None,
            "total_mv": (f(45) or 0) * 1e8 if f(45) is not None else None,
            "pb": f(46),
            "limit_up": f(47),
            "limit_down": f(48),
            "volume_ratio": f(49),
            "avg_price": f(51),
            "pe_forecast": f(52),
            "pe_static": f(53),
            "high_52w": f(67),
            "low_52w": f(68),
            "total_shares": f(72),
            "circ_shares": f(73),
            "time": p[30] if len(p) > 30 else "",
            "source": self.name,
            "suspended": (price or 0) <= 0 or (vol_hand or 0) <= 0,
        }

    def quotes(self, symbols: list[Symbol]) -> dict[str, dict[str, Any]]:
        if not symbols:
            return {}
        codes = ",".join(s.tencent for s in symbols)
        text = self.http.get_text(QT_URL + codes, encoding="gbk",
                                  referer="https://gu.qq.com/")
        out: dict[str, dict[str, Any]] = {}
        for line in text.splitlines():
            if not line.strip() or "=" not in line:
                continue
            q = self._parse_qt(line)
            if q and q["code"]:
                out[q["code"]] = q
        return out

    def quote(self, symbol: Symbol) -> dict[str, Any]:
        res = self.quotes([symbol])
        q = res.get(symbol.code)
        if not q:
            raise ValueError(f"腾讯未返回 {symbol.full} 行情")
        if not q["name"]:
            q["name"] = INDEX_FIELDS.get(symbol.tencent, "")
        return q

    # ---------------- K 线 ----------------
    def kline(self, symbol: Symbol, days: int = 300, adjust: str = "qfq") -> list[dict[str, Any]]:
        param = f"{symbol.tencent},day,,,{max(int(days), 20)},{adjust}"
        url = f"{KLINE_URL}?param={param}"
        data = self.http.get_json(url, referer="https://gu.qq.com/")
        node = (data.get("data") or {}).get(symbol.tencent) or {}
        rows = node.get(f"{adjust}day") or node.get("day") or []
        if not rows:
            return []
        out: list[dict[str, Any]] = []
        prev_close = to_float(node.get("prec"))
        for row in rows:
            if len(row) < 6:
                continue
            date = str(row[0])
            o, c, h, l, v = (to_float(row[1]), to_float(row[2]), to_float(row[3]),
                             to_float(row[4]), to_float(row[5]))
            if None in (o, c, h, l):
                continue
            bar = {
                "date": date, "open": o, "close": c, "high": h, "low": l,
                "volume": v or 0.0,  # 手
                "amount": to_float(row[6]) if len(row) > 6 else None,
            }
            if prev_close:
                bar["pct"] = (c / prev_close - 1) * 100
            out.append(bar)
            prev_close = c
        # 成交额缺失时用均价估算，保证资金面有值可用
        for bar in out:
            if bar.get("amount") is None:
                bar["amount"] = (bar["close"] + bar["open"]) / 2 * bar["volume"] * 100
        return out

    # ---------------- 分时 ----------------
    def minute(self, symbol: Symbol) -> list[dict[str, Any]]:
        try:
            data = self.http.get_json(f"{MINUTE_URL}?code={symbol.tencent}",
                                      referer="https://gu.qq.com/")
            raw = data["data"][symbol.tencent]["data"]["data"]
        except (KeyError, TypeError, ValueError):
            return []
        out = []
        for item in raw:
            parts = str(item).split()
            if len(parts) < 3:
                continue
            out.append({"time": parts[0], "price": to_float(parts[1]),
                        "volume": to_float(parts[2])})
        return out
