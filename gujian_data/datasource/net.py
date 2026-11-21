# -*- coding: utf-8 -*-
"""HTTP 客户端：限速 + 重试 + 熔断 + 多行情源自动降级。

东财 push2 会临时限流（RemoteDisconnected），因此必须：
  1. 每主机最小请求间隔；
  2. 连续失败进入冷却期，期间直接跳过该源；
  3. 调用方按 order 顺序降级到腾讯/新浪。
"""
from __future__ import annotations

import json
import random
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..utils import Log

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class HostState:
    __slots__ = ("last_call", "fails", "cooldown_until", "lock")

    def __init__(self) -> None:
        self.last_call = 0.0
        self.fails = 0
        self.cooldown_until = 0.0
        # 每主机一把锁：并发线程排队判断「下一次最早什么时候能发」，
        # 避免多个线程同时读到旧的 last_call 而一起放行、瞬间打满数据源。
        self.lock = threading.Lock()


class HttpClient:
    def __init__(self, timeout: float = 12, retries: int = 3, min_interval: float = 0.18,
                 host_cooldown: float = 90, log: Log | None = None):
        self.timeout = timeout
        self.retries = max(1, retries)
        self.min_interval = min_interval
        self.host_cooldown = host_cooldown
        self.log = log or Log(verbose=False)
        self._lock = threading.Lock()
        self._hosts: dict[str, HostState] = {}
        self._ssl_ctx = ssl.create_default_context()
        # 部分环境证书链不全，行情站不涉及支付，放宽校验
        self._ssl_ctx.check_hostname = True
        self._ssl_ctx.verify_mode = ssl.CERT_REQUIRED
        self.stats = {"requests": 0, "failures": 0, "cooldowns": 0}

    # ---------------- 主机状态 ----------------
    def _state(self, host: str) -> HostState:
        with self._lock:
            st = self._hosts.get(host)
            if st is None:
                st = self._hosts[host] = HostState()
            return st

    def host_available(self, host: str) -> bool:
        return time.time() >= self._state(host).cooldown_until

    def _throttle(self, host: str) -> None:
        st = self._state(host)
        # 持锁判断并占位：并发下仍保证同一主机两次请求至少间隔 min_interval。
        with st.lock:
            wait = st.last_call + self.min_interval - time.time()
            if wait > 0:
                time.sleep(wait)
            st.last_call = time.time()

    def _mark_fail(self, host: str, reason: str) -> None:
        st = self._state(host)
        st.fails += 1
        self.stats["failures"] += 1
        if st.fails >= 3:
            st.cooldown_until = time.time() + self.host_cooldown
            st.fails = 0
            self.stats["cooldowns"] += 1
            self.log.warn(f"数据源 {host} 连续失败，冷却 {self.host_cooldown:.0f}s（{reason}）")

    def _mark_ok(self, host: str) -> None:
        self._state(host).fails = 0

    # ---------------- 请求 ----------------
    def get_bytes(self, url: str, referer: str | None = None, timeout: float | None = None,
                  retries: int | None = None) -> bytes:
        host = urllib.parse.urlsplit(url).netloc
        tries = retries or self.retries
        last_exc: Exception | None = None

        for attempt in range(tries):
            if not self.host_available(host):
                raise ConnectionError(f"数据源 {host} 处于冷却期")
            self._throttle(host)
            headers = {
                "User-Agent": UA,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Connection": "close",
            }
            if referer:
                headers["Referer"] = referer
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout or self.timeout,
                                            context=self._ssl_ctx) as resp:
                    data = resp.read()
                self.stats["requests"] += 1
                self._mark_ok(host)
                return data
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code in (403, 429):
                    self._mark_fail(host, f"HTTP {exc.code}")
                    break
                if exc.code >= 500:
                    self._mark_fail(host, f"HTTP {exc.code}")
                else:
                    break
            except (urllib.error.URLError, ConnectionError, socket.timeout, OSError) as exc:
                last_exc = exc
                self._mark_fail(host, type(exc).__name__)
            if attempt < tries - 1:
                time.sleep(0.35 * (2 ** attempt) + random.uniform(0, 0.2))
        raise last_exc or ConnectionError("请求失败")

    def get_text(self, url: str, encoding: str = "utf-8", referer: str | None = None,
                 timeout: float | None = None, retries: int | None = None) -> str:
        data = self.get_bytes(url, referer=referer, timeout=timeout, retries=retries)
        for enc in (encoding, "utf-8", "gbk", "gb18030"):
            try:
                return data.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return data.decode("utf-8", "ignore")

    def get_json(self, url: str, referer: str | None = None, timeout: float | None = None,
                 retries: int | None = None) -> Any:
        text = self.get_text(url, referer=referer, timeout=timeout, retries=retries)
        text = text.strip()
        if text.startswith("\ufeff"):
            text = text[1:]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # JSONP: callback({...})
            start, end = text.find("("), text.rfind(")")
            if 0 <= start < end:
                inner = text[start + 1: end].strip().rstrip(";")
                inner = inner.replace("day:", '"day":').replace("open:", '"open":')
                try:
                    return json.loads(inner)
                except json.JSONDecodeError:
                    pass
            raise ValueError(f"返回内容不是合法 JSON：{text[:120]}")


def quote_url(base: str, **params: Any) -> str:
    clean = {k: v for k, v in params.items() if v is not None}
    return f"{base}?{urllib.parse.urlencode(clean, safe=',:()*\"')}"
