# -*- coding: utf-8 -*-
"""数据中枢：多源降级 + 磁盘缓存 + 统一数据契约。

对外只暴露 DataHub，分析层不关心数据来自腾讯、新浪还是东财。
任一数据源失败都不会让整份报告失败：缺失项进入 meta.warnings。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable

from ..config import Config
from ..utils import Log, is_trading_time, market_phase, now_str, to_float
from .cache import Cache
from .eastmoney import EastmoneySource
from .hithink import HithinkSource
from .net import HttpClient
from .sina import SinaSource
from .symbols import MARKET_INDEXES, Symbol, normalize
from .tencent import TencentSource

__all__ = ["DataHub", "Symbol", "normalize", "MARKET_INDEXES"]


class DataHub:
    def __init__(self, cfg: Config, log: Log | None = None):
        self.cfg = cfg
        self.log = log or Log(verbose=False)
        self.http = HttpClient(
            timeout=cfg.ds.get("timeout", 12),
            retries=cfg.ds.get("retries", 3),
            min_interval=cfg.ds.get("min_interval", 0.18),
            host_cooldown=cfg.ds.get("host_cooldown", 90),
            log=self.log,
        )
        self.cache = Cache(cfg.cache_dir, enabled=cfg.data["cache"]["enabled"])
        self._sources: dict[str, Any] = {
            "tencent": TencentSource(self.http),
            "sina": SinaSource(self.http),
            "eastmoney": EastmoneySource(self.http),
            # 2026-11-21：同花顺 CLI 快照（买卖取价新增一路，批量为多只一次调用）
            "hithink": HithinkSource(self.http),
        }
        self.order: list[str] = [s for s in cfg.ds.get("order", ["tencent", "sina", "eastmoney"])
                                 if s in self._sources]
        self.today_calls = 0
        self.last_errors: list[str] = []
        self._idx: dict[str, str] | None = None
        self._board_down_until = 0.0

    # ---------------- 基础调用 ----------------
    def _ttl(self, kind: str, default: float = 300) -> float:
        ttl = to_float(self.cfg.ttl.get(kind), default) or default
        return ttl

    def _call(self, kind: str, key: str, method: str, *args: Any,
              ttl: float | None = None, sources: Iterable[str] | None = None,
              validate: Callable[[Any], bool] | None = None, **kwargs: Any) -> Any:
        """按源顺序调用 method，成功即缓存返回。"""
        ttl = self._ttl(kind) if ttl is None else ttl
        sources = list(sources or self.order)
        errors: list[str] = []

        def producer() -> Any:
            for name in sources:
                src = self._sources.get(name)
                if src is None or not self.http.host_available(self._host_of(name)):
                    continue
                fn = getattr(src, method, None)
                if fn is None:
                    continue
                try:
                    data = fn(*args, **kwargs)
                except Exception as exc:                       # noqa: BLE001
                    errors.append(f"{name}.{method}: {type(exc).__name__}")
                    continue
                if data is None:
                    continue
                if validate and not validate(data):
                    errors.append(f"{name}.{method}: 数据不合格")
                    continue
                return data
            raise RuntimeError(f"{method} 全部数据源失败 ({(args[0] if args else '')}) "
                               f"{'; '.join(errors[:4])}")

        try:
            return self.cache.remember(kind, key, ttl, producer)
        except Exception as exc:
            self.last_errors.append(str(exc))
            raise

    @staticmethod
    def _host_of(source: str) -> str:
        return {"tencent": "qt.gtimg.cn", "sina": "hq.sinajs.cn",
                "eastmoney": "push2.eastmoney.com"}.get(source, source)

    # ---------------- 买入窗口取价：显式 failover ----------------
    BUY_ORDER = ("eastmoney", "tencent", "hithink", "sina")

    @staticmethod
    def _error_status(exc: Exception) -> str:
        """HTTP 状态码（有就带上），否则给异常类型。"""
        code = getattr(exc, "code", None)
        if code is None:
            import re
            m = re.search(r"HTTP\s+(\d{3})", str(exc))
            code = m.group(1) if m else None
        return f"HTTP {code}" if code else type(exc).__name__

    def _failure_detail(self, name: str, exc: Exception,
                        symbol: Symbol | None = None) -> str:
        host = self._host_of(name)
        target = f" ({symbol.full})" if symbol is not None else ""
        return f"{name}@{host} {self._error_status(exc)}: {str(exc)[:80]}{target}"

    def _record_source_failure(self, name: str, exc: Exception,
                               symbol: Symbol | None = None,
                               side: str = "买入") -> str:
        """单源失败：写 incidents（host + 状态码），让「哪家挂了」可追溯。"""
        detail = self._failure_detail(name, exc, symbol)
        self.last_errors.append(detail)
        self.log.warn(f"{side}行情降级：{detail}")
        try:
            from ..incidents import record
            record("datasource", f"{side}行情源 {name} 失败", detail,
                   evidence=f"{name} {getattr(symbol, 'full', '')}".strip())
        except Exception:                            # noqa: BLE001
            pass
        return detail

    def _quote_failover(self, symbol: Symbol, side: str, tag: str,
                        order: Iterable[str] | None = None) -> dict[str, Any]:
        """买入/卖出窗口共用的逐源取价：东财 → 腾讯 → 同花顺 → 新浪。

        卖出与买入行为一致：逐源记录 host+状态码，全挂时抛出明确原因；
        重试次数/间隔走 datasource.buy_retries / buy_retry_interval 配置。
        """
        order = [s for s in (list(order) if order else
                             (self.cfg.ds.get("buy_order") or list(self.BUY_ORDER)))
                 if s in self._sources]
        try:
            attempts = max(1, int(self.cfg.ds.get("buy_retries") or 1))
        except (TypeError, ValueError):
            attempts = 1
        try:
            interval = max(0.0, float(self.cfg.ds.get("buy_retry_interval") or 0))
        except (TypeError, ValueError):
            interval = 0.0
        failures: list[str] = []

        for attempt in range(attempts):
            for name in order:
                src = self._sources.get(name)
                if src is None:
                    continue
                try:
                    q = src.quote(symbol)
                except Exception as exc:                       # noqa: BLE001
                    failures.append(self._record_source_failure(
                        name, exc, symbol, side=side))
                    continue
                try:
                    valid = bool(q) and float(q.get("price") or 0) > 0
                except (TypeError, ValueError):
                    valid = False
                if not valid:
                    failures.append(self._record_source_failure(
                        name, ValueError("无有效报价"), symbol, side=side))
                    continue
                out = dict(q)
                out.setdefault("source", name)
                out[tag] = {"order": order, "source": name,
                            "attempt": attempt + 1,
                            "failures": failures[:]}
                self.log.info(f"{side}行情 {symbol.full} 取自 {name}（第 {attempt + 1} 轮）")
                try:
                    from .. import ds_diag
                    ds_diag.note("quote_ok", side=side, symbol=symbol.full,
                                 source=name, attempt=attempt + 1,
                                 price=out.get("price"), chg=out.get("change_pct"),
                                 tried=order, failures=failures[:6])
                except Exception:                      # noqa: BLE001
                    pass
                return out
            if attempt < attempts - 1 and interval > 0:
                time.sleep(interval)

        uniq = list(dict.fromkeys(failures))
        try:
            from .. import ds_diag
            ds_diag.note("quote_fail", side=side, symbol=symbol.full,
                         tried=order, attempts=attempts, failures=uniq[:6])
        except Exception:                              # noqa: BLE001
            pass
        raise RuntimeError(
            f"{side}行情全部数据源失败 {symbol.full}："
            + ("；".join(uniq) if uniq else "没有可用的数据源"))

    def quote_buy(self, symbol: Symbol | str,
                  order: Iterable[str] | None = None) -> dict[str, Any]:
        """买入窗口取价：东财 → 腾讯 → 同花顺 → 新浪 failover。

        2026-11-16：接受字符串代码（内部 normalize）。此前传 str 会在失败路径
        抛 AttributeError: 'str' object has no attribute 'full'。
        """
        if isinstance(symbol, str):
            symbol = normalize(symbol)
        return self._quote_failover(symbol, "买入", "buy_quote", order)

    def quote_sell(self, symbol: Symbol | str,
                   order: Iterable[str] | None = None) -> dict[str, Any]:
        """卖出窗口取价：与 quote_buy 同一套 failover（东财 → 腾讯 → 同花顺 → 新浪）。"""
        if isinstance(symbol, str):
            symbol = normalize(symbol)
        return self._quote_failover(symbol, "卖出", "sell_quote", order)

    # ---------------- 代码解析 ----------------
    def _name_index(self) -> dict[str, str]:
        if self._idx is None:
            try:
                self._idx = {r["code"]: r["name"] for r in self.stock_list()}
            except Exception:
                self._idx = {}
        return self._idx

    def resolve(self, text: str) -> Symbol:
        """代码/名称 → Symbol，并校验代码是否真实存在。"""
        sym = normalize(text)
        if sym.code:
            if not sym.is_index:
                index = self._name_index()
                if index and sym.code not in index:
                    raise ValueError(f"股票代码 {sym.code} 不存在（或已退市），请检查")
                if index and not sym.name:
                    sym = Symbol(sym.code, sym.exchange, index.get(sym.code, ""),
                                 sym.is_index)
            return sym
        query = (sym.name or "").strip()
        index = self._name_index()
        for code, name in index.items():
            if name == query or code == query:
                return normalize(code)
        for code, name in index.items():
            if query and query in name:
                return normalize(code)
        raise ValueError(f"找不到标的「{text}」，请检查代码或名称")

    def stock_list(self, force: bool = False) -> list[dict[str, Any]]:
        """全市场股票列表（统一字段与单位），缓存 1 天。"""
        # 交易时段用短 TTL：早盘快照不能用来做尾盘选股（审计缺陷 #1）
        import time as _t
        _now = _t.localtime()
        _in_session = (_t.strftime("%H:%M") >= "09:15" and _t.strftime("%H:%M") <= "15:05")
        ttl = self._ttl("stocklist", 120 if _in_session else 86400)
        if force:
            self.cache.clear("stocklist")

        def producer() -> list[dict[str, Any]]:
            try:
                rows = self._sources["eastmoney"].whole_market(6000)
                if len(rows) > 1000:
                    return self._normalize_list(rows)
            except Exception as exc:
                self.log.info(f"东财全市场列表不可用（{type(exc).__name__}），改用新浪")
            return self._normalize_list(self._sources["sina"].stock_list())

        try:
            rows = self.cache.remember("stocklist", "all_a", ttl, producer)
        except Exception as exc:
            self.log.warn(f"无法获取全市场列表：{exc}")
            return []
        return [r for r in rows if r.get("code")]

    @staticmethod
    def _normalize_list(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """补齐字段，保证下游拿到的结构一致。"""
        keys = ("price", "change_pct", "volume", "amount", "turnover_rate", "pe_ttm",
                "pb", "total_mv", "circ_mv", "main_net", "main_pct", "chg_60d",
                "chg_ytd", "volume_ratio", "amplitude")
        out = []
        for r in rows:
            code = str(r.get("code") or "")
            if len(code) != 6:
                continue
            row = {"code": code, "name": str(r.get("name") or "").strip()}
            for k in keys:
                row[k] = r.get(k)
            out.append(row)
        return out

    # ---------------- 行情 ----------------
    def quote(self, symbol: Symbol) -> dict[str, Any]:
        ttl = self._ttl("quote", 120) if is_trading_time() else max(self._ttl("quote", 120), 300)
        try:
            q = self._call("quote", f"{symbol.tencent}", "quote", symbol, ttl=ttl)
        except Exception:
            raise
        q.setdefault("industry", None)
        q["code"] = symbol.code
        return q

    def quotes(self, symbols: Iterable[Symbol]) -> dict[str, dict[str, Any]]:
        symbols = list(symbols)
        out: dict[str, dict[str, Any]] = {}
        # 腾讯支持批量，一次请求拿全部
        try:
            out = self._sources["tencent"].quotes(symbols)
        except Exception:
            out = {}
        # 批量补漏：整批失败时先重试批量（分块），只有少量缺失才逐只补
        missing = [s for s in symbols if s.code not in out]
        for _ in range(3):
            if len(missing) <= 20:
                break
            got = {}
            for i in range(0, len(missing), 150):
                chunk = missing[i:i + 150]
                try:
                    got.update(self._sources["tencent"].quotes(chunk))
                except Exception:                       # noqa: BLE001
                    pass
            out.update(got)
            missing = [s for s in missing if s.code not in out]
            if not got:
                break
        # 最后才逐只补（数量已经很小）
        for s in missing[:80]:
            try:
                out[s.code] = self.quote(s)
            except Exception:                           # noqa: BLE001
                continue
        return out

    def kline(self, symbol: Symbol, days: int = 300) -> list[dict[str, Any]]:
        data = self._call("kline", f"{symbol.tencent}_{days}", "kline", symbol, days,
                          ttl=self._ttl("kline", 43200),
                          validate=lambda d: isinstance(d, list) and len(d) >= 5)
        return data

    # ---------------- 衍生数据 ----------------
    def moneyflow(self, symbol: Symbol, days: int = 40) -> list[dict[str, Any]]:
        try:
            return self._call("moneyflow", f"{symbol.tencent}_{days}", "moneyflow", symbol, days,
                              ttl=self._ttl("moneyflow", 1800),
                              sources=["eastmoney", "sina"],
                              validate=lambda d: isinstance(d, list) and len(d) >= 3)
        except Exception:
            return []

    def announcements(self, code: str, limit: int = 10) -> list[dict[str, Any]]:
        try:
            return self._call("announcement", f"{code}_{limit}", "announcements", code, limit,
                              ttl=self._ttl("announcement", 7200), sources=["eastmoney"])
        except Exception:
            return []

    def financials(self, symbol: Symbol) -> dict[str, Any]:
        """财报三件套 + 分红，任一失败降级为空。"""
        code = symbol.code
        ttl = self._ttl("financial", 43200)
        out: dict[str, Any] = {"reports": [], "indicators": [], "valuation": [], "dividends": []}
        em = self._sources["eastmoney"]
        try:
            out["reports"] = self.cache.remember(
                "financial", f"lico_{code}", ttl, lambda: em.financial_reports(code, 8))
        except Exception as exc:
            self.log.warn(f"业绩报表获取失败 {code}: {exc}")
        try:
            out["indicators"] = self.cache.remember(
                "financial", f"main_{code}", ttl, lambda: em.main_indicators(code, 8))
        except Exception as exc:
            self.log.warn(f"财务指标获取失败 {code}: {exc}")
        try:
            out["valuation"] = self.cache.remember(
                "valuation", f"val_{code}", self._ttl("valuation", 43200),
                lambda: em.valuation_history(code, 750))
        except Exception as exc:
            self.log.warn(f"估值历史获取失败 {code}: {exc}")
        try:
            out["dividends"] = self.cache.remember(
                "financial", f"div_{code}", ttl, lambda: em.dividends(code, 6))
        except Exception:
            out["dividends"] = []
        return out

    def concepts(self, symbol: Symbol) -> list[dict[str, Any]]:
        try:
            return self.cache.remember(
                "board", f"concept_{symbol.code}", self._ttl("board", 3600),
                lambda: self._sources["eastmoney"].stock_concepts(symbol, 24))
        except Exception:
            return []

    def industry_snapshot(self, industry_name: str, board_code: str = "") -> dict[str, Any]:
        """行业板块表现 + 成分股（用于同行业对比）。"""
        snap: dict[str, Any] = {"name": industry_name, "boards": [], "peers": []}
        if not industry_name and not board_code:
            return snap
        # 接口限流时进入负缓存，避免每只股票都重试并刷屏
        if time.time() < self._board_down_until:
            return snap
        try:
            boards = self.cache.remember("board", "industry_boards",
                                         self._ttl("board", 3600),
                                         lambda: self._sources["eastmoney"].boards("industry", 120))
            snap["boards"] = boards
            hit = None
            for b in boards:
                if board_code and b["code"] == board_code:
                    hit = b
                    break
                if b["name"] == industry_name:
                    hit = b
                    break
            if hit:
                snap["perf"] = hit
                snap["name"] = hit["name"]
                snap["peers"] = self.cache.remember(
                    "board", f"members_{hit['code']}", self._ttl("board", 3600),
                    lambda: self._sources["eastmoney"].board_members(hit["code"], 30))
        except Exception as exc:
            self._board_down_until = time.time() + 300
            self.log.info(f"行业对比数据暂不可用（{type(exc).__name__}），5 分钟内不再重试")
        return snap

    def board_rank(self, kind: str = "gainers", limit: int = 20) -> list[dict[str, Any]]:
        """榜单：涨幅 / 跌幅 / 主力净流入 / 换手率 / 成交额。

        东财 clist 不可用时，退化为对全市场快照 (stock_list) 本地排序。
        """
        spec = {
            "gainers": ("f3", True, "change_pct"),
            "losers": ("f3", False, "change_pct"),
            "mainflow": ("f62", True, "main_net"),
            "turnover": ("f8", True, "turnover_rate"),
            "amount": ("f6", True, "amount"),
            "chg60": ("f24", True, "chg_60d"),
        }.get(kind, ("f3", True, "change_pct"))
        try:
            rows = self._sources["eastmoney"].rank(spec[0], limit, descending=spec[1])
            if rows:
                return rows
        except Exception:
            pass
        rows = self.stock_list()
        key = spec[2]
        usable = [r for r in rows if r.get(key) is not None]
        usable.sort(key=lambda r: r[key], reverse=spec[1])
        return usable[:limit]

    def market_context(self) -> dict[str, Any]:
        """大盘环境：主要指数 + 涨跌家数 + 领涨/领跌行业。"""
        ttl = self._ttl("index", 120) if is_trading_time() else max(self._ttl("index", 120), 300)

        def build() -> dict[str, Any]:
            ctx: dict[str, Any] = {
                "phase": market_phase(),
                "time": now_str(),
                "indexes": [],
                "breadth": None,
                "boards_top": [],
                "boards_bottom": [],
            }
            try:
                qs = self._sources["tencent"].quotes([s for _, s in MARKET_INDEXES])
            except Exception:
                qs = {}
            for label, sym in MARKET_INDEXES:
                q = qs.get(sym.code)
                if not q:
                    continue
                ctx["indexes"].append({
                    "name": label, "code": sym.tencent,
                    "price": q.get("price"), "change_pct": q.get("change_pct"),
                    "amount": q.get("amount"),
                })
            # 涨跌家数：全市场一次请求
            try:
                rows = self.stock_list()
                if rows:
                    up = down = flat = limit_up = limit_down = 0
                    for r in rows:
                        pct = r.get("change_pct")
                        if pct is None:
                            continue
                        if pct > 0:
                            up += 1
                        elif pct < 0:
                            down += 1
                        else:
                            flat += 1
                        sym = normalize(r["code"])
                        lim = sym.limit_pct
                        if lim and pct >= lim - 0.35:
                            limit_up += 1
                        elif lim and pct <= -lim + 0.35:
                            limit_down += 1
                    ctx["breadth"] = {"up": up, "down": down, "flat": flat,
                                      "limit_up": limit_up, "limit_down": limit_down,
                                      "total": up + down + flat}
            except Exception:
                pass
            try:
                boards = self._sources["eastmoney"].boards("industry", 100)
                boards = [b for b in boards if b.get("change_pct") is not None]
                ctx["boards_top"] = sorted(boards, key=lambda b: -b["change_pct"])[:5]
                ctx["boards_bottom"] = sorted(boards, key=lambda b: b["change_pct"])[:5]
            except Exception:
                pass
            # 指数趋势（MA20/MA60），用于判断大盘环境
            ctx["trend"] = {}
            for label, sym in (("上证指数", Symbol("000001", "sh", "上证指数", True)),
                               ("创业板指", Symbol("399006", "sz", "创业板指", True))):
                try:
                    bars = self.kline(sym, 120)
                    if len(bars) < 60:
                        continue
                    closes = [b["close"] for b in bars]
                    from ..analysis import indicators as ind
                    ma20 = ind.last(ind.sma(closes, 20))
                    ma60 = ind.last(ind.sma(closes, 60))
                    ctx["trend"][label] = {
                        "price": closes[-1], "ma20": ma20, "ma60": ma60,
                        "ma20_slope": ind.slope_pct(closes, 20),
                        "chg20": (closes[-1] / closes[-21] - 1) * 100 if len(closes) > 21 else None,
                        "chg60": (closes[-1] / closes[-61] - 1) * 100 if len(closes) > 61 else None,
                    }
                except Exception:
                    continue
            return ctx

        try:
            return self.cache.remember("index", "market_ctx", ttl, build)
        except Exception:
            return {"phase": market_phase(), "time": now_str(), "indexes": [],
                    "breadth": None, "boards_top": [], "boards_bottom": []}

    # ---------------- 打包 ----------------
    @staticmethod
    def _capture(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[Any, Exception | None]:
        """在线程池里取数；异常放进返回值，避免 Future.result() 直接抛给调用方。"""
        try:
            return fn(*args, **kwargs), None
        except Exception as exc:                       # noqa: BLE001
            return None, exc

    def bundle(self, symbol: Symbol, days: int | None = None) -> dict[str, Any]:
        """一次性取齐分析所需数据。单项失败不影响整体。"""
        days = days or int(self.cfg.analysis.get("kline_days", 300))
        warnings: list[str] = []
        sources_used: dict[str, str] = {}
        is_index = symbol.is_index

        # quote / kline / 财报 / 资金流 / 公告 / 概念 / 大盘互不依赖，并发拉取，
        # 总等待由「各段耗时之和」降为「最慢一段」；同一主机的请求仍由
        # HttpClient 的每主机限速器排队，不会突破最小间隔。
        jobs: dict[str, tuple[Callable[..., Any], tuple[Any, ...]]] = {
            "quote": (self.quote, (symbol,)),
            "kline": (self.kline, (symbol, days)),
            "market": (self.market_context, ()),
        }
        if not is_index:
            jobs.update({
                "financials": (self.financials, (symbol,)),
                "moneyflow": (self.moneyflow, (symbol,)),
                "announcements": (self.announcements, (symbol.code,)),
                "concepts": (self.concepts, (symbol,)),
            })

        results: dict[str, tuple[Any, Exception | None]] = {}
        with ThreadPoolExecutor(max_workers=len(jobs),
                                thread_name_prefix="datasource") as pool:
            futures = {name: pool.submit(self._capture, fn, *args)
                       for name, (fn, args) in jobs.items()}
            for name, future in futures.items():
                results[name] = future.result()

        quote, exc = results["quote"]
        if exc is None:
            sources_used["quote"] = quote.get("source", "?")
        else:
            warnings.append(f"实时行情获取失败：{exc}")
            quote = {}

        kline, exc = results["kline"]
        if exc is None:
            sources_used["kline"] = "ok"
        else:
            warnings.append(f"K线获取失败：{exc}")
            kline = []

        if is_index:
            fin: dict[str, Any] = {"reports": [], "indicators": [],
                                   "valuation": [], "dividends": []}
            flow: list[dict[str, Any]] = []
            news: list[dict[str, Any]] = []
            concepts: list[dict[str, Any]] = []
        else:
            # 单项失败不影响整体（与拼接前的承诺一致）：失败记 warning + 给默认值
            fin, exc = results["financials"]
            if exc is not None:
                warnings.append(f"财报获取失败：{exc}")
                fin = {"reports": [], "indicators": [], "valuation": [], "dividends": []}
            flow, exc = results["moneyflow"]
            if exc is not None:
                warnings.append(f"资金流获取失败：{exc}")
                flow = []
            news, exc = results["announcements"]
            if exc is not None:
                warnings.append(f"公告获取失败：{exc}")
                news = []
            concepts, exc = results["concepts"]
            if exc is not None:
                warnings.append(f"概念获取失败：{exc}")
                concepts = []

        market, exc = results["market"]
        if exc is not None:
            warnings.append(f"大盘数据获取失败：{exc}")
            market = {}

        info = Symbol(symbol.code, symbol.exchange,
                      quote.get("name") or symbol.name, symbol.is_index)

        industry_name = ""
        for r in fin.get("reports", []):
            if r.get("industry"):
                industry_name = r["industry"]
                break
        if not industry_name:
            industry_name = quote.get("industry") or ""
        industry = self.industry_snapshot(industry_name) if industry_name else {
            "name": "", "boards": [], "peers": []}

        return {
            "symbol": {"code": info.code, "exchange": info.exchange, "name": info.name,
                       "full": info.full, "board": info.board, "is_index": info.is_index,
                       "limit_pct": info.limit_pct},
            "quote": quote,
            "kline": kline,
            "moneyflow": flow,
            "financial": fin,
            "announcements": news,
            "concepts": concepts,
            "industry": industry,
            "market": market,
            "meta": {
                "fetched_at": now_str(),
                "days": days,
                "sources": sources_used,
                "warnings": warnings,
                "http": dict(self.http.stats),
                "cache": self.cache.stats_line(),
            },
        }
