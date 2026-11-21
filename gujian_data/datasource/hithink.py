# -*- coding: utf-8 -*-
"""同花顺行情源：通过 hithink-finance CLI 的 `market snapshot` 取实时快照。

对接时间：2026-11-21（首版经独立复核后被退回修正，见下）。

用途：
  - 给 A/B 买卖窗口的取价 failover 增加一路「同花顺」数据源（默认第 3 位兜底）
  - 不参与日线历史（历史仍用通达信本地 .day）

复核修正（reviewer-hithink / R0004）：
  1. 超时改为「杀进程树」：CLI 是 .cmd → node.exe 两层，只 kill cmd 会留下 node 占着管道，
     原 15s 超时实测能阻塞 60s+；现在 timeout 默认 3s，超时用 taskkill /T /F 杀整棵树
  2. 补 name / avg_price（VWAP=成交额/成交量），避免账本名称空白、alpha 标签恒 False
  3. 停牌（last_price 为空/0）不返回 item，交给下一个源，避免每次都记一条假故障
  4. `time` 不再暴露 epoch 数字（避免被当日期解析），改为 ISO 时间字符串
  5. 显式 `--format json`；支持 GUJIAN_HITHINK_CLI / GUJIAN_HITHINK_TIMEOUT 环境变量
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from ..utils import to_float
from .symbols import Symbol

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
CLI_CANDIDATES = (
    r"C:\Users\61246\.dsh\bin\hithink-finance.CMD",
    r"C:\Users\61246\.dsh\bin\hithink-finance.cmd",
)
DEFAULT_TIMEOUT = 3.0
MAX_BATCH = 80

_SUFFIX_MAP = {"SH": "sh", "SZ": "sz", "BJ": "bj"}


def _guess_exchange(code: str) -> str:
    if code.startswith("920") or code.startswith(("4", "8")):
        return "bj"
    if code.startswith(("6", "5", "9")):
        return "sh"
    return "sz"


def _kill_tree(pid: int) -> None:
    """taskkill /T /F 杀掉 CLI 进程树（.cmd → node），避免子进程占住管道。"""
    try:
        cmd = ["taskkill", "/T", "/F", "/PID", str(pid)]
        subprocess.run(cmd, capture_output=True, timeout=5, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass


class HithinkSource:
    """同花顺 CLI 行情源（批量快照）。"""

    name = "hithink"

    def __init__(self, http: Any = None) -> None:
        self.http = http
        self.cli = self._locate_cli()
        try:
            self.timeout = max(1.0, float(os.environ.get("GUJIAN_HITHINK_TIMEOUT") or DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            self.timeout = DEFAULT_TIMEOUT

    # ---------------- 基础设施 ----------------
    @staticmethod
    def _locate_cli() -> str | None:
        env = (os.environ.get("GUJIAN_HITHINK_CLI") or "").strip()
        if env and Path(env).is_file():
            return env
        for cand in CLI_CANDIDATES:
            if Path(cand).is_file():
                return cand
        for cmd in ("hithink-finance.CMD", "hithink-finance.cmd", "hithink-finance"):
            found = shutil.which(cmd)
            if found:
                return found
        return None

    def available(self) -> bool:
        return bool(self.cli and Path(self.cli).is_file())

    @staticmethod
    def _ths(symbol: Symbol) -> str:
        """Symbol → 同花顺 thscode，例如 600519.SH。"""
        return f"{symbol.code}.{symbol.sufix}"

    # ---------------- 实时行情 ----------------
    def _snapshot(self, thscodes: list[str]) -> dict[str, dict[str, Any]]:
        if not thscodes:
            return {}
        if not self.available():
            raise ValueError("同花顺 CLI 不可用（找不到 hithink-finance）")
        cmd = [str(self.cli), "--format", "json", "market", "snapshot",
               "--thscodes", ",".join(thscodes)]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=NO_WINDOW | 0x00000200,  # CREATE_NEW_PROCESS_GROUP
            )
        except (OSError, ValueError) as exc:
            raise ValueError(f"同花顺 CLI 启动失败：{exc}") from exc

        try:
            out, err = proc.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
            try:
                proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            raise ValueError(f"同花顺 CLI 超时（>{self.timeout:.0f}s，已杀进程树）") from None
        except (OSError, subprocess.SubprocessError) as exc:
            _kill_tree(proc.pid)
            raise ValueError(f"同花顺 CLI 调用失败：{exc}") from exc

        text = (out or "").strip()
        if not text:
            # 真实 CLI 报错时 JSON 写在 stderr、exit=4，把 stderr 用上更清楚
            raise ValueError(f"同花顺 CLI 无输出（exit={proc.returncode}）：{(err or '')[:120]}")
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"同花顺返回非 JSON：{text[:120]}") from exc
        if not isinstance(doc, dict):
            raise ValueError(f"同花顺返回结构异常：{type(doc).__name__}")
        if not doc.get("ok"):
            msg = (doc.get("error") or {}).get("message") or "unknown"
            raise ValueError(f"同花顺返回错误：{str(msg)[:140]}")

        data = doc.get("data") or {}
        ts = data.get("timestamp")
        try:
            ts_text = (datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d %H:%M:%S")
                       if ts else "")
        except (TypeError, ValueError, OSError):
            ts_text = ""
        out_map: dict[str, dict[str, Any]] = {}
        for it in data.get("item") or []:
            if not isinstance(it, dict):
                continue
            thscode = str(it.get("thscode") or "")
            code = str(it.get("ticker") or "").strip()
            if not code:
                continue
            price = to_float(it.get("last_price"))
            if price is None or price <= 0:      # 停牌/无成交：不返回，交给下一个源
                continue
            vol_shares = to_float(it.get("volume")) or 0.0
            amount = to_float(it.get("turnover"))
            exch = _SUFFIX_MAP.get(thscode.rsplit(".", 1)[-1].upper()) or _guess_exchange(code)
            out_map[code] = {
                "code": code,
                "name": "",
                "exchange": exch,
                "price": price,
                "prev_close": to_float(it.get("prev_price")),
                "open": to_float(it.get("open_price")),
                "high": to_float(it.get("high_price")),
                "low": to_float(it.get("low_price")),
                "change": to_float(it.get("price_change")),
                "change_pct": to_float(it.get("price_change_ratio_pct")),
                "volume": vol_shares / 100.0,          # 股 → 手（与腾讯口径一致）
                "amount": amount,                       # 元
                "avg_price": (amount / vol_shares) if (amount and vol_shares) else None,
                "turnover_rate": None,
                "time": ts_text,                        # ISO 时间（响应组装时刻）
                "source": self.name,
                "suspended": False,
            }
        return out_map

    def quotes(self, symbols: Iterable[Symbol]) -> dict[str, dict[str, Any]]:
        syms = list(symbols)
        if not syms:
            return {}
        out: dict[str, dict[str, Any]] = {}
        # 分块（CLI 单次 thscodes 不宜过长）
        for i in range(0, len(syms), MAX_BATCH):
            chunk = syms[i:i + MAX_BATCH]
            res = self._snapshot([self._ths(s) for s in chunk])
            for sym in chunk:
                q = res.get(sym.code)
                if q is not None:
                    out[sym.code] = q
        return out

    def quote(self, symbol: Symbol) -> dict[str, Any]:
        q = self.quotes([symbol]).get(symbol.code)
        if not q:
            raise ValueError(f"同花顺未返回 {symbol.full} 行情")
        if not q.get("name"):
            q["name"] = symbol.name or ""
        return q
