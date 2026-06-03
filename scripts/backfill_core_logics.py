#!/usr/bin/env python3
"""
核心逻辑占位符规则回填脚本
==============================
读取重点观察池.json，对每只核心逻辑为"四维审查综合判断"的股票，
用综合分和驱动级别字段生成有意义的摘要替代占位符。

Usage:
    python scripts/backfill_core_logics.py

入池目录: 五池管理/
"""

import json
import sys
from pathlib import Path


def generate_core_logic(stock: dict) -> str:
    """根据综合分和驱动级别生成核心逻辑摘要"""
    score = stock.get("综合分", 0)
    level = stock.get("驱动级别", "")
    name = stock.get("名称", "")

    if level in ("S级", "A级") and score >= 70:
        return f"{name}：{level}驱动，综合评分{score}分，持续关注"
    elif level == "B级":
        return f"{name}：B级驱动，评分{score}分，谨慎观察"
    elif score >= 80:
        return f"{name}：高分标的({score}分)，等待最佳入场时机"
    elif score >= 70:
        return f"{name}：评分{score}分，{level}驱动，跟踪中"
    else:
        return f"{name}：评分{score}分，维持观察"


def main():
    # 确定项目根目录
    script_dir = Path(__file__).resolve().parent  # scripts/
    project_root = script_dir.parent  # 项目根目录
    pool_dir = project_root / "五池管理"
    pool_path = pool_dir / "重点观察池.json"

    if not pool_path.exists():
        print(f"[Error] 重点观察池.json 不存在: {pool_path}")
        sys.exit(1)

    # 读取数据
    with open(pool_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    stocks = data.get("stocks", [])
    if not stocks:
        print("[Info] 重点观察池为空，无需处理")
        return

    updated = 0
    for stock in stocks:
        core_logic = stock.get("核心逻辑", "").strip()
        if core_logic != "四维审查综合判断":
            continue

        score = stock.get("综合分", 0)
        if score == 0 or score is None:
            print(f"  [Skip] {stock.get('名称','?')}({stock.get('代码','?')}) 综合分为0，跳过")
            continue

        new_logic = generate_core_logic(stock)
        stock["核心逻辑"] = new_logic
        print(f"  [Backfill] {stock.get('名称','')}({stock.get('代码','')}): {core_logic} → {new_logic}")
        updated += 1

    if updated > 0:
        # 写回文件
        with open(pool_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 核心逻辑回填完成: 共更新 {updated} 只股票")
    else:
        print("\n[Info] 没有需要回填的核心逻辑占位符")


if __name__ == "__main__":
    main()
