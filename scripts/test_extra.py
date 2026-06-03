#!/usr/bin/env python3
"""额外核验: 仓位正则 & 边缘情况"""
import sys, os, re
sys.path.insert(0, '/home/seven/hermes-data/tianshu-quanheng/scripts')
from 回头看 import (
    extract_fast_screen_stocks,
    extract_review_results,
    extract_decision_results,
    parse_date_from_filename,
)

PASS = 0
FAIL = 0
total = 0

def check(name, condition, detail=""):
    global total, PASS, FAIL
    total += 1
    if condition:
        print(f"  ✅ PASS | {name}")
        PASS += 1
    else:
        print(f"  ❌ FAIL | {name}")
        if detail:
            print(f"         | {detail}")
        FAIL += 1

# ─── Test: 仓位正则 - 总仓位负向前瞻 ───
print("\n" + "="*60)
print("📋 仓位正则验证 (负向前瞻避开'总仓位')")
print("="*60)

# 模拟包含"总仓位"的文本
text_with_total = "单笔仓位：15%（1.5万元）\n总仓位不超50%"
pos_match = re.search(r'(?:单笔仓位|仓位)[：:]?\s*(\d+)%\s*(?!仓位|总|整体)', text_with_total)
check("仓位正则: 避开'总仓位'后的%", pos_match is not None and pos_match.group(1) == '15',
      f"match={pos_match.group(1) if pos_match else 'None'}")

# 验证总仓位不被误匹配
total_pos = re.search(r'总仓位\s*(?:不超|控制|不高于)?\s*(\d+)%', text_with_total)
# The negative lookahead should prevent the regex from matching "总仓位不超50%"
pos_match2 = re.findall(r'(?:单笔仓位|仓位)[：:]?\s*(\d+)%\s*(?!仓位|总|整体)', text_with_total)
check("仓位正则: 只匹配单笔仓位, 不匹配总仓位", 
      len(pos_match2) == 1 and pos_match2[0] == '15',
      f"found positions: {pos_match2}")

# ─── Test: 5月底各日期快筛解析 ───
print("\n" + "="*60)
print("📋 多日期快筛解析验证 (2026-05-26~2026-06-03)")
print("="*60)

dates = ['2026-05-26', '2026-05-27', '2026-05-28', '2026-05-29', 
         '2026-06-01', '2026-06-02', '2026-06-03']
base = '/home/seven/hermes-data/tianshu-quanheng/data/历史记录'
total_stocks = 0
for d in dates:
    fp = f"{base}/{d}_快筛报告.md"
    if os.path.exists(fp):
        s = extract_fast_screen_stocks(fp)
        total_stocks += len(s)
        check(f"快筛 {d}: 解析出 {len(s)} 只", len(s) >= 2, f"got {len(s)}")
        if s:
            check(f"快筛 {d}: 有_format标记", '_format' in s[0], f"keys={list(s[0].keys())}")
            check(f"快筛 {d}: 有_completeness标记", '_completeness' in s[0])

print(f"\n  总计解析快筛: {total_stocks} 只")

# ─── Test: 各日期审查解析 ───
print("\n" + "="*60)
print("📋 多日期审查解析验证")
print("="*60)

total_reviews = 0
for d in dates:
    fp = f"{base}/{d}_审查报告.md"
    if os.path.exists(fp):
        r = extract_review_results(fp)
        total_reviews += len(r)
        check(f"审查 {d}: 解析出 {len(r)} 条", len(r) >= 1, f"got {len(r)}")
        if r:
            check(f"审查 {d}: 有_format标记", '_format' in r[0])
            check(f"审查 {d}: 有_completeness标记", '_completeness' in r[0])

print(f"\n  总计解析审查: {total_reviews} 条")

# ─── Test: 各日期决策解析 ───
print("\n" + "="*60)
print("📋 多日期决策解析验证")
print("="*60)

total_decisions = 0
for d in dates:
    fp = f"{base}/{d}_决策报告.md"
    if os.path.exists(fp):
        dec = extract_decision_results(fp)
        total_decisions += len(dec)
        check(f"决策 {d}: 解析出 {len(dec)} 条", len(dec) >= 1, f"got {len(dec)}")
        if dec:
            check(f"决策 {d}: 有_format标记", '_format' in dec[0])
            check(f"决策 {d}: 有_completeness标记", '_completeness' in dec[0])
            if dec[0]['main_stocks']:
                check(f"决策 {d}: 主推仓位>0", dec[0]['main_stocks'][0]['position'] > 0,
                      f"pos={dec[0]['main_stocks'][0]['position']}%")

print(f"\n  总计解析决策: {total_decisions} 条")

# ─── Final Summary ───
print(f"\n{'='*60}")
print(f"🏁 核验完成: {total} 项测试, {PASS} PASS, {FAIL} FAIL")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)