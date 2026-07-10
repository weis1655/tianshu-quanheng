"""PoolUpdater - 池管理操作独立模块"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any
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

        matches = re.findall(r"【主推】\s*([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]", decision_result)
        # ── P0: debug日志——验证【主推】正则匹配 ──
        plog("INFO", f"[PoolUpdater] 🔍 决策报告扫描【主推】: 找到{len(matches)}个匹配")
        if not matches:
            plog("INFO", f"[PoolUpdater] 📄 报告末尾300字符: ...{decision_result[-300:]}")
            # 检查是否包含"主推"字样但不匹配格式
            if "主推" in decision_result:
                plog("INFO", f"[PoolUpdater] ⚠️ 发现「主推」字样但正则未匹配，可能是格式异常")
                # 宽松匹配：找StockName(Code)格式
                broad = re.findall(r"([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]", decision_result)
                if broad:
                    plog("INFO", f"[PoolUpdater] 💡 宽松匹配到{broad}，但缺乏【主推】标记")
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
            position_warning = self._check_price_position(code, entry_price)
            if position_warning:
                plog("INFO", f"[PoolUpdater] 🚫 {name}({code}) {position_warning}, 拒绝入S级操作池")
                continue

            # 新条目：优先从 scored_stocks 取分，fallback 正则提取
            score = scored_map.get(code, 0) or self._extract_score(name, code, decision_result)
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
        old_data = self._safe_read_json(pool_file, {})
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

        # 按code去重合并
        existing_codes = {s.get("代码", "") for s in retained_stocks}
        new_deduped = [s for s in new_stocks if s.get("代码", "") not in existing_codes]
        merged = new_deduped + retained_stocks

        data = {
            "池名称": "S级操作池",
            "池定义": "当日决策主推标的，容量≤3，T+0可追，T+1需评估",
            "stocks": merged[:3],
            "统计": {"创建日期": today, "当日进入": len(new_stocks)},
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

        self._safe_write_json(pool_file, data)
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
                pool_data = self._safe_read_json(pool_file, {})
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
        """提取该股票决策报告中的核心逻辑"""
        pattern = rf"【主推】\s*{re.escape(name)}\s*[（(]\d{{6}}[）)].*?(?=\n### |\n---\n|$)"
        m = re.search(pattern, decision_result, re.DOTALL)
        if not m:
            return ""
        paragraph = m.group(0)
        for line in paragraph.split("\n"):
            if any(k in line for k in ["核心驱动", "逻辑支撑", "驱动"]):
                text = line.split("：", 1)[-1].split("——")[0].strip()
                if text:
                    return text[:60]
        return ""

    # ── P0: 从决策报告提取评分 ──
    def _extract_score(self, name, code, decision_result):
        """从决策报告中提取该股票的综合评分（0次LLM，纯正则）"""
        import re
        # 策略1：找该股票附近区域的"综合分N分"或"评分:N分"
        pattern = rf"(?:{re.escape(name)}|{re.escape(code)})[\s\S]{{0,300}}(?:综合分|综合评分|评分)\s*(?:\*{{0,2}}\s*[：:\s]\s*\*{{0,2}}\s*|[\s：:]*)(\d+)"
        m = re.search(pattern, decision_result)
        if m and m.lastindex and m.group(m.lastindex):
            try:
                return min(100, max(0, int(m.group(m.lastindex))))
            except (ValueError, TypeError):
                pass
        # 策略2：StockName(Code)附近找评分
        pattern2 = rf"{re.escape(name)}\s*[（(]{re.escape(code)}[）)][\s\S]{{0,300}}(?:综合分|综合评分|评分)\s*[：:\s]*\*?\s*(\d+)"
        m2 = re.search(pattern2, decision_result)
        if m2 and m2.lastindex and m2.group(m2.lastindex):
            try:
                return min(100, max(0, int(m2.group(m2.lastindex))))
            except (ValueError, TypeError):
                pass
        return 0

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
            data = self._safe_read_json(pf, {})
            for s in data.get("stocks", []):
                code = str(s.get("代码", s.get("股票代码", ""))).strip()
                if code:
                    codes.append(code)

        if not codes:
            return {}

        quotes = fetch_quotes([to_api(c) for c in codes])
        return {q["代码"]: q.get("现价", q.get("current", 0)) for q in quotes if q.get("代码")}

    def _safe_read_json(self, path: Path, default=None):
        if default is None:
            default = {}
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # 安全降级: JSON文件读取失败→返回空dict，不影响后续
            pass
        return default

    def _safe_write_json(self, path: Path, data: dict):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")