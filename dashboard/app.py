#!/usr/bin/env python3
"""
天枢权衡看板 - FastAPI后端
"""

import sys
import json
from pathlib import Path
from datetime import datetime

# 添加agents路径
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from pool_manager import PoolManager
from market_agent import fetch_quotes
from base_agent import add_market_prefix
from notifier import notify_holding_alert, notify_screen_hit, check_holdings_and_alert


app = FastAPI(title="天枢权衡看板", version="1.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_pool_stocks(pool_name: str, pm: PoolManager = None) -> list:
    """获取池内股票，带实时行情"""
    if pm is None:
        pm = PoolManager()
    stocks = pm.get_stocks(pool_name)

    if not stocks:
        return []

    # 获取实时行情
    codes = []
    for s in stocks:
        code = s.get("股票代码") or s.get("代码", "")
        if code:
            prefixed = add_market_prefix(code)
            if prefixed:
                codes.append(prefixed)

    quotes = fetch_quotes(codes) if codes else []
    quote_map = {q.get("代码", ""): q for q in quotes}

    results = []
    for s in stocks:
        code = s.get("股票代码") or s.get("代码", "")
        name = s.get("股票名称") or s.get("名称", "?")

        quote = quote_map.get(code, {})
        current_price = quote.get("现价", 0)
        change_pct = quote.get("涨跌幅", 0)

        # 计算持仓盈亏
        entry_price = s.get("成本") or s.get("买入价", 0) or current_price
        if entry_price and current_price:
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
        else:
            pnl_pct = change_pct

        results.append({
            "code": code,
            "name": name,
            "market": s.get("市场", ""),
            "theme": s.get("主题", ""),
            "entry_price": entry_price,
            "current_price": current_price,
            "change_pct": change_pct,
            "pnl_pct": pnl_pct,
            "date": s.get("建仓日期") or s.get("纳入日期", ""),
            "remark": s.get("备注", ""),
        })

    return results


# ─── 告警状态追踪（防止重复推送）────────────────────────
_alert_history_file = PROJECT_ROOT / "data" / "alert_history.json"
_ALERT_COOLDOWN = 3600  # 同类告警间隔（秒）


def _load_alert_history() -> dict:
    try:
        if _alert_history_file.exists():
            return json.loads(_alert_history_file.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_alert_history(hist: dict):
    try:
        _alert_history_file.parent.mkdir(parents=True, exist_ok=True)
        _alert_history_file.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _can_alert(alert_key: str) -> bool:
    """检查告警是否在冷却期内"""
    hist = _load_alert_history()
    last = hist.get(alert_key, 0)
    if datetime.now().timestamp() - last < _ALERT_COOLDOWN:
        return False
    hist[alert_key] = datetime.now().timestamp()
    _save_alert_history(hist)
    return True


def check_and_notify_alerts(pools_data: dict, threshold: float = -3.0) -> list:
    """检查持仓+快筛，触发告警，返回已触发列表"""
    triggered = []
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    # 1. 持仓警戒
    holdings = pools_data.get("持仓池", {}).get("stocks", [])
    for s in holdings:
        change = s.get("change_pct", 0)
        if change <= threshold:
            alert_key = f"holding_alert_{s.get('code', '')}_{now.strftime('%Y%m%d')}"
            if _can_alert(alert_key):
                success = notify_holding_alert(s, threshold)
                triggered.append({
                    "type": "holding_alert",
                    "stock": s.get("name"),
                    "change": change,
                    "sent": success,
                    "time": now_str
                })
                print(f"[看板告警] {'✅' if success else '❌'} 持仓警戒: {s.get('name')} {change:+.2f}%")

    # 2. 快筛命中（新候选池有标的时）
    candidates = pools_data.get("快筛候选池", {}).get("stocks", [])
    themed = {}
    for s in candidates:
        theme = s.get("主题", "")
        if theme:
            themed.setdefault(theme, []).append(s)

    for theme, stocks in themed.items():
        alert_key = f"screen_hit_{theme}_{now.strftime('%Y%m%d')}"
        if len(stocks) >= 2 and _can_alert(alert_key):
            success = notify_screen_hit(stocks, theme)
            triggered.append({
                "type": "screen_hit",
                "theme": theme,
                "count": len(stocks),
                "sent": success,
                "time": now_str
            })
            print(f"[看板告警] {'✅' if success else '❌'} 快筛命中: {theme} x{len(stocks)}")

    return triggered


@app.get("/api/dashboard")
def get_dashboard(silent: bool = False):
    """获取看板数据
    silent=True 时不触发告警（用于轮询）
    silent=False 时自动检查并推送告警
    """
    pm = PoolManager()
    pools = pm.POOL_NAMES

    data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pools": {},
        "alerts": []
    }

    for pool_name in pools:
        stocks = get_pool_stocks(pool_name, pm=pm)
        data["pools"][pool_name] = {
            "count": len(stocks),
            "stocks": stocks
        }

    # 自动告警检查（默认开启，防止重复推送）
    if not silent:
        alerts = check_and_notify_alerts(data["pools"])
        data["alerts"] = alerts

    return data


@app.get("/api/alerts/trigger")
def trigger_alerts():
    """手动触发一次告警检查"""
    pm = PoolManager()
    pools = pm.POOL_NAMES

    pools_data = {}
    for pool_name in pools:
        stocks = get_pool_stocks(pool_name, pm=pm)
        pools_data[pool_name] = {"count": len(stocks), "stocks": stocks}

    alerts = check_and_notify_alerts(pools_data)
    return {"triggered": alerts, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


@app.get("/api/alerts/history")
def get_alert_history():
    """查看告警历史"""
    hist = _load_alert_history()
    return {"history": hist, "count": len(hist)}


@app.get("/api/market")
def get_market():
    """获取大盘行情"""
    try:
        quotes = fetch_quotes(["sh000001", "sz399001", "sz399006"])
    except Exception as e:
        print(f"[Dashboard] 获取大盘行情失败: {e}")
        quotes = []
    result = []
    for q in quotes:
        result.append({
            "code": q.get("代码", ""),
            "name": q.get("名称", ""),
            "price": q.get("现价", 0),
            "change_pct": q.get("涨跌幅", 0),
        })
    return {"market": result, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


@app.get("/api/history")
def get_history():
    """获取近期报告摘要"""
    history_dir = PROJECT_ROOT / "data" / "历史记录"
    reports = []

    today = datetime.now().strftime("%Y-%m-%d")
    for fname in sorted(history_dir.glob("*.md"), reverse=True)[:10]:
        name = fname.name
        if name.startswith(today):
            ftype = name.split("_")[1].replace(".md", "") if "_" in name else "report"
            reports.append({
                "name": name,
                "type": ftype,
                "time": datetime.fromtimestamp(fname.stat().st_mtime).strftime("%H:%M"),
            })

    return {"reports": reports}


@app.get("/")
def index():
    """返回看板页面"""
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    print("🚀 天枢权衡看板启动中...")
    print("📍 访问地址: http://localhost:8765")
    print("💡 Ctrl+C 停止服务")
    uvicorn.run(app, host="0.0.0.0", port=8765, reload=False)