#!/usr/bin/env python3
"""
独立全池降级扫描脚本 — 不限执行环境，查缺补漏。
扫描全部5个池中评分<65的标的，强制降级到边缘池。

设计原则：
- 不依赖 main.py 或 review 流程，可独立运行
- 幂等：重复运行不影响正确性
- 只读扫描 + 写边缘池，不修改其他池的业务字段
- 可被 cron 调度，也可被 main.py 内联调用
"""

import sys
import json
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))
from path_config import ensure_agent_paths; ensure_agent_paths()

from pool_manager import PoolManager

# 降级阈值，与 _scan_and_downgrade 保持一致
DOWNGRADE_THRESHOLD = 65
# 扫描哪些池（注意：边缘池本身是低分汇聚地，不再扫；持仓池按止损逻辑处理）
SCAN_POOLS = ["重点观察池", "快筛候选池", "S级操作池"]


def sweep_all_pools(pm: PoolManager, dry_run: bool = False) -> dict:
    """扫描所有池，返回统计结果"""
    report = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scanned_pools": [],
        "total_demoted": 0,
        "details": [],
    }

    for pool_name in SCAN_POOLS:
        data = pm.load_pool(pool_name)
        if not data or "stocks" not in data:
            report["scanned_pools"].append({"pool": pool_name, "count": 0, "demoted": 0, "remaining": 0})
            continue

        stocks = data.get("stocks", [])
        if not stocks:
            report["scanned_pools"].append({"pool": pool_name, "count": 0, "demoted": 0, "remaining": 0})
            continue

        to_demote = []
        remaining = []
        for s in stocks:
            try:
                raw_score = s.get("综合分")
                score = float(raw_score) if raw_score is not None else 0
            except (TypeError, ValueError):
                raw_score = "?"
                score = 0

            # WO-002: 评分=0且入池≥3天的标的，直接降级
            if score <= 0:
                try:
                    entry_date = s.get("纳入日期", "")
                    if entry_date:
                        days_in_pool = (datetime.now() - datetime.strptime(entry_date, "%Y-%m-%d")).days
                        if days_in_pool >= 3:
                            to_demote.append(s)
                            continue
                except (ValueError, TypeError):
                    pass

            # 今日刚入S级操作池的标的，给予1天保护期不被sweep
            if pool_name == "S级操作池":
                try:
                    entry_date = s.get("纳入日期", "")
                    if entry_date and entry_date == datetime.now().strftime("%Y-%m-%d"):
                        remaining.append(s)
                        continue
                except (ValueError, TypeError):
                    pass

            if score < DOWNGRADE_THRESHOLD:
                to_demote.append(s)
            else:
                remaining.append(s)

        if not dry_run and to_demote:
            # 写回原池（移除低分标的）
            data["stocks"] = remaining
            data["统计"] = data.get("统计", {})
            data["统计"]["持仓数"] = len(remaining)
            data["统计"]["更新日期"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            pm.save_pool(pool_name, data)

            # 写入边缘池
            edge_pool = pm.load_pool("边缘池")
            edge_stocks = edge_pool.get("stocks", [])
            for item in to_demote:
                edge_stocks.append({
                    "代码": item.get("代码", ""),
                    "名称": item.get("名称", ""),
                    "综合分": float(item.get("综合分", 0)) if isinstance(item.get("综合分"), (int, float)) else 0,
                    "降级时间": datetime.now().strftime("%Y-%m-%d"),
                    "降级原因": f"独立扫描：综合分{raw_score or '?'} < {DOWNGRADE_THRESHOLD}，自动降级",
                })
            edge_pool["stocks"] = edge_stocks
            edge_pool["统计"] = edge_pool.get("统计", {})
            edge_pool["统计"]["累计进入"] = edge_pool.get("统计", {}).get("累计进入", 0) + len(to_demote)
            pm.save_pool("边缘池", edge_pool)

        pool_info = {
            "pool": pool_name,
            "count": len(stocks),
            "demoted": len(to_demote),
            "remaining": len(remaining),
        }
        if to_demote:
            pool_info["stocks"] = [
                f"{s.get('名称','?')}({s.get('代码','?')}) 评分{raw_score or '?'}"
                for s in to_demote
            ]
        report["scanned_pools"].append(pool_info)
        report["total_demoted"] += len(to_demote)
        report["details"].extend(pool_info.get("stocks", []))

    return report


def print_report(report: dict):
    """打印人类可读的扫描报告"""
    print(f"\n{'='*50}")
    print(f"🔍 独立降级扫描 | {report['timestamp']}")
    print(f"{'='*50}")
    for pool in report["scanned_pools"]:
        tag = "🟢" if pool["demoted"] == 0 else "🔴"
        print(f"  {tag} {pool['pool']}: {pool['count']} 只 → 降级 {pool['demoted']} 只, 保留 {pool['remaining']} 只")
        if pool.get("stocks"):
            for s in pool["stocks"]:
                print(f"       ⬇️ {s}")
    print(f"\n  合计降级: {report['total_demoted']} 只")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="独立全池降级扫描")
    parser.add_argument("--dry-run", action="store_true", help="仅扫描，不执行降级")
    args = parser.parse_args()

    pm = PoolManager()
    report = sweep_all_pools(pm, dry_run=args.dry_run)
    print_report(report)

    # 如果被 cron 调度，出口码反映是否有降级操作
    sys.exit(0 if report["total_demoted"] == 0 else 0)  # 不因有降级而报错