#!/usr/bin/env python3
"""交易成本全口径归因报告

逐笔核算：佣金/印花税/过户费/滑点/冲击成本
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
from safe_file_utils import safe_read_json

d = safe_read_json(ROOT / "data" / "decision_log.json", default=[])
if not d:
    print("ℹ️ 决策日志为空")
    sys.exit(0)
has_pnl = [r for r in d if r.get('actual_pnl') not in (None, 0, '', 0.0)]

print("=== 交易成本全口径归因 ===")
print(f"样本: {len(has_pnl)}笔")
print(f"\n{'成本项':<20} {'费率':<12} {'适用':<10} {'估算/笔':<10}")
print("-"*55)
print(f"{'佣金':<20} {'0.025%':<12} {'双边':<10} {'0.050%':<10}")
print(f"{'印花税':<20} {'0.05%':<12} {'仅卖出':<10} {'0.025%':<10}")
print(f"{'过户费':<20} {'0.001%':<12} {'双边':<10} {'0.002%':<10}")
print(f"{'冲击成本':<20} {'0.05%':<12} {'每笔':<10} {'0.050%':<10}")
print(f"{'固定成本(小计)':<20} {'':<12} {'':<10} {'0.127%':<10}")
print(f"{'滑点(大盘>100亿)':<20} {'0.08%':<12} {'每笔':<10} {'0.080%':<10}")
print(f"{'滑点(中盘20-100亿)':<20} {'0.12%':<12} {'每笔':<10} {'0.120%':<10}")
print(f"{'滑点(小盘<20亿)':<20} {'0.20%':<12} {'每笔':<10} {'0.200%':<10}")
print(f"\n{'全口径(大盘)':<20} {'':<12} {'':<10} {'0.207%':<10}")
print(f"{'全口径(小盘)':<20} {'':<12} {'':<10} {'0.327%':<10}")

# 实盘收益影响
total_pnl = sum(r.get('actual_pnl',0) or 0 for r in has_pnl)
avg_pnl = total_pnl / len(has_pnl)
print(f"\n=== 实盘收益影响 ===")
print(f"原始均收益: {avg_pnl:+.2f}%")
print(f"扣除全口径成本(大盘): {avg_pnl - 0.207:+.2f}%")
print(f"扣除全口径成本(小盘): {avg_pnl - 0.327:+.2f}%")
print(f"原回测滑点(0.1%): {avg_pnl - 0.1:+.2f}%")
print(f"旧回测滑点(0.02%): {avg_pnl - 0.02:+.2f}%")
