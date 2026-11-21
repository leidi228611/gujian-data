# -*- coding: utf-8 -*-
"""通用工具：数值安全转换、金额/涨跌格式化、日志。"""
from __future__ import annotations

from pathlib import Path

import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

CN_TZ = timezone(timedelta(hours=8))


def ensure_stdio() -> None:
    """统一标准输出编码，并在窗口模式下把 stdout/stderr 接到日志文件。

    中文 Windows 默认 GBK，一旦 stdout 被重定向到文件/管道，
    print 特殊符号（✓ ★ ▁ 等）会抛 UnicodeEncodeError；
    打包成窗口程序后 stdout 还可能直接是 None。两种情况都在这里兜住。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError, AttributeError):
                pass
    if sys.stdout is not None and sys.stderr is not None:
        return
    handle = None
    try:
        from .config import DATA_HOME          # 延迟导入，避免循环依赖
        log_dir = DATA_HOME / "data"
        log_dir.mkdir(parents=True, exist_ok=True)
        handle = open(log_dir / "app.log", "a", encoding="utf-8", buffering=1)
    except Exception:                          # noqa: BLE001
        try:
            handle = open(os.devnull, "w", encoding="utf-8")
        except OSError:
            return
    if sys.stdout is None:
        sys.stdout = handle
    if sys.stderr is None:
        sys.stderr = handle


# ---------------- 数值 ----------------
def to_float(value: Any, default: float | None = None) -> float | None:
    """把东财/腾讯返回的 "-"、""、None、带 % 的字符串统一成 float。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return default
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "").replace("+", "")
    if text in ("", "-", "--", "None", "null", "nan", "NaN", "—"):
        return default
    mult = 1.0
    for suffix, m in (("万亿", 1e12), ("亿", 1e8), ("万", 1e4)):
        if text.endswith(suffix):
            text, mult = text[: -len(suffix)], m
            break
    try:
        return float(text) * mult
    except ValueError:
        return default


def clamp(x: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, x))


def safe_div(a: float | None, b: float | None, default: float | None = None) -> float | None:
    if a is None or b in (None, 0):
        return default
    try:
        return a / b
    except ZeroDivisionError:
        return default


def mean(values: Iterable[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return (new / old - 1.0) * 100.0


# ---------------- 格式化 ----------------
def fmt_money(value: float | None, digits: int = 2) -> str:
    """金额：自动用亿/万。"""
    if value is None:
        return "—"
    av = abs(value)
    if av >= 1e12:
        return f"{value / 1e12:.{digits}f}万亿"
    if av >= 1e8:
        return f"{value / 1e8:.{digits}f}亿"
    if av >= 1e4:
        return f"{value / 1e4:.{digits}f}万"
    return f"{value:.{max(digits, 0)}f}"


def fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def fmt_pct(value: float | None, digits: int = 2, sign: bool = True) -> str:
    if value is None:
        return "—"
    return f"{value:+.{digits}f}%" if sign else f"{value:.{digits}f}%"


def fmt_ratio(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


# ---------------- 时间 ----------------
def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return now_cn().strftime(fmt)


def today_str(fmt: str = "%Y%m%d") -> str:
    return now_cn().strftime(fmt)


def is_trading_time(dt: datetime | None = None) -> bool:
    dt = (dt or now_cn()).replace(tzinfo=None)
    if dt.weekday() >= 5:
        return False
    hm = dt.hour * 100 + dt.minute
    return (930 <= hm <= 1130) or (1300 <= hm <= 1500)


def market_phase() -> str:
    dt = now_cn().replace(tzinfo=None)
    if dt.weekday() >= 5:
        return "休市（周末）"
    hm = dt.hour * 100 + dt.minute
    if hm < 915:
        return "盘前"
    if 915 <= hm < 925:
        return "集合竞价"
    if 925 <= hm <= 1130:
        return "交易中"
    if 1130 < hm < 1300:
        return "午间休市"
    if 1300 <= hm <= 1500:
        return "交易中"
    return "已收盘"


# ---------------- 日志 ----------------
class Log:
    """轻量彩色日志。--quiet 时只输出警告以上。"""

    COLORS = {
        "dim": "\033[2m",
        "green": "\033[32m",
        "red": "\033[31m",
        "yellow": "\033[33m",
        "blue": "\033[36m",
        "bold": "\033[1m",
        "reset": "\033[0m",
    }

    def __init__(self, verbose: bool = True, color: bool | None = None):
        self.verbose = verbose
        if color is None:
            color = bool(sys.stdout is not None and sys.stdout.isatty())
        self.color = color

    def _c(self, text: str, color: str) -> str:
        if not self.color or color not in self.COLORS:
            return text
        return f"{self.COLORS[color]}{text}{self.COLORS['reset']}"

    @staticmethod
    def _emit(text: str, stream=None) -> None:
        """打印兜底：任何编码异常都不应该让业务流程中断。"""
        stream = stream if stream is not None else sys.stdout
        if stream is None:
            return
        try:
            print(text, file=stream)
        except UnicodeEncodeError:
            try:
                print(text.encode("ascii", "replace").decode("ascii"), file=stream)
            except (OSError, ValueError):
                pass
        except (OSError, ValueError):
            pass

    def info(self, msg: str) -> None:
        if self.verbose:
            self._emit(f"{self._c('·', 'dim')} {msg}")

    def step(self, msg: str) -> None:
        if self.verbose:
            self._emit(self._c(f"▶ {msg}", "blue"))

    def ok(self, msg: str) -> None:
        if self.verbose:
            self._emit(self._c(f"✓ {msg}", "green"))

    def warn(self, msg: str) -> None:
        self._emit(self._c(f"! {msg}", "yellow"), sys.stderr)

    def error(self, msg: str) -> None:
        self._emit(self._c(f"✗ {msg}", "red"), sys.stderr)

    def title(self, msg: str) -> None:
        if self.verbose:
            self._emit("")
            self._emit(self._c(f"  {msg}", "bold"))
            self._emit(self._c("  " + "─" * max(20, len(msg) * 2), "dim"))


class Stopwatch:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    def __str__(self) -> str:
        return f"{self.elapsed():.2f}s"


def short(text: str, limit: int = 60) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def atomic_write(path, text: str, encoding: str = "utf-8") -> None:
    """原子写：先写临时文件再替换，避免断电/崩溃时把原文件写坏。

    2026-11-09：Windows 上 `os.replace` 会偶发 `WinError 5 拒绝访问`
    （另一个进程/线程正持有目标文件句柄，比如监控线程刚 open 它在读）。
    这种冲突是瞬时的，重试即可；以前不重试会直接抛错、这一次写入就丢了。
    """
    import os, tempfile, time
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        last: Exception | None = None
        for _ in range(20):                     # 最多等 ~1 秒
            try:
                os.replace(tmp, p)
                return
            except PermissionError as exc:      # Windows 文件被占用
                last = exc
                time.sleep(0.05)
        if last:
            raise last
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
