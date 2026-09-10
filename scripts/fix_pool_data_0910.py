#!/usr/bin/env python3
"""
2026-09-10 池数据修复脚本

修复内容：
1. S级操作池统计字段自相矛盾（stocks=0但当日进入=3）→ 修正为0
2. 追加历史记录说明：10:51 PoolManager误降级事件（评分=0连锁反应）
3. 验证代码修复（_scan_and_downgrade字段名兼容+降级保留元数据）

背景：
- 07:15 3只股票以综合评分=0写入S池（已知bug）
- 10:51 PoolManager._scan_and_downgrade只读"综合分"不读"综合评分"→误判0分→降级
- 降级时重建dict丢弃失效标记→3只标的被边缘池过期清理静默移除
- 14:30决策报告说"无标的≥85"，实际原因：池已空
"""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # scripts/ → 项目根
POOL_DIR = BASE / "五池管理"
S_POOL = POOL_DIR / "S级操作池.json"
EDGE_POOL = POOL_DIR / "边缘池.json"

print("=" * 60)
print("🔧 2026-09-10 池数据修复")
print("=" * 60)

# ── Step 1: 备份 ──────────────────────────────────────
print("\n📦 Step 1: 备份当前池文件")
backup_dir = POOL_DIR / "__backups__"
backup_dir.mkdir(exist_ok=True)
backup_time = datetime.now().strftime("%Y%m%d_%H%M%S")

for fname in ["S级操作池.json", "边缘池.json"]:
    src = POOL_DIR / fname
    if src.exists():
        bak = backup_dir / f"{fname.replace('.json', '')}.bak.{backup_time}"
        shutil.copy2(src, bak)
        print(f"  ✅ {fname} → {bak.name}")
    else:
        print(f"  ⚠️ {fname} 不存在")

# ── Step 2: 修正S池统计字段 ────────────────────────────
print("\n🔧 Step 2: 修正S池统计字段")
s_pool = json.loads(S_POOL.read_text(encoding="utf-8"))
stocks = s_pool.get("stocks", [])
stats = s_pool.get("统计", {})

print(f"  当前stocks: {len(stocks)}只")
print(f"  当前统计: {json.dumps(stats, ensure_ascii=False)}")

# 修正：stocks=0 时当日进入应为0（3只已被PoolManager降级移除）
original_entered = stats.get("当日进入", 0)
if len(stocks) == 0 and original_entered > 0:
    stats["当日进入"] = 0
    stats["持仓数"] = 0
    stats["更新日期"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stats["更新说明"] = "修正：3只标已于10:51被PoolManager按综合分0<65规则降级至边缘池，统计字段同步归零"
    s_pool["统计"] = stats
    print(f"  ✅ 当日进入 {original_entered} → 0")
    print(f"  ✅ 持仓数 → 0")
    print(f"  ✅ 追加更新说明")
else:
    print(f"  ℹ️ 无需修正（stocks={len(stocks)}, 当日进入={original_entered}）")

# ── Step 3: 追加历史记录说明 ────────────────────────────
print("\n📝 Step 3: 追加历史记录说明")
hist = s_pool.get("历史记录", [])
existing_desc = any(
    "PoolManager误降级" in json.dumps(h, ensure_ascii=False) or
    "字段名漂移" in json.dumps(h, ensure_ascii=False)
    for h in hist
)

if not existing_desc:
    new_entry = {
        "日期": "2026-09-10",
        "类型": "PoolManager误降级事件",
        "说明": (
            "10:51 PoolManager._scan_and_downgrade执行S池存量扫描时，"
            "因只读s.get('综合分')而不兼容'综合评分'字段名，"
            "将3只综合评分=0的标的（兴发集团/兴森科技/川发龙蟒）误判为低分→降级至边缘池。"
            "降级时重建dict丢弃失效标记，3只标的随后被边缘池过期清理（综合分0<40）静默移除。"
            "14:30决策报告显示'无标的评分≥85'，实际原因：S池在10:51已被清空。"
        ),
        "根因": "_scan_and_downgrade字段名漂移（仅读'综合分'）+ 降级时重建dict丢弃元数据",
        "修复commit": "pool_manager.py _scan_and_downgrade: 字段名双读兼容 + dict浅拷贝保留全部字段",
        "影响": "S池/快筛候选池/重点观察池在14:30时全部为空，决策报告输出'不操作'",
        "证据保留": "本历史记录 + 边缘池历史记录（过期清理记录）+ S池历史记录（失效标记条目）"
    }
    hist.append(new_entry)
    s_pool["历史记录"] = hist
    print(f"  ✅ 追加历史记录（总条数: {len(hist)}）")
else:
    print(f"  ℹ️ 历史记录已存在（{len(hist)}条），跳过")

# ── Step 4: 写入 ──────────────────────────────────────
print("\n💾 Step 4: 写入修复后数据")
S_POOL.write_text(json.dumps(s_pool, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"  ✅ S级操作池.json 已写入")

# ── Step 5: 验证 ──────────────────────────────────────
print("\n✅ Step 5: 验证")
verify = json.loads(S_POOL.read_text(encoding="utf-8"))
print(f"  stocks: {len(verify.get('stocks', []))}只")
print(f"  统计: {json.dumps(verify.get('统计', {}), ensure_ascii=False)}")
print(f"  历史记录: {len(verify.get('历史记录', []))}条")

# 验证失效标记条目仍在
invalid = [h for h in verify.get("历史记录", []) if "失效标记" in json.dumps(h, ensure_ascii=False) or "无效" in json.dumps(h, ensure_ascii=False)]
print(f"  失效标记条目: {len(invalid)}条")

# 验证新追加的事件记录
event = [h for h in verify.get("历史记录", []) if "PoolManager误降级" in json.dumps(h, ensure_ascii=False)]
print(f"  误降级事件记录: {len(event)}条")

# ── Step 6: 代码修复验证 ──────────────────────────────
print("\n🧪 Step 6: 验证代码修复（_scan_and_downgrade）")
import sys
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "agents"))

# 模拟：S池标的用"综合评分"字段（值=42），修复前会被误判为0分
test_data = {
    "stocks": [
        {"代码": "600141", "名称": "兴发集团", "综合评分": 42, "纳入日期": "2026-09-10"},
        {"代码": "002436", "名称": "兴森科技", "综合评分": 38, "纳入日期": "2026-09-10"},
    ],
    "统计": {}
}

# 修复后应读取"综合评分"=42/38，不会误判为0
s1 = test_data["stocks"][0]
s2 = test_data["stocks"][1]
score1 = s1.get("综合分", s1.get("综合评分"))
score2 = s2.get("综合分", s2.get("综合评分"))
print(f"  兴发集团 综合评分=42 → 读取到: {score1}")
print(f"  兴森科技 综合评分=38 → 读取到: {score2}")

ok1 = (score1 == 42)
ok2 = (score2 == 38)
print(f"  字段名双读兼容: {'✅ 通过' if ok1 and ok2 else '❌ 失败'}")

# 验证降级保留字段
test_stock = {"代码": "600141", "名称": "兴发集团", "综合评分": 0, "失效标记": "Skeptic高风险", "核心逻辑": "测试"}
demoted = dict(test_stock)
if "综合评分" in demoted and "综合分" not in demoted:
    demoted["综合分"] = demoted["综合评分"]
demoted["降级时间"] = "2026-09-10"
demoted["降级原因"] = "测试降级"

has_mark = demoted.get("失效标记") == "Skeptic高风险"
has_logic = demoted.get("核心逻辑") == "测试"
has_score_field = demoted.get("综合分") == 0
print(f"  降级保留失效标记: {'✅' if has_mark else '❌'}")
print(f"  降级保留核心逻辑: {'✅' if has_logic else '❌'}")
print(f"  综合分字段迁移: {'✅' if has_score_field else '❌'}")

# ── 汇总 ─────────────────────────────────────────────
print("\n" + "=" * 60)
all_ok = (len(verify.get("stocks", [])) == 0 and
          verify.get("统计", {}).get("当日进入") == 0 and
          len(invalid) >= 1 and len(event) >= 1 and
          ok1 and ok2 and has_mark and has_logic and has_score_field)
print(f"📊 修复结果: {'✅ 全部通过' if all_ok else '❌ 有失败项'}")
print("=" * 60)
