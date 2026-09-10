"""09-10 S池止血 ①②③ 验证脚本
隔离策略：拷贝「裁决+池文件」到临时 root，真实数据全程零接触。
"""
import sys, os, json, shutil, tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "agents"))

from agents.pool_updater import PoolUpdater
from agents.gate_controller import GateController

# ── 隔离环境：临时 root + 拷贝裁决与池文件 ──
TMP = Path(tempfile.mkdtemp(prefix="s_pool_test_"))
(TMP / "data/历史记录").mkdir(parents=True)
(TMP / "五池管理").mkdir(parents=True)
shutil.copy2(BASE / "data/历史记录/2026-09-10_质疑审查裁决.json", TMP / "data/历史记录/")
shutil.copy2(BASE / "五池管理/S级操作池.json", TMP / "五池管理/")

bc, gp = GateController.read_verdict(TMP / "data/历史记录/2026-09-10_质疑审查裁决.json")
print(f"[前置] 裁决读取: blocked={sorted(bc)}  gate_passed={gp}")
assert bc == {"600141", "002436"}, "裁决解析异常"

class DummyPM:
    def add_stock(self, *a, **k): pass

POOL = TMP / "五池管理" / "S级操作池.json"

def run_case(report_text):
    """在隔离临时池上跑一次，返回新增标的名单"""
    before = {s["代码"] for s in json.loads(POOL.read_text())["stocks"]}
    PoolUpdater(TMP, pool_manager=DummyPM()).update_s_pool(
        report_text, pool_manager=DummyPM(), scored_stocks=[])
    after = json.loads(POOL.read_text())["stocks"]
    return [s["名称"] for s in after if s["代码"] not in before]

results = []
def check(label, expect_empty, actual):
    """expect_empty=True 表示该场景不应新增标的；False 表示应新增"""
    ok = (len(actual) == 0) == expect_empty
    status = "✅" if ok else "❌"
    results.append(ok)
    print(f"  {status} 新增入池: {actual if actual else '无'}")
    return ok

print("\n=== 场景1: 真实9-10报告（Skeptic blocked 2只 + LLM声明0只主推）===")
real = (BASE / "data/历史记录/2026-09-10_决策报告.md").read_text()
check("real", True, run_case(real))

print("\n=== 场景2: 声明非0只但仅否决语境（②action_pats收窄 + 否决语境守卫）===")
r2 = real.replace("- **S级操作池**: 0只今日主推标的", "- **S级操作池**: 1只今日主推标的")
check("neg_only", True, run_case(r2))

print("\n=== 场景3: blocked标的被LLM误推为【主推】（①逐标的Gate）===")
r3 = real.replace("- **S级操作池**: 0只今日主推标的", "- **S级操作池**: 1只今日主推标的")
r3 = r3.replace("### 今日暂无通过审查的股票", "【主推】兴发集团（600141）")
check("blocked_promoted", True, run_case(r3))

print("\n=== 场景4: 合法主推+未blocked+空池（反向验证：不误杀）===")
empty = json.loads(POOL.read_text())
empty["stocks"] = []
POOL.write_text(json.dumps(empty, ensure_ascii=False, indent=2))
r4 = """## 今日操作建议
【主推】隆基绿能（601012）
- 核心驱动：光伏组件需求回升，Q3排产超预期
- 买入价：18.50元
- 止损：17.00元
- 仓位：10%
- 目标价：22.00元
- 综合评分：82分
- **S级操作池**: 1只今日主推标的
"""
added4 = run_case(r4)
check("valid_promotion", False, added4)

print("\n=== 场景5: 合法主推但池中已有今日3只满员（容量守卫，不应新增）===")
empty = json.loads(POOL.read_text())
empty["stocks"] = [
    {"代码": c, "名称": n, "综合评分": 0, "纳入日期": "2026-09-10",
     "驱动来源": "决策主推", "核心逻辑": "", "入场价": 10.0,
     "t1_验证": None, "t3_验证": None, "评价": None}
    for c, n in [("600141","兴发集团"),("002436","兴森科技"),("002312","川发龙蟒")]
]
POOL.write_text(json.dumps(empty, ensure_ascii=False, indent=2))
added5 = run_case(r4)
check("capacity_full", True, added5)

shutil.rmtree(TMP)
print(f"\n{'='*40}")
print(f"结果: {sum(results)}/{len(results)} 通过，隔离环境已清理，真实数据零接触")
sys.exit(0 if all(results) else 1)
