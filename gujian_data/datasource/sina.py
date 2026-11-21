# -*- coding: utf-8 -*-
"""新浪行情源（hq.sinajs.cn / money.finance.sina.com.cn）——备源。

新浪日线 volume 单位为「股」，统一换算成「手」。
"""
from __future__ import annotations

from typing import Any

from ..utils import to_float
from .net import HttpClient
from .symbols import Symbol

QT_URL = "https://hq.sinajs.cn/list="
KLINE_URL = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "CN_MarketData.getKLineData")
LIST_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData")
FLOW_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "MoneyFlow.ssl_qsfx_lscjfb")


class SinaSource:
    name = "sina"
    REFERER = "https://finance.sina.com.cn"

    def __init__(self, http: HttpClient):
        self.http = http

    def quote(self, symbol: Symbol) -> dict[str, Any]:
        text = self.http.get_text(QT_URL + symbol.tencent, encoding="gbk", referer=self.REFERER)
        body = text.partition("=")[2].strip().strip(";").strip('"')
        fields = body.split(",")
        if len(fields) < 32:
            raise ValueError(f"新浪未返回 {symbol.full} 行情")
        price = to_float(fields[3])
        prev = to_float(fields[2])
        vol_shares = to_float(fields[8]) or 0.0     # 股
        amount = to_float(fields[9])
        change = (price - prev) if (price is not None and prev) else None
        return {
            "code": symbol.code,
            "name": fields[0].strip(),
            "exchange": symbol.exchange,
            "price": price,
            "prev_close": prev,
            "open": to_float(fields[1]),
            "high": to_float(fields[4]),
            "low": to_float(fields[5]),
            "change": change,
            "change_pct": (change / prev * 100) if (change is not None and prev) else None,
            "volume": vol_shares / 100.0,          # 手
            "amount": amount,
            "turnover_rate": None,
            "pe_ttm": None,
            "amplitude": ((to_float(fields[4]) - to_float(fields[5])) / prev * 100)
            if (prev and to_float(fields[4]) is not None and to_float(fields[5]) is not None) else None,
            "circ_mv": None, "total_mv": None, "pb": None,
            "limit_up": None, "limit_down": None, "volume_ratio": None,
            "pe_forecast": None, "pe_static": None,
            "high_52w": None, "low_52w": None,
            "total_shares": None, "circ_shares": None,
            "time": f"{fields[30]} {fields[31]}" if len(fields) > 31 else "",
            "source": self.name,
            "suspended": (price or 0) <= 0,
        }

    def kline(self, symbol: Symbol, days: int = 300, adjust: str = "qfq") -> list[dict[str, Any]]:
        url = (f"{KLINE_URL}?symbol={symbol.tencent}&scale=240&ma=no&datalen={max(int(days), 20)}")
        data = self.http.get_json(url, referer=self.REFERER)
        if not isinstance(data, list):
            return []
        out: list[dict[str, Any]] = []
        prev_close: float | None = None
        for row in data:
            o = to_float(row.get("open"))
            c = to_float(row.get("close"))
            h = to_float(row.get("high"))
            l = to_float(row.get("low"))
            if None in (o, c, h, l):
                continue
            v_shares = to_float(row.get("volume")) or 0.0
            bar = {
                "date": str(row.get("day"))[:10], "open": o, "close": c, "high": h, "low": l,
                "volume": v_shares / 100.0,
                "amount": (h + l + o + c) / 4 * v_shares,
            }
            if prev_close:
                bar["pct"] = (c / prev_close - 1) * 100
            out.append(bar)
            prev_close = c
        return out

    # ---------------- 资金流（历史逐日） ----------------
    def moneyflow(self, symbol: Symbol, days: int = 40) -> list[dict[str, Any]]:
        """新浪资金流分档：r0 超大单 / r1 大单 / r2 中单 / r3 小单。

        主力口径 = 超大单 + 大单，与东财口径一致（阈值略有差异）。
        """
        url = (f"{FLOW_URL}?page=1&num={max(int(days), 5)}&sort=opendate&asc=0"
               f"&daima={symbol.tencent}")
        data = self.http.get_json(url, referer="https://vip.stock.finance.sina.com.cn/")
        if not isinstance(data, list):
            return []
        out: list[dict[str, Any]] = []
        for row in reversed(data):          # 旧 → 新
            super_net = to_float(row.get("r0_net")) or 0.0
            big_net = to_float(row.get("r1_net")) or 0.0
            mid_net = to_float(row.get("r2_net")) or 0.0
            small_net = to_float(row.get("r3_net")) or 0.0
            gross = sum(to_float(row.get(k)) or 0.0 for k in ("r0", "r1", "r2", "r3"))
            main_net = super_net + big_net
            out.append({
                "date": str(row.get("opendate") or "")[:10],
                "main_net": main_net,
                "small_net": small_net,
                "middle_net": mid_net,
                "big_net": big_net,
                "super_net": super_net,
                "main_pct": (main_net / gross * 100) if gross else None,
                "small_pct": None, "middle_pct": None,
                "big_pct": None, "super_pct": None,
                "close": to_float(row.get("trade")),
                "pct": (to_float(row.get("changeratio")) or 0.0) * 100,
                "amount": gross or None,
                "source": self.name,
            })
        return out

    # ---------------- 全市场列表（东财不可用时的兜底） ----------------
    def stock_list(self, pages: int = 60, num: int = 100) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(1, pages + 1):
            url = (f"{LIST_URL}?page={page}&num={num}&sort=symbol&asc=1&node=hs_a"
                   f"&symbol=&_s_r_a=page")
            try:
                data = self.http.get_json(url, referer="https://vip.stock.finance.sina.com.cn/")
            except Exception:
                break
            if not isinstance(data, list) or not data:
                break
            for item in data:
                code = str(item.get("code") or "").strip()
                if len(code) != 6:
                    continue
                # 注意单位：新浪 mktcap/nmc 为「万元」，volume 为「股」
                rows.append({
                    "code": code,
                    "name": str(item.get("name") or "").strip(),
                    "price": to_float(item.get("trade")),
                    "change_pct": to_float(item.get("changepercent")),
                    "volume": (to_float(item.get("volume")) or 0.0) / 100.0,
                    "amount": to_float(item.get("amount")),
                    "turnover_rate": to_float(item.get("turnoverratio")),
                    "pe_ttm": to_float(item.get("per")),
                    "pb": to_float(item.get("pb")),
                    "total_mv": (to_float(item.get("mktcap")) or 0.0) * 1e4,
                    "circ_mv": (to_float(item.get("nmc")) or 0.0) * 1e4,
                })
            if len(data) < num:
                break
        return rows
