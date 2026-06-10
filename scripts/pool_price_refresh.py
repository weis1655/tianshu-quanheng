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

# 也载入 market_agent 用于 fetch_quotes
spec2 = importlib.util.spec_from_file_location(
    "market_agent",
    str(PROJECT_ROOT / "agents" / "market_agent.py")
)
ma = importlib.util.module_from_spec(spec2)
sys.modules["market_agent"] = ma
spec2.loader.exec_module(ma)

pm = mod.PoolManager()
from market_agent import fetch_quotes, to_api

pool_dir = PROJECT_ROOT / "五池管理"
now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
results = []

# ── 重点观察池 ──
try:
    r1 = pm.refresh_key_watch_prices()
    results.append(f"重点池:{len(r1)}只")
except Exception as e:
    results.append(f"重点池❌:{e}")

# ── 持仓池 ──
try:
    r2 = pm.refresh_holdings_prices()
    results.append(f"持仓池:{len(r2)}只")
except Exception as e:
    results.append(f"持仓池❌:{e}")

# ── S级操作池（pool_manager 不覆盖，手动刷） ──
try:
    s_pool_file = pool_dir / "S级操作池.json"
    s_refreshed = []
    if s_pool_file.exists():
        with open(s_pool_file) as f:
            s_data = json.load(f)
        s_stocks = s_data.get("stocks", [])
        if s_stocks:
            s_codes = [s.get("代码", "") for s in s_stocks if s.get("代码")]
            if s_codes:
                api_codes = [to_api(c) for c in s_codes]
                s_quotes = fetch_quotes(api_codes)
                s_qmap = {q["代码"]: q for q in s_quotes if q.get("代码")}
                for s in s_stocks:
                    code = s.get("代码", "")
                    q = s_qmap.get(code)
                    if q and q.get("现价") is not None:
                        s["今日收盘"] = q["现价"]
                        chg = q.get("涨跌幅", 0)
                        s["今日涨跌"] = f"{chg:+.2f}%" if isinstance(chg, float) else chg
                        s["换手率"] = q.get("换手率", s.get("换手率", 0))
                        s["量比"] = q.get("量比", s.get("量比", 0))
                        s["更新时间"] = now_str
                        s_refreshed.append(code)
                # 止损检查
                entry_price = s.get("入场价", 0)
                s_name = s.get("名称", "?")
                s_code = s.get("代码", "")
                now_price_s = q.get("现价", 0)
                if entry_price and now_price_s and entry_price > 0:
                    loss_pct = (now_price_s - entry_price) / entry_price * 100
                    if loss_pct < -5:
                        s["操作建议"] = "⚠️ 跌幅超5%，关注止损"
                        results.append(f"止损预警:{s_name}({s_code})亏损{loss_pct:.1f}%")
                        print(f"  [止损预警] {s_name}({s_code}) 入场{entry_price}→现{now_price_s}, 亏损{loss_pct:.1f}%")
                # 写回
                with open(s_pool_file, "w") as f:
                    json.dump(s_data, f, ensure_ascii=False, indent=2)
        results.append(f"S级池:{len(s_refreshed)}只")
    else:
        results.append("S级池:无文件")
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
