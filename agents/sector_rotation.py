#!/usr/bin/env python3
"""
板块轮动跟踪模块

功能：
1. 每日记录涨幅前3板块
2. 轮动到前3板块内选股
3. 放弃下跌趋势板块

数据来源：
- 腾讯行业板块行情
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


def fetch_sector_data() -> List[Dict]:
    """获取板块行情数据"""
    # 申万行业指数代码（简化版）
    sectors = [
        ("801010", "农林牧渔"),
        ("801020", "采掘"),
        ("801030", "化工"),
        ("801040", "钢铁"),
        ("801050", "有色金属"),
        ("801080", "电子"),
        ("801110", "家用电器"),
        ("801120", "纺织服装"),
        ("801150", "轻工制造"),
        ("801170", "医药生物"),
        ("801180", "公用事业"),
        ("801190", "交通运输"),
        ("801210", "商业贸易"),
        ("801230", "休闲服务"),
        ("801250", "建筑装饰"),
        ("801260", "金融业"),
        ("801280", "计算机"),
        ("801290", "通信"),
        ("801300", "传媒"),
        ("801880", "汽车"),
        ("801890", "电力设备"),
    ]
    
    results = []
    
    # 构建腾讯API请求
    codes = ",".join([f"r.{s[0]}" for s in sectors])
    cmd = f'curl -sL --max-time 15 "https://qt.gtimg.cn/q={codes}"'
    
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=20)
        content = r.stdout.decode("gbk", errors="replace")
        
        for line in content.strip().split("\n"):
            if not line:
                continue
            parts = line.split("~")
            if len(parts) > 10:
                # 解析申万行业指数代码
                raw_code = parts[0].replace("r.", "")
                # 查找对应行业名
                sector_name = next((s[1] for s in sectors if s[0] == raw_code), raw_code)
                
                price = float(parts[6]) if parts[6] else 0
                change = float(parts[7]) if parts[7] else 0
                
                results.append({
                    "code": raw_code,
                    "name": sector_name,
                    "price": price,
                    "change_pct": change,
                })
    except Exception as e:
        print(f"获取板块数据失败: {e}")
    
    return results


def get_top_sectors(n: int = 3) -> List[Dict]:
    """获取涨幅前N板块"""
    sectors = fetch_sector_data()
    if not sectors:
        return []
    
    # 按涨幅排序
    sectors.sort(key=lambda x: x.get("change_pct", 0), reverse=True)
    
    return sectors[:n]


def get_bottom_sectors(n: int = 3) -> List[Dict]:
    """获取跌幅前N板块"""
    sectors = fetch_sector_data()
    if not sectors:
        return []
    
    # 按涨幅排序
    sectors.sort(key=lambda x: x.get("change_pct", 0))
    
    return sectors[:n]


def save_sector_rotation() -> dict:
    """保存板块轮动数据"""
    today = datetime.now().strftime("%Y-%m-%d")
    
    top3 = get_top_sectors(3)
    bottom3 = get_bottom_sectors(3)
    
    result = {
        "date": today,
        "top_sectors": top3,
        "bottom_sectors": bottom3,
    }
    
    # 保存
    rot_file = PROJECT_ROOT / "data" / "sector_rotation.json"
    rot_file.parent.mkdir(parents=True, exist_ok=True)
    rot_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return result


def load_sector_rotation() -> dict:
    """加载板块轮动数据"""
    rot_file = PROJECT_ROOT / "data" / "sector_rotation.json"
    if not rot_file.exists():
        return {}
    
    return json.loads(rot_file.read_text(encoding="utf-8"))


def should_focus_sectors() -> List[str]:
    """判断应关注的板块（轮动到前3）"""
    rot = load_sector_rotation()
    if not rot:
        return []
    
    top = rot.get("top_sectors", [])
    return [s.get("name", "") for s in top if s.get("change_pct", 0) > 0]


def should_avoid_sectors() -> List[str]:
    """判断应回避的板块（轮动到后3）"""
    rot = load_sector_rotation()
    if not rot:
        return []
    
    bottom = rot.get("bottom_sectors", [])
    return [s.get("name", "") for s in bottom if s.get("change_pct", 0) < -2]


if __name__ == "__main__":
    print("=== 🌀 板块轮动跟踪 ===\n")
    
    result = save_sector_rotation()
    
    print(f"日期: {result['date']}")
    print(f"\n🔥 涨幅前3:")
    for i, s in enumerate(result["top_sectors"], 1):
        print(f"  {i}. {s['name']}: {s['change_pct']:+.2f}%")
    
    print(f"\n❄️ 跌幅前3:")
    for i, s in enumerate(result["bottom_sectors"], 1):
        print(f"  {i}. {s['name']}: {s['change_pct']:+.2f}%")
    
    focus = should_focus_sectors()
    avoid = should_avoid_sectors()
    
    print(f"\n✅ 应关注板块: {focus if focus else '无'}")
    print(f"⚠️ 应回避板块: {avoid if avoid else '无'}")