#!/usr/bin/env python3
"""
触发条件模块 - 多元化触发器

支持：
1. 条件触发：新闻S级驱动/涨停/量能异常/北向资金大幅流入
2. 手动触发：盟主一句话"分析XX"
3. 定时触发：cron已有

触发流程：
check_triggers() → 有触发 → 通知盟主 → 确认后执行
"""

import json
import re
import subprocess
import sys
import logging
from datetime import datetime
from pathlib import Path

from safe_file_utils import safe_read_json, safe_write_file

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

from market_agent import fetch_quotes


# 触发阈值配置
TRIGGER_CONFIG = {
    # ，涨幅超5%且成交额超10亿
    "涨停触发": {"pct": 5.0, "amount": 10_0000_0000},
    # 量能放大超2倍
    "量能触发": {"ratio": 2.0},
    # 北向资金单日流入超50亿
    "北向资金触发": {"amount": 50_0000_0000},
    # 新闻S级驱动（从新闻分析结果中提取）
}


def check_trigger_config() -> dict:
    """加载触发配置"""
    config_file = PROJECT_ROOT / "data" / "trigger_config.json"
    if config_file.exists():
        data = safe_read_json(config_file, default=None, log_error=False)
        return data if data is not None else TRIGGER_CONFIG.copy()
    return TRIGGER_CONFIG.copy()


def save_trigger_config(config: dict) -> None:
    """保存触发配置"""
    config_file = PROJECT_ROOT / "data" / "trigger_config.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    success = safe_write_file(config_file, json.dumps(config, ensure_ascii=False, indent=2))
    if not success:
        logger.error(f"[Trigger] 保存触发配置失败: {config_file}")


def check_limit_up() -> list:
    """检查涨停股"""
    print("=== 📈 涨停检查 ===")
    config = check_trigger_config()
    
    # 获取涨幅榜
    cmd = 'curl -sL --max-time 10 "https://qt.gtimg.cn/q=flashing_china"'
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=15)
        content = r.stdout.decode("gbk", errors="replace")
        
        # 解析涨停股
        limit_ups = []
        for line in content.strip().split("\n"):
            if not line:
                continue
            parts = line.split("~")
            if len(parts) > 45:
                code = parts[0].replace("v_pv_", "")
                name = parts[1]
                price = float(parts[6]) if parts[6] else 0
                change = float(parts[7]) if parts[7] else 0
                
                if change >= config["涨停触发"]["pct"]:
                    limit_ups.append({
                        "code": code,
                        "name": name,
                        "price": price,
                        "change_pct": change,
                    })
        
        if limit_ups:
            print(f"发现 {len(limit_ups)} 只涨停股")
            for s in limit_ups[:5]:
                print(f"  {s['code']} {s['name']} {s['change_pct']:+.2f}%")
        else:
            print("无涨停股")
        
        return limit_ups
    except Exception as e:
        print(f"检查失败: {e}")
        return []


def check_volume_surge() -> list:
    """检查量能异动"""
    print("=== 📊 量能检查 ===")
    config = check_trigger_config()
    
    # 获取沪深成交额前20
    codes = ["sh000001", "sz399001"]
    cmd = 'curl -sL --max-time 10 "https://qt.gtimg.cn/q=sh000001,sz399001"'
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=15)
        content = r.stdout.decode("gbk", errors="replace")
        
        surges = []
        for line in content.strip().split("\n"):
            if not line:
                continue
            parts = line.split("~")
            if len(parts) > 45:
                code = parts[0].replace("v_pv_", "")
                name = parts[1]
                
                # 成交量/成交额
                vol = float(parts[37]) if parts[37] else 0  # 成交量
                
                # 简化：没有历史数据对比，暂跳过
                # TODO: 接入历史数据对比
        
        print("需接入历史数据")
        return []
    except Exception as e:
        print(f"检查失败: {e}")
        return []


def check_north_money() -> dict:
    """检查北向资金"""
    print("=== 🌍 北向资金检查 ===")
    # 这个需要付费数据，暂时跳过
    # TODO: 接入北向资金数据
    
    print("需接入北向资金数据源")
    return {}


def check_news_trigger() -> list:
    """检查新闻S级驱动"""
    print("=== 📰 新闻驱动检查 ===")
    
    # 检查最近的新闻分析
    today = datetime.now().strftime("%Y-%m-%d")
    news_file = PROJECT_ROOT / "data" / "历史记录" / f"{today}_宏观前置分析.md"
    
    if not news_file.exists():
        # 检查昨日
        yesterday = (datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        news_file = PROJECT_ROOT / "data" / "历史记录" / f"{yesterday}_宏观前置分析.md"
    
    if not news_file.exists():
        print("无新闻分析文件")
        return []
    
    content = news_file.read_text(encoding="utf-8")
    
    # 提取S级驱动
    s_drives = re.findall(r"【S级驱动】([^\n]+)", content)
    
    triggers = []
    for drive in s_drives:
        triggers.append({"type": "S级驱动", "content": drive.strip()})
    
    if triggers:
        print(f"发现 {len(triggers)} 个S级驱动")
        for t in triggers:
            print(f"  {t['content']}")
    else:
        print("无S级驱动")
    
    return triggers


def check_all_triggers() -> dict:
    """检查所有触发条件"""
    print("=" * 40)
    print("🔍 触发条件检查")
    print("=" * 40)
    
    result = {
        "triggered": False,
        "triggers": [],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    
    # 检查各项
    limit_ups = check_limit_up()
    if limit_ups:
        result["triggered"] = True
        result["triggers"].append({"type": "涨停", "count": len(limit_ups), "stocks": limit_ups[:3]})
    
    news_drives = check_news_trigger()
    if news_drives:
        result["triggered"] = True
        result["triggers"].extend(news_drives)
    
    # 返回
    if result["triggered"]:
        print("\n⚠️ 触发条件满足！")
    else:
        print("\n✅ 无触发条件")
    
    return result


def parse_manual_trigger(text: str) -> dict:
    """解析手动触发"""
    text = text.strip()
    
    # 提取板块/行业
    sectors = re.findall(r"(AI算力|光模块|半导体|新能源|汽车|医药|军工|芯片|云计算|数字经济|卫星|机器人|储能|电力|石油|煤炭)", text)
    
    # 提取股票代码
    codes = re.findall(r"\b(\d{6})\b", text)
    
    return {
        "type": "manual",
        "sectors": sectors,
        "codes": codes,
        "original": text,
    }


def suggest_action(triggers: dict) -> str:
    """建议执行���作"""
    if not triggers.get("triggered"):
        return "无需执行"
    
    action_parts = []
    for t in triggers.get("triggers", []):
        if isinstance(t, dict):
            t_type = t.get("type", "未知")
            if t_type == "涨停":
                action_parts.append(f"关注涨停股: {t.get('count', 0)}只")
            elif t_type == "S级驱动":
                action_parts.append(f"分析S级驱动: {t.get('content', '')[:20]}")
        else:
            action_parts.append(str(t)[:30])
    
    return "; ".join(action_parts) if action_parts else "检查触发条件"


if __name__ == "__main__":
    result = check_all_triggers()
    print(f"\n建议动作: {suggest_action(result)}")