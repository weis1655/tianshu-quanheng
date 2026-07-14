#!/usr/bin/env python3
"""
天枢信号推送 - 飞书通知模块
触发条件：快筛命中主题 / 决策信号 / 持仓警戒
"""

import os
import sys
import requests
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from logger import plog

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))


# ─── Feishu API ────────────────────────────────────────────

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_HOME_CHANNEL = os.environ.get("FEISHU_HOME_CHANNEL", "")  # oc_xxx

TENANT_TOKEN = None
TENANT_TOKEN_EXPIRES = 0


def get_tenant_token() -> Optional[str]:
    """获取 tenant access token"""
    global TENANT_TOKEN, TENANT_TOKEN_EXPIRES

    # 缓存检查（有效期 2 小时）
    if TENANT_TOKEN and datetime.now().timestamp() < TENANT_TOKEN_EXPIRES - 300:
        return TENANT_TOKEN

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }, timeout=10)

    data = resp.json()
    if data.get("code") != 0:
        plog("INFO", f"[Notifier] 获取Token失败: {data}")
        return None

    TENANT_TOKEN = data["tenant_access_token"]
    TENANT_TOKEN_EXPIRES = datetime.now().timestamp() + data.get("expire", 7200)
    return TENANT_TOKEN


def send_card(receive_id: str, card: dict) -> bool:
    """发送交互卡片"""
    token = get_tenant_token()
    if not token:
        return False

    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    params = {"receive_id_type": "chat_id"}

    payload = {
        "receive_id": receive_id,
        "msg_type": "interactive",
        "content": json.dumps(card)
    }

    resp = requests.post(url, headers=headers, params=params, json=payload, timeout=15)
    result = resp.json()

    if result.get("code") == 0:
        return True
    plog("INFO", f"[Notifier] 发送失败: {result}")
    return False


# ─── 卡片构建 ──────────────────────────────────────────────

def build_alert_card(alert_type: str, title: str, body: list) -> dict:
    """构建告警卡片"""
    colors = {
        "danger": ("red", "🔴 警戒"),
        "profit": ("green", "🟢 盈利"),
        "screen": ("blue", "🔍 快筛"),
        "decision": ("yellow", "💡 决策"),
        "info": ("grey", "ℹ️ 通知"),
    }
    color, icon = colors.get(alert_type, ("grey", "ℹ️"))

    elements = [
        {
            "tag": "markdown",
            "content": f"**{icon} {title}**\n{datetime.now().strftime('%H:%M:%S')}"
        },
        {"tag": "hr"}
    ]

    for line in body:
        elements.append({"tag": "markdown", "content": line})

    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": "🏛️ 天枢权衡 | 天枢 V3"}]
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"🏛️ 天枢信号 | {alert_type.upper()}"},
            "template": color
        },
        "elements": elements
    }


def build_holding_alert(stock: dict, threshold: float) -> dict:
    """构建持仓警戒卡片"""
    change = stock.get("change_pct", 0)
    price = stock.get("current_price", 0)
    code = stock.get("code", "")
    name = stock.get("name", "")
    pnl_pct = stock.get("pnl_pct", 0)
    entry_price = stock.get("entry_price", 0)

    is_loss = change < 0
    alert_type = "danger" if is_loss else "profit"

    title = f"持仓警戒 | {name}({code})"
    body = [
        f"**现价**: {price:.2f} 元",
        f"**今日涨跌**: {'🔴' if is_loss else '🟢'} {change:+.2f}%",
        f"**持仓盈亏**: {'🔴' if pnl_pct < 0 else '🟢'} {pnl_pct:+.2f}% (成本 {entry_price:.2f})",
        f"**警戒线**: {'跌幅' if is_loss else '涨幅'}超 {threshold:.1f}%",
    ]

    if is_loss:
        body.append("> ⚠️ 建议关注止损纪律")
    else:
        body.append("> 💰 涨势强劲，注意分批止盈")

    return build_alert_card(alert_type, title, body)


def build_screen_alert(candidates: list, theme: str) -> dict:
    """构建快筛告警卡片"""
    title = f"快筛命中 | {theme}"
    body = [f"**主题**: {theme}", f"**命中**: {len(candidates)} 只"]

    for s in candidates[:5]:
        code = s.get("code", s.get("股票代码", "?"))
        name = s.get("name", s.get("股票名称", "?"))
        change = s.get("change_pct", 0)
        body.append(f"- **{name}**({code}) {change:+.2f}%")

    if len(candidates) > 5:
        body.append(f"> ... 还有 {len(candidates) - 5} 只")

    return build_alert_card("screen", title, body)


def build_decision_alert(action: str, stocks: list, reason: str) -> dict:
    """构建决策告警卡片"""
    action_emoji = {"buy": "🟢 买入信号", "sell": "🔴 卖出信号", "hold": "🟡 持仓观察", "empty": "⚪ 空仓"}
    title = f"决策信号 | {action_emoji.get(action, action)}"

    body = [f"**动作**: {action_emoji.get(action, action)}", f"**原因**: {reason}"]

    if stocks:
        body.append(f"**标的**: {len(stocks)} 只")
        for s in stocks[:3]:
            name = s.get("name", "?")
            code = s.get("code", "?")
            body.append(f"- {name}({code})")

    return build_alert_card(
        "danger" if action == "sell" else ("profit" if action == "buy" else "info"),
        title, body
    )


# ─── 对外接口 ──────────────────────────────────────────────

def notify_holding_alert(stock: dict, threshold: float = -3.0) -> bool:
    """发送持仓警戒通知"""
    channel = FEISHU_HOME_CHANNEL
    if not channel:
        plog("INFO", "[Notifier] 未配置 FEISHU_HOME_CHANNEL，跳过推送")
        return False

    card = build_holding_alert(stock, threshold)
    return send_card(channel, card)


def notify_screen_hit(candidates: list, theme: str) -> bool:
    """发送快筛命中通知"""
    channel = FEISHU_HOME_CHANNEL
    if not channel:
        return False

    card = build_screen_alert(candidates, theme)
    return send_card(channel, card)


def notify_decision(action: str, stocks: list, reason: str) -> bool:
    """发送决策信号通知"""
    channel = FEISHU_HOME_CHANNEL
    if not channel:
        return False

    card = build_decision_alert(action, stocks, reason)
    return send_card(channel, card)


# ─── 集成钩子 ──────────────────────────────────────────────

def check_holdings_and_alert(pools: dict, threshold: float = -3.0) -> list:
    """检查持仓池，触发警戒（供 main.py 或 cron 调用）"""
    alerted = []
    holdings = pools.get("持仓池", {}).get("stocks", [])

    for s in holdings:
        change = s.get("change_pct", 0)
        if change <= threshold:
            success = notify_holding_alert(s, threshold)
            alerted.append({**s, "alerted": success})
            plog("INFO", f"[Notifier] {'✅' if success else '❌'} 持仓警戒: {s.get('name')} {change:+.2f}%")
    return alerted


if __name__ == "__main__":
    # 测试：检查持仓并告警
    plog("INFO", "[Notifier] 测试模式")
    if not FEISHU_APP_ID:
        plog("INFO", "❌ 缺少 FEISHU_APP_ID 环境变量")
        sys.exit(1)

    # 测试通知
    test_card = build_alert_card("info", "测试通知 | 天枢看板", [
        "✅ 飞书推送通道正常",
        "📊 天枢权衡看板已上线",
        "🔗 http://localhost:8765"
    ])
    result = send_card(FEISHU_HOME_CHANNEL, test_card)
    plog("INFO", f"{'✅' if result else '❌'} 推送结果: {'成功' if result else '失败'}")