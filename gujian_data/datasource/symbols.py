# -*- coding: utf-8 -*-
"""股票代码归一化：6 位代码 / sh600519 / 600519.SH / 名称 → Symbol。"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 主要指数（避免 000001 二义性：个股=平安银行，指数=上证指数）
INDEXES: dict[str, tuple[str, str]] = {
    "上证指数": ("000001", "sh"),
    "上证综指": ("000001", "sh"),
    "沪指": ("000001", "sh"),
    "深证成指": ("399001", "sz"),
    "深成指": ("399001", "sz"),
    "创业板指": ("399006", "sz"),
    "创业板": ("399006", "sz"),
    "沪深300": ("000300", "sh"),
    "上证50": ("000016", "sh"),
    "中证500": ("000905", "sh"),
    "中证1000": ("000852", "sh"),
    "科创50": ("000688", "sh"),
    "北证50": ("899050", "bj"),
    "中证2000": ("932000", "sh"),
    "红利指数": ("000015", "sh"),
}

EXCHANGE_NAMES = {"sh": "上交所", "sz": "深交所", "bj": "北交所"}

_SH_PREFIX = ("600", "601", "603", "605", "688", "689", "900")
_SZ_PREFIX = ("000", "001", "002", "003", "004", "300", "301", "200")
_BJ_PREFIX = ("430", "830", "831", "832", "833", "834", "835", "836", "837",
              "838", "839", "870", "871", "872", "873", "874", "875", "920", "400")


@dataclass(frozen=True)
class Symbol:
    code: str          # 6 位
    exchange: str      # sh / sz / bj
    name: str = ""
    is_index: bool = False

    @property
    def tencent(self) -> str:
        """腾讯/新浪风格：sh600519"""
        return f"{self.exchange}{self.code}"

    @property
    def secid(self) -> str:
        """东财风格：1.600519（1=沪, 0=深, 0=北）"""
        market = "1" if self.exchange == "sh" else "0"
        return f"{market}.{self.code}"

    @property
    def sufix(self) -> str:
        return {"sh": "SH", "sz": "SZ", "bj": "BJ"}[self.exchange]

    @property
    def full(self) -> str:
        return f"{self.code}.{self.sufix}"

    @property
    def board(self) -> str:
        c = self.code
        if self.is_index:
            return "指数"
        if c.startswith(("688", "689")):
            return "科创板"
        if c.startswith(("300", "301")):
            return "创业板"
        if self.exchange == "bj":
            return "北交所"
        if c.startswith(("600", "601", "603", "605")):
            return "沪主板"
        if c.startswith(("000", "001")):
            return "深主板"
        if c.startswith(("002", "003", "004")):
            return "中小板"
        return "其他"

    @property
    def limit_pct(self) -> float:
        """涨跌停幅度（%）"""
        if self.is_index:
            return 0.0
        if self.exchange == "bj":
            return 30.0
        if self.code.startswith(("688", "689", "300", "301")):
            return 20.0
        return 10.0

    def __str__(self) -> str:
        return f"{self.name or self.code}({self.full})" if self.name else self.full


def infer_exchange(code: str) -> str:
    code = code.zfill(6)
    if code.startswith(_SH_PREFIX):
        return "sh"
    if code.startswith(_SZ_PREFIX):
        return "sz"
    if code.startswith(_BJ_PREFIX):
        return "bj"
    # 兜底：5/6/9 开头归沪，其余归深
    return "sh" if code[0] in "569" else "sz"


def is_index_code(code: str) -> bool:
    """仅 399 开头（深证指数系列）在无交易所前缀时可确定为指数；
    沪市 000001 等与个股同号，必须靠 sh 前缀或中文名区分。"""
    return code.startswith("399")


def normalize(text: str) -> Symbol:
    """接受 600519 / sh600519 / 600519.SH / 上证指数 / 沪深300 等写法。"""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("股票代码为空")
    raw = raw.replace("．", ".").replace(" ", "")

    # 名称 → 指数
    if raw in INDEXES:
        code, exch = INDEXES[raw]
        return Symbol(code, exch, raw, True)

    # 带交易所前缀：sh600519 / SH.600519
    m = re.fullmatch(r"(?i)(sh|sz|bj)[.\-]?(\d{6})", raw)
    if m:
        exch, code = m.group(1).lower(), m.group(2)
        known_index = code in {v[0] for v in INDEXES.values() if v[1] == exch}
        return Symbol(code, exch, "", known_index or is_index_code(code))

    # 带后缀：600519.SH / 000001.SZ
    m = re.fullmatch(r"(?i)(\d{6})[.\-](sh|sz|bj|xe|SS)", raw)
    if m:
        code, exch = m.group(1), m.group(2).lower()
        exch = {"xe": "sh", "ss": "sh"}.get(exch, exch)
        return Symbol(code, exch, "", is_index_code(code))

    # 纯数字（自动补零）
    m = re.fullmatch(r"\d{1,6}", raw)
    if m:
        code = raw.zfill(6)
        return Symbol(code, infer_exchange(code), "", is_index_code(code))

    # 其余视为名称，交给上层用全市场列表解析
    return Symbol("", "", raw, False)


def resolve_index_alias(name: str) -> Symbol | None:
    if name in INDEXES:
        code, exch = INDEXES[name]
        return Symbol(code, exch, name, True)
    return None


MARKET_INDEXES: list[tuple[str, Symbol]] = [
    ("上证指数", Symbol("000001", "sh", "上证指数", True)),
    ("深证成指", Symbol("399001", "sz", "深证成指", True)),
    ("创业板指", Symbol("399006", "sz", "创业板指", True)),
    ("沪深300", Symbol("000300", "sh", "沪深300", True)),
    ("科创50", Symbol("000688", "sh", "科创50", True)),
]


def limit_status(symbol: Symbol, price: float | None, prev_close: float | None,
                 name: str = "") -> str:
    """粗略判断涨停/跌停/风险警示。"""
    if price is None or prev_close in (None, 0):
        return ""
    pct = (price / prev_close - 1) * 100
    lim = symbol.limit_pct
    if lim and pct >= lim - 0.35:
        return "涨停"
    if lim and pct <= -lim + 0.35:
        return "跌停"
    if "ST" in (name or "").upper() or (name or "").startswith("*"):
        return "ST"
    return ""
