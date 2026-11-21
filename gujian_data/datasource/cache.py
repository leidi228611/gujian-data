# -*- coding: utf-8 -*-
"""磁盘缓存：带 TTL，取不到新数据时自动回退到过期缓存（保证离线可用）。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


class Cache:
    def __init__(self, root: Path, enabled: bool = True):
        self.root = Path(root)
        self.enabled = enabled
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.stale_hits = 0
        if enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, kind: str, key: str) -> Path:
        safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in key)[:120]
        folder = self.root / kind
        return folder / f"{safe}.json"

    def get(self, kind: str, key: str, ttl: float) -> Any | None:
        if not self.enabled:
            return None
        path = self._path(kind, key)
        if not path.exists():
            self.misses += 1
            return None
        try:
            with self._lock:
                blob = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self.misses += 1
            return None
        age = time.time() - blob.get("ts", 0)
        if age <= ttl:
            self.hits += 1
            return blob.get("data")
        self.misses += 1
        return None

    def get_stale(self, kind: str, key: str, max_age: float = 86400 * 7) -> Any | None:
        """读取过期缓存（网络失败时的兜底）。"""
        if not self.enabled:
            return None
        path = self._path(kind, key)
        if not path.exists():
            return None
        try:
            with self._lock:
                blob = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if time.time() - blob.get("ts", 0) > max_age:
            return None
        self.stale_hits += 1
        return blob.get("data")

    def set(self, kind: str, key: str, data: Any) -> None:
        if not self.enabled:
            return
        path = self._path(kind, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"ts": time.time(), "data": data}, ensure_ascii=False)
        try:
            with self._lock:
                fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                os.replace(tmp, path)
        except OSError:
            pass

    def remember(self, kind: str, key: str, ttl: float, producer) -> Any:
        """先查缓存 → 再取网络 → 失败回退过期缓存。"""
        cached = self.get(kind, key, ttl)
        if cached is not None:
            return cached
        try:
            data = producer()
        except Exception:
            # 审计缺陷 #4：行情类数据不得回退到数天前（会按旧价成交）
            if kind == "stocklist":
                # 2026-11-17 修（审计 #11）：全市场快照原来可回退 7 天，
                # 会用数天前的涨幅/换手/成交额选股且不报错。收紧到 20 小时。
                _max_age = 20 * 3600.0
            else:
                _max_age = 180.0 if kind in ("quote", "quotes", "kline", "minute") else 86400 * 7
            stale = self.get_stale(kind, key, _max_age)
            if stale is not None:
                return stale
            raise
        if data is not None:
            self.set(kind, key, data)
        return data

    def stats_line(self) -> str:
        return (f"缓存 命中 {self.hits} / 过期回退 {self.stale_hits} / 未命中 {self.misses}")

    def clear(self, kind: str | None = None) -> int:
        target = self.root / kind if kind else self.root
        removed = 0
        for p in target.rglob("*.json") if target.exists() else []:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
        return removed
