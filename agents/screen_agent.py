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
from datetime import datetime

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

from market_agent import fetch_quotes, calculate_technical_score, to_api

ROLE_PROMPT = """你是一个短线选股专家，根据新闻驱动筛选股票。

⚠️ 硬性约束（违反即淘汰）：
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
            max_tokens=3000,
        )

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

        # 验证股票代码（宽松兜底：验证失败时保留所有，6位数字已足够可靠）
        if all_found and validate_stock_codes is not None:
            codes = [s[1] for s in all_found]
            try:
                valid_codes = validate_stock_codes(codes)
                if valid_codes:  # 有结果才过滤；空结果说明网络问题，保守保留
                    all_found = [s for s in all_found if s[1] in valid_codes]
            except Exception:
                pass

        # P1: 批量获取实时行情，附着到候选池
        realtime_pool_map = {}
        if all_found and fetch_quotes is not None:
            try:
                codes = [s[1] for s in all_found[:10]]
                if codes:
                    qs = fetch_quotes([to_api(c) for c in codes])
                    realtime_pool_map = {q["代码"]: q for q in qs if q.get("代码")}
            except Exception:
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
                except Exception:
                    pass
            
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

        # ── P1-1：48小时重复筛选防护 ──────────────────────────
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
                last_date = datetime.strptime(last_seen, "%Y-%m-%d")
                if (today - last_date).days < 2:
                    continue  # 48小时内已筛过，跳过
            filtered.append(s)

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
                except ValueError:
                    pass
            active.append(s)

        if stale_removed:
            data["stocks"] = (active + filtered)[:20]
            data.setdefault("历史记录", []).append({
                "日期": today.strftime("%Y-%m-%d"),
                "过期淘汰": len(stale_removed),
                "新进入": len(filtered),
                "淘汰标的": [s.get("名称", "?") for s in stale_removed[:5]],
            })
        else:
            data["stocks"] = (active + filtered)[:20]
        # 历史记录（按日聚合，与其他池一致）
        if filtered:
            data.setdefault("历史记录", [])
            today = datetime.now().strftime("%Y-%m-%d")
            existing_dates = {r.get("日期") for r in data["历史记录"]}
            if today not in existing_dates:
                data["历史记录"].append({"日期": today, "进入": len(filtered)})
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

        self.logger.pool_operation("快筛候选池", "add", count=len(filtered))

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
        except Exception:
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
        print(f"✅ 快筛完成")
        print(f"📄 保存: {result['saved_to']}")
        print("\n" + "=" * 40)
        print(result["report"][:800])