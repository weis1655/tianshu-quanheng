#!/usr/bin/env python3
"""
Screen Agent - 快筛 Agent（重构版）
基于新闻驱动 + 规则引擎筛选候选股票
1次LLM调用

设计原则：双盲机制
- 此Agent只看新闻驱动，不看五池现有持仓
- 避免"手里有票就找理由推荐"的前后一致偏见

继承BaseAgent获得：
- 统一的LLM调用（指数退避重试）
- 安全文件读写
- 统计跟踪
"""

import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from logger import plog

# P1: 实时行情数据导入（使涨幅数据可提取）
try:
    from market_agent import fetch_quotes, to_api, validate_stock_codes
except ImportError:
    fetch_quotes = to_api = validate_stock_codes = None
from pathlib import Path
from typing import Optional, List

from base_agent import BaseAgent, build_agent_system_prompt
from logger import StructuredLogger
from schemas import ScreenOutput, ScreenResult, StockCandidate, SCREEN_SCHEMA

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))
from path_config import ensure_agent_paths; ensure_agent_paths()

from market_agent import fetch_quotes, calculate_technical_score, to_api

# ── 板块归类规则 ─────────────────────────────────────────────
# 顺序即优先级：先匹配先返回。地产规则放最前，避免"招商蛇口"被"招商"误归为银行。
# 覆盖 trigger.py 与 sector_rotation 已识别的核心板块 + 09-18 快筛候选池真实股票。
SECTOR_RULES = [
    ("地产", (
        "保利", "招商蛇口", "招商地产", "滨江集团", "华发", "万科", "金地",
        "华润置地", "中海", "绿城", "新城控股", "雅居乐", "碧桂园",
    )),
    ("光模块", (
        "光模块", "光通信", "光器件", "中际旭创", "天孚", "新易盛", "光迅", "长芯",
    )),
    ("半导体", (
        "半导体", "芯片", "晶圆", "中芯", "联电", "台积电", "封测", "DRAM",
        "NAND", "中微", "沪电", "北方华创", "拓荆", "芯原", "芯海", "芯源",
        "兆易创新", "韦尔", "卓胜", "澜起", "海光", "紫光", "晶盛", "沪硅",
        "士兰微", "时代电气", "长鑫", "华虹", "立昂微", "三环", "中环",
    )),
    ("黄金", (
        "黄金", "金矿", "紫金", "中金黄金", "山东黄金",
    )),
    ("新能源", (
        "新能源", "动力电池", "锂", "电池", "宁德", "国轩", "比亚迪", "特斯拉",
        "新能", "亿纬", "天赐", "天齐", "赣锋", "璞泰来", "恩捷",
    )),
    ("AI", (
        "AI", "人工智能", "算力", "服务器", "数据中心", "浪潮", "润泽", "曙光", "中科",
    )),
    ("银行", (
        "银行", "交行", "建行", "招行", "工商", "农业", "中行", "民生", "兴业", "浦发",
    )),
    ("医药", (
        "医药", "生物", "药", "制药", "创新药", "恒瑞", "药明", "迈瑞", "复宏",
    )),
]


def classify_sector(name: str) -> str:
    """按名称归类板块（顺序即优先级；未匹配返回 '其他'）。"""
    if not name:
        return "其他"
    for sector, keywords in SECTOR_RULES:
        for kw in keywords:
            if kw in name:
                return sector
    return "其他"


def _load_max_per_sector() -> int:
    """从 config.yaml 读取 quick.max_per_sector（默认 3）。"""
    try:
        import yaml
        cfg = yaml.safe_load(open(PROJECT_ROOT / "config.yaml", encoding="utf-8"))
        val = (cfg or {}).get("screening", {}).get("quick", {}).get("max_per_sector", 3)
        return int(val)
    except Exception:
        return 3


def enforce_sector_quota(stocks: list, max_per_sector: int) -> list:
    """板块配额硬校验：超过 max_per_sector 的板块按 (S级优先, 综合分降序) 保留 top-k。

    - 补齐 "板块" 字段（若缺失）
    - S 级驱动排在同板块综合分之上，确保强制保留
    - 被丢弃的标的用 plog 记录
    """
    if max_per_sector <= 0 or not stocks:
        return stocks

    # 补齐板块字段（保持向后兼容：新字典直接带，旧字典走 classify）
    for s in stocks:
        s.setdefault("板块", classify_sector(s.get("名称", "")))

    by_sector: dict = {}
    for s in stocks:
        by_sector.setdefault(s["板块"], []).append(s)

    kept = []
    dropped = []
    for sec, items in by_sector.items():
        if len(items) <= max_per_sector:
            kept.extend(items)
            continue

        def _key(s):
            is_s = 1 if (s.get("驱动级别") or "").upper() == "S" else 0
            score = s.get("综合分") or 0
            return (is_s, score)

        items_sorted = sorted(items, key=_key, reverse=True)
        kept.extend(items_sorted[:max_per_sector])
        dropped.extend(items_sorted[max_per_sector:])

    if dropped:
        dropped_desc = [
            f"{s.get('名称','?')}({s.get('板块','?')},综合分={s.get('综合分')})"
            for s in dropped
        ]
        plog("INFO",
             f"[ScreenAgent] 🚫 板块配额硬校验: 每板块≤{max_per_sector}，"
             f"丢弃 {len(dropped)} 只 - {dropped_desc}")
    return kept

ROLE_PROMPT = """你是一个短线选股专家，根据新闻驱动筛选股票。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚫【最高优先级 · 反思维链硬约束】🚫
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
你**必须直接从第一行输出结构化结论**，中间不允许任何思考铺垫。

绝对禁止输出以下任何内容（出现即视为格式违规）：
- 英文元思考句式："Let me..."、"I will..."、"I should..."、"I need to..."、"I am..."、"Now let me..."、"Wait, I..."、"OK..."、"Actually..."、"Let me finalize..."
- 任何铺垫："让我分析"、"我先梳理"、"首先我需要理解"、"接下来我会"、"让我确认"
- 任何反复纠结："重新审视"、"再想想"、"我需要再检查"、"考虑到这一点我应该"
- 任何自问自答、犹豫、修正痕迹

正确做法：把分析全部放在大脑里，输出时**只写结论**。第一行就是 `## 🔥 强势对象`。
你的整个输出预算有限，浪费在思考铺垫上会导致后续股票被截断、评分丢失。直接输出 = 全部股票都能被评分。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ **输出格式严格约束（违反即淘汰）：**
- 只输出筛选结果，每行一条 `- 名称（代码）- 理由`
- 不要输出你的思考过程、不要评论数据是否完整、不要解释为什么选或不选
- 不要写"我没有实时数据""我根据常识""我需要筛选"等元评论
- 如果你不知道某个数据，假设它符合条件后基于逻辑推荐

**绝对禁止：**
- 不要写"我注意到""我认为""我需要""从新闻来看"等第一人称表述
- 不要写学习过程或推理步骤
- 直接给出候选股票列表

1. PE_TTM 必须 >0 且 <50（亏损股和高估值泡沫股直接排除）
2. 换手率必须 >1%（低流动性标的排除）
3. 流通市值必须 >5亿
4. 每类板块最多推荐 2 只（不是 3 只，减少噪音）
5. 优先推荐驱动级别为 S 级或 A 级的标的

必须输出具体的股票代码和名称，不能回答"无法推荐"。
如果没有具体新闻，就基于以下通用逻辑：
- 关注AI算力、光模块业绩确定性
- 关注资源品涨价、石油煤炭
- 关注硬科技国产替代
- **⚠️ 当日新闻中出现S级催化剂（如OpenAI/英伟达/美光等巨头重大发布、国家级产业政策），必须优先匹配受益标的，不能遗漏。S级催化剂对应的股票即使PE略高（≤60）也可考虑**

输出格式：
## 🔥 强势对象
- 板块名称
  - 股票名称（代码）- 入选理由 [驱动级别:S/A/B]

只输出上面格式，不要解释。"""


USER_PROMPT_TEMPLATE = """根据以下新闻驱动分析结果，请筛选候选股票：

{news_report}

{context}

要求：
1. 每类最多推荐 2 只（减少噪音）
2. 只推荐有真实A股代码的股票
3. 强势对象优先考虑行业龙头
4. 低位转强优先考虑底部刚放量突破的
5. **参考上方实时行情和五池现状，优先推尚未在持仓/重点观察池中的标的**
6. **每只股票必须标注驱动级别 [S/A/B]，S级=政策级/业绩级核心驱动，A级=行业景气驱动，B级=轮动/补涨驱动**
7. **PE>50 或换手<1% 的股票直接排除，不要推荐**"""


class ScreenAgent(BaseAgent):
    """快筛 Agent（继承BaseAgent）"""

    def __init__(self, agent_name: str = "ScreenAgent"):
        super().__init__(agent_name)
        self.history_dir = self.root / "data" / "历史记录"
        self.logger = StructuredLogger("ScreenAgent")

    def run(self, news_report: Optional[str] = None, wake_ctx: str = "") -> dict:
        """执行快筛"""
        with self.logger.agent_action("run"):
            return self._run_impl(news_report, wake_ctx)

    def _run_impl(self, news_report: Optional[str], wake_ctx: str = "") -> dict:
        today = datetime.now().strftime("%Y-%m-%d")

        # 读取新闻分析报告（如果没有传入）
        if news_report is None:
            news_file = self.history_dir / f"{today}_宏观前置分析.md"
            if news_file.exists():
                news_report = self.safe_read_text(news_file)
            else:
                return {"success": False, "error": "没有找到今日宏观分析报告，请先执行 News Agent"}

        if len(news_report) < 50:
            return {"success": False, "error": "新闻报告内容不足"}

        # ── 注入实时行情 + 五池现状 + 大盘环境 ───────────────────
        context_section = self._build_context_section()
        # ──────────────────────────────────────────────────────────

        # LLM 快筛
        self.logger.llm_call("screen_stocks", tokens=len(news_report))
        # P0-3: 智能截断，保护传导链分析不被切断，传导链是快筛的核心依据
        truncated = self._smart_truncate(news_report)
        user_prompt = USER_PROMPT_TEMPLATE.format(
            news_report=truncated,
            context=context_section,
        )
        result = self.call_llm(
            user_prompt,
            system=build_agent_system_prompt(ROLE_PROMPT, "ScreenAgent", extra_context=wake_ctx),
            max_tokens=5000,
        )
        # P2-思维链外露: 剥离 LLM 输出中的思维链/元思考残留，与其他 Agent 对齐
        result = self.strip_chain_of_thought(result)

        # ── 技术面补位扫描：捕获新闻未覆盖但有量价异动的标的 ──
        tech_candidates = self._scan_technical_signals()
        if tech_candidates:
            tech_section = "\n\n".join([
                "## 📊 量价异动补位（非新闻驱动，基于实时行情）",
                "| 代码 | 名称 | 涨幅 | 换手率 | 量比 | 振幅 | 说明 |",
                "|------|------|:----:|:-----:|:----:|:----:|------|",
            ] + [
                f"| {s['code']} | {s['name']} | +{s['chg_pct']:.1f}% | {s['turnover']:.1f}% | {s['vol_ratio']:.1f} | {s['amplitude']:.1f}% | {s['reason']} |"
                for s in tech_candidates
            ] + [
                "",
                "⚠️ 量价异动标的未经过新闻驱动审查，仅供技术面参考，需经审查评估后方可执行。",
            ])
            result = result + "\n\n" + tech_section if result else tech_section
            # 技术面标的也加入候选池更新
            for s in tech_candidates:
                result += f"\n- {s['name']}（{s['code']}）- {s['reason']} [驱动级别:B]"
            plog("INFO", f"[技术面补位] 📊 发现 {len(tech_candidates)} 只量价异动标的: {[s['name'] for s in tech_candidates]}")

        # 格式化报告
        report = f"""# 【快筛报告】{today}

━━━━━━━━━━━━━━━━

## 宏观前置摘要

{self._extract_summary(news_report)}

## 快筛分层结果

{result}

## 候选池更新

{self._extract_new_candidates(result)}

---
快筛执行时间：{datetime.now().strftime('%H:%M')}
"""

        # 保存
        out_file = self.history_dir / f"{today}_快筛报告.md"
        self.safe_write_text(out_file, report)

        # ── P2-3：闭环追踪记录 ──────────────────────────────
        from closed_loop_tracker import ClosedLoopTracker
        tracker = ClosedLoopTracker()
        try:
            parsed = self._parse_screen_result(result)
            for stock in parsed.get("stocks", []):
                tracker.record_screen(
                    code=stock.get("code", ""),
                    name=stock.get("name", ""),
                    reason=stock.get("reason", ""),
                    driver_level=stock.get("driver_level", ""),
                )
        except Exception as e:
            self.logger.warning("closed_loop_screen_fail", error=str(e))

        # 更新快筛候选池
        self._update_candidate_pool(result)

        self.logger.info("screen_complete",
                         saved_to=str(out_file),
                         stats=self.get_stats())

        # ── 构建 ScreenResult（新增 schema 结构化输出）─────────────
        candidates = self._parse_screen_result(result)
        screen_output = ScreenOutput(
            raw_text=result,
            timestamp=datetime.now().isoformat(),
        )
        screen_result = ScreenResult(
            success=True,
            output=screen_output,
            candidates=candidates,
            report_file=str(out_file),
        )
        # 保留旧 dict 返回格式供主流程兼容（后续 agents 逐步迁移到 ScreenResult）
        return {
            "success": True,
            "report": report,
            "raw_result": result,
            "saved_to": str(out_file),
            "screen_result": screen_result,  # 新增：结构化结果
            "candidates": candidates,          # 新增：候选股列表
        }

    def _scan_technical_signals(self) -> list:
        """技术面补位扫描：从东方财富获取涨幅榜，筛选量价异动但未在池中的标的"""
        pooled_codes = set()
        try:
            import json, urllib.request
            today = datetime.now().strftime("%Y-%m-%d")

            # 获取涨幅前50（f38=换手率%、f6=成交额、f8=量比、f15=振幅、f20=市值）
            url = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=50&po=1&np=1&fields=f2,f3,f5,f6,f8,f12,f14,f15,f20,f38&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&ut=bd1d9ddb04089700cf9c27f6f7426281"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=8)
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("data", {}).get("diff", [])

            if not items:
                return []

            # 加载现有池中标的（去重）
            pooled_codes = set()
            for pname in ["快筛候选池", "重点观察池", "S级操作池", "持仓池", "边缘池"]:
                pf = self.root / "五池管理" / f"{pname}.json"
                if pf.exists():
                    try:
                        pd = json.loads(pf.read_text(encoding="utf-8"))
                        for s in pd.get("stocks", []):
                            pooled_codes.add(s.get("代码", ""))
                    except Exception:  # 安全降级: 存量池代码去重失败→跳过，不影响本轮快筛
                        pass

            # 筛选：涨幅>5% + 换手>8%(涨停股放宽到>3%) + 量比>1.5 + 振幅>5% + 市值>50亿 + 不在现有池中
            candidates = []
            for s in items:
                code = str(s.get("f12", ""))
                f3 = s.get("f3", 0)
                chg_pct = f3 / 100.0 if abs(f3) > 100 else f3
                if chg_pct < 5.0:
                    continue
                name = s.get("f14", "")
                # 任务③修复：换手率取 f38（东财 f38 本身即百分比口径），
                # 原代码误用 f6（成交额/元）当换手率，导致"换手>8%"筛选形同虚设。
                turnover = s.get("f38", 0)
                # 涨停/近涨停票换手可能很低但价值高，放宽到>3%
                turn_threshold = 3.0 if chg_pct >= 9.5 else 8.0
                if turnover < turn_threshold:
                    continue
                vol_ratio = s.get("f8", 0) / 100.0 if abs(s.get("f8", 0)) > 100 else s.get("f8", 0)
                if vol_ratio < 1.5:
                    continue
                amplitude = s.get("f15", 0) / 100.0 if abs(s.get("f15", 0)) > 100 else s.get("f15", 0)
                if amplitude < 5.0:
                    continue
                mcap = s.get("f20", 0) / 10000.0 if abs(s.get("f20", 0) or 0) > 1e6 else (s.get("f20", 0) or 0)
                if mcap < 50:
                    continue
                if code in pooled_codes:
                    continue
                # 构建说明
                reason = f"量价异动: 涨{chg_pct:.1f}%+换手{turnover:.1f}%+量比{vol_ratio:.1f}"
                candidates.append({
                    "code": code, "name": name or "?",
                    "chg_pct": chg_pct, "turnover": turnover,
                    "vol_ratio": vol_ratio, "amplitude": amplitude,
                    "reason": reason,
                })

            return candidates[:5]  # 最多5只，减少干扰

        except Exception as e:
            plog("INFO", f"[技术面补位] ⚠️ 扫描失败: {e}")
            # fallback：尝试新浪财经涨幅榜
            try:
                plog("INFO", f"[技术面补位] 🔄 降级到新浪接口...")
                import json, urllib.request
                url = f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=1&num=30&sort=changepercent&asc=0&node=hs_a&symbol=&_=1"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw = resp.read().decode("gbk", errors="replace")
                fallback_data = json.loads(raw) if raw.startswith("[") else []
                cand = []
                # 2026-09-29 修复：该接口不返回 turnover/volume_ratio/amplitude（实测全为 None），
                # 原逻辑 float(None)=0.0 后四重阈值(换手3%+量比1.5+振幅5%)恒不满足，
                # 这个 fallback 一次都没成功过，只默默打印"无结果"。
                # 实测 30 条涨幅榜里迈信林/迅捷兴(20cm涨停)本可入选，被字段缺失判死。
                # 改为硬门槛只用涨幅（该接口稳定提供），换手/量比取到就作为附加条件。
                for s in fallback_data:
                    code = s.get("code", "")
                    if code in pooled_codes:
                        continue
                    try:
                        chg = float(s.get("changepercent", 0))
                    except (TypeError, ValueError):
                        continue
                    # 换手率/量比/振幅：字段缺失时按"未知"处理，不阻塞入选
                    def _num(v):
                        try:
                            return float(v) if v not in (None, "", "None") else None
                        except (TypeError, ValueError):
                            return None
                    turnover = _num(s.get("turnover"))
                    vol_ratio = _num(s.get("volume_ratio"))
                    amplitude = _num(s.get("amplitude"))
                    # 硬门槛：涨幅≥5%；软门槛：已知的换手/量比不达标则跳过
                    if chg < 5:
                        continue
                    if turnover is not None and turnover < 3:
                        continue
                    if vol_ratio is not None and vol_ratio < 1.5:
                        continue
                    name = s.get("name", "?")
                    # 2026-09-29: 剔除新股/次新股首日（名称带 N/C 前缀，如 N鸿富诚 654%）。
                    # 上市首日不设涨跌幅限制，量能数据无意义，且封板无法买入。
                    if name.startswith(("N", "C")):
                        continue
                    # 剔除 *ST/ST（涨跌幅5%，且退市风险高，快筛主流程同样排除）
                    if name.startswith("*ST") or name.startswith("ST"):
                        continue
                    bits = [f"涨{chg:.1f}%"]
                    if turnover is not None:
                        bits.append(f"换手{turnover:.1f}%")
                    if vol_ratio is not None:
                        bits.append(f"量比{vol_ratio:.1f}")
                    reason = f"量价异动(新浪): {'+'.join(bits)}"
                    cand.append({"code": code, "name": name, "chg_pct": chg, "reason": reason})
                if cand:
                    plog("INFO", f"[技术面补位] 📊 新浪fallback 发现 {len(cand)} 只异动标的: {[s['name'] for s in cand[:3]]}")
                    return cand[:5]
                plog("INFO", f"[技术面补位] 📭 新浪fallback 无结果")
            except Exception as e2:
                plog("INFO", f"[技术面补位] ❌ 新浪fallback也失败: {e2}")
            return []

    def _parse_screen_result(self, raw_text: str) -> List[StockCandidate]:
        """
        从 LLM 原始输出中解析候选股票（正则提取，0次LLM）
        返回结构化 StockCandidate 列表（含实时行情数据）
        """
        import re
        candidates = []

        # 格式A: 名称（代码）- 理由
        stocks = re.findall(r"([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]\s*[-–—]\s*([^\n]{1,80})", raw_text)
        # 格式B: 代码 名称 - 理由
        stocks_b = re.findall(r"(\d{6})\s+([\u4e00-\u9fa5]{2,6})\s*[-–—]\s*([^\n]{1,80})", raw_text)
        # 合并
        all_stocks = list(stocks) + [(code, name, reason) for code, name, reason in stocks_b
                                      if (name, code) not in [(a, b) for a, b, _ in stocks]]

        # P1: 批量获取实时行情
        realtime_map = {}
        if all_stocks and fetch_quotes is not None:
            try:
                codes = [c[1] for c in all_stocks[:10]]
                api_codes = [to_api(c) for c in codes]
                quotes = fetch_quotes(api_codes)
                realtime_map = {q["代码"]: q for q in quotes if q.get("代码")}
            except Exception:
                pass  # 行情获取失败不阻塞

        # 推断驱动级别
        def infer_level(text: str) -> str:
            text_lower = text.lower()
            explicit_match = re.search(r'\[?\s*驱动级别\s*[：:]\s*([SsAaBb])', text)
            if explicit_match:
                return explicit_match.group(1).upper()
            explicit_match2 = re.search(r'\[([SsAaBb])\]\s*$', text.strip())
            if explicit_match2:
                return explicit_match2.group(1).upper()
            if any(k in text for k in ["s级", "s级驱动", "强烈推荐", "核心龙头", "业绩爆发"]):
                return "S"
            if any(k in text for k in ["a级", "业绩", "确定性", "核心", "景气度"]):
                return "A"
            if any(k in text for k in ["b级", "补涨", "轮动", "跟随"]):
                return "B"
            return "C"

        for name, code, reason in all_stocks[:10]:
            q = realtime_map.get(code, {})
            # P1: 计算技术面评分（原代码导入但未调用，已修复）
            tech_score = {"技术面评分": None, "评分理由": [], "风险提示": []}
            if q and q.get("现价"):
                try:
                    tech_score = calculate_technical_score(q)
                except Exception as e:
                    logger.warning(f"[ScreenAgent] 评分计算失败 {code}: {e}")
            
            candidates.append(StockCandidate(
                code=code,
                name=name,
                reason=reason.strip(),
                driver_level=infer_level(reason),
                pool="",
                # P1: 附着实时行情数据
                current_price=q.get("现价"),
                change_pct=q.get("涨跌幅"),
                change_amount=q.get("涨跌额"),
                turnover_rate=q.get("换手率"),
                volume_ratio=q.get("量比"),
                price_time=q.get("更新时间", datetime.now().strftime("%H:%M")),
                # P1: 技术面评分
                technical_score=tech_score.get("技术面评分"),
                score_reasons=tech_score.get("评分理由", []),
                risk_warnings=tech_score.get("风险提示", []),
            ))
        return candidates

    def _extract_summary(self, news_report: str) -> str:
        """提取宏观摘要"""
        lines = []
        for level in ["S级", "A级", "B级"]:
            if level in news_report:
                part = news_report.split(level)[1]
                first_line = part.split("\n")[0].strip()
                lines.append(f"- **{level}**：{first_line}")
        return "\n".join(lines[:5]) if lines else "（见宏观分析报告）"

    def _extract_new_candidates(self, text: str) -> str:
        """提取新增候选股票（复用 _parse_screen_result 验证过的正则）"""
        # 格式A: 名称（代码）- 理由
        stocks = re.findall(r"([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]\s*[-–—]", text)
        # 格式B: 代码 名称 - 理由
        stocks_b = re.findall(r"(\d{6})\s+([\u4e00-\u9fa5]{2,6})\s*[-–—]", text)
        seen = set()
        all_found = []
        for name, code in stocks:
            key = (name, code)
            if key not in seen:
                seen.add(key)
                all_found.append((name, code))
        for code, name in stocks_b:
            key = (name, code)
            if key not in seen:
                seen.add(key)
                all_found.append((name, code))
        if all_found:
            items = [f"{name}({code})" for name, code in all_found[:5]]
            return f"今日新增候选：{'、'.join(items)}"
        return "（请人工确认候选股票）"

    def _update_candidate_pool(self, screen_result: str):
        """更新快筛候选池，保留原始JSON结构（带代码验证 + 去重 + 过期淘汰）"""
        pool_file = self.root / "五池管理" / "快筛候选池.json"
        pool_file.parent.mkdir(parents=True, exist_ok=True)

        # 提取股票代码（兼容全角/半角括号，名称与括号间可能有空格）
        # 格式: 北方国际 (000065) 或 格力电器（000651）
        stocks = re.findall(r"([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]", screen_result)
        # 格式B兜底: 600690 名称 - 理由
        stocks_b = re.findall(r"(\d{6})\s+([\u4e00-\u9fa5]{2,6})(?:[^\d]|$)", screen_result)
        # 合并去重
        all_found = list(stocks) + [(code, name) for code, name in stocks_b
                                     if (name, code) not in stocks]

        # P1-2: 捕获理由文本（用于S级豁免判断，不匹配时 reason_map 为空字典）
        reason_map = {}
        for _n, _c, _r in re.findall(r"([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]\s*[-–—]\s*([^\n]{1,80})", screen_result):
            reason_map.setdefault(_c, _r)
        for _c, _n, _r in re.findall(r"(\d{6})\s+([\u4e00-\u9fa5]{2,6})\s*[-–—]\s*([^\n]{1,80})", screen_result):
            reason_map.setdefault(_c, _r)

        # 验证股票代码（宽松兜底：验证失败时保留所有，6位数字已足够可靠）
        if all_found and validate_stock_codes is not None:
            codes = [s[1] for s in all_found]
            try:
                valid_codes = validate_stock_codes(codes)
                if valid_codes:  # 有结果才过滤；空结果说明网络问题，保守保留
                    all_found = [s for s in all_found if s[1] in valid_codes]
            except Exception:  # 安全降级: 历史池验证解析失败→跳过，不影响快筛
                pass

        # P1: 批量获取实时行情，附着到候选池
        realtime_pool_map = {}
        if all_found and fetch_quotes is not None:
            try:
                codes = [s[1] for s in all_found[:10]]
                if codes:
                    qs = fetch_quotes([to_api(c) for c in codes])
                    realtime_pool_map = {q["代码"]: q for q in qs if q.get("代码")}
            except Exception:  # 安全降级: 实时行情映射失败→使用默认空映射
                pass

        new_stocks = []
        for name, code in all_found[:10]:
            q = realtime_pool_map.get(code, {})
            # P1: 计算技术面评分
            tech_score_val = None
            if q and q.get("现价"):
                try:
                    ts = calculate_technical_score(q)
                    tech_score_val = ts.get("技术面评分")
                except Exception:  # 安全降级: 技术面评分获取失败→跳过该标的
                    pass

            # P1-2: Screen阶段最低技术评分门槛（65分），S/A级驱动豁免
            # 理由：与 PoolManager._scan_and_downgrade 降级线 65 对齐（09-12 c79418e 教训）；
            # S/A 级驱动标的技术评分低也不过滤，保留强信号通道。
            # P3-快筛萎缩: 豁免改读 reason_text 中的 [驱动级别:S/A] 显式标记 + 关键词，
            # 兼容 _parse_screen_result.infer_level 的分类规则，避免漏豁免。
            reason_text = reason_map.get(code, "")
            driver_level_match = re.search(r'\[\s*驱动级别\s*[：:]\s*([SsAa])', reason_text)
            tail_level_match = re.search(r'\[\s*([SsAa])\s*\]\s*$', reason_text.strip())
            driver_level = (driver_level_match.group(1).upper() if driver_level_match
                            else (tail_level_match.group(1).upper() if tail_level_match else ""))
            is_s_level = driver_level in ("S", "A") or any(
                k in reason_text for k in [
                    "s级", "S级", "s级驱动", "S级驱动", "a级", "A级",
                    "强烈推荐", "核心龙头", "业绩爆发", "确定性", "核心", "景气度",
                ]
            )
            if tech_score_val is not None and tech_score_val < 65 and not is_s_level:
                plog("INFO", f"[ScreenAgent] ⏭️ {name}({code}) 技术评分{tech_score_val}<65，跳过入池")
                continue

            new_stocks.append({
                "代码": code, "名称": name,
                "纳入日期": datetime.now().strftime("%Y-%m-%d"),
                "驱动来源": "快筛新增",
                # P1: 实时行情数据
                "最新价": q.get("现价"),
                "涨跌幅": q.get("涨跌幅"),
                "涨跌额": q.get("涨跌额"),
                "换手率": q.get("换手率"),
                "量比": q.get("量比"),
                "更新时间": q.get("更新时间", datetime.now().strftime("%H:%M")),
                # P1: 技术面评分
                "综合分": tech_score_val,
                # 驱动级别（供板块配额 S 级优先保留判定使用）
                "驱动级别": driver_level,
                # 板块归类（供板块配额硬校验使用）
                "板块": classify_sector(name),
            })

        # 读取现有数据
        data = self.safe_read_json(pool_file, {
            "池名称": "快筛候选池",
            "池定义": "收纳'值得先纳入视野'的对象",
            "进入条件": ["有正文级驱动(S/A/B级)", "有明确逻辑支撑", "风险可控"],
            "stocks": [],
            "历史记录": [],
            "统计": {"创建日期": datetime.now().strftime("%Y-%m-%d"), "累计进入": 0}
        })

        # ── P1-1：48小时重复筛选防护（P4-1 分级豁免）──────────────────
        # 原逻辑无条件 continue，即使本次驱动级别从 B 升到 S 也会被拦。
        # 修复：S 级驱动强制放行，A 级驱动仅当历史分数较低时放行，B 级及以下维持 48h 硬拦截。
        today = datetime.now()
        all_existing = data.get("stocks", [])
        existing_codes = {s.get("代码", s.get("股票代码", "")) for s in all_existing}
        filtered = []
        for s in new_stocks:
            if s["代码"] in existing_codes:
                continue  # 已在池中，不重复添加
            # 检查48小时内是否被筛过但未进入池（检查fast_screen_history）
            fast_history = data.get("_fast_screen_history", {})
            last_seen = fast_history.get(s["代码"])
            if last_seen:
                # history 值可能是纯日期(str) 或 dict({"date":..., "score":...})，兼容旧格式
                if isinstance(last_seen, dict):
                    last_date_str = last_seen.get("date", "")
                    hist_score = last_seen.get("score") or 0
                else:
                    last_date_str = str(last_seen)
                    hist_score = 0
                if last_date_str:
                    try:
                        last_date = datetime.strptime(last_date_str[:10], "%Y-%m-%d")
                    except ValueError:
                        last_date = today - timedelta(days=999)
                    if (today - last_date).days < 2:
                        # P4-1: 分级豁免判断（driver_level 从 reason_map 取，因为 new_stocks 本身不带驱动级别字段）
                        reason_text = reason_map.get(s["代码"], "")
                        driver_match = re.search(r'\[\s*驱动级别\s*[：:]\s*([SsAa])', reason_text)
                        tail_match = re.search(r'\[\s*([SsAa])\s*\]\s*$', reason_text.strip())
                        driver_level = (driver_match.group(1).upper() if driver_match
                                         else (tail_match.group(1).upper() if tail_match else ""))
                        current_score = s.get("综合分", 0) or 0
                        if driver_level == "S":
                            # S 级驱动强制豁免：即便刚被筛过，S 级信号不放过
                            plog("INFO", f"[ScreenAgent] ⚡ {s['代码']}({s['名称']}) 48h 内 S 级驱动豁免")
                            filtered.append(s)
                            continue
                        elif driver_level == "A" and hist_score and current_score >= hist_score + 3:
                            # A 级驱动豁免：仅当历史有分数且本次分数比历史高 ≥3 分时放行
                            plog("INFO", f"[ScreenAgent] ⚡ {s['代码']}({s['名称']}) 48h 内 A 级+分差{int(current_score - hist_score)}豁免")
                            filtered.append(s)
                            continue
                        # B 级及以下维持 48h 硬拦截
                        continue
            filtered.append(s)

        # ── P0-3: 跨池防护优化 — 仅阻塞活跃池（S级/持仓），边缘池和观察池允许回流 ──
        # 09-14实测：快筛5只标的全部被跨池防护拦截（中际旭创/兆易创新等在边缘池）
        # 根因：边缘池是暂存区而非处理管道，S级驱动的新信号不应被永久封死
        cross_pool_blocked = set()
        try:
            from pool_manager import PoolManager
            pm = PoolManager(pool_dir=self.root / "五池管理")
            # 仅阻塞活跃池：S级操作池（今日有效）+ 持仓池（已在操作）
            for pool_name in ["S级操作池", "持仓池"]:
                pool_stocks = pm.get_stocks(pool_name)
                for ps in pool_stocks:
                    code = ps.get("代码", ps.get("股票代码", ""))
                    if code:
                        cross_pool_blocked.add(code)
            if cross_pool_blocked:
                plog("INFO", f"[ScreenAgent] 活跃池防护: {sorted(cross_pool_blocked)}")
        except Exception:
            pass  # 安全降级: 跨池检查失败不阻断快筛

        final_stocks = []
        for s in filtered:
            if s["代码"] in cross_pool_blocked:
                plog("INFO", f"[ScreenAgent] ⚠️ {s['代码']}({s['名称']}) 已存在于其他池，跳过重复添加")
                continue
            final_stocks.append(s)

        # ── 板块配额硬校验（max_per_sector）──────────────────────
        # config.yaml:screening.quick.max_per_sector = 3
        # 09-18 快筛候选池 9 只中地产 4 只（保利/招商蛇口/滨江/华发）超 3 上限
        # 此处按 (S级优先, 综合分降序) 保留 top-k，其余丢弃并 plog
        final_stocks = enforce_sector_quota(final_stocks, _load_max_per_sector())

        # 记录这次筛选历史（即使未入池也记录，用于48h防护）
        data.setdefault("_fast_screen_history", {})
        for s in new_stocks:
            data["_fast_screen_history"][s["代码"]] = today.strftime("%Y-%m-%d")
        # 清理30天前的历史记录
        stale_history = [k for k, v in data["_fast_screen_history"].items()
                         if (today - datetime.strptime(v, "%Y-%m-%d")).days > 30]
        for k in stale_history:
            del data["_fast_screen_history"][k]

        # ── P1-2：过期淘汰机制（移除在池中停留>14天且未升级的标的）──
        stale_removed = []
        active = []
        for s in all_existing:
            entry_date = s.get("纳入日期", s.get("更新时间", ""))
            if entry_date:
                try:
                    dt_entry = datetime.strptime(entry_date[:10], "%Y-%m-%d")
                    if (today - dt_entry).days > 14 and s.get("操作建议", "") != "买入":
                        stale_removed.append(s)
                        continue
                except ValueError:  # 安全降级: 字段类型转换失败→跳过该标的
                    pass
            active.append(s)

        if stale_removed:
            data["stocks"] = (active + final_stocks)[:20]
            data.setdefault("历史记录", []).append({
                "日期": today.strftime("%Y-%m-%d"),
                "过期淘汰": len(stale_removed),
                "新进入": len(final_stocks),
                "淘汰标的": [s.get("名称", "?") for s in stale_removed[:5]],
            })
        else:
            data["stocks"] = (active + final_stocks)[:20]
        # 历史记录（按日聚合，与其他池一致）
        if final_stocks:
            data.setdefault("历史记录", [])
            today = datetime.now().strftime("%Y-%m-%d")
            existing_dates = {r.get("日期") for r in data["历史记录"]}
            if today not in existing_dates:
                data["历史记录"].append({"日期": today, "进入": len(final_stocks)})
                existing_dates.add(today)
        elif not data["stocks"]:
            # 空池写占位（先到先得，由 ReviewAgent 移出时覆盖）
            data.setdefault("历史记录", [])
            today = datetime.now().strftime("%Y-%m-%d")
            existing_dates = {r.get("日期") for r in data["历史记录"]}
            if today not in existing_dates:
                data["历史记录"].append({"日期": today, "进入": 0})
        # 统计：直接等于当前 stocks 数量（已含升池扣减），不用维护增量
        stats = data.get("统计", {})
        stats["累计进入"] = len(data.get("stocks", []))
        data["统计"] = stats

        self.safe_write_json(pool_file, data)

        self.logger.pool_operation("快筛候选池", "add", count=len(final_stocks))

    def _build_context_section(self) -> str:
        """
        收集五池现状 + 候选池粗筛结果 + 大盘指数，注入快筛 prompt。
        解决快筛 LLM 盲打问题。
        """
        import sys
        for mod in list(sys.modules.keys()):
            if 'market_agent' in mod:
                del sys.modules[mod]
        try:
            from market_agent import fetch_quotes, to_api
        except Exception:
            return ""

        parts = []

        # ── 1. 大盘指数 ──────────────────────────────────────────
        try:
            idx_quotes = fetch_quotes(["sh000001", "sz399001", "sz399006"])
            if idx_quotes:
                idx_lines = ["**【大盘环境】**"]
                for q in idx_quotes:
                    name = q.get("名称", "?")
                    price = q.get("现价", 0)
                    chg = q.get("涨跌幅", 0)
                    vol = q.get("成交量", 0)
                    if vol:
                        vol_str = f"{float(vol)/1e8:.1f}亿"
                    else:
                        vol_str = "—"
                    trend = "📈" if chg > 0 else "📉" if chg < 0 else "➡️"
                    idx_lines.append(f"- {trend} {name}: {price:.2f} ({chg:+.2f}%) 成交{vol_str}")
                parts.append("\n".join(idx_lines))
        except Exception:  # 安全降级: 索引行拼接失败→降级到空索引
            pass

        # ── 2. 五池现状（持仓 + 重点观察）────────────────────────
        pool_info = []
        for pool_name, pool_key in [
            ("持仓池", "持仓池"),
            ("重点观察池", "重点观察池"),
        ]:
            pool_file = self.root / "五池管理" / f"{pool_name}.json"
            if not pool_file.exists():
                continue
            data = self.safe_read_json(pool_file, {})
            stocks = data.get("stocks", [])
            if stocks:
                names = [s.get("名称") or s.get("股票名称", "?") for s in stocks[:8]]
                pool_info.append(f"- **{pool_name}**：{', '.join(names)}")
        if pool_info:
            parts.append("**【五池现状】**\n" + "\n".join(pool_info))

        # ── 3. 候选池粗筛（PE/换手率/市值/涨跌幅过滤）────────────
        candidate_file = self.root / "五池管理" / "快筛候选池.json"
        rough_lines = []
        if candidate_file.exists():
            data = self.safe_read_json(candidate_file, {})
            candidate_stocks = data.get("stocks", [])
            if candidate_stocks:
                # 提取代码并加前缀
                codes_raw = [
                    str(s.get("代码") or s.get("股票代码", "")).strip()
                    for s in candidate_stocks
                    if (s.get("代码") or s.get("股票代码", ""))
                ]
                if codes_raw:
                    # 直接调 fetch_quotes（前缀由 to_api 保证）
                    api_codes = [to_api(c) for c in codes_raw]
                    quotes = fetch_quotes(api_codes)
                    qmap = {item["代码"]: item for item in quotes}

                    screened = []
                    for s in candidate_stocks:
                        raw = str(s.get("代码") or s.get("股票代码", "")).strip()
                        q = qmap.get(raw, {})
                        if not q:
                            continue
                        pe = q.get("市盈率_TTM", 0)
                        turnover = q.get("换手率", 0)
                        circ_mv = q.get("流通市值_亿", 0)
                        chg = q.get("涨跌幅", 0)
                        if pe and (pe <= 0 or pe > 50):
                            continue
                        if turnover and turnover < 1.0:
                            continue
                        if circ_mv and circ_mv < 5:
                            continue
                        if chg < -10:
                            continue
                        screened.append(q)

                    if screened:
                        rough_lines.append(
                            f"**【候选池粗筛通过】（PE<50/换手>1%/市值>5亿/跌幅>-10%）**"
                        )
                        for q in screened[:10]:
                            code = q.get("代码", "?")
                            name = q.get("名称", "?")
                            price = q.get("现价", 0)
                            chg = q.get("涨跌幅", 0)
                            pe = q.get("市盈率_TTM", "—")
                            turnover = q.get("换手率", 0)
                            rough_lines.append(
                                f"- {name}({code}) 现价{price:.2f} {chg:+.2f}% "
                                f"PE={pe} 换手{turnover:.2f}%"
                            )
                    else:
                        rough_lines.append(
                            "**【候选池粗筛通过】** 暂无（候选池为空或全部被过滤）"
                        )
        if rough_lines:
            parts.append("\n".join(rough_lines))

        return "\n\n".join(parts) if parts else ""

    def _smart_truncate(self, text: str, max_chars: int = 6000) -> str:
        """
        P0-3: 智能截断——传导链分析优先。
        """
        chain_marker = "## 详细新闻"
        if chain_marker in text and len(text) > max_chars:
            cutoff = text.index(chain_marker)
            prefix = text[:cutoff]
            if len(prefix) > max_chars:
                return text[:max_chars]
            return prefix
        return text[:max_chars] if len(text) > max_chars else text


if __name__ == "__main__":
    agent = ScreenAgent()
    result = agent.run()
    if result["success"]:
        plog("INFO", f"✅ 快筛完成")
        plog("INFO", f"📄 保存: {result['saved_to']}")
        plog("INFO", "\n" + "=" * 40)
        plog("INFO", result["report"][:800])
