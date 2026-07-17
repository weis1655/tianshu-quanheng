#!/usr/bin/env python3
"""因子有效性监控脚本（每日运行）
计算各因子的IC值、胜率、盈亏比，识别失效因子。
"""
import json, sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
from safe_file_utils import safe_read_json


def load_decision_log():
    data = safe_read_json(ROOT / "data" / "decision_log.json", default=[])
    if not isinstance(data, list):
        return []
    return [r for r in data if r.get('actual_pnl') not in (None, 0, '', 0.0)]

def analyze():
    records = load_decision_log()
    if not records:
        print("ℹ️ 无可用pnl记录")
        return
    
    print(f"📊 因子有效性报告 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"样本: {len(records)}条")
    
    # 评分分层
    bins = [(0,60), (60,70), (70,75), (75,80), (80,100)]
    for lo, hi in bins:
        g = [r for r in records if lo <= (r.get('tech_score',0) or 0) < hi]
        if g:
            wins = sum(1 for r in g if (r.get('actual_pnl',0) or 0) > 0)
            avg = sum(r.get('actual_pnl',0) or 0 for r in g) / len(g)
            print(f"  评分[{lo}-{hi}): {len(g)}笔 胜率{wins/len(g)*100:.0f}% 均收益{avg:+.2f}%")
    
    # IC值（评分与收益的Spearman秩相关近似）
    if len(records) >= 3:
        scores = [(r.get('tech_score',0) or 0) for r in records]
        pnls = [(r.get('actual_pnl',0) or 0) for r in records]
        # 统计评分-收益排序方向：高分是否对应高收益
        high = [p for s,p in zip(scores,pnls) if s >= 75]
        low = [p for s,p in zip(scores,pnls) if s < 75]
        if high and low:
            h_avg = sum(high)/len(high)
            l_avg = sum(low)/len(low)
            print(f"  IC方向: 高分均值{h_avg:+.2f}% vs 低分均值{l_avg:+.2f}% ({'正向✅' if h_avg > l_avg else '负向❌'})")

    print(f"  总胜率: {sum(1 for r in records if (r.get('actual_pnl',0) or 0) > 0)}/{len(records)}")
    print(f"  总均收益: {sum(r.get('actual_pnl',0) or 0 for r in records)/len(records):+.2f}%")

if __name__ == '__main__':
    analyze()
