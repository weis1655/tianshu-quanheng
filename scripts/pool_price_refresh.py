#!/usr/bin/env python3
"""高频池价格刷新脚本 - 盘中每30分钟刷新重点观察池+持仓池+ S级池行情"""
import sys
import os
import json
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path.home() / "hermes-data" / "tianshu-quanheng"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))
from path_config import ensure_agent_paths; ensure_agent_paths()

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

# 正常导入（验证通过，无循环依赖）
from pool_manager import PoolManager
from market_agent import fetch_quotes

pm = PoolManager()

pool_dir = PROJECT_ROOT / "五池管理"
now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
results = []

# ── 重点观察池 ──
try:
    r1 = pm.refresh_key_watch_prices()
    results.append(f"重点池:{len(r1)}只")
except Exception as e:
    results.append(f"重点池❌:{e}")

# ── 快筛候选池（含存量降级扫描） ──
try:
    r3 = pm.refresh_screen_candidate_prices()
    results.append(f"候选池:{len(r3)}只")
except Exception as e:
    results.append(f"候选池❌:{e}")

# ── 持仓池 ──
try:
    r2 = pm.refresh_holdings_prices()
    results.append(f"持仓池:{len(r2)}只")
except Exception as e:
    results.append(f"持仓池❌:{e}")

# ── S级操作池（委托 pool_manager，含降级扫描+评分衰减）──
try:
    r3 = pm.refresh_s_operation_prices()
    results.append(f"S级操作池:{len(r3)}只")
except Exception as e:
    results.append(f"S级操作池❌:{e}")
except Exception as e:
    results.append(f"S级池❌:{e}")

msg = f"[池刷新] {' | '.join(results)}"
print(msg)

# ── 输出各池行情快照 ──────────────────────────────────
for pool_name, label in [("重点观察池", "📊 重点池"), ("快筛候选池", "📊 候选池"), ("S级操作池", "📊 S级池")]:
    pool_file = pool_dir / f"{pool_name}.json"
    if not pool_file.exists():
        continue
    try:
        with open(pool_file) as f:
            data = json.load(f)
        stocks = data.get("stocks", [])
        if not stocks:
            continue
        print(f"\n{label}:")
        for s in stocks[:6]:
            name = s.get("名称", "?")
            code = s.get("代码", "")
            price = s.get("今日收盘", s.get("最新价", "-"))
            chg = s.get("今日涨跌", s.get("涨跌幅", "-"))
            score = s.get("综合分", s.get("技术面评分", "-"))
            print(f"  {name}({code})  收盘:{price}  涨跌:{chg}  评分:{score}")
    except Exception:
        pass

# ── OPT-1: 实盘止损集成 — 条件单扫描 ─────────────────────
try:
    sys.path.insert(0, str(PROJECT_ROOT / "agents"))
    from conditional_order import OrderEngine
    engine = OrderEngine()
    triggers = engine.scan_once()
    if triggers:
        for t in triggers:
            print(f"  🔔 条件单触发: {t.code} {t.name} → {t.action} @ {t.trigger_price}")
except Exception as e:
    print(f"  ⚠️ 条件单扫描: {e}")
# ───────────────────────────────────────────────────────
