# -*- coding: utf-8 -*-
"""东方财富源：财报/估值/资金流/榜单/公告（datacenter-web 与 np-anotice 稳定）。

push2 系列接口偶尔限流（RemoteDisconnected），由 HttpClient 熔断保护。

clist 字段（fltt=2，值已换算为元/百分比）：
 f2 最新价 f3 涨跌幅 f4 涨跌额 f5 成交量(手) f6 成交额(元) f7 振幅 f8 换手率
 f9 市盈率(动) f10 量比 f11 5分钟涨跌 f12 代码 f13 市场 f14 名称 f15 最高
 f16 最低 f17 今开 f18 昨收 f20 总市值 f21 流通市值 f22 涨速 f23 市净率
 f24 60日涨跌幅 f25 年初至今涨跌幅 f62 主力净流入 f66 超大单净流入
 f72 大单净流入 f78 中单净流入 f84 小单净流入 f115 市盈率TTM f184 主力净占比
"""
from __future__ import annotations

import time
import urllib.parse
from typing import Any

from ..utils import to_float
from .net import HttpClient
from .symbols import Symbol

PUSH2 = "https://push2.eastmoney.com/api/qt"
PUSH2HIS = "https://push2his.eastmoney.com/api/qt"
DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"
NOTICE = "https://np-anotice-stock.eastmoney.com/api/security/ann"

DC_REFERER = "https://data.eastmoney.com/"
QT_REFERER = "https://quote.eastmoney.com/"

ALL_A = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

QUOTE_FIELDS = ",".join([
    "f43", "f44", "f45", "f46", "f47", "f48", "f57", "f58", "f59", "f60",
    "f84", "f85", "f92", "f107", "f116", "f117", "f162", "f164", "f167",
    "f168", "f169", "f170", "f171", "f127",
])

RANK_FIELDS = ",".join([
    "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f12", "f13", "f14",
    "f20", "f21", "f23", "f24", "f25", "f62", "f66", "f72", "f78", "f84",
    "f115", "f184",
])


class EastmoneySource:
    name = "eastmoney"

    def __init__(self, http: HttpClient):
        self.http = http

    # ============ 实时行情 ============
    def quote(self, symbol: Symbol) -> dict[str, Any]:
        url = f"{PUSH2}/stock/get?secid={symbol.secid}&fields={QUOTE_FIELDS}&fltt=2&invt=2"
        d = (self.http.get_json(url, referer=QT_REFERER) or {}).get("data") or {}
        if not d:
            raise ValueError(f"东财未返回 {symbol.full} 行情")
        price = to_float(d.get("f43"))
        prev = to_float(d.get("f60"))
        change = to_float(d.get("f169"))
        return {
            "code": d.get("f57") or symbol.code,
            "name": d.get("f58") or symbol.name,
            "exchange": symbol.exchange,
            "price": price,
            "prev_close": prev,
            "open": to_float(d.get("f46")),
            "high": to_float(d.get("f44")),
            "low": to_float(d.get("f45")),
            "change": change,
            "change_pct": to_float(d.get("f170")),
            "volume": to_float(d.get("f47")),
            "amount": to_float(d.get("f48")),
            "turnover_rate": to_float(d.get("f168")),
            "pe_ttm": to_float(d.get("f164")),
            "pe_forecast": to_float(d.get("f162")),
            "pb": to_float(d.get("f167")),
            "amplitude": to_float(d.get("f171")),
            "total_mv": to_float(d.get("f116")),
            "circ_mv": to_float(d.get("f117")),
            "total_shares": to_float(d.get("f84")),
            "circ_shares": to_float(d.get("f85")),
            "industry": d.get("f127") if isinstance(d.get("f127"), str) else None,
            "volume_ratio": None,
            "limit_up": None, "limit_down": None,
            "high_52w": None, "low_52w": None, "avg_price": None,
            "pe_static": None,
            "time": str(int(time.time())),
            "source": self.name,
            "suspended": (price or 0) <= 0,
        }

    def kline(self, symbol: Symbol, days: int = 300, adjust: str = "qfq") -> list[dict[str, Any]]:
        fqt = {"qfq": 1, "hfq": 2, "none": 0}.get(adjust, 1)
        url = (f"{PUSH2HIS}/stock/kline/get?secid={symbol.secid}"
               f"&fields1=f1,f2,f3,f4,f5,f6"
               f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
               f"&klt=101&fqt={fqt}&end=20500101&lmt={max(int(days), 20)}")
        d = (self.http.get_json(url, referer=QT_REFERER) or {}).get("data") or {}
        out = []
        for row in d.get("klines") or []:
            p = str(row).split(",")
            if len(p) < 6:
                continue
            out.append({
                "date": p[0], "open": to_float(p[1]), "close": to_float(p[2]),
                "high": to_float(p[3]), "low": to_float(p[4]),
                "volume": to_float(p[5]) or 0.0,
                "amount": to_float(p[6]) if len(p) > 6 else None,
                "amplitude": to_float(p[7]) if len(p) > 7 else None,
                "pct": to_float(p[8]) if len(p) > 8 else None,
                "change": to_float(p[9]) if len(p) > 9 else None,
                "turnover_rate": to_float(p[10]) if len(p) > 10 else None,
            })
        return out

    # ============ 资金流 ============
    def moneyflow(self, symbol: Symbol, days: int = 30) -> list[dict[str, Any]]:
        url = (f"{PUSH2HIS}/stock/fflow/daykline/get?secid={symbol.secid}&lmt={days}&klt=101"
               f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63")
        d = (self.http.get_json(url, referer=QT_REFERER) or {}).get("data") or {}
        out = []
        for row in d.get("klines") or []:
            p = str(row).split(",")
            if len(p) < 6:
                continue
            out.append({
                "date": p[0],
                "main_net": to_float(p[1]) or 0.0,        # 主力净流入(元)
                "small_net": to_float(p[2]) or 0.0,
                "middle_net": to_float(p[3]) or 0.0,
                "big_net": to_float(p[4]) or 0.0,
                "super_net": to_float(p[5]) or 0.0,
                "main_pct": to_float(p[6]) if len(p) > 6 else None,
                "small_pct": to_float(p[7]) if len(p) > 7 else None,
                "middle_pct": to_float(p[8]) if len(p) > 8 else None,
                "big_pct": to_float(p[9]) if len(p) > 9 else None,
                "super_pct": to_float(p[10]) if len(p) > 10 else None,
                "close": to_float(p[11]) if len(p) > 11 else None,
                "pct": to_float(p[12]) if len(p) > 12 else None,
                "source": self.name,
            })
        return out

    # ============ 榜单 / 板块 ============
    def rank(self, sort_field: str = "f3", limit: int = 100, fs: str = ALL_A,
             descending: bool = True, fields: str = RANK_FIELDS) -> list[dict[str, Any]]:
        url = (f"{PUSH2}/clist/get?pn=1&pz={limit}&po={1 if descending else 0}&np=1"
               f"&fltt=2&invt=2&fid={sort_field}&fs={urllib.parse.quote(fs, safe=',:')}"
               f"&fields={fields}")
        d = (self.http.get_json(url, referer=QT_REFERER) or {}).get("data") or {}
        diff = d.get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        rows = []
        for x in diff:
            code = str(x.get("f12") or "")
            if len(code) != 6:
                continue
            rows.append({
                "code": code,
                "name": str(x.get("f14") or "").strip(),
                "price": to_float(x.get("f2")),
                "change_pct": to_float(x.get("f3")),
                "change": to_float(x.get("f4")),
                "volume": to_float(x.get("f5")),
                "amount": to_float(x.get("f6")),
                "amplitude": to_float(x.get("f7")),
                "turnover_rate": to_float(x.get("f8")),
                "pe_forecast": to_float(x.get("f9")),
                "volume_ratio": to_float(x.get("f10")),
                "total_mv": to_float(x.get("f20")),
                "circ_mv": to_float(x.get("f21")),
                "pb": to_float(x.get("f23")),
                "chg_60d": to_float(x.get("f24")),
                "chg_ytd": to_float(x.get("f25")),
                "main_net": to_float(x.get("f62")),
                "super_net": to_float(x.get("f66")),
                "big_net": to_float(x.get("f72")),
                "middle_net": to_float(x.get("f78")),
                "small_net": to_float(x.get("f84")),
                "pe_ttm": to_float(x.get("f115")),
                "main_pct": to_float(x.get("f184")),
                "market": to_float(x.get("f13")),
            })
        return rows

    def whole_market(self, limit: int = 6000) -> list[dict[str, Any]]:
        return self.rank("f3", limit=limit, fs=ALL_A)

    def boards(self, kind: str = "industry", limit: int = 60) -> list[dict[str, Any]]:
        fs = {"industry": "m:90+t:2", "concept": "m:90+t:3", "region": "m:90+t:1"}.get(kind, "m:90+t:2")
        url = (f"{PUSH2}/clist/get?pn=1&pz={limit}&po=1&np=1&fltt=2&invt=2&fid=f3"
               f"&fs={urllib.parse.quote(fs, safe=',:')}"
               f"&fields=f2,f3,f4,f8,f12,f14,f20,f62,f104,f105,f128,f136,f140,f141,f207,f208,f209")
        d = (self.http.get_json(url, referer=QT_REFERER) or {}).get("data") or {}
        diff = d.get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        out = []
        for x in diff:
            out.append({
                "code": str(x.get("f12") or ""),
                "name": str(x.get("f14") or ""),
                "change_pct": to_float(x.get("f3")),
                "turnover_rate": to_float(x.get("f8")),
                "total_mv": to_float(x.get("f20")),
                "main_net": to_float(x.get("f62")),
                "up_count": to_float(x.get("f104")),
                "down_count": to_float(x.get("f105")),
                "leader": str(x.get("f128") or ""),
                "leader_code": str(x.get("f140") or ""),
                "leader_pct": to_float(x.get("f136")),
            })
        return out

    def board_members(self, board_code: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.rank("f3", limit=limit, fs=f"b:{board_code}")

    def stock_concepts(self, symbol: Symbol, limit: int = 20) -> list[dict[str, Any]]:
        url = (f"{PUSH2}/slist/get?fltt=2&invt=2&secid={symbol.secid}&spt=3"
               f"&fields=f12,f13,f14,f3,f152&pn=1&pz={limit}&po=1&fid=f3")
        d = (self.http.get_json(url, referer=QT_REFERER) or {}).get("data") or {}
        diff = d.get("diff") or {}
        items = diff.values() if isinstance(diff, dict) else diff
        return [{"code": str(x.get("f12")), "name": str(x.get("f14")),
                 "change_pct": to_float(x.get("f3"))} for x in items if x.get("f12")]

    # ============ 财报 / 估值 / 分红（datacenter） ============
    def _dc(self, report: str, *, filters: str | None = None, columns: str = "ALL",
            page_size: int = 10, sort_columns: str | None = None,
            sort_types: str = "-1", page_number: int = 1) -> list[dict[str, Any]]:
        # filter 必须整体百分号编码（含 = 和 "），否则 datacenter 返回 400
        parts = [
            f"reportName={report}",
            f"columns={columns}",
            f"pageNumber={page_number}",
            f"pageSize={page_size}",
            "source=WEB",
            "client=WEB",
        ]
        if filters:
            parts.append("filter=" + urllib.parse.quote(filters, safe=""))
        if sort_columns:
            parts.append(f"sortColumns={sort_columns}")
            parts.append(f"sortTypes={sort_types}")
        url = f"{DATACENTER}?{'&'.join(parts)}"
        d = self.http.get_json(url, referer=DC_REFERER) or {}
        if not d.get("success"):
            return []
        return (d.get("result") or {}).get("data") or []

    def financial_reports(self, code: str, limit: int = 8) -> list[dict[str, Any]]:
        """业绩报表（营收/净利/同比/ROE/毛利率/EPS）。"""
        rows = self._dc("RPT_LICO_FN_CPD", filters=f'(SECURITY_CODE="{code}")',
                        page_size=limit, sort_columns="REPORTDATE")
        out = []
        for r in rows:
            out.append({
                "report_date": str(r.get("REPORTDATE") or "")[:10],
                "period": r.get("DATATYPE") or "",
                "revenue": to_float(r.get("TOTAL_OPERATE_INCOME")),
                "net_profit": to_float(r.get("PARENT_NETPROFIT")),
                "revenue_yoy": to_float(r.get("YSTZ")),
                "profit_yoy": to_float(r.get("SJLTZ")),
                "roe": to_float(r.get("WEIGHTAVG_ROE")),
                "gross_margin": to_float(r.get("XSMLL")),
                "eps": to_float(r.get("BASIC_EPS")),
                "bps": to_float(r.get("BPS")),
                "ocf_ps": to_float(r.get("MGJYXJJE")),
                "industry": r.get("PUBLISHNAME") or r.get("BOARD_NAME") or "",
                "board_code": r.get("BOARD_CODE") or "",
                "announced": str(r.get("NOTICE_DATE") or "")[:10],
            })
        return out

    def main_indicators(self, code: str, limit: int = 8) -> list[dict[str, Any]]:
        """主要财务指标（负债率/流动比率/净利率/现金流等）。"""
        rows = self._dc("RPT_F10_FINANCE_MAINFINADATA",
                        filters=f'(SECURITY_CODE="{code}")',
                        page_size=limit, sort_columns="REPORT_DATE")
        out = []
        for r in rows:
            out.append({
                "report_date": str(r.get("REPORT_DATE") or "")[:10],
                "period": r.get("REPORT_DATE_NAME") or "",
                "report_type": r.get("REPORT_TYPE") or "",
                "eps": to_float(r.get("EPSJB")),
                "bps": to_float(r.get("BPS")),
                "ocf_ps": to_float(r.get("MGJYXJJE")),
                "revenue": to_float(r.get("TOTALOPERATEREVE")),
                "gross_profit": to_float(r.get("MLR")),
                "net_profit": to_float(r.get("PARENTNETPROFIT")),
                "revenue_yoy": to_float(r.get("TOTALOPERATEREVETZ")),
                "profit_yoy": to_float(r.get("PARENTNETPROFITTZ")),
                "roe": to_float(r.get("ROEJQ")),
                "roa": to_float(r.get("ZZCJLL")),
                "net_margin": to_float(r.get("XSJLL")),
                "gross_margin": to_float(r.get("XSMLL")),
                "debt_ratio": to_float(r.get("ZCFZL")),
                "current_ratio": to_float(r.get("LD")),
                "quick_ratio": to_float(r.get("SD")),
                "ocf_ratio": to_float(r.get("JYXJLYYSR")),
                "cash_flow_ratio": to_float(r.get("XJLLB")),
                "asset_turnover": to_float(r.get("ZZCZZTS")),
                "tax_rate": to_float(r.get("TAXRATE")),
            })
        return out

    def valuation_history(self, code: str, days: int = 750) -> list[dict[str, Any]]:
        """历史估值（PE/PB 分位用）。"""
        rows = self._dc("RPT_VALUEANALYSIS_DET", filters=f'(SECURITY_CODE="{code}")',
                        page_size=days, sort_columns="TRADE_DATE")
        out = []
        for r in rows:
            out.append({
                "date": str(r.get("TRADE_DATE") or "")[:10],
                "close": to_float(r.get("CLOSE_PRICE")),
                "pe_ttm": to_float(r.get("PE_TTM")),
                "pe_static": to_float(r.get("PE_LAR")),
                "pb": to_float(r.get("PB_MRQ")),
                "ps": to_float(r.get("PS_TTM")),
                "pcf": to_float(r.get("PCF_OCF_TTM")),
                "peg": to_float(r.get("PEG_CAR")),
                "dividend_yield": to_float(r.get("ZXGXL")),
                "total_mv": to_float(r.get("TOTAL_MARKET_CAP")),
                "industry": r.get("BOARD_NAME") or "",
            })
        out.reverse()  # 旧 → 新
        return out

    def dividends(self, code: str, limit: int = 6) -> list[dict[str, Any]]:
        rows = self._dc("RPT_SHAREBONUS_DET", filters=f'(SECURITY_CODE="{code}")',
                        page_size=limit, sort_columns="REPORT_DATE")
        out = []
        for r in rows:
            out.append({
                "report_date": str(r.get("REPORT_DATE") or "")[:10],
                "plan": r.get("IMPL_PLAN_PROFILE") or "",
                "progress": r.get("ASSIGN_PROGRESS") or "",
                "notice_date": str(r.get("PLAN_NOTICE_DATE") or "")[:10],
                "ex_date": str(r.get("EX_DIVIDEND_DATE") or "")[:10],
                "pretax_bonus": to_float(r.get("PRETAX_BONUS_RMB")),
            })
        return out

    def concepts_of(self, code: str, suffix: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._dc("RPT_F10_CORETHEME_BOARDTYPE", filters=f'(SECUCODE="{code}.{suffix}")',
                        page_size=limit)
        return [{"code": r.get("BOARD_CODE"), "name": r.get("BOARD_NAME"),
                 "reason": r.get("SELECTED_BOARD_REASON")} for r in rows]

    # ============ 公告 ============
    def announcements(self, code: str, limit: int = 10) -> list[dict[str, Any]]:
        url = (f"{NOTICE}?page_size={limit}&page_index=1&ann_type=A&client_source=web"
               f"&stock_list={code}&f_node=0&s_node=0")
        d = self.http.get_json(url, referer=DC_REFERER) or {}
        items = ((d.get("data") or {}).get("list")) or []
        out = []
        for it in items:
            out.append({
                "date": str(it.get("notice_date") or "")[:10],
                "title": (it.get("title") or "").strip(),
                "type": ((it.get("columns") or [{}])[0] or {}).get("column_name") or "",
                "url": f"https://data.eastmoney.com/notices/detail/{code}/{it.get('art_code')}.html"
                if it.get("art_code") else "",
            })
        return out
