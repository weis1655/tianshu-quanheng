#!/usr/bin/env python3
"""四轮联动核验：回头看脚本策略B 改动验证"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from 回头看 import (
    extract_fast_screen_stocks,
    extract_review_results,
    extract_decision_results,
)
import json

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

def check_dict_field(item, key, expected, label=""):
    actual = item.get(key, '<<MISSING>>')
    check(f"{label}.{key} == {expected!r}", actual == expected,
          f"got {actual!r}")

# ─── Test 1: Old format (2026-04-25) 快筛 ───
print("\n" + "="*60)
print("📋 测试1: 旧格式快筛 (2026-04-25 风格)")
print("="*60)

old_fast_path = "/home/seven/hermes-data/tianshu-quanheng/data/历史记录/2026-04-25_快筛报告.md"
stocks = extract_fast_screen_stocks(old_fast_path)
check(f"快筛-旧格式: 解析出 {len(stocks)} 只股票 (预期>=2)", len(stocks) >= 2,
      f"got {len(stocks)}")

if stocks:
    # 中国卫星（600118）应该被解析
    codes = [s['code'] for s in stocks]
    check("快筛-旧格式: 包含中国卫星(600118)", '600118' in codes,
          f"codes found: {codes}")
    check("快筛-旧格式: 包含天银机电(300342)", '300342' in codes,
          f"codes found: {codes}")
    # 检查 _format
    check_dict_field(stocks[0], '_format', 'v1-standard', '快筛-旧格式')
    check_dict_field(stocks[0], '_completeness', 'full', '快筛-旧格式')

# ─── Test 2: Old format (2026-04-25) 审查 ───
print("\n" + "="*60)
print("📋 测试2: 旧格式审查 (2026-04-25 风格)")
print("="*60)

old_review_path = "/home/seven/hermes-data/tianshu-quanheng/data/历史记录/2026-04-25_审查报告.md"
results = extract_review_results(old_review_path)
check(f"审查-旧格式: 解析出 {len(results)} 条 (预期>=2)", len(results) >= 2,
      f"got {len(results)}")

if results:
    r1 = results[0]
    check(f"审查-旧格式: 第1只代码长度6位", len(r1['code']) == 6,
          f"code={r1['code']}")
    check(f"审查-旧格式: 有分数且>0", r1.get('score', 0) > 0,
          f"score={r1.get('score')}")
    check(f"审查-旧格式: 有流转方向", bool(r1.get('flow')),
          f"flow={r1.get('flow')!r}")
    check(f"审查-旧格式: 日期正确", r1.get('date') == '2026-04-25',
          f"date={r1.get('date')}")
    # 检查格式标记
    check_dict_field(r1, '_format', 'v1-fallback-2', '审查-旧格式')
    check_dict_field(r1, '_completeness', 'partial', '审查-旧格式')

    # 验证中国卫星和天银机电的分数
    codes_scores = {r['code']: r['score'] for r in results}
    check("审查-旧格式: 中国卫星分数=74", codes_scores.get('600118') == 74,
          f"got score={codes_scores.get('600118')}")
    check("审查-旧格式: 天银机电分数=73", codes_scores.get('300342') == 73,
          f"got score={codes_scores.get('300342')}")

    # 验证流转方向
    flows = {r['code']: r['flow'] for r in results}
    check("审查-旧格式: 中国卫星flow=升级", flows.get('600118') == '升级',
          f"got flow={flows.get('600118')}")
    check("审查-旧格式: 天银机电flow=升级", flows.get('300342') == '升级',
          f"got flow={flows.get('300342')}")

    # 验证目标池
    pools = {r['code']: r.get('target_pool', '') for r in results}
    check("审查-旧格式: 中国卫星target_pool包含'重点'",
          '重点' in pools.get('600118', ''),
          f"got target_pool={pools.get('600118')!r}")

# ─── Test 3: Old format (2026-04-25) 决策 ───
print("\n" + "="*60)
print("📋 测试3: 旧格式决策 (2026-04-25 风格)")
print("="*60)

old_decision_path = "/home/seven/hermes-data/tianshu-quanheng/data/历史记录/2026-04-25_决策报告.md"
decisions = extract_decision_results(old_decision_path)
check(f"决策-旧格式: 解析出 {len(decisions)} 条决策", len(decisions) == 1,
      f"got {len(decisions)}")

if decisions:
    d = decisions[0]
    check("决策-旧格式: 非空仓", not d['is_empty'],
          f"is_empty={d['is_empty']}")
    check("决策-旧格式: 主推>0只", len(d['main_stocks']) > 0,
          f"main_stocks={d['main_stocks']}")
    if d['main_stocks']:
        ms = d['main_stocks'][0]
        check("决策-旧格式: 主推名称=天银机电",
              ms['name'] == '天银机电',
              f"name={ms['name']!r}")
        check("决策-旧格式: 主推代码=300342",
              ms['code'] == '300342',
              f"code={ms['code']}")
        check("决策-旧格式: 仓位=15%",
              ms['position'] == 15,
              f"position={ms['position']}%")
    check("决策-旧格式: 有备选", len(d['backup_stocks']) > 0,
          f"backup_stocks={d['backup_stocks']}")
    check_dict_field(d, '_format', 'v1-fallback-1', '决策-旧格式')
    check_dict_field(d, '_completeness', 'full', '决策-旧格式')

# ─── Test 4: New format (2026-06-03) 快筛 ───
print("\n" + "="*60)
print("📋 测试4: 新格式快筛 (2026-06-03 风格)")
print("="*60)

new_fast_path = "/home/seven/hermes-data/tianshu-quanheng/data/历史记录/2026-06-03_快筛报告.md"
stocks_new = extract_fast_screen_stocks(new_fast_path)
check(f"快筛-新格式: 解析出 {len(stocks_new)} 只股票 (预期>=8)", len(stocks_new) >= 8,
      f"got {len(stocks_new)}")

if stocks_new:
    codes_new = [s['code'] for s in stocks_new]
    check("快筛-新格式: 包含中国海油(600938)", '600938' in codes_new,
          f"codes: {codes_new}")
    check("快筛-新格式: 包含中际旭创(300308)", '300308' in codes_new,
          f"codes: {codes_new}")
    check_dict_field(stocks_new[0], '_format', 'v1-standard', '快筛-新格式')
    check_dict_field(stocks_new[0], '_completeness', 'full', '快筛-新格式')

# ─── Test 5: New format (2026-06-03) 审查 ───
print("\n" + "="*60)
print("📋 测试5: 新格式审查 (2026-06-03 风格)")
print("="*60)

new_review_path = "/home/seven/hermes-data/tianshu-quanheng/data/历史记录/2026-06-03_审查报告.md"
results_new = extract_review_results(new_review_path)
check(f"审查-新格式: 解析出 {len(results_new)} 条 (预期>=10)", len(results_new) >= 10,
      f"got {len(results_new)}")

if results_new:
    rn = results_new[0]
    check_dict_field(rn, '_format', 'v1-standard', '审查-新格式')

    # 检查具体的股票解析
    codes_scores_new = {r['code']: r['score'] for r in results_new}
    check("审查-新格式: 中国海油分数=71",
          codes_scores_new.get('600938') == 71,
          f"got score={codes_scores_new.get('600938')}")
    check("审查-新格式: 东芯股份分数=43.75",
          codes_scores_new.get('688110') == 43.75,
          f"got score={codes_scores_new.get('688110')}")

    # 检查流转方向（双箭头格式）
    flows_new = {r['code']: r['flow'] for r in results_new}
    check("审查-新格式: 中国海油flow=升级",
          flows_new.get('600938') == '升级',
          f"got flow={flows_new.get('600938')}")
    check("审查-新格式: 东芯股份flow=淘汰",
          flows_new.get('688110') == '淘汰',
          f"got flow={flows_new.get('688110')}")

    # 检查目标池
    pools_new = {r['code']: r.get('target_pool', '') for r in results_new}
    check("审查-新格式: 中国海油target_pool包含'重点'",
          '重点' in pools_new.get('600938', ''),
          f"got target_pool={pools_new.get('600938')!r}")

# ─── Test 6: New format (2026-06-03) 决策 ───
print("\n" + "="*60)
print("📋 测试6: 新格式决策 (2026-06-03 风格)")
print("="*60)

new_decision_path = "/home/seven/hermes-data/tianshu-quanheng/data/历史记录/2026-06-03_决策报告.md"
decisions_new = extract_decision_results(new_decision_path)
check(f"决策-新格式: 解析出 {len(decisions_new)} 条决策", len(decisions_new) == 1,
      f"got {len(decisions_new)}")

if decisions_new:
    dn = decisions_new[0]
    check("决策-新格式: 非空仓", not dn['is_empty'],
          f"is_empty={dn['is_empty']}")
    check("决策-新格式: 主推>0只", len(dn['main_stocks']) > 0,
          f"main_stocks={dn['main_stocks']}")
    if dn['main_stocks']:
        msn = dn['main_stocks'][0]
        check("决策-新格式: 主推名称=中国海油",
              msn['name'] == '中国海油',
              f"name={msn['name']!r}")
        check("决策-新格式: 主推代码=600938",
              msn['code'] == '600938',
              f"code={msn['code']}")
        check("决策-新格式: 仓位=15%",
              msn['position'] == 15,
              f"position={msn['position']}%")
    check_dict_field(dn, '_format', 'v1-standard', '决策-新格式')
    check_dict_field(dn, '_completeness', 'full', '决策-新格式')

# ─── Test 7: 空仓决策格式兼容性 ───
print("\n" + "="*60)
print("📋 测试7: 空仓决策 & 风险标记")
print("="*60)

# 检查 2026-06-01 空仓决策
empty_decision_path = "/home/seven/hermes-data/tianshu-quanheng/data/历史记录/2026-06-01_决策报告.md"
if os.path.exists(empty_decision_path):
    empty_decisions = extract_decision_results(empty_decision_path)
    check(f"决策-空仓: 解析出 {len(empty_decisions)} 条", len(empty_decisions) == 1,
          f"got {len(empty_decisions)}")
    if empty_decisions:
        ed = empty_decisions[0]
        check("决策-空仓: 标记为空仓", ed['is_empty'],
              f"is_empty={ed['is_empty']}")

# 检查风险标记
for r in results_new:
    if r['code'] == '688110':  # 东芯股份 - 有一票否决风险
        check("审查-新格式: 东芯股份风险标记包含'一票否决'",
              '一票否决' in r.get('risk_tags', []),
              f"risk_tags={r.get('risk_tags')}")
        break

# ─── Test 8: _format/_completeness 不在报告输出中 ───
print("\n" + "="*60)
print("📋 测试8: 格式标记不泄露到报告")
print("="*60)

report_path = "/home/seven/hermes-data/tianshu-quanheng/data/回顾报告/2026-06-03_回头看报告_v3.md"
if os.path.exists(report_path):
    with open(report_path, 'r') as f:
        content = f.read()
    check("报告: 不包含'_format'字段名", '_format' not in content,
          "'_format' appeared in report output!")
    check("报告: 不包含'_completeness'字段名", '_completeness' not in content,
          "'_completeness' appeared in report output!")

# ─── Test 9: 无退化 - 2026-04-25 "值得审查对象"识别完整性 ───
print("\n" + "="*60)
print("📋 测试9: 无退化验证 (旧格式完整性)")
print("="*60)

# 旧格式快筛应该能识别出"值得审查对象"下的所有3只
check("快筛-旧格式: 至少3只 '值得审查对象'",
      len(stocks) >= 3,
      f"actually got {len(stocks)} stocks, expected >= 3")

# 旧格式审查应该能解析出3条
check("审查-旧格式: 至少解析出3条",
      len(results) >= 3,
      f"got {len(results)} results, expected >= 3")

# ─── Final Summary ───
print("\n" + "="*60)
print(f"🏁 核验完成: {total} 项测试, {PASS} PASS, {FAIL} FAIL")
print("="*60)

sys.exit(0 if FAIL == 0 else 1)
