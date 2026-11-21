# -*- coding: utf-8 -*-
"""配置加载：config.json + .env 覆盖。

优先级：环境变量 > .env 文件 > config.json > 代码默认值
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any


def _app_root() -> Path:
    """应用根目录：源码运行时是项目目录，打包后是 exe 所在目录。

    打包后用 exe 同级目录，用户改 config.json / .env、看 reports 都能找到。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bundle_root() -> Path:
    """只读资源根目录：打包后前端静态文件在 _MEIPASS 临时解压目录里。"""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else _app_root()


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


ROOT = _app_root()
BUNDLE = _bundle_root()

# 数据目录（缓存/报告/日志）：exe 目录不可写时（例如放在 Program Files）回退到用户目录
if _is_writable(ROOT):
    DATA_HOME = ROOT
    # 支持第二实例：GUJIAN_DATA_HOME 指定独立数据目录（A/B 双实例用）
    if os.environ.get("GUJIAN_DATA_HOME"):
        DATA_HOME = Path(os.environ["GUJIAN_DATA_HOME"])
else:
    DATA_HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "StockLens"
    DATA_HOME.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = ROOT / "config.json"
ENV_PATH = ROOT / ".env"

# 大模型服务商预设（全部 OpenAI 兼容协议）
PROVIDERS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key_envs": ["DEEPSEEK_API_KEY", "STOCKLENS_LLM_API_KEY"],
        "site": "https://platform.deepseek.com",
        "note": "国产性价比首选，中文财报解读能力强",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "key_envs": ["DASHSCOPE_API_KEY", "QWEN_API_KEY", "STOCKLENS_LLM_API_KEY"],
        "site": "https://bailian.console.aliyun.com",
        "note": "阿里通义千问，国内直连稳定",
    },
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-32k",
        "key_envs": ["MOONSHOT_API_KEY", "STOCKLENS_LLM_API_KEY"],
        "site": "https://platform.moonshot.cn",
        "note": "月之暗面 Kimi，长上下文适合塞入财报",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "key_envs": ["ZHIPU_API_KEY", "GLM_API_KEY", "STOCKLENS_LLM_API_KEY"],
        "site": "https://open.bigmodel.cn",
        "note": "智谱 GLM，flash 版免费额度较多",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_envs": ["OPENAI_API_KEY", "STOCKLENS_LLM_API_KEY"],
        "site": "https://platform.openai.com",
        "note": "需海外网络",
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:14b",
        "key_envs": ["STOCKLENS_LLM_API_KEY"],
        "site": "https://ollama.com",
        "note": "本地部署，完全离线，无需 Key（随便填 sk-local）",
    },
    "doubao": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-1-6-250615",
        "key_envs": ["ARK_API_KEY", "DOUBAO_API_KEY", "STOCKLENS_PEER_API_KEY",
                     "STOCKLENS_LLM_API_KEY"],
        "site": "https://console.volcengine.com/ark",
        "note": "豆包官方接口（火山方舟），model 填推理接入点 ep-xxxx 或模型 ID",
    },
    "doubao-local": {
        "base_url": "http://192.168.1.10:8000/v1",
        "model": "doubao",
        "key_envs": ["STOCKLENS_PEER_API_KEY"],
        "site": "https://github.com/5201213/doubao-free-api",
        "note": "另一台电脑上的豆包客户端转成 OpenAI 兼容接口（局域网直连，需填对方 IP）",
    },
    "custom": {
        "base_url": "",
        "model": "",
        "key_envs": ["STOCKLENS_LLM_API_KEY"],
        "site": "",
        "note": "任意 OpenAI 兼容网关（vLLM / oneapi / 中转站）",
    },
}

DEFAULTS: dict[str, Any] = {
    "llm": {
        "enabled": True,
        "provider": "deepseek",
        "base_url": "",
        "api_key": "",
        "model": "",
        "temperature": 0.35,
        "max_tokens": 2600,
        "timeout": 120,
        "retries": 2,
        # 复核方（另一台电脑的豆包 / 火山方舟）：用于 crosscheck 交叉验证
        "peer": {
            "provider": "doubao",
            "base_url": "",
            "api_key": "",
            "model": "",
            "temperature": 0.3,
            "max_tokens": 2000,
            "timeout": 120,
            "retries": 2,
        },
    },
    "datasource": {
        "order": ["tencent", "sina", "eastmoney"],
        "timeout": 12,
        "retries": 3,
        "min_interval": 0.18,
        "host_cooldown": 90,
        # 买入窗口专用（2026-11-21 起）：东财 → 腾讯 → 同花顺 → 新浪（11-09 A 因单源 501 缺勤）
        "buy_order": ["eastmoney", "tencent", "hithink", "sina"],
        "buy_retries": 3,
        "buy_retry_interval": 3.0,
    },
    "cache": {
        "enabled": True,
        "dir": "data/cache",
        "ttl": {
            "quote": 120,
            "kline": 43200,
            "financial": 43200,
            "valuation": 43200,
            "moneyflow": 1800,
            "announcement": 7200,
            "board": 3600,
            "stocklist": 86400,
            "index": 120,
        },
    },
    "analysis": {
        "kline_days": 300,
        "weights": {
            "technical": 0.30,
            "fundamental": 0.25,
            "capital": 0.20,
            "valuation": 0.15,
            "sentiment": 0.10,
        },
    },
    "report": {
        "dir": "reports",
        "export_markdown": True,
        "export_excel": True,
        "export_json": True,
        "company_note": "本报告由程序自动生成，仅供研究参考，不构成投资建议。股市有风险，投资需谨慎。",
    },
    "screener": {
        "universe": 5000,
        "top": 30,
        "min_turnover": 0.8,
        "min_market_cap": 40,
        "exclude_st": True,
        "exclude_new_days": 60,
        "exclude_bj": False,
    },
    "strategy": {
        "price_min": 2.0,
        "price_max": 9.0,
        "min_change_pct": 2.0,
        "max_change_pct": 9.5,
        "min_amount": 50000000,
        "min_turnover": 1.0,
        "max_turnover": 25.0,
        "min_volume_ratio": 1.0,
        "exclude_st": True,
        "exclude_bj": True,
        "top": 1,
        "sort": "gain",
        "interval": 60,
        "asset": "stock",
        "lot": 100,
    },
    "paper": {
        "stop_loss_pct": -2.0,       # 浮亏到 -2% 提醒止损
        "take_profit_pct": 3.0,      # 浮盈到 +3% 提醒止盈
        "auto_refresh": 30,          # 页面自动刷新 / 后台盯盘间隔（秒）
        "monitor": True,             # 后台盯盘 + 系统气泡通知
        "preopen_reminder": True,    # 开盘前持仓汇总提醒
        "preopen_time": "09:25",     # 提醒时间
    },
    "picks": {
        "enabled": True,             # 每交易日自动跑「机构推荐精选」
        "time": "09:20",             # 推送时间（早于开盘提醒）
        "price_max": 20,
        "top": 3,
        "notify": True,              # 是否弹气泡
        "open_check": True,          # 开盘后校验高低开
        "open_check_time": "09:31",
    },
    "forecast": {
        "enabled": False,            # 收盘后单独推送（已合并进 review）
        "time": "15:05",
        "notify": True,
    },
    "review": {
        "enabled": True,             # 收盘复盘：情绪 + 板块 + 明日关注
        "time": "15:10",
        "notify": True,
    },
    "web": {"host": "127.0.0.1", "port": 8765, "open_browser": True},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def save_env(values: dict[str, str], path: Path = ENV_PATH) -> None:
    """把键值写回 .env（保留原有注释与其他行），同时更新当前进程的环境变量。"""
    lines: list[str] = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = path.read_text(encoding="gbk", errors="ignore").splitlines()
    pending = dict(values)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in pending:
                out.append(f"{key}={pending.pop(key)}")
                continue
        out.append(line)
    for key, val in pending.items():
        out.append(f"{key}={val}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    for key, val in values.items():
        os.environ[key] = val


def load_dotenv(path: Path = ENV_PATH) -> dict[str, str]:
    """极简 .env 解析，写入 os.environ（不覆盖已存在的真实环境变量）。"""
    found: dict[str, str] = {}
    if not path.exists():
        return found
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = path.read_text(encoding="gbk", errors="ignore")
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:]
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if not key:
            continue
        found[key] = val
        os.environ.setdefault(key, val)
    return found


class Config:
    """全局配置对象，属性式访问：cfg.llm.enabled / cfg.get("analysis.weights")。"""

    def __init__(self, data: dict[str, Any] | None = None):
        self.data = _deep_merge(DEFAULTS, data or {})
        self.dotenv = load_dotenv()
        self._resolve_llm()
        self._resolve_peer()
        self._resolve_paths()

    # ---------- 内部 ----------
    def _resolve_llm(self) -> None:
        llm = self.data["llm"]
        env = os.environ
        # 环境变量 / .env 优先于 config.json（README 里约定的优先级）
        provider = (env.get("STOCKLENS_LLM_PROVIDER")
                    or llm.get("provider") or "deepseek").strip().lower()
        preset = PROVIDERS.get(provider, PROVIDERS["custom"])

        if not llm.get("base_url"):
            llm["base_url"] = env.get("STOCKLENS_LLM_BASE_URL") or preset["base_url"]
        if not llm.get("model"):
            llm["model"] = env.get("STOCKLENS_LLM_MODEL") or preset["model"]
        if not llm.get("api_key"):
            for k in preset["key_envs"]:
                if env.get(k):
                    llm["api_key"] = env[k].strip()
                    break
        llm["provider"] = provider
        llm["configured"] = bool(llm.get("api_key") and llm.get("base_url") and llm.get("model"))
        # ollama 本地服务不需要真实 Key
        if provider == "ollama" and llm.get("base_url"):
            llm["configured"] = True

    def _resolve_peer(self) -> None:
        """复核方（豆包）：与主模型同一套 OpenAI 兼容解析逻辑，独立配置。"""
        llm = self.data["llm"]
        peer = llm.setdefault("peer", {})
        env = os.environ
        provider = (env.get("STOCKLENS_PEER_PROVIDER")
                    or peer.get("provider") or "doubao").strip().lower()
        preset = PROVIDERS.get(provider, PROVIDERS["custom"])

        if not peer.get("base_url"):
            peer["base_url"] = env.get("STOCKLENS_PEER_BASE_URL") or preset["base_url"]
        if not peer.get("model"):
            peer["model"] = env.get("STOCKLENS_PEER_MODEL") or preset["model"]
        if not peer.get("api_key"):
            for k in dict.fromkeys(("STOCKLENS_PEER_API_KEY", *preset["key_envs"])):
                if env.get(k):
                    peer["api_key"] = env[k].strip()
                    break
        peer["provider"] = provider
        # 局域网代理（doubao-local）通常不校验 Key，给个占位即可
        if provider in ("doubao-local", "ollama") and not peer.get("api_key"):
            peer["api_key"] = "sk-local"
        peer["configured"] = bool(peer.get("api_key") and peer.get("base_url")
                                  and peer.get("model"))

    def _resolve_paths(self) -> None:
        self.data_home = DATA_HOME
        cdir = self.data["cache"]["dir"]
        self.cache_dir = (DATA_HOME / cdir) if not os.path.isabs(cdir) else Path(cdir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        rdir = self.data["report"]["dir"]
        self.report_dir = (DATA_HOME / rdir) if not os.path.isabs(rdir) else Path(rdir)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 访问 ----------
    @property
    def llm(self) -> dict[str, Any]:
        return self.data["llm"]

    @property
    def peer(self) -> dict[str, Any]:
        """复核方（另一台电脑的豆包 / 火山方舟）配置。"""
        return self.data["llm"].setdefault("peer", {})

    @property
    def ds(self) -> dict[str, Any]:
        return self.data["datasource"]

    @property
    def analysis(self) -> dict[str, Any]:
        return self.data["analysis"]

    @property
    def weights(self) -> dict[str, float]:
        w = dict(self.data["analysis"]["weights"])
        total = sum(w.values()) or 1.0
        return {k: v / total for k, v in w.items()}

    @property
    def ttl(self) -> dict[str, int]:
        return self.data["cache"]["ttl"]

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self.data
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value

    def save(self) -> None:
        CONFIG_PATH.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def describe_llm(self) -> str:
        llm = self.llm
        if not llm.get("configured"):
            return "未配置大模型（使用内置规则模板生成结论）"
        return f"{llm['provider']} / {llm['model']}"


_cfg: Config | None = None


def get_config(reload: bool = False) -> Config:
    global _cfg
    if _cfg is None or reload:
        data: dict[str, Any] = {}
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:  # 配置写坏了也要能启动
                print(f"[配置警告] config.json 解析失败（{exc}），使用默认配置")
        _cfg = Config(data)
    return _cfg
