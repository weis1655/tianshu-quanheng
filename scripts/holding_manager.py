#!/usr/bin/env python3
"""
天枢权衡持仓管理工具
支持添加、列出、删除持仓
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime

def load_manual_holdings():
    """加载手动持仓文件"""
    project_root = Path(__file__).parent.parent
    manual_file = project_root / "data" / "盟主持仓.json"
    
    if manual_file.exists():
        with open(manual_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        # 返回默认结构
        return {
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

def save_manual_holdings(data):
    """保存手动持仓文件"""
    project_root = Path(__file__).parent.parent
    manual_file = project_root / "data" / "盟主持仓.json"
    
    data["updated"] = datetime.now().strftime("%Y-%m-%d")
    # 更新持仓数统计
    data["统计"]["持仓数"] = len(data.get("持仓", []))
    
    with open(manual_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def sync_to_pool(manual_data):
    """同步持仓到持仓池"""
    project_root = Path(__file__).parent.parent
    pool_file = project_root / "五池管理" / "持仓池.json"
    
    # 读取现有持仓池数据（保留其他结构）
    if pool_file.exists():
        with open(pool_file, 'r', encoding='utf-8') as f:
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
    
    # 保存持仓池文件
    with open(pool_file, 'w', encoding='utf-8') as f:
        json.dump(pool_data, f, ensure_ascii=False, indent=2)


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


def add_holding(stock_code, stock_name, cost=0.0, market="", remark="持仓管理工具添加", latest_price=0.0):
    """添加持仓"""
    data = load_manual_holdings()
    
    # 自动判断市场（如果未指定）
    if not market:
        if stock_code.startswith(('6', '5')):
            market = "SH"
        else:
            market = "SZ"
    
    # 检查是否已存在
    for holding in data.get("持仓", []):
        if holding.get("股票代码") == stock_code:
            print(f"⚠️  持仓 {stock_code} 已存在，将被更新")
            # 更新现有持仓
            holding.update({
                "股票名称": stock_name,
                "市场": market,
                "成本": cost,
                "最新价": latest_price,
                "盈亏": latest_price - cost if latest_price > 0 and cost > 0 else 0,
                "盈亏比例": ((latest_price - cost) / cost * 100) if cost > 0 else 0,
                "建仓日期": datetime.now().strftime("%Y-%m-%d"),
                "备注": remark
            })
            break
    else:
        # 添加新持仓
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
        if "持仓" not in data:
            data["持仓"] = []
        data["持仓"].append(new_holding)
        print(f"✅  添加持仓: {stock_name}({stock_code})")
    
    # 保存并同步
    save_manual_holdings(data)
    sync_to_pool(data)
    # P0-3：从流转池移除该股（避免边缘池↔持仓池重叠）
    _remove_from_trade_pools(stock_code)
    
    return True

def list_holdings():
    """列出所有持仓"""
    data = load_manual_holdings()
    holdings = data.get("持仓", [])
    
    if not holdings:
        print("📭  暂无持仓")
        return
    
    print(f"\n📊 当前持仓 ({len(holdings)} 只):")
    print("-" * 80)
    print(f"{'代码':<8} {'名称':<12} {'市场':<4} {'成本':<8} {'现价':<8} {'盈亏':<8} {'盈亏%':<8} {'日期':<12} {'备注'}")
    print("-" * 80)
    
    for holding in holdings:
        code = holding.get("股票代码", "")
        name = holding.get("股票名称", "")
        market = holding.get("市场", "")
        cost = holding.get("成本", 0)
        price = holding.get("最新价", 0)
        pnl = holding.get("盈亏", 0)
        pnl_pct = holding.get("盈亏比例", 0)
        date = holding.get("建仓日期", "")
        remark = holding.get("备注", "")
        
        print(f"{code:<8} {name:<12} {market:<4} {cost:<8.2f} {price:<8.2f} {pnl:<+8.2f} {pnl_pct:<+7.2f}% {date:<12} {remark}")
    
    print("-" * 80)
    stats = data.get("统计", {})
    print(f"📈 统计: 持仓数 {stats.get('持仓数', 0)} | 总市值 {stats.get('总市值', 0):.2f} | 总盈亏 {stats.get('总盈亏', 0):.2f} | 胜率 {stats.get('胜率', 0)}%")

def remove_holding(stock_code):
    """删除持仓"""
    data = load_manual_holdings()
    holdings = data.get("持仓", [])
    
    # 查找持仓
    found_index = -1
    for i, holding in enumerate(holdings):
        if holding.get("股票代码") == stock_code:
            found_index = i
            break
    
    if found_index == -1:
        print(f"❌ 未找到持仓: {stock_code}")
        return False
    
    # 删除持仓
    removed = holdings.pop(found_index)
    print(f"🗑️  删除持仓: {removed.get('股票名称')}({removed.get('股票代码')})")
    
    # 保存并同步
    save_manual_holdings(data)
    sync_to_pool(data)
    
    return True

def show_help():
    """显示帮助信息"""
    print("""
天枢权衡持仓管理工具

使用方法:
  python scripts/holding_manager.py <命令> [参数]

命令:
  add <股票代码> <股票名称> [选项]   添加持仓
  list                               列出所有持仓
  rm <股票代码>                      删除持仓
  help                               显示此帮助

添加选项:
  --成本 <价格>          持仓成本价 (默认: 0)
  --市场 <SH|SZ>         市场标识 (默认: 根据代码自动判断)
  --备注 <文本>          持仓备注 (默认: 持仓管理工具添加)
  --最新价 <价格>        最新价 (默认: 0)

示例:
  python scripts/holding_manager.py add 000001 平安银行 --成本 12.50
  python scripts/holding_manager.py add 601899 紫金矿业 --成本 33.04 --市场 SH
  python scripts/holding_manager.py list
  python scripts/holding_manager.py rm 000001

注意: 执行添加/删除后，系统会自动同步到持仓池，您可以立即运行
      'python main.py feedback' 查看更新效果。
    """)

def main():
    if len(sys.argv) < 2:
        show_help()
        return
    
    command = sys.argv[1].lower()
    
    if command == "add":
        if len(sys.argv) < 4:
            print("❌ 错误: 添加持仓需要至少提供股票代码和名称")
            print("用法: python scripts/holding_manager.py add <股票代码> <股票名称> [选项]")
            return
        
        stock_code = sys.argv[2]
        stock_name = sys.argv[3]
        
        # 解析选项
        cost = 0.0
        market = ""
        remark = "持仓管理工具添加"
        latest_price = 0.0
        
        i = 4
        while i < len(sys.argv):
            if sys.argv[i] == "--成本" and i + 1 < len(sys.argv):
                try:
                    cost = float(sys.argv[i + 1])
                except ValueError:
                    print(f"❌ 错误: 成本价必须是数字: {sys.argv[i + 1]}")
                    return
                i += 2
            elif sys.argv[i] == "--市场" and i + 1 < len(sys.argv):
                market = sys.argv[i + 1].upper()
                if market not in ["SH", "SZ"]:
                    print(f"❌ 错误: 市场必须是 SH 或 SZ: {sys.argv[i + 1]}")
                    return
                i += 2
            elif sys.argv[i] == "--备注" and i + 1 < len(sys.argv):
                remark = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--最新价" and i + 1 < len(sys.argv):
                try:
                    latest_price = float(sys.argv[i + 1])
                except ValueError:
                    print(f"❌ 错误: 最新价必须是数字: {sys.argv[i + 1]}")
                    return
                i += 2
            else:
                print(f"❌ 错误: 未知选项: {sys.argv[i]}")
                print("可用选项: --成本 --市场 --备注 --最新价")
                return
        
        add_holding(stock_code, stock_name, cost, market, remark, latest_price)
        print("💡 提示: 执行 'python main.py feedback' 查看更新效果")
    
    elif command == "list":
        list_holdings()
    
    elif command == "rm" or command == "remove" or command == "delete":
        if len(sys.argv) < 3:
            print("❌ 错误: 删除持仓需要提供股票代码")
            print("用法: python scripts/holding_manager.py rm <股票代码>")
            return
        
        stock_code = sys.argv[2]
        remove_holding(stock_code)
        print("💡 提示: 执行 'python main.py feedback' 查看更新效果")
    
    elif command == "help":
        show_help()
    
    else:
        print(f"❌ 错误: 未知命令: {command}")
        print("运行 'python scripts/holding_manager.py help' 查看可用命令")

if __name__ == "__main__":
    main()