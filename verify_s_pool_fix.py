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

# ── 隔离环境：临时 root + 自包含裁决与池文件 ──
TMP = Path(tempfile.mkdtemp(prefix="s_pool_test_"))
(TMP / "data/历史记录").mkdir(parents=True)
(TMP / "五池管理").mkdir(parents=True)

# 自包含裁决文件：不依赖真实数据（14:30运行会覆盖真实裁决，导致测试失效）
# read_verdict 用 s.get("code") 读 blocked，必须是 dict 列表不是字符串列表
verdict = {
    "generated_at": "2026-09-10 07:15:00",
    "mode": "weak_market_simplified",
    "gate_status": "blocked",
    "blocked": [{"code": "600141"}, {"code": "002436"}],
    "passed_codes": [],
    "has_high_risk": True
}
shutil.copy2(BASE / "五池管理/S级操作池.json", TMP / "五池管理/")
with open(TMP / "data/历史记录/2026-09-10_质疑审查裁决.json", "w", encoding="utf-8") as f:
    json.dump(verdict, f, ensure_ascii=False, indent=2)

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

# ══════════ ⑥ 准入契约四象限（C1/C3/C4/C5）══════════
VALID_BODY = """## 今日操作建议
【主推】{name}（{code}）
- 核心驱动：{logic}
- 买入价：18.50元
- 止损：17.00元
- 仓位：10%
- 目标价：22.00元
- 综合评分：{score}
- **S级操作池**: 1只今日主推标的
"""

def make_pool_empty():
    d = json.loads(POOL.read_text()); d["stocks"] = []
    POOL.write_text(json.dumps(d, ensure_ascii=False, indent=2))

def write_verdict(blocked):
    """覆写隔离环境的裁决文件"""
    vp = TMP / "data/历史记录" / "2026-09-10_质疑审查裁决.json"
    vp.write_text(json.dumps({
        "date": "2026-09-10", "total": len(blocked), "total_veto": 0,
        "total_high": len(blocked),
        "passed": [], "blocked": [
            {"code": c, "name": n, "verdict": "challenge_required",
             "has_high_risk": True, "veto_count": 0, "high_count": 1,
             "weighted_count": 1.0, "block_reason": ""} for c, n in blocked],
        "gate_status": "blocked" if blocked else "passed",
    }, ensure_ascii=False))

print("\n═══ ⑥ 准入契约四象限 ═══")

# Q1: Skeptic 通过（C1 满足）→ 应入池
make_pool_empty(); write_verdict([])
q1 = run_case(VALID_BODY.format(name="隆基绿能", code="601012",
                                logic="光伏需求回升", score="82分"))
print(f"  {'✅' if q1 else '❌'} C1通过+Skeptic裁决passed → 入池 {q1}")
results.append(bool(q1))

# Q2: Skeptic 标记 high（C1 违反）→ 应拒绝
make_pool_empty(); write_verdict([("601012", "隆基绿能")])
q2 = run_case(VALID_BODY.format(name="隆基绿能", code="601012",
                                logic="光伏需求回升", score="82分"))
print(f"  {'✅' if not q2 else '❌'} C1违反(裁决blocked) → 拒绝 {q2 if q2 else '✓'}")
results.append(not q2)

# Q3: 未审查标的（C3 违反）→ 应拒绝
make_pool_empty(); write_verdict([])
q3_body = """## 重点观察池标的状态
| 代码 | 名称 | 现价 | Skeptic判定 | 状态 |
|------|------|------|-------------|------|
| 601012 | 隆基绿能 | 18.50 | 未审查 | 观望 |

- **S级操作池**: 1只今日主推标的
"""
q3 = run_case(q3_body)
print(f"  {'✅' if not q3 else '❌'} C3违反(未审查/观望) → 拒绝 {q3 if q3 else '✓'}")
results.append(not q3)

# Q4: LLM 自洽矛盾（C4 违反：声明0只但正文有主推）→ 应跳过
make_pool_empty(); write_verdict([])
q4 = run_case(VALID_BODY.format(name="隆基绿能", code="601012",
                                logic="光伏需求回升", score="82分")
              .replace("**S级操作池**: 1只", "**S级操作池**: 0只"))
print(f"  {'✅' if not q4 else '❌'} C4违反(声明0只) → 跳过 {q4 if q4 else '✓'}")
results.append(not q4)

# ══════════ ④ 评分/逻辑提取回归 ══════════
print("\n═══ ④ 评分/逻辑提取回归 ═══")
pu = PoolUpdater(TMP, pool_manager=DummyPM())

cases = [
    ("**综合评分**仅42分（虚词+加粗）", "- **综合评分**仅42分", 42),
    ("综合评分：82（冒号）", "- 综合评分：82", 82),
    ("综合评分 82（空格）", "- 综合评分 82", 82),
    ("综合评分约78分（虚词）", "- 综合评分约78分", 78),
    ("综合评分为76分（虚词）", "- 综合评分为76分", 76),
    ("综合评分82分（无分隔）", "- 综合评分82分", 82),
]
for label, frag, want in cases:
    txt = f"### 隆基绿能（601012）\n- 核心驱动：测试\n{frag}\n"
    got = pu._extract_score("隆基绿能", "601012", txt)
    print(f"  {'✅' if got == want else '❌'} {label} → {got}")
    results.append(got == want)

# 无任何评分 → None（非0）
none_txt = "### 隆基绿能（601012）\n- 核心驱动：测试驱动\n- 暂无评分依据\n"
got_n = pu._extract_score("隆基绿能", "601012", none_txt)
print(f"  {'✅' if got_n is None else '❌'} 无评分 → {got_n}（None语义，非0）")
results.append(got_n is None)

# ── 跨标的污染防护（09-10事故复现）──
# 9-10真实报告结构：审查汇总表(中国石化42) + 重点观察池状态表(兴发31.50/兴森39.45/川发9.55)
# + "### 兴发集团（600141）"标题段。旧全局正则会匹配到表格列名"综合评分"后抓到
# 相邻行的39.45元(川发龙蟒的现价) —— 兴发集团根本没有综合评分。
pollution_txt = """| 代码 | 名称 | 综合评分 | 流转方向 |
|------|------|---------|---------|
| 600028 | 中国石化 | 42 | 淘汰 |

| 代码 | 名称 | 现价 | 今日涨跌 | Skeptic判定 | 状态 |
|------|------|------|---------|------------|------|
| 600141 | 兴发集团 | 31.50 | -1.32% | 🔴 高风险 | 不操作 |
| 002436 | 兴森科技 | 39.45 | -1.67% | 🔴 高风险 | 不操作 |
| 002312 | 川发龙蟒 | 9.55 | +2.25% | 未审查 | 观望 |

### 兴发集团（600141）
- 参考报告评分仅42分，缺乏驱动逻辑与量能支撑

### 兴森科技（002436）
- 缺乏审查评分支撑，不满足执行条件
"""
got_p1 = pu._extract_score("兴发集团", "600141", pollution_txt)
print(f"  {'✅' if got_p1 == 42 else '❌'} 兴发集团 → {got_p1}（命中自身段内'评分仅42分'）")
results.append(got_p1 == 42)

got_p2 = pu._extract_score("兴森科技", "002436", pollution_txt)
print(f"  {'✅' if got_p2 is None else '❌'} 兴森科技 → {got_p2}（无评分，未污染取39.45元价格）")
results.append(got_p2 is None)

got_p3 = pu._extract_score("川发龙蟒", "002312", pollution_txt)
print(f"  {'✅' if got_p3 is None else '❌'} 川发龙蟒 → {got_p3}（无标题段落 → None）")
results.append(got_p3 is None)

# 核心逻辑：无【主推】前缀的标题段落（07-21格式漂移修复）
logic_txt = "### 隆基绿能（601012）\n- 核心驱动：光伏组件需求回升，Q3排产超预期\n- 风险：低\n"
logic = pu._extract_logic_snippet("隆基绿能", logic_txt)
print(f"  {'✅' if '光伏' in logic else '❌'} 无【主推】前缀核心逻辑 → {logic!r}")
results.append("光伏" in logic)

# 核心逻辑：有【主推】前缀（向后兼容）
logic_txt2 = "### 【主推】隆基绿能（601012）\n- 逻辑支撑：政策利好+产能释放\n"
logic2 = pu._extract_logic_snippet("隆基绿能", logic_txt2)
print(f"  {'✅' if logic2 else '❌'} 【主推】前缀核心逻辑(向后兼容) → {logic2!r}")
results.append(bool(logic2))

# ══════════ GateController.score_of 的 None 安全性 ══════════
print("\n═══ GateController.score_of None安全性 ═══")
for label, stock, want in [
    ("综合评分=None", {"综合评分": None}, 0),
    ("综合评分=82", {"综合评分": 82}, 82),
    ("综合分=60", {"综合分": 60}, 60),
    ("字段缺失", {"名称": "x"}, 0),
    ("字符串'85'", {"综合评分": "85"}, 85),
]:
    got_s = GateController.score_of(stock)
    print(f"  {'✅' if got_s == want else '❌'} {label} → {got_s}")
    results.append(got_s == want)

# enforce_writing_rules 不因 None 崩溃
try:
    rule = GateController.enforce_writing_rules(
        {"代码": "601012", "名称": "隆基绿能", "综合评分": None}, "S级操作池")
    crashed = False
except Exception as e:
    crashed = True
    print(f"  ❌ enforce_writing_rules 崩溃: {e}")
print(f"  {'✅' if not crashed else '❌'} 综合评分=None 写入S池规则不崩溃")
results.append(not crashed)

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
