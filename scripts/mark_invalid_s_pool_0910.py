#!/usr/bin/env python3
"""⑤ S池失效数据修正 + T+1回收验证

- 给 2026-09-10 三只无效标的打失效标记（证据化，不删除原始数据）
- 隔离环境验证 clean_expired_s_pool 次日自动回收
"""
import sys, json, shutil, tempfile
from pathlib import Path

# 本脚本位于 scripts/ 下，仓库根为上一级
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "agents"))

from pool_manager import PoolManager

POOL = BASE / "五池管理" / "S级操作池.json"
MARK = "09-10止血: Skeptic裁决阻塞(has_high_risk)，经宽松兜底误晋级S池；未实际执行，无效建议"

# ── ⑤ 失效标记（幂等）──
data = json.loads(POOL.read_text())
marked = 0
for s in data.get("stocks", []):
    if s.get("纳入日期") == "2026-09-10" and "失效标记" not in s:
        s["失效标记"] = MARK
        s["评分来源"] = "无有效评分（报告未给该标的评分）"
        marked += 1
# 历史记录追加失效声明（供复盘引用）
hist = data.get("历史记录", [])
if not any(h.get("类型") == "无效标的失效标记" for h in hist):
    hist.append({
        "日期": "2026-09-10",
        "类型": "无效标的失效标记",
        "失效标的": [{"代码": s["代码"], "名称": s["名称"]}
                     for s in data.get("stocks", []) if s.get("纳入日期") == "2026-09-10"],
        "说明": "09-10止血批次确认：兴发集团/兴森科技被Skeptic标记high风险，"
                "川发龙蟒未经审查。三者经PoolUpdater宽松兜底误晋级S池，综合评分0，"
                "无实际执行。原始记录保留作为流程脱节复盘证据。",
        "修复commit": "S池止血①②③ + ④⑤⑥收尾批次",
    })
    added_hist = True
else:
    added_hist = False
POOL.write_text(json.dumps(data, ensure_ascii=False, indent=2))
print(f"⑤ 失效标记: 标记{marked}只标的 + 历史记录{'追加1条' if added_hist else '已存在，跳过(幂等)'}")

# ── T+1回收验证（隔离临时目录，不碰真实池）──
# 纳入日期是 09-10，今天也是 09-10 → age_days=0 未过期，属预期。
# 这里把纳入日期回拨一天模拟「次日」，验证 T+1 自动回收路径。
TMP = Path(tempfile.mkdtemp(prefix="t1_verify_"))
_sim = json.loads(POOL.read_text())
for s in _sim.get("stocks", []):
    if s.get("纳入日期") == "2026-09-10":
        s["纳入日期"] = "2026-09-09"
(TMP / "S级操作池.json").write_text(json.dumps(_sim, ensure_ascii=False, indent=2))
pm = PoolManager(pool_dir=TMP)
res = pm.clean_expired_s_pool(max_age_days=1)
print(f"\n⑤ T+1回收验证(模拟次日): removed={len(res['removed'])} remaining={len(res['remaining'])}")
for r in res["removed"]:
    print(f"    → {r['名称']}({r['代码']}) 综合分={r.get('综合分')}")
    assert r.get("综合分") in (0, None), f"❌ 失效标的评分未带出: {r.get('综合分')}"
assert len(res["removed"]) == 3, "❌ 未回收全部3只"
print("  ✅ 3只失效标的次日自动回收，综合分0（非幻影80分）原样带出")
shutil.rmtree(TMP)
print("\n完成：真实池已加失效标记，T+1自动回收路径验证通过")
