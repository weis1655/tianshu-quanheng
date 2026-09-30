#!/usr/bin/env python3
"""
板块配额硬校验单元测试

覆盖：
1. 板块归类准确性（关键回归点：中际旭创→光模块、招商蛇口→地产）
2. 地产 4 只 → 截到 3（默认综合分降序）
3. S 级驱动强制保留（即使综合分低也排在前面）
4. max_per_sector=0 时不启用配额
5. config.yaml 读到的默认值 = 3
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from screen_agent import (
    classify_sector,
    enforce_sector_quota,
    _load_max_per_sector,
)

PASS = 0
FAIL = 0
TOTAL = 0


def check(name, condition, detail=""):
    global TOTAL, PASS, FAIL
    TOTAL += 1
    if condition:
        print(f"  ✅ {name}")
        PASS += 1
    else:
        print(f"  ❌ {name}  {detail}")
        FAIL += 1


def mk(name, code, score, driver="A"):
    return {
        "代码": code, "名称": name, "综合分": score,
        "驱动级别": driver,
    }


print("━" * 60)
print("【1】板块归类准确性")
print("━" * 60)
cases = [
    ("中际旭创", "光模块"),
    ("新易盛", "光模块"),
    ("光迅科技", "光模块"),
    ("天孚通信", "光模块"),
    ("保利发展", "地产"),
    ("招商蛇口", "地产"),
    ("滨江集团", "地产"),
    ("华发股份", "地产"),
    ("中芯国际", "半导体"),
    ("拓荆科技", "半导体"),
    ("海光信息", "半导体"),
    ("北方华创", "半导体"),
    ("宁德时代", "新能源"),
    ("比亚迪", "新能源"),
    ("紫金矿业", "黄金"),
    ("招商银行", "银行"),
    ("工商银行", "银行"),
    ("恒瑞医药", "医药"),
    ("浪潮信息", "AI"),
    ("科大讯飞", "其他"),   # 未列关键词
    ("海格通信", "其他"),
    ("", "其他"),
    (None, "其他"),
]
for name, expected in cases:
    got = classify_sector(name or "")
    check(f"{name!r:14} → {expected:6} (got {got})", got == expected,
          f"expected={expected} got={got}")

print()
print("━" * 60)
print("【2】地产 4 只 → 截到 3（综合分降序）")
print("━" * 60)
# 09-18 快筛候选池真实案例（4只地产 + 3只半导体 + 2只光模块 = 9只）
stocks = [
    mk("保利发展", "600048", 86, "A"),   # 综合分 86
    mk("招商蛇口", "001979", 86, "A"),   # 综合分 86
    mk("滨江集团", "002244", 76, "A"),   # 综合分 76
    mk("华发股份", "600325", 83, "A"),   # 综合分 83
    mk("中芯国际", "688981", 78, "A"),
    mk("拓荆科技", "688072", 71, "A"),
    mk("中际旭创", "300308", 80, "A"),
    mk("新易盛", "300502", 73, "A"),
    mk("光迅科技", "002281", 76, "A"),
]
kept = enforce_sector_quota(stocks, 3)

# 各板块数量校验
from collections import Counter
sector_count = Counter(s["板块"] for s in kept)
print(f"  保留 {len(kept)} 只；板块分布: {dict(sector_count)}")

check("地产板块 ≤ 3", sector_count.get("地产", 0) <= 3,
      f"got={sector_count.get('地产')}")
check("半导体板块 ≤ 3", sector_count.get("半导体", 0) <= 3)
check("光模块板块 ≤ 3", sector_count.get("光模块", 0) <= 3)

# 地产保留的是综合分 top-3：86(保利)、86(招商)、83(华发)；丢弃 76(滨江)
kept_real_estate = {s["名称"] for s in kept if s["板块"] == "地产"}
check("地产保留 top-3 综合分 (保利/招商/华发)",
      kept_real_estate == {"保利发展", "招商蛇口", "华发股份"},
      f"got={kept_real_estate}")
check("滨江集团 (地产综合分最低) 被丢弃", "滨江集团" not in kept_real_estate)

# 每只保留标的都有"板块"字段
for s in kept:
    check(f"  {s['名称']} 有板块字段", "板块" in s)

print()
print("━" * 60)
print("【3】S 级驱动强制保留（即使综合分低）")
print("━" * 60)
stocks = [
    mk("保利发展", "600048", 90, "S"),   # S 级但综合分低
    mk("招商蛇口", "001979", 70, "A"),
    mk("滨江集团", "002244", 88, "A"),
    mk("华发股份", "600325", 85, "A"),
    mk("金地集团", "600383", 82, "A"),   # 第 5 只，综合分 82 > 保利 90
]
kept = enforce_sector_quota(stocks, 3)
kept_names = {s["名称"] for s in kept}
print(f"  保留: {kept_names}")

check("S 级保利发展 (综合分最低) 强制保留",
      "保利发展" in kept_names)
# 剩余 2 席归综合分最高：88(滨江)、85(华发)；S级保利占据第1席
check("综合分 top-2 保留 (滨江/华发)",
      {"滨江集团", "华发股份"}.issubset(kept_names),
      f"got={kept_names}")
# 金地(82) 与 招商(70) 都低于 华发(85) 且非 S 级，被丢弃
check("金地集团 (A级 82) 被丢弃", "金地集团" not in kept_names)
check("招商蛇口 (A级 70) 被丢弃", "招商蛇口" not in kept_names)
check("共保留 3 只地产", len(kept) == 3, f"got={len(kept)}")

print()
print("━" * 60)
print("【4】边界条件")
print("━" * 60)
# 无标的
check("空列表返回空", enforce_sector_quota([], 3) == [])

# max_per_sector=0 表示不启用配额
stocks = [mk("保利发展", "600048", 86), mk("招商蛇口", "001979", 86)]
kept = enforce_sector_quota(stocks, 0)
check("max_per_sector=0 时不启用配额（保留全部）",
      len(kept) == 2, f"got={len(kept)}")

# 板块数量 <= max 时不触发淘汰
stocks = [mk("中际旭创", "300308", 80), mk("新易盛", "300502", 73)]
kept = enforce_sector_quota(stocks, 3)
check("板块数量未超阈值时全部保留",
      len(kept) == 2, f"got={len(kept)}")

# 板块字段自动补齐
s = {"代码": "600048", "名称": "保利发展", "综合分": 86, "驱动级别": "A"}
kept = enforce_sector_quota([s], 3)
check("缺失'板块'字段时自动补齐",
      kept[0].get("板块") == "地产",
      f"got={kept[0].get('板块')}")

print()
print("━" * 60)
print("【5】config.yaml 读取")
print("━" * 60)
cfg_val = _load_max_per_sector()
check(f"config.yaml 中 max_per_sector = {cfg_val}", cfg_val == 3,
      f"got={cfg_val}")

print()
print("━" * 60)
print(f"总计: {TOTAL} | ✅ 通过: {PASS} | ❌ 失败: {FAIL}")
print("━" * 60)
# 2026-09-30 T01/Q-H02: sys.exit 移入 __main__ 守卫，避免 pytest 收集阶段 SystemExit 导致全套 0 tests ran。
if __name__ == "__main__":
    sys.exit(0 if FAIL == 0 else 1)
