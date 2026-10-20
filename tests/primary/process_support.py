"""当前工作树的独立进程测试环境；本地进程不继承端点代理。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROXIES = {p for key in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY") for p in (key, key.lower())}


def clean_env(source):
    return {**{k: v for k, v in os.environ.items() if k not in PROXIES},
            "PYTHONPATH": str(source), "PYTHONHASHSEED": "0"}
