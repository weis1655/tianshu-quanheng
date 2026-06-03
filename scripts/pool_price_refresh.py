#!/usr/bin/env python3
"""高频池价格刷新脚本 - 盘中每30分钟刷新重点观察池+持仓池行情"""
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path.home() / "hermes-data" / "tianshu-quanheng"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

# 加载 .env
env_path = Path.home() / ".hermes" / ".env"
if env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(str(env_path))
    except ImportError:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

# 绕过 __init__.py 的 orcherstrator 依赖，直接载入 pool_manager
import importlib.util
spec = importlib.util.spec_from_file_location(
    "pool_manager",
    str(PROJECT_ROOT / "agents" / "pool_manager.py")
)
mod = importlib.util.module_from_spec(spec)
sys.modules["pool_manager"] = mod
spec.loader.exec_module(mod)

pm = mod.PoolManager()
results = []

try:
    r1 = pm.refresh_key_watch_prices()
    results.append(f"重点池:{len(r1)}只")
except Exception as e:
    results.append(f"重点池❌:{e}")

try:
    r2 = pm.refresh_holdings_prices()
    results.append(f"持仓池:{len(r2)}只")
except Exception as e:
    results.append(f"持仓池❌:{e}")

msg = f"[池刷新] {' | '.join(results)}"
print(msg)
