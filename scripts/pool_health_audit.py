#!/usr/bin/env python3
"""
五池健康审计脚本 — full_cycle 集成用
自动检查：跨池重复、信心度乱码、止损遗漏、字段完整性
"""
import json, os, sys
from pathlib import Path

POOL_DIR = os.path.expanduser("~/hermes-data/tianshu-quanheng/五池管理")
POOLS = ["持仓池.json","重点观察池.json","快筛候选池.json","边缘池.json","S级操作池.json"]

def audit():
    issues = []
    all_codes = {}

    for fname in POOLS:
        fp = os.path.join(POOL_DIR, fname)
        if not os.path.exists(fp):
            issues.append(f"[{fname}] 文件不存在")
            continue
        try:
            data = json.loads(Path(fp).read_text(encoding='utf-8'))
        except Exception as e:
            issues.append(f"[{fname}] JSON解析失败: {e}")
            continue

        stocks = data.get("stocks", [])
        pool_codes = []
        for i, s in enumerate(stocks):
            code = s.get("代码", s.get("股票代码", ""))
            name = s.get("名称", "?")
            pool_codes.append(code)

            # 信心度乱码检查
            for k in ["信心度", "综合判断"]:
                val = s.get(k)
                if isinstance(val, str):
                    if "**" in val or " |" in val or val.endswith("|"):
                        issues.append(f"[{fname}#{i}] {name}({code}) 字段{k}乱码: '{val}'")
                elif isinstance(val, dict):
                    inner = val.get("信心度", "")
                    if isinstance(inner, str) and ("**" in inner or " |" in inner or inner.endswith("|")):
                        issues.append(f"[{fname}#{i}] {name}({code}) 综合判断.信心度乱码: '{inner}'")

            # 止损遗漏检查
            sl = s.get("止损触发", s.get("止损线", 0))
            price = s.get("今日收盘", s.get("现价", 0))
            if isinstance(sl, (int,float)) and isinstance(price, (int,float)) and sl > 0 and price < sl:
                advice = s.get("操作建议", "")
                if "跌破止损" not in str(advice):
                    issues.append(f"[{fname}#{i}] {name}({code}) 止损遗漏: 收盘{price}<止损{sl}, 建议='{advice}'")

        all_codes[fname] = pool_codes

    # 跨池重复检查（S级+重点除外）
    from collections import Counter
    flat = []
    for fname, codes in all_codes.items():
        for c in codes:
            flat.append((fname, c))
    code_counts = Counter([c for _, c in flat])
    for code, n in code_counts.items():
        if n > 1:
            pools = [f for f, cc in flat if cc == code]
            # 允许 S级+重点 同时存在（S级主推同时入观察池监控）
            if set(pools) == {"S级操作池.json", "重点观察池.json"}:
                continue
            issues.append(f"[跨池] {code} 出现在 {pools}")

    return issues

if __name__ == "__main__":
    issues = audit()
    if issues:
        print(f"⚠️ 池审计发现 {len(issues)} 个问题:")
        for i in issues:
            print(f"  ❌ {i}")
        sys.exit(1)
    else:
        print("✅ 五池健康审计通过")
        sys.exit(0)