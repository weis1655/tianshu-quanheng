"""PoolUpdater - 池管理操作独立模块"""
import json
from safe_file_utils import safe_read_json, safe_write_file

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from logger import plog


class PoolUpdater:
    """盘池更新操作：S级池写入、去重检查等"""

    def __init__(self, root: Path, pool_manager=None):
        self.root = root
        self.pool_manager = pool_manager
        # 惰性导入 QualityGate（打破循环导入：decision_agent→pool_updater→quality_gate→review_scorer）
        from agents.quality_gate import QualityGate
        self.quality_gate = QualityGate(root)

    def update_s_pool(self, decision_result: str, pool_manager=None, scored_stocks: Optional[list] = None):
        """从决策报告提取【主推】标的，写入S级操作池
        完整逻辑移植自 DecisionAgent._update_s_pool（含今日已修复的merge逻辑）
        """
        from market_agent import to_api, fetch_quotes

        pm = pool_manager or self.pool_manager
        if not pm:
            return
            
        pool_file = self.root / "五池管理" / "S级操作池.json"
        pool_file.parent.mkdir(parents=True, exist_ok=True)

        # ── 09-10止血①: Skeptic裁决硬前置 ──
        # S池准入须消费结构化裁决，与 decision_agent L558 Gate 对齐。此前Gate只拦
        # pools/scored_stocks，S池写入通道对 blocked_codes 零可见性，导致被Skeptic
        # 标记 high 风险的标的经宽松兜底绕进S池（流程脱节根因）。
        try:
            from agents.gate_controller import GateController
            verdict_file = self.root / "data" / "历史记录" / f"{datetime.now().strftime('%Y-%m-%d')}_质疑审查裁决.json"
            blocked_codes, gate_passed = GateController.read_verdict(verdict_file)
        except Exception as e:
            # 安全降级：裁决读取失败不阻断池更新（Skeptic未运行时不误杀）
            plog("INFO", f"[PoolUpdater] ⚠️ 裁决读取失败，跳过Gate前置校验: {e}")
            blocked_codes, gate_passed = set(), True
        if blocked_codes:
            plog("INFO", f"[PoolUpdater] 🔴 Skeptic裁决阻塞: {sorted(blocked_codes)}")

        # ── 09-10止血③: LLM自洽交叉校验 ──
        # 决策报告尾部由LLM声明当日主推数量。声明0只时S池写入应为0，
        # 硬拦截"报告说0只、池里写3只"的自相矛盾。
        cap_m = re.search(r"S级操作池\*{0,2}\s*[：:]\s*(\d+)\s*只", decision_result)
        if cap_m and int(cap_m.group(1)) == 0:
            plog("INFO", "[PoolUpdater] 🛑 LLM声明S级操作池0只主推，S池写入交叉校验不通过，跳过")
            return

        matches = re.findall(r"【主推】\s*([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]", decision_result)
        # ── P0: debug日志——验证【主推】正则匹配 ──
        plog("INFO", f"[PoolUpdater] 🔍 决策报告扫描【主推】: 找到{len(matches)}个匹配")
        if not matches:
            plog("INFO", f"[PoolUpdater] 📄 报告末尾300字符: ...{decision_result[-300:]}")
            # 任务①: 07-21原则「有可执行交易信息即有效」的落地。
            # LLM若未按【主推】格式输出，不再整批丢弃，而是从「有可执行交易信息」的
            # 标的中宽松提取（需带6位代码 + 仓位/止损/止盈/买入/目标价等行动字段），
            # 避免因格式漂移导致有效决策整批丢失S池记录。
            # 09-10止血: 兜底须防两类误判——①"操作"是"不操作"的子串、"买入"是
            # "买入逻辑矛盾"的子串（action_kw 收窄为带数值的行动字段）；②重点观察池
            # 状态表里的否决标的（不操作/高风险）被误判为决策主推（否定语境守卫）。
            broad = re.findall(r"([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]", decision_result)
            if "主推" in decision_result:
                plog("INFO", f"[PoolUpdater] ⚠️ 发现「主推」字样但正则未匹配，可能是格式异常")
            if broad:
                # 仅保留「确有可执行交易信息」的标的：窗口内须出现带数值的行动字段
                action_pats = (
                    r"止损[价]?[:：]\s*\d",
                    r"止盈[价]?[:：]\s*\d",
                    r"买入价[:：]\s*\d",
                    r"目标价[:：]\s*\d",
                    r"仓位[:：]?\s*\d+\s*%",
                    r"买入[区价]?\s*\d+(\.\d+)?\s*[-~]\s*\d+(\.\d+)?",
                )
                neg_pats = (
                    "不操作", "观望", "淘汰", "高风险", "未审查", "空仓",
                    "不满足", "短线无价值", "无法确认", "缺乏", "矛盾",
                    "拒绝", "不满足执行", "全部淘汰", "低于60",
                )
                text = decision_result
                kept = []
                rejected = []
                for name, code in broad:
                    idx = text.find(f"{name}")
                    window = text[idx:idx+400] if idx >= 0 else text[-400:]
                    if any(re.search(p, window) for p in action_pats):
                        neg_hit = [k for k in neg_pats if k in window]
                        if neg_hit:
                            # 否决语境优先：Skeptic/决策层已明确否决，禁止兜底晋级
                            rejected.append((name, code, neg_hit))
                        else:
                            kept.append((name, code))
                for name, code, neg_hit in rejected:
                    plog("INFO", f"[PoolUpdater] 🚫 宽松兜底否决语境拦截: {name}({code}) 命中{neg_hit}")
                if kept:
                    plog("INFO", f"[PoolUpdater] 🔁 宽松兜底：从可执行交易信息中提取{kept}")
                    matches = kept
                else:
                    plog("INFO", f"[PoolUpdater] 💡 宽松匹配到{broad}，但无可执行交易信息或处否决语境，不写入S池")
            if not matches:
                return
        elif len(matches) > 0:
            plog("INFO", f"[PoolUpdater] ✅ 成功匹配: {[(n,c) for n,c in matches]}")

        today = datetime.now().strftime("%Y-%m-%d")
        # 获取当前行情作为入场参考价
        current_prices = self._fetch_current_prices()

        # 构建 scored_stocks 查询字典（code → score）
        scored_map = {}
        if scored_stocks:
            for ss in scored_stocks:
                sc = str(ss.get("code", ss.get("代码", "")))
                sv = ss.get("score", ss.get("综合评分", 0))
                if sc:
                    scored_map[sc] = sv

        new_stocks = []
        for name, code in matches[:3]:
            # 09-10止血①: 逐标的Gate拦截。严格【主推】路径也要消费Skeptic裁决，
            # 否则Skeptic标记high风险的标的被LLM误推时仍会晋级S池。
            if str(code) in blocked_codes:
                plog("INFO", f"[PoolUpdater] 🚫 {name}({code}) 被Skeptic裁决阻塞，拒绝入S级操作池")
                continue

            # 记事本模式：决策agent已跑完全流程审查，S池只做记录+价格检查
            # 不再二次审查已通过的标的（防线一+质检门已下沉到决策agent+SkepticGate）

            # 价格位置检查（唯一保留的防线：防52周高位追涨）
            entry_price = current_prices.get(code, 0)
            if entry_price <= 0:
                try:
                    from market_agent import to_api, fetch_quotes
                    q = fetch_quotes([to_api(code)])
                    if q and len(q) > 0:
                        entry_price = q[0].get("现价", 0)
                except Exception:  # 安全降级: 价格获取失败→保持默认价格，不影响池更新
                    pass
            # 从决策报告提取推荐买入价（优先于实时行情）
            buy_price_m = re.search(
                rf'{re.escape(name)}\s*[（(]{re.escape(code)}[）)][^$]*?买入价[：:]\s*([\d.]+)',
                decision_result
            )
            if not buy_price_m:
                buy_price_m = re.search(
                    r'买入[价区][^$]*?([\d.]+)\s*[~-]\s*([\d.]+)',
                    decision_result
                )
            if buy_price_m:
                try:
                    recommended_price = float(buy_price_m.group(1))
                    if recommended_price > 0:
                        entry_price = recommended_price
                        plog("INFO", f"[PoolUpdater] 📐 从决策报告提取买入价: {name}({code}) {entry_price}元")
                except (ValueError, IndexError):
                    pass
            position_warning = self._check_price_position(code, entry_price)
            if position_warning:
                plog("INFO", f"[PoolUpdater] 🚫 {name}({code}) {position_warning}, 拒绝入S级操作池")
                continue

            # 新条目：优先从 scored_stocks 取分，fallback 正则提取
            score = scored_map.get(code) if scored_map.get(code) is not None else self._extract_score(name, code, decision_result)
            s = {
                "代码": code,
                "名称": name,
                "综合评分": score,  # 从决策报告提取（P0修复：不再硬编码0）
                "纳入日期": today,
                "驱动来源": "决策主推",
                "核心逻辑": self._extract_logic_snippet(name, decision_result),
                "入场价": entry_price,
                "t1_验证": None,
                "t3_验证": None,
                "评价": None,
            }
            new_stocks.append(s)
            plog("INFO", f"[PoolUpdater] ✅ {name}({code}) → S级操作池 (记事本模式)")

        self._check_s_pool_overlap(new_stocks)

        # 读取现有池数据（保留历史记录 + 合并今日标的）
        old_data = safe_read_json(pool_file, {})
        old_history = old_data.get("历史记录", [])

        # ── 合并旧池中未过期的标的（今日新加入的或未满1天的）──
        old_stocks = old_data.get("stocks", [])
        today_dt = datetime.now()
        retained_stocks = []
        for s in old_stocks:
            entry_date_str = s.get("纳入日期", "")
            try:
                entry_date = datetime.strptime(entry_date_str, "%Y-%m-%d")
                age = (today_dt - entry_date).days
                if age == 0:
                    retained_stocks.append(s)
            except (ValueError, TypeError):
                # 日期格式异常，跳过该标的（非关键路径，不阻塞）
                pass

        # 按code去重合并（今日已入池的标的优先保留，不被新推荐挤占）
        existing_codes = {s.get("代码", "") for s in retained_stocks}
        new_deduped = [s for s in new_stocks if s.get("代码", "") not in existing_codes]
        # 容量限制：先确保保留(stocks)全部保留，新条目不超过容量上限
        max_remaining = 3 - len(retained_stocks)
        new_trimmed = new_deduped[:max_remaining] if max_remaining > 0 else []
        merged = retained_stocks + new_trimmed

        data = {
            "池名称": "S级操作池",
            "池定义": "当日决策主推标的，容量≤3，T+0可追，T+1需评估",
            "stocks": merged,
            "统计": {"创建日期": today, "当日进入": len(new_stocks), "更新日期": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
            "历史记录": old_history,
        }

        existing_dates = {r.get("日期") for r in data["历史记录"]}
        if today not in existing_dates:
            data["历史记录"].append({
                "日期": today,
                "进入": len(new_stocks),
                "标的": [{"代码": s["代码"], "名称": s["名称"], "入场价": s["入场价"]} for s in new_stocks],
                "核心逻辑": {s["名称"]: s["核心逻辑"] for s in new_stocks},
            })

        safe_write_file(pool_file, json.dumps(data, ensure_ascii=False, indent=2))
        plog("INFO", f"[PoolUpdater] ✅ S级操作池更新: {len(new_stocks)} 只主推标的")

    def _check_price_position(self, code: str, current_price: float) -> str:
            """检查当前价格在52周中的位置，返回空字符串表示通过，非空表示警告。

            P1-3修复：增加趋势感知——上升趋势中的高位=强势股，不拦截。
            P0-7修复：API无MA数据时视为无法判断趋势，放行不拦截。
            """
            try:
                from market_agent import to_api, fetch_history, fetch_quotes
                symbol = to_api(code)
                history = fetch_history(symbol, "month", 12)
                if not history:
                    return ""
                high_52w = max(float(item.get("最高", 0)) for item in history)
                if high_52w <= 0:
                    return ""
                ratio = current_price / high_52w
                if ratio > 0.85:
                    # P1-3: 检查是否处于上升趋势——趋势中的高位是强势股，不拦截
                    try:
                        q = fetch_quotes([symbol])
                        if q and len(q) > 0:
                            ma5 = q[0].get('MA5', 0)
                            ma10 = q[0].get('MA10', 0)
                            if ma5 and ma10 and ma5 > ma10:
                                # 上升趋势中52周高位=强势股，放行
                                return ""
                            if not ma5 and not ma10:
                                # API不返回MA数据，无法判断趋势，放行不拦截
                                return ""
                    except Exception:  # 安全降级: 池记录读取失败→返回空字符串，不影响更新
                        pass
                    # 阈值从85%放宽到92%（P1-3放松）
                    if ratio > 0.92:
                        return f"追高风险: 当前价{current_price}/52周最高{high_52w}={ratio:.0%}>92%"
                    return ""
                return ""
            except Exception as e:
                plog("INFO", f"[PoolUpdater] ⚠️ 价格位置检查失败({code}): {e}")
                return ""

    def _get_market_state(self) -> dict:
        """获取当前市场状态（P2修复：对齐5档标准，基于沪深300 vs MA20）。
        
        返回 {state, s_pool_cap, suggestion}。
        与 skeptic_agent._get_market_state_from_index() 逻辑对齐。
        """
        try:
            # 复用skeptic_agent的5档判定（沪深300 vs MA20）
            from agents.skeptic_agent import SkepticAgent
            sa = SkepticAgent("temp_state")
            state = sa._get_market_state_from_index()
            
            # 5档 → s_pool_cap 映射（P2修复：偏空也保留1只）
            cap_map = {
                "偏多": 3,        # 牛市，可推3只
                "震荡偏强": 3,    # 强势震荡，可推3只
                "震荡": 2,        # 中性，2只
                "震荡偏弱": 2,    # 弱市，保留2只（原为1只）
                "偏空": 1,        # 偏空，至少1只（原为0只）
            }
            sug_map = {
                "偏多": "积极，关注科技+券商",
                "震荡偏强": "谨慎积极",
                "震荡": "标准",
                "震荡偏弱": "防御为主，关注高股息",
                "偏空": "严格风控，仅极优标的",
            }
            return {
                "state": state,
                "s_pool_cap": cap_map.get(state, 2),
                "suggestion": sug_map.get(state, "标准"),
            }
        except Exception:  # 安全降级: 池读取失败→返回空pool，不影响流转
            pass
        # 兜底：直接从 shared_memory.json 读取（原逻辑的降级版）
        try:
            import json
            sm_file = self.root / "data" / "shared_memory.json"
            if sm_file.exists():
                data = json.loads(sm_file.read_text(encoding="utf-8"))
                if data and isinstance(data, list):
                    sh = next((s for s in data if s.get("代码") == "000001"), None)
                    if sh:
                        sh_chg = float(sh.get("涨跌幅", 0))
                        if sh_chg > 1:
                            return {"state": "偏多", "s_pool_cap": 3}
                        elif sh_chg > 0:
                            return {"state": "震荡偏强", "s_pool_cap": 3}
                        elif sh_chg > -1:
                            return {"state": "震荡偏弱", "s_pool_cap": 2}
                        else:
                            return {"state": "偏空", "s_pool_cap": 1}
        except Exception:  # 安全降级: 市场状态获取失败→降级为偏空，保守处理
            pass
        return {"state": "震荡", "s_pool_cap": 2}

    def _check_s_pool_overlap(self, new_stocks: list):
        """检查S级主推标的是否已在其他流转池中，若在重点观察池则移除（晋级S级=移出重点池）"""
        check_pools = ["快筛候选池", "重点观察池", "边缘池"]
        for s in new_stocks:
            code = s.get("代码", "")
            name = s.get("名称", "")
            for pool_name in check_pools:
                pool_file = self.root / "五池管理" / f"{pool_name}.json"
                pool_data = safe_read_json(pool_file, {})
                pool_codes = {str(x.get("代码", "")) for x in pool_data.get("stocks", [])}
                if code in pool_codes:
                    if pool_name == "重点观察池":
                        # P1-2026-06-04: 晋级S级=移出重点池，防跨池重复
                        removed = self.pool_manager.remove_stock("重点观察池", code) if self.pool_manager else False
                        plog("INFO", f"[PoolUpdater] ⬆️ {name}({code}) 已从{pool_name}移除（晋级S级操作池）{'✅' if removed else '⚠️未成功'}")
                    elif pool_name == "快筛候选池":
                        # P1-2026-06-04: 晋级S级也应从快筛候选池移除（该标的不应同时在候选池和S级池）
                        removed = self.pool_manager.remove_stock("快筛候选池", code) if self.pool_manager else False
                        plog("INFO", f"[PoolUpdater] ⬆️ {name}({code}) 已从{pool_name}移除（晋级S级操作池）{'✅' if removed else '⚠️未成功'}")
                    else:
                        plog("INFO", f"[PoolUpdater] ⚠️ {name}({code}) 同时存在于 {pool_name}（非活跃池，仅警告）")

    def _extract_logic_snippet(self, name: str, decision_result: str) -> str:
        """提取该股票决策报告中的核心逻辑

        09-10修复: 原实现只匹配【主推】前缀段落，07-21 格式漂移后报告不再有
        【主推】段落 → 核心逻辑全空。改为按"### 标名（code）"标题锚点定位标的段落，
        兼容有/无【主推】前缀，并扩大驱动关键词。
        """
        # 标题锚点：支持 【主推】兴发集团（600141） 与 ### 兴发集团（600141）
        pattern = rf"(?:^|\n)#+\s*(?:【主推】\s*)?{re.escape(name)}\s*[（(]\d{{6}}[）)].*?(?=\n#+\s|\n---\n|\Z)"
        m = re.search(pattern, decision_result, re.DOTALL)
        if not m:
            return ""
        paragraph = m.group(0)
        # 驱动关键词按优先级：最具体的优先，避免泛化命中噪声行
        keywords = ["核心驱动", "驱动级别", "逻辑支撑", "核心逻辑", "催化剂", "驱动", "逻辑"]
        for kw in keywords:
            for line in paragraph.split("\n"):
                if kw in line:
                    text = line.lstrip("#- •·").strip()
                    text = text.split("：", 1)[-1].split("——")[0].strip()
                    # 去掉行首编号/列表符
                    text = re.sub(r"^[-*•·\d.\s]*", "", text).strip()
                    if text:
                        return text[:60]
        # 兜底：取段落内第一条实质内容行
        for line in paragraph.split("\n"):
            t = re.sub(r"^[-*•·\d.\s#]+", "", line).strip()
            if len(t) > 6 and not re.match(r"^\|?\s*[-|：:]", t):
                return t[:60]
        return ""

    # ── P0: 从决策报告提取评分 ──
    def _extract_score(self, name, code, decision_result):
        """从决策报告中提取该股票的综合评分（0次LLM，纯正则）

        09-10修复:
        ① 原正则不容忍"评分**仅**42分"这类虚词/markdown加粗 → 误判为0
        ② 提取范围限定在「标名（代码）」锚定的标的段落内，避免跨标的污染
           ——旧实现全局正则会把审查汇总表的他人评分误配给当前标的，
           更严重的是会匹配到表格列名"综合评分"后抓到相邻行别的标的的价格
           （如兴发集团的"综合评分"实际抓到川发龙蟒的39.45元）
        ③ 全部失败时返回 None 而非 0，区分"无评分"与"真0分"的语义
        """
        # 评分标记后的分隔/加粗/虚词组合：
        #   综合评分：82 / 综合评分 82 / **综合评分**仅42分 / 综合评分约78分 / 综合评分为76分
        suffix = r"\*{0,2}(?:\s*[：:]\s*|\s*)\*{0,2}(?:仅|约|为|达|共|已)?\s*(\d{2,3})"
        score_pat = re.compile(r"(?:综合评分|综合分|评分)" + suffix)

        # 锚点：标名（代码）行。段落边界 = 下一个标名（代码）行 / 分隔线 / 文末
        seg_pat = re.compile(
            rf"(?:^|\n)(?:#+\s*)?(?:【主推】\s*)?{re.escape(name)}\s*[（(]{re.escape(code)}[）)]"
            r"[\s\S]{0,300}?(?=\n(?:#+\s*)?(?:【主推】\s*)?[\u4e00-\u9fa5]{2,6}\s*[（(]\d{6}[）)]"
            r"|\n---\n|\Z)"
        )
        m = seg_pat.search(decision_result)
        if m:
            s = score_pat.search(m.group(0))
            if s:
                return min(100, max(0, int(s.group(1))))
            # 段内无评分：不回退全局搜索（会跨标的污染），直接判"无评分"
            return None
        # 无锚点：标的无独立段落（如仅在表格/列表中提及）。
        # 不做全局回退——短窗口同样可能抓到相邻标的的评分或价格
        return None

    def _fetch_current_prices(self) -> dict:
        """获取各池股票当前行情，返回 {代码: 现价} 字典"""
        try:
            from market_agent import fetch_quotes, to_api
        except Exception:
            return {}

        pool_files = [
            self.root / "五池管理" / "重点观察池.json",
            self.root / "五池管理" / "快筛候选池.json",
        ]
        codes = []
        for pf in pool_files:
            if not pf.exists():
                continue
            data = safe_read_json(pf, {})
            for s in data.get("stocks", []):
                code = str(s.get("代码", s.get("股票代码", ""))).strip()
                if code:
                    codes.append(code)

        if not codes:
            return {}

        quotes = fetch_quotes([to_api(c) for c in codes])
        return {q["代码"]: q.get("现价", q.get("current", 0)) for q in quotes if q.get("代码")}