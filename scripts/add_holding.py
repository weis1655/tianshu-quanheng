#!/usr/bin/env python3
"""
便捷工具：添加持仓到天枢权衡系统
使用方法: python scripts/add_holding.py <股票代码> <股票名称> [选项]
例如: python scripts/add_holding.py 000001 平安银行 --成本 12.50 --市场 SZ
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime

def add_holding():
    if len(sys.argv) < 3:
        print("使用方法: python scripts/add_holding.py <股票代码> <股票名称> [选项]")
        print("选项:")
        print("  --成本 <价格>          持仓成本价 (默认: 0)")
        print("  --市场 <SH|SZ>         市场标识 (默认: 根据代码自动判断)")
        print("  --备注 <文本>          持仓备注 (默认: 便捷工具添加)")
        print("  --最新价 <价格>        最新价 (默认: 0)")
        print("")
        print("示例:")
        print("  python scripts/add_holding.py 000001 平安银行 --成本 12.50")
        print("  python scripts/add_holding.py 601899 紫金矿业 --成本 33.04 --市场 SH")
        sys.exit(1)
    
    # 解析参数
    stock_code = sys.argv[1]
    stock_name = sys.argv[2]
    
    # 默认值
    cost = 0.0
    market = ""
    remark = "便捷工具添加"
    latest_price = 0.0
    
    # 解析可选参数
    i = 3
    while i < len(sys.argv):
        if sys.argv[i] == "--成本" and i + 1 < len(sys.argv):
            cost = float(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--市场" and i + 1 < len(sys.argv):
            market = sys.argv[i + 1].upper()
            i += 2
        elif sys.argv[i] == "--备注" and i + 1 < len(sys.argv):
            remark = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--最新价" and i + 1 < len(sys.argv):
            latest_price = float(sys.argv[i + 1])
            i += 2
        else:
            print(f"未知参数: {sys.argv[i]}")
            sys.exit(1)
    
    # 自动判断市场（如果未指定）
    if not market:
        if stock_code.startswith(('6', '5')):
            market = "SH"
        else:
            market = "SZ"
    
    # 项目根目录
    project_root = Path(__file__).parent.parent
    manual_holding_file = project_root / "data" / "盟主持仓.json"
    pool_holding_file = project_root / "五池管理" / "持仓池.json"
    
    # 读取现有持仓数据
    if manual_holding_file.exists():
        with open(manual_holding_file, 'r', encoding='utf-8') as f:
            manual_data = json.load(f)
    else:
        # 创建基本结构
        manual_data = {
            "version": "1.0",
            "updated": datetime.now().strftime("%Y-%m-%d"),
            "owner": "盟主",
            "本金": 100000,
            "持仓": [],
            "统计": {
                "持仓数": 0,
                "总市值": 0,
                "总盈亏": 0,
                "胜率": 0
            },
            "历史交易": []
        }
    
    # 检查是否已存在相同股票代码的持仓
    existing_index = -1
    for i, holding in enumerate(manual_data.get("持仓", [])):
        if holding.get("股票代码") == stock_code:
            existing_index = i
            break
    
    # 创建新持仓对象
    new_holding = {
        "股票代码": stock_code,
        "股票名称": stock_name,
        "市场": market,
        "成本": cost,
        "最新价": latest_price,
        "盈亏": latest_price - cost if latest_price > 0 and cost > 0 else 0,
        "盈亏比例": ((latest_price - cost) / cost * 100) if cost > 0 else 0,
        "建仓日期": datetime.now().strftime("%Y-%m-%d"),
        "备注": remark
    }
    
    # 添加或更新持仓
    if existing_index >= 0:
        manual_data["持仓"][existing_index] = new_holding
        print(f"更新持仓: {stock_name}({stock_code})")
    else:
        if "持仓" not in manual_data:
            manual_data["持仓"] = []
        manual_data["持仓"].append(new_holding)
        print(f"添加新持仓: {stock_name}({stock_code})")
    
    # 更新时间戳和统计
    manual_data["updated"] = datetime.now().strftime("%Y-%m-%d")
    manual_data["统计"]["持仓数"] = len(manual_data.get("持仓", []))
    
    # 保存手动持仓文件
    with open(manual_holding_file, 'w', encoding='utf-8') as f:
        json.dump(manual_data, f, ensure_ascii=False, indent=2)
    
    print(f"已更新手动持仓文件: {manual_holding_file}")
    
    # 同步到持仓池
    sync_to_pool(manual_data, pool_holding_file)
    # P0-3：从流转池移除该股（避免边缘池↔持仓池重叠）
    _remove_from_trade_pools(stock_code)
    
    print("✅ 持仓添加完成！")
    print("💡 提示: 执行 'python main.py feedback' 或其他主要命令以查看更新效果")

def sync_to_pool(manual_data, pool_holding_file):
    """将手动持仓数据同步到持仓池文件"""
    # 读取现有持仓池数据（保留其他结构）
    if pool_holding_file.exists():
        with open(pool_holding_file, 'r', encoding='utf-8') as f:
            pool_data = json.load(f)
    else:
        # 创建基本持仓池结构
        pool_data = {
            "池名称": "持仓池",
            "池定义": "当前真实持仓",
            "资金配置": {
                "总资金": 100000,
                "可用资金": 100000,
                "持仓市值": 0,
                "持仓比例": 0
            },
            "stocks": [],
            "历史持仓": [],
            "统计": {
                "创建日期": datetime.now().strftime("%Y-%m-%d"),
                "盈利次数": 0,
                "亏损次数": 0,
                "持仓数": 0,
                "更新日期": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }
    
        # 更新持仓池中的 stocks
    pool_data["stocks"] = manual_data.get("持仓", [])
    
    # 更新统计信息
    pool_data["统计"]["持仓数"] = len(pool_data["stocks"])
    pool_data["统计"]["更新日期"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 保持其他字段不变（如资金配置等）
    
    # 保存持仓池文件
    with open(pool_holding_file, 'w', encoding='utf-8') as f:
        json.dump(pool_data, f, ensure_ascii=False, indent=2)
    
    print(f"已同步到持仓池文件: {pool_holding_file}")


def _remove_from_trade_pools(stock_code):
    """P0-3：买入后从流转池（候选/重点/边缘池）移除该股，避免跨池重复"""
    pool_dir = Path(__file__).parent.parent / "五池管理"
    for pool_name in ["快筛候选池", "重点观察池", "边缘池"]:
        pool_file = pool_dir / f"{pool_name}.json"
        if not pool_file.exists():
            continue
        with open(pool_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        stocks = data.get("stocks", [])
        before = len(stocks)
        data["stocks"] = [s for s in stocks if (s.get("代码") or s.get("股票代码", "")) != stock_code]
        removed = before - len(data["stocks"])
        if removed > 0:
            with open(pool_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"✅ [P0-3] {pool_name}: 移除 {stock_code}（已转持仓）")


if __name__ == "__main__":
    add_holding()