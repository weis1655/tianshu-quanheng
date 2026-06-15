#!/usr/bin/env python3
"""
Pool Manager Class - 集中管理所有股票池操作
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

# 确保能找到其他agents
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / "agents"))


class PoolManager:
    """集中管理五池操作的类"""
    
    # 池名称常量（与五池管理目录下的实际文件名保持一致）
    POOL_NAMES = [
        "快筛候选池",
        "重点观察池",
        # 接近决策池已停用（全链路移除）
        "边缘池",
        "持仓池",
        "S级操作池",
    ]
    
    # 池容量限制（P0-2 + P2-1：代码级强制限制）
    POOL_CAPACITY_LIMITS = {
        "快筛候选池": 20,
        "重点观察池": 20,
        "边缘池": 20,
        "持仓池": None,  # 无上限
        "S级操作池": 3,  # ≤3，当日决策主推标的
    }
    
    # 统一的字段名（标准格式）
    STANDARD_FIELDS = {
        "code": "股票代码",
        "name": "股票名称", 
        "cost": "成本",
        "price": "最新价",
        "pnl": "盈亏",
        "pnl_pct": "盈亏比例",
        "date": "建仓日期",
        "remark": "备注"
    }
    
    def __init__(self, pool_dir: Optional[Path] = None):
        """
        初始化PoolManager
        
        Args:
            pool_dir: 池文件所在目录，默认为项目根目录下的五池管理
        """
        self.pool_dir = pool_dir or (PROJECT_ROOT / "五池管理")
        self.pool_dir.mkdir(parents=True, exist_ok=True)
    
    def get_pool_path(self, pool_name: str) -> Path:
        """获取指定池的文件路径"""
        return self.pool_dir / f"{pool_name}.json"
    
    def load_pool(self, pool_name: str) -> Dict[str, Any]:
        """
        加载指定的池
        
        Args:
            pool_name: 池名称
            
        Returns:
            池数据字典
        """
        pool_file = self.get_pool_path(pool_name)
        if not pool_file.exists():
            # 返回空池结构
            return self._empty_pool(pool_name)
        
        try:
            data = json.loads(pool_file.read_text(encoding="utf-8"))
            return data
        except Exception as e:
            print(f"[PoolManager] 加载池失败 {pool_name}: {e}")
            return self._empty_pool(pool_name)
    
    def save_pool(self, pool_name: str, data: Dict[str, Any]) -> bool:
        """
        保存指定的池
        
        Args:
            pool_name: 池名称
            data: 池数据

        Returns:
            是否成功
        """
        pool_file = self.get_pool_path(pool_name)
        try:
            # ── P0-2 + P2-1：容量限制检查 ─────────────────────
            limit = self.POOL_CAPACITY_LIMITS.get(pool_name)
            if limit is not None:
                stocks = data.get("stocks", [])
                if len(stocks) > limit:
                    # S级操作池：保留最新加入的（按纳入日期排序，最新的在前）
                    if pool_name == "S级操作池":
                        def get_date(s):
                            d = s.get("纳入日期", s.get("建仓日期", ""))
                            try:
                                return datetime.strptime(d, "%Y-%m-%d")
                            except:
                                return datetime.min
                        stocks_sorted = sorted(stocks, key=get_date, reverse=True)
                        data["stocks"] = stocks_sorted[:limit]
                        removed = stocks_sorted[limit:]
                        print(f"[PoolManager] ⚠️ {pool_name} 超出容量限制({limit})，移除最旧标的: {[s.get('代码', s.get('股票代码', '?')) for s in removed]}")
                    else:
                        # 其他池：保留最新的
                        data["stocks"] = stocks[-limit:]
                        print(f"[PoolManager] ⚠️ {pool_name} 超出容量限制({limit})，已自动截断")
            
            # ── 入池后自动排序：按综合分降序（无分值的排最后）──
            self._maybe_sort_pool(data, pool_name)
            # ── 同步更新持仓数统计（兜底：确保统计与 stocks 一致）──
            stocks = data.get("stocks", [])
            data["统计"] = data.get("统计", {})
            data["统计"]["持仓数"] = len(stocks)
            self.pool_dir.mkdir(parents=True, exist_ok=True)
            pool_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            return True
        except Exception as e:
            print(f"[PoolManager] 保存池失败 {pool_name}: {e}")
            return False

    # ── 需要自动排序的池（按综合分降序）─────────────────────
    _SCORE_SORT_POOLS = {"重点观察池", "快筛候选池", "边缘池"}

    def _maybe_sort_pool(self, data: dict, pool_name: str):
        """按综合分降序排列池内股票（无分值/空值的排最后）。"""
        if pool_name not in self._SCORE_SORT_POOLS:
            return
        stocks = data.get("stocks", [])
        if not stocks:
            return

        def sort_key(s):
            score = s.get("综合分")
            if score is None or score == "":
                return -1
            try:
                return float(score)
            except (TypeError, ValueError):
                return -1

        data["stocks"] = sorted(stocks, key=sort_key, reverse=True)
    
    def get_stocks(self, pool_name: str) -> List[Dict[str, Any]]:
        """获取指定池中的所有股票"""
        data = self.load_pool(pool_name)
        return data.get("stocks", [])
    
    def add_stock(self, pool_name: str, stock: Dict[str, Any], 
                 max_stocks: int = None) -> bool:
        """
        向池中添加股票
        
        Args:
            pool_name: 池名称
            stock: 股票信息字典
            max_stocks: 最大股票数量（None时从 POOL_CAPACITY_LIMITS 自动获取）
            
        Returns:
            是否成功
        """
        data = self.load_pool(pool_name)
        stocks = self.get_stocks(pool_name)
        
        # 获取股票代码（支持多种字段名）
        code = stock.get("股票代码") or stock.get("代码") or ""
        
        if not code:
            print(f"[PoolManager] 股票信息缺少代码: {stock}")
            return False
        
        # 检查是否已存在
        existing_codes = {s.get("股票代码") or s.get("代码", "") for s in stocks}
        if code in existing_codes:
            print(f"[PoolManager] 股票 {code} 已存在于 {pool_name}")
            return False
        
        # P2-1：容量限制（使用常量或参数）
        if max_stocks is None:
            max_stocks = self.POOL_CAPACITY_LIMITS.get(pool_name, 20)
        
        # 添加到列表（带去重）
        existing_codes = {s.get("代码", s.get("股票代码", "")) for s in stocks}
        stock_code = stock.get("代码", stock.get("股票代码", ""))
        if stock_code in existing_codes:
            print(f"[PoolManager] ⚠️ {stock_code} 已在 {pool_name} 中，跳过重复添加")
            return True
        stocks.append(stock)
        
        # 限制数量
        if len(stocks) > max_stocks:
            stocks = stocks[-max_stocks:]
            print(f"[PoolManager] ⚠️ {pool_name} 超容量({len(stocks)+1}>{max_stocks})，自动移除最旧标的")
        
        # 保存
        data["stocks"] = stocks  # 统一用 stocks，与 review_agent.py 保持一致
        data["统计"] = data.get("统计", {})
        data["统计"]["持仓数"] = len(stocks)
        data["统计"]["更新日期"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        return self.save_pool(pool_name, data)
    
    def remove_stock(self, pool_name: str, stock_code: str) -> bool:
        """
        从池中移除股票
        
        Args:
            pool_name: 池名称
            stock_code: 股票代码
            
        Returns:
            是否成功
        """
        data = self.load_pool(pool_name)
        stocks = self.get_stocks(pool_name)
        
        # 查找并移除（注意括号：先获取代码，再比较）
        new_stocks = [
            s for s in stocks
            if (s.get("股票代码") or s.get("代码", "")) != stock_code
        ]
        
        if len(new_stocks) == len(stocks):
            print(f"[PoolManager] 股票 {stock_code} 不存在于 {pool_name}")
            return False
        
        # 保存
        data["stocks"] = new_stocks
        data["统计"] = data.get("统计", {})
        data["统计"]["持仓数"] = len(new_stocks)
        data["统计"]["更新日期"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        return self.save_pool(pool_name, data)

    def add_to_holding(
        self,
        code: str,
        name: str,
        cost: float,
        market: str = None,
        remark: str = "",
        build_date: str = None,
    ) -> dict | None:
        """
        建仓——入持仓池的唯一入口。

        自动完成：
        1. 拉实时行情
        2. LLM 评估止损线 / 第一止盈 / 第二止盈
        3. 写入持仓池（含止损止盈字段）
        4. 返回完整建仓记录

        Args:
            code:      股票代码（6位纯数字）
            name:      股票名称
            cost:      成本价
            market:    市场（可选，自动从代码推断）
            remark:    备注
            build_date: 建仓日期（可选，默认今天）

        Returns:
            建仓记录 dict，失败返回 None
        """
        import re
        from market_agent import fetch_quotes, to_api

        today = build_date or datetime.now().strftime("%Y-%m-%d")

        # ── Step 1：推断市场 ──────────────────────────────
        if not market:
            market = "SZ" if code.startswith(("0", "3")) else "SH"

        # ── Step 2：检查是否已持仓 ────────────────────────
        holding_data = self.load_pool("持仓池")
        existing = {str(s.get("代码", "")): s for s in holding_data.get("stocks", [])}
        if code in existing:
            print(f"[PoolManager] {name}({code}) 已在持仓池中，跳过")
            return None

        # ── Step 3：拉实时行情 ─────────────────────────────
        api_code = to_api(code)
        try:
            quotes = fetch_quotes([api_code])
        except Exception as e:
            print(f"[PoolManager] 拉行情失败 {code}: {e}")
            quotes = []

        q = next((q for q in quotes if q.get("代码") == code), {})
        cur_price = q.get("现价") or cost
        chg_pct = q.get("涨跌幅", 0)
        pe = q.get("市盈率_TTM", "—")
        turnover = q.get("换手率", 0)
        profit = round(cur_price - cost, 2)
        profit_pct = round((cur_price - cost) / cost * 100, 1) if cost else 0

        # ── Step 4：LLM 评估止损止盈 ─────────────────────
        stop_loss, tp1, tp2, advice = self._eval_holding_limits(
            code, name, cost, cur_price, chg_pct, pe, turnover, profit_pct
        )

        # ── Step 5：构建记录 ───────────────────────────────
        record = {
            "代码": code,
            "名称": name,
            "市场": market,
            "成本价": cost,
            "盈亏额": profit,
            "盈亏比例": profit_pct,
            "建仓日期": today,
            "备注": remark or "建仓",
            "今日收盘": cur_price,
            "今日涨跌": f"{chg_pct:+.2f}%" if isinstance(chg_pct, float) else chg_pct,
            "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "止损线": stop_loss,
            "第一止盈": tp1,
            "第二止盈": tp2,
            "操作建议": advice,
        }

        # ── Step 6：写入持仓池 ─────────────────────────────
        if "入池记录" not in holding_data:
            holding_data["入池记录"] = []
        holding_data["入池记录"].append({
            "日期": today,
            "股票": name,
            "代码": code,
            "类型": "建仓",
            "成本价": cost,
            "备注": remark or "建仓",
        })

        holding_data["stocks"].append(record)
        holding_data["统计"] = holding_data.get("统计", {})
        holding_data["统计"]["持仓数"] = len(holding_data["stocks"])
        holding_data["统计"]["更新日期"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self.save_pool("持仓池", holding_data)

        print(f"[PoolManager] ✅ {name}({code}) 建仓成功")
        print(f"   成本:{cost} 现价:{cur_price}({chg_pct:+.2f}%) 浮盈:{profit_pct}%")
        print(f"   止损:{stop_loss} 一止:{tp1} 二止:{tp2} 操作:{advice}")
        return record

    # ── P0-2：S级操作池 T+1 过期清理 ──────────────────────
    def clean_expired_s_pool(self, max_age_days: int = 1) -> dict:
        """
        清理S级操作池中超过max_age_days的标的（T+1过期）
        
        Args:
            max_age_days: 最大停留天数，默认1天（T+1）
        
        Returns:
            {"removed": [...], "remaining": [...], "cleaned": bool}
        """
        data = self.load_pool("S级操作池")
        stocks = data.get("stocks", [])
        if not stocks:
            return {"removed": [], "remaining": [], "cleaned": False}
        
        today = datetime.now()
        removed = []
        remaining = []
        
        for stock in stocks:
            entry_date_str = stock.get("纳入日期", "")
            try:
                entry_date = datetime.strptime(entry_date_str, "%Y-%m-%d")
                age_days = (today - entry_date).days
                if age_days > max_age_days:
                    removed.append({
                        "代码": stock.get("代码", stock.get("股票代码", "?")),
                        "名称": stock.get("名称", stock.get("股票名称", "?")),
                        "综合分": stock.get("综合分", stock.get("综合评分", None)),
                        "纳入日期": entry_date_str,
                        "停留天数": age_days,
                        "driver_source": "S级过期降级",
                    })
                else:
                    remaining.append(stock)
            except ValueError:
                # 日期格式异常，保留
                remaining.append(stock)
        
        if removed:
            data["stocks"] = remaining
            data["历史记录"] = data.get("历史记录", [])
            data["历史记录"].append({
                "日期": today.strftime("%Y-%m-%d"),
                "类型": "T+1过期清理",
                "移除标的": [r["代码"] for r in removed],
            })
            self.save_pool("S级操作池", data)
            print(f"[PoolManager] 🧹 S级操作池 T+1清理：移除 {len(removed)} 只过期标的")
            for r in removed:
                print(f"   - {r['名称']}({r['代码']}) 停留{r['停留天数']}天")
        
        return {"removed": removed, "remaining": remaining, "cleaned": len(removed) > 0}

    def clean_expired_edge_pool(self, max_age_days: int = 45, min_score: float = 40) -> dict:
        """
        清理边缘池中过期或低评分标的（P3-边缘池清理）

        规则：
        1. 入池（纳入日期/降级时间）超过 max_age_days 天的标的自动移除
        2. 连续评分 < min_score 的标的自动移除

        Args:
            max_age_days: 最大停留天数，默认45天
            min_score: 最低评分阈值，低于此值的自动移除

        Returns:
            {"removed": [...], "remaining_count": int, "cleaned": bool}
        """
        data = self.load_pool("边缘池")
        stocks = data.get("stocks", [])
        if not stocks:
            return {"removed": [], "remaining_count": 0, "cleaned": False}

        today = datetime.now()
        removed = []
        remaining = []
        reasons = []

        for stock in stocks:
            code = stock.get("代码", stock.get("股票代码", "?"))
            name = stock.get("名称", stock.get("股票名称", "?"))
            score = stock.get("综合分", None)
            remove_reason = None

            # 规则1：检查入池时间（同时支持纳入日期 和 降级时间 两种字段）
            entry_date_str = stock.get("纳入日期", stock.get("降级时间", ""))
            if entry_date_str:
                try:
                    entry_date = datetime.strptime(entry_date_str, "%Y-%m-%d")
                    age_days = (today - entry_date).days
                    if age_days > max_age_days:
                        remove_reason = f"入池超过{max_age_days}天（{age_days}天）"
                except ValueError:
                    pass  # 日期格式异常，保留

            # 规则2：检查综合分（需明确 < min_score 才移除）
            if remove_reason is None and score is not None:
                try:
                    score_val = float(score)
                    if score_val < min_score:
                        remove_reason = f"综合分{score_val}<{min_score}"
                except (TypeError, ValueError):
                    pass  # 评分无法解析，保留

            if remove_reason:
                removed.append({
                    "代码": code,
                    "名称": name,
                    "评分": score,
                    "日期": entry_date_str,
                    "移除原因": remove_reason,
                    "driver_source": "边缘池过期清理",
                })
                reasons.append(f"  - {name}({code}): {remove_reason}")
            else:
                remaining.append(stock)

        if removed:
            data["stocks"] = remaining
            data["历史记录"] = data.get("历史记录", [])
            data["历史记录"].append({
                "日期": today.strftime("%Y-%m-%d"),
                "类型": "边缘池过期清理",
                "移除标的": [r["代码"] for r in removed],
                "移除详情": reasons,
            })
            # 更新统计
            data["统计"] = data.get("统计", {})
            data["统计"]["持仓数"] = len(remaining)
            data["统计"]["更新日期"] = today.strftime("%Y-%m-%d %H:%M:%S")
            self.save_pool("边缘池", data)
            print(f"[PoolManager] 🧹 边缘池清理：移除 {len(removed)} 只标的")
            for r in reasons:
                print(r)
        else:
            print(f"[PoolManager] ✅ 边缘池无需清理（{len(stocks)} 只均符合条件）")

        return {
            "removed": removed,
            "remaining_count": len(remaining),
            "cleaned": len(removed) > 0,
        }

    def evaluate_s_pool_history(self) -> dict:
        """
        评价 S级操作池历史推荐命中率。
        读取历史记录中每只标的 -> 查当前价 -> 计算涨跌幅 -> 标记命中/偏差。
        """
        data = self.load_pool("S级操作池")
        history = data.get("历史记录", [])
        if not history:
            return {"evaluated": 0, "hits": 0, "misses": 0, "avg_change": 0}

        try:
            from market_agent import fetch_quotes, to_api
        except Exception:
            return {"error": "market_agent 导入失败"}

        # 收集所有历史标的中未被评价的代码
        all_entries = []
        for record in history:
            stocks = record.get("标的", [])
            for s in stocks:
                if isinstance(s, str):
                    all_entries.append({"代码": s, "名称": s, "入场价": 0})
                elif isinstance(s, dict):
                    all_entries.append(s)

        if not all_entries:
            return {"evaluated": 0}

        # 查当前行情
        codes = [s["代码"] for s in all_entries if s.get("代码")]
        if not codes:
            return {"evaluated": 0}

        quotes = fetch_quotes([to_api(c) for c in codes])
        price_map = {q["代码"]: q.get("现价", 0) for q in quotes if q.get("代码")}

        # 逐条评价
        results = []
        hits = 0
        misses = 0
        changes = []
        for s in all_entries:
            code = s.get("代码", "")
            name = s.get("名称", "?")
            entry = s.get("入场价", 0)
            current = price_map.get(code, 0)
            if not entry or not current:
                continue
            change_pct = round((current - entry) / entry * 100, 2)
            changes.append(change_pct)
            if change_pct >= 3:
                verdict = "命中"
                hits += 1
            elif change_pct >= 0:
                verdict = "微涨"
            elif change_pct >= -3:
                verdict = "微跌"
            else:
                verdict = "偏差"
                misses += 1
            results.append({
                "代码": code, "名称": name,
                "入场价": entry, "最新价": current,
                "涨跌幅": change_pct, "评价": verdict,
            })

        # 汇总
        avg_change = round(sum(changes) / len(changes), 2) if changes else 0
        total = hits + misses
        hit_rate = round(hits / total * 100, 1) if total > 0 else 0

        return {
            "evaluated": len(results),
            "hits": hits,
            "misses": misses,
            "hit_rate": hit_rate,
            "avg_change": avg_change,
            "details": results,
        }

    def _eval_holding_limits(
        self, code: str, name: str, cost: float,
        cur_price: float, chg_pct: float, pe, turnover: float, profit_pct: float
    ) -> tuple:
        """
        调用 LLM 评估持仓的止损线、第一止盈、第二止盈。
        返回 (止损线, 第一止盈, 第二止盈, 操作建议)
        """
        import requests

        holding_text = (
            f"- {name}({code}) | 成本:{cost} | 现价:{cur_price}({chg_pct:+.2f}%) | "
            f"浮盈:{profit_pct}% | 持仓:当天 | PE:{pe} | 换手:{turnover:.2f}%"
        )

        prompt = f"""## 持仓评估任务

请对以下持仓股票评估止损线和止盈目标：

{holding_text}

要求：结合大盘环境、行业景气度、个股驱动逻辑，给出：
- 止损线（具体价格 + 距成本%）
- 第一止盈目标（距现价涨幅%）
- 第二止盈目标（距现价涨幅%）
- 操作建议（持有/减仓/清仓）

直接输出结论，每只股票格式如下，不需要开场白：
## {name}（{code}）
止损线：XX元（-X%）
第一止盈：XX元（+X%）
第二止盈：XX元（+X%）
操作建议：持有/减仓/清仓"""

        system = """你是专业的A股持仓管理专家。
短线风格、快进快出。单笔亏损不超5%，止盈分两档（+8%、+15%）。
输出格式严格如下（每只股票）：
## 股票名称（代码）
止损线：XX元（-X%）
第一止盈：XX元（+X%）
第二止盈：XX元（+X%）
操作建议：持有/减仓/清仓"""

        try:
            from config_loader import get_config
            cfg = get_config()
            api_key = cfg.get("llm", {}).get("api_key", "") or cfg.get("opencode", {}).get("api_key", "")
            api_url = cfg.get("llm", {}).get("api_url", "") or cfg.get("opencode", {}).get("api_url", "")
            model = cfg.get("llm", {}).get("model", "") or cfg.get("opencode", {}).get("model", "")
            if not api_url:
                print("[PoolManager] ⚠️  未配置 LLM API URL，跳过止损止盈评估，使用硬编码兜底")
                return None, None, None, "持有"
            if not api_key:
                print("[PoolManager] ⚠️  未配置 LLM API Key，跳过止损止盈评估，使用硬编码兜底")
                return None, None, None, "持有"
        except Exception as e:
            print(f"[PoolManager] ⚠️  config_loader 加载失败 ({e})，跳过止损止盈评估，使用硬编码兜底")
            return None, None, None, "持有"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 600,
            "temperature": 0.3,
        }

        for attempt in range(3):
            try:
                r = requests.post(api_url, json=payload, headers=headers, timeout=60)
                if r.status_code == 200:
                    data = r.json()
                    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    break
                elif r.status_code == 429:
                    import time; time.sleep(2 ** attempt)
                    continue
                else:
                    text = ""
                    break
            except Exception:
                text = ""
                break

        # ── 解析 LLM 结果 ─────────────────────────────────
        stop_loss, tp1, tp2 = None, None, None
        advice = "持有"

        for line in text.split("\n"):
            m = re.match(r"止损线[：:]\s*([\d.]+)", line)
            if m:
                stop_loss = round(float(m.group(1)), 2)
            m = re.match(r"第一止盈[：:]\s*([\d.]+)", line)
            if m:
                tp1 = round(float(m.group(1)), 2)
            m = re.match(r"第二止盈[：:]\s*([\d.]+)", line)
            if m:
                tp2 = round(float(m.group(1)), 2)
            m = re.match(r"操作建议[：:]\s*(\S+)", line)
            if m:
                advice = m.group(1)

        # ── 兜底：硬编码计算 ─────────────────────────────
        if not stop_loss:
            stop_loss = round(cost * 0.95, 2)   # -5%
        if not tp1:
            tp1 = round(cur_price * 1.08, 2)    # +8%
        if not tp2:
            tp2 = round(cur_price * 1.15, 2)    # +15%

        return stop_loss, tp1, tp2, advice

    # ── 重点观察池评估字段 ──────────────────────────────
    def enrich_key_watch(self, code: str, name: str) -> dict:
        """
        为入重点观察池的股票评估买入区和止损/目标。
        拉实时行情后调用 LLM，补充推荐买入价、止损触发、目标价。
        返回 dict（含推荐买入价/止损触发/第一目标/第二目标/操作建议）。
        若评估失败返回空 dict。
        """
        from market_agent import fetch_quotes, to_api

        api_code = to_api(code)
        try:
            quotes = fetch_quotes([api_code])
        except Exception as e:
            print(f"[PoolManager] 重点观察池拉行情失败 {code}: {e}")
            quotes = []

        q = next((q for q in quotes if q.get("代码") == code), {})
        cur_price = q.get("现价", 0)
        chg_pct = q.get("涨跌幅", 0)
        pe = q.get("市盈率_TTM", "—")
        turnover = q.get("换手率", 0)

        # ── LLM 评估买入区 ───────────────────────────────
        buy_zone, stop_trigger, target1, target2, advice = self._eval_key_watch_limits(
            code, name, cur_price, chg_pct, pe, turnover
        )

        return {
            "推荐买入价": buy_zone,
            "止损触发": stop_trigger,
            "第一目标": target1,
            "第二目标": target2,
            "操作建议": advice,
            "今日收盘": cur_price,
            "今日涨跌": f"{chg_pct:+.2f}%" if isinstance(chg_pct, float) else chg_pct,
            "PE": pe,
            "换手率": turnover,
            "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

    @staticmethod
    def _call_llm_for_limits(stocks_text: str, system: str, prompt: str, timeout: int = 120, max_tokens: int = 600) -> str:
        """
        封装 LLM 的 requests.post + 重试 + 配置读取（P0-2 共享函数）。
        返回 LLM 响应文本，失败返回空字符串。
        修复 P0-降级延迟 #300757：增加多层 fallback，确保降级逻辑不因 API 缺失而跳过。
        """
        import requests
        import os

        # ── 第1层：config_loader ─────────────────────────────────
        api_key = ""
        api_url = ""
        model = ""
        try:
            from config_loader import get_config
            cfg = get_config()
            api_key = cfg.get("llm", {}).get("api_key", "") or cfg.get("opencode", {}).get("api_key", "")
            api_url = cfg.get("llm", {}).get("api_url", "") or cfg.get("opencode", {}).get("api_url", "")
            model = cfg.get("llm", {}).get("model", "") or cfg.get("opencode", {}).get("model", "")
        except Exception:
            pass

        # ── 第2层：环境变量 ──────────────────────────────────────
        if not api_key:
            api_key = os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY", "") or \
                      os.environ.get("OPENCODE_ZEN_API_KEY", "") or \
                      os.environ.get("OPENAI_API_KEY", "")
        if not api_url:
            api_url = os.environ.get("GOOGLE_GENERATIVE_AI_API_URL", "") or \
                      os.environ.get("OPENCODE_ZEN_API_URL", "") or \
                      "https://openai.azure.com"  # fallback placeholder
        if not model:
            model = os.environ.get("GOOGLE_GENERATIVE_AI_MODEL", "") or \
                    os.environ.get("OPENCODE_ZEN_MODEL", "") or \
                    "gemini-1.5-flash"

        # ── 第3层：硬编码兜底（OpenCode Zen 免费端点）──────────────
        if not api_url or not api_key:
            # P0-降级延迟修复：即使无API也返回结构化提示，让调用方能走硬编码降级逻辑
            print("[PoolManager] ⚠️  LLM API 完全未配置，跳过 LLM 评估，使用硬编码兜底降级规则")
            return ""

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }

        for attempt in range(3):
            try:
                r = requests.post(api_url, json=payload, headers=headers, timeout=timeout)
                if r.status_code == 200:
                    data = r.json()
                    return data.get("choices", [{}])[0].get("message", {}).get("content", "")
                elif r.status_code == 429:
                    import time; time.sleep(2 ** attempt)
                    continue
                else:
                    break
            except Exception:
                break
        return ""

    def _eval_key_watch_limits(
        self, code: str, name: str,
        cur_price: float, chg_pct: float, pe, turnover: float
    ) -> tuple:
        """
        调用 LLM 评估重点观察池股票的买入区/止损触发/目标价。
        返回 (推荐买入价, 止损触发, 第一目标, 第二目标, 操作建议)
        """
        import re

        holding_text = (
            f"- {name}({code}) | 现价:{cur_price}({chg_pct:+.2f}%) | "
            f"PE:{pe} | 换手:{turnover:.2f}%"
        )

        prompt = f"""## 重点观察池建仓前评估

请评估以下股票作为重点观察对象的建仓前参考（短线风格）：

{holding_text}

要求：结合大盘环境、行业景气度、技术形态，给出：
- 推荐买入价（现价附近或回调支撑位，具体价格）
- 止损触发价（买入后跌破此价则放弃关注，具体价格）
- 第一目标价（距买入价约+10%，具体价格）
- 第二目标价（距买入价约+20%，具体价格）
- 操作建议（买入/观望/回避）

直接输出结论，不需要开场白：
## {name}（{code}）
推荐买入价：XX元
止损触发：XX元
第一目标：XX元
第二目标：XX元
操作建议：买入/观望/回避"""

        system = """你是专业的A股短线交易专家。
结合量价形态、技术支撑、大盘环境评估买入区。
止损触发：买入价下方5-8%，跌破代表逻辑失效需放弃。
目标：+10%（第一目标）、+20%（第二目标）。
输出格式严格如下：
## 股票名称（代码）
推荐买入价：XX元
止损触发：XX元
第一目标：XX元
第二目标：XX元
操作建议：买入/观望/回避"""

        # 共享 LLM 调用（P0-2）
        text = PoolManager._call_llm_for_limits(holding_text, system, prompt, timeout=60)
        if not text:
            print("[PoolManager] ⚠️  LLM 调用失败/未配置，使用硬编码兜底")
            buy_zone = round(cur_price * 1.01, 2) if cur_price else None
            stop_trigger = round(buy_zone * 0.95, 2) if buy_zone else None
            target1 = round(buy_zone * 1.10, 2) if buy_zone else None
            target2 = round(buy_zone * 1.20, 2) if buy_zone else None
            return buy_zone, stop_trigger, target1, target2, "观望"

        # ── 解析 LLM 结果 ─────────────────────────────────
        buy_zone, stop_trigger, target1, target2 = None, None, None, None
        advice = "观望"

        for line in text.split("\n"):
            m = re.match(r"推荐买入价[：:]\s*([\d.]+)", line)
            if m:
                buy_zone = round(float(m.group(1)), 2)
            m = re.match(r"止损触发[：:]\s*([\d.]+)", line)
            if m:
                stop_trigger = round(float(m.group(1)), 2)
            m = re.match(r"第一目标[：:]\s*([\d.]+)", line)
            if m:
                target1 = round(float(m.group(1)), 2)
            m = re.match(r"第二目标[：:]\s*([\d.]+)", line)
            if m:
                target2 = round(float(m.group(1)), 2)
            m = re.match(r"操作建议[：:]\s*(\S+)", line)
            if m:
                advice = m.group(1)

        # ── 兜底：基于现价计算 ─────────────────────────
        if not buy_zone:
            buy_zone = round(cur_price * 1.01, 2) if cur_price else None
        if not stop_trigger:
            stop_trigger = round(buy_zone * 0.95, 2) if buy_zone else None
        if not target1:
            target1 = round(buy_zone * 1.10, 2) if buy_zone else None
        if not target2:
            target2 = round(buy_zone * 1.20, 2) if buy_zone else None

        return buy_zone, stop_trigger, target1, target2, advice

    def move_stock(self, from_pool: str, to_pool: str, stock_code: str) -> bool:
        """
        将股票从一个池移动到另一个池
        
        Args:
            from_pool: 源池名称
            to_pool: 目标池名称
            stock_code: 股票代码
            
        Returns:
            是否成功
        """
        # 从源池获取股票
        from_data = self.load_pool(from_pool)
        stocks = self.get_stocks(from_pool)
        
        stock = next(
            (s for s in stocks 
             if (s.get("股票代码") or s.get("代码", "")) == stock_code),
            None
        )
        
        if not stock:
            print(f"[PoolManager] 股票 {stock_code} 不存在于 {from_pool}")
            return False
        
        # 移除 from 源池
        if not self.remove_stock(from_pool, stock_code):
            return False

        # 重置入池日期（新池计时器刷新）
        date_field = self._get_pool_date_field(to_pool)
        if date_field:
            stock[date_field] = datetime.now().strftime("%Y-%m-%d")

        # 添加到目标池
        return self.add_stock(to_pool, stock)

    def _get_pool_date_field(self, pool_name: str) -> str:
        """返回指定池的日期字段名（用于重置入池日期）"""
        mapping = {
            "快筛候选池": "纳入日期",
            "重点观察池": "纳入日期",
            # 接近决策池已停用
            "边缘池": "纳入日期",
            "持仓池": "建仓日期",
        }
        return mapping.get(pool_name, "纳入日期")
    
    def get_all_pools(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        获取所有池的数据
        
        Returns:
            池名称到股票列表的字典
        """
        result = {}
        for pool_name in self.POOL_NAMES:
            result[pool_name] = self.get_stocks(pool_name)
        return result
    
    def get_pool_summary(self) -> Dict[str, int]:
        """
        获取所有池的摘要统计
        
        Returns:
            池名称到股票数量的字典
        """
        result = {}
        for pool_name in self.POOL_NAMES:
            stocks = self.get_stocks(pool_name)
            result[pool_name] = len(stocks)
        return result
    
    def _empty_pool(self, pool_name: str) -> Dict[str, Any]:
        """创建空池结构"""
        pool_definitions = {
            "快筛候选池": "收纳快筛初选、尚未审查的股票",
            "重点观察池": "审查通过，值得重点跟踪的对象",
            "边缘池": "审查后降级，暂不符合条件的对象，满足条件可回归候选池",
            "持仓池": "当前真实持仓",
        }
        
        return {
            "池名称": pool_name,
            "池定义": pool_definitions.get(pool_name, ""),
            "stocks": [],
            "历史记录": [],
            "统计": {
                "创建日期": datetime.now().strftime("%Y-%m-%d"),
                "盈利次数": 0,
                "亏损次数": 0,
                "持仓数": 0,
                "更新日期": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }
    
    # ── 评分等级转换 ────────────────────────────────────
    def _score_to_level(self, score: float) -> str:
        """将综合分转换为评级等级"""
        if score >= 90: return "S级"
        if score >= 70: return "A级"
        if score >= 65: return "B级(黄色预警)"
        if score >= 55: return "C级(观察区)"
        return "D级(淘汰)"

    def _scan_and_downgrade(self, data: dict, pool_name: str = "重点观察池") -> list:
        """扫描池中评分<65的股票，自动降级到边缘池。返回被降级的股票列表。"""
        stocks = data.get("stocks", [])
        to_demote = []
        remaining = []
        for s in stocks:
            score = float(s.get("综合分") or 0)
            if score == 0:
                remaining.append(s)
                continue
            level = self._score_to_level(score) if hasattr(self, '_score_to_level') else None
            if score < 65:  # C级(55-64) + D级(<55) 需降级
                to_demote.append(s)
                print(f"  [PoolManager] ⬇️ 降级 {s.get('名称','')}({s.get('代码','')}) 综合分{score} < 65 → 边缘池")
            else:
                remaining.append(s)

        if to_demote:
            data["stocks"] = remaining
            # 写入边缘池
            edge_pool = self.load_pool("边缘池")
            edge_stocks = edge_pool.get("stocks", [])
            for item in to_demote:
                edge_stocks.append({
                    "代码": item.get("代码", ""),
                    "名称": item.get("名称", ""),
                    "综合分": float(item.get("综合分", 0)) if isinstance(item.get("综合分"), (int, float)) else 0,
                    "降级时间": datetime.now().strftime("%Y-%m-%d"),
                    "降级原因": f"存量扫描：综合分{item.get('综合分','?')} < 65，自动降级"
                })
            edge_pool["stocks"] = edge_stocks
            edge_pool["统计"]["累计进入"] = edge_pool.get("统计", {}).get("累计进入", 0) + len(to_demote)
            self.save_pool("边缘池", edge_pool)
            # 更新统计
            data["统计"]["持仓数"] = len(remaining)
            data["统计"]["更新日期"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return to_demote

    # ── P0：重点观察池实时价格刷新 ──────────────────────────
    def refresh_key_watch_prices(self) -> list:
        """
        刷新重点观察池所有股票的实时价格。
        读取重点观察池 JSON -> 提取代码 -> fetch_quotes() 获取实时行情
        -> 更新 今日收盘/今日涨跌/更新时间 -> 写回 JSON。

        Returns:
            成功刷新的股票代码列表
        """
        from market_agent import fetch_quotes, to_api

        data = self.load_pool("重点观察池")
        stocks = data.get("stocks", [])
        if not stocks:
            print("[PoolManager] 重点观察池为空，无需刷新")
            return []

        codes = []
        for s in stocks:
            code = s.get("代码", s.get("股票代码", ""))
            if code:
                codes.append(code)

        if not codes:
            print("[PoolManager] 重点观察池无有效股票代码")
            return []

        # 拉实时行情
        api_codes = [to_api(c) for c in codes]
        try:
            quotes = fetch_quotes(api_codes)
        except Exception as e:
            print(f"[PoolManager] 重点观察池行情刷新失败: {e}")
            return []

        qmap = {q["代码"]: q for q in quotes if q.get("代码")}
        refreshed = []

        for stock in stocks:
            code = stock.get("代码", stock.get("股票代码", ""))
            q = qmap.get(code)
            if not q:
                continue
            now_price = q.get("现价")
            chg_pct = q.get("涨跌幅", 0)
            if now_price is not None:
                stock["今日收盘"] = now_price
                stock["今日涨跌"] = f"{chg_pct:+.2f}%" if isinstance(chg_pct, float) else chg_pct
                stock["换手率"] = q.get("换手率", stock.get("换手率", 0))
                stock["量比"] = q.get("量比", stock.get("量比", 0))
                stock["成交量_手"] = q.get("成交量", stock.get("成交量_手", 0))
                stock["振幅"] = q.get("振幅", stock.get("振幅", 0))
                stock["更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                # 止损检查（根因5防护）：收盘价低于止损线时自动标记
                stop_loss = stock.get("止损触发", stock.get("止损线", 0))
                if isinstance(stop_loss, (int, float)) and stop_loss > 0:
                    if now_price < stop_loss:
                        stock["操作建议"] = "已跌破止损，建议调出"
                        print(f"  [止损] {stock.get('名称','?')}({code}) 收盘{now_price}<止损{stop_loss}, 已标记")
                    else:
                        # 价格已回升至止损线上，清除过期标记
                        if stock.get("操作建议") == "已跌破止损，建议调出":
                            stock["操作建议"] = "正常"
                            print(f"  [止损解除] {stock.get('名称','?')}({code}) 收盘{now_price}>止损{stop_loss}, 标记清除")
                # ── 评分时间衰减：入池>7天且评分未更新的存量股 ⭐ v5.92 ──
                entry_date = stock.get("纳入日期", "")
                orig_score = stock.get("综合分", 0)
                if entry_date and isinstance(orig_score, (int, float)) and orig_score > 0:
                    try:
                        days_in_pool = (datetime.now() - datetime.strptime(entry_date, "%Y-%m-%d")).days
                    except ValueError:
                        days_in_pool = 999  # 日期格式异常，强制衰减
                    if days_in_pool > 7:
                        # 基础衰减：0.5分/天，上限15分
                        decay = min(days_in_pool * 0.5, 15)
                        # 今日上涨则衰减减半（趋势向好）
                        chg_str = stock.get("今日涨跌", "")
                        if chg_str and "+" in str(chg_str):
                            decay *= 0.5
                        new_score = max(round(orig_score - decay), 40)
                        if new_score != orig_score:
                            stock["综合分"] = new_score
                            stock["评分最后更新"] = f"{orig_score}→{new_score}(入池{days_in_pool}天)"
                            print(f"  [评分衰减] {stock.get('名称','?')}({code}) {orig_score}→{new_score} (入池{days_in_pool}天)")
                refreshed.append(code)

        # 更新统计（P0：即使行情刷新失败也执行降级扫描）
        data["统计"] = data.get("统计", {})
        data["统计"]["更新日期"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 扫描评分<65的存量股，自动降级
        demoted = self._scan_and_downgrade(data)

        if refreshed:
            # ── 止损自动降级：跌破止损且评分≥65的存量股移入边缘池 ──
            stop_loss_demoted = []
            remaining_stocks = []
            for s in data.get("stocks", []):
                if s.get("操作建议") == "已跌破止损，建议调出":
                    stop_loss_demoted.append(s)
                    print(f"  ⬇️ [止损降级] {s.get('名称','?')}({s.get('代码','?')}) → 边缘池")
                else:
                    remaining_stocks.append(s)
            if stop_loss_demoted:
                data["stocks"] = remaining_stocks
                edge_pool = self.load_pool("边缘池")
                edge_stocks = edge_pool.get("stocks", [])
                for item in stop_loss_demoted:
                    edge_stocks.append({
                        "代码": item.get("代码", ""),
                        "名称": item.get("名称", ""),
                        "降级时间": datetime.now().strftime("%Y-%m-%d"),
                        "降级原因": f"止损触发：收盘价{item.get('今日收盘','?')}<止损线{item.get('止损触发','?')}"
                    })
                edge_pool["stocks"] = edge_stocks[-20:]  # 最多20只
                self.save_pool("边缘池", edge_pool)
                print(f"  [PoolManager] ✅ {len(stop_loss_demoted)} 只跌破止损的股票已移入边缘池")
            # ────────────────────────────────────────────────────────
            print(f"[PoolManager] ✅ 重点观察池价格刷新完成: {len(refreshed)}/{len(stocks)} 只股票")

        self.save_pool("重点观察池", data)

        # ── P1：S级操作池止损检查（根因2修复：扩展止损覆盖范围）──
        s_pool_data = self.load_pool("S级操作池")
        s_stocks = s_pool_data.get("stocks", [])
        if s_stocks:
            s_stop_loss_warnings = []
            s_demoted = []
            for s in s_stocks:
                s_code = s.get("代码", s.get("股票代码", ""))
                s_name = s.get("名称", "?")
                s_stop = s.get("止损触发", s.get("止损线", 0))
                s_price = s.get("今日收盘", s.get("最新价", 0))
                if s_stop and s_price and isinstance(s_stop, (int, float)) and s_stop > 0:
                    if s_price < s_stop:
                        warn = f"⚠️ [S级] {s_name}({s_code}) 现价{s_price}<止损{s_stop}"
                        s_stop_loss_warnings.append(warn)
                        print(f"[PoolManager] {warn}")
                        s["操作建议"] = "⚠️ 已跌破止损，建议调出"
                    elif s.get("操作建议") == "已跌破止损，建议调出":
                        s["操作建议"] = "正常"
                        print(f"[S级止损解除] {s_name}({s_code}) 现价{s_price}>止损{s_stop}")
            if s_stop_loss_warnings:
                s_pool_data["统计"] = s_pool_data.get("统计", {})
                s_pool_data["统计"]["S级止损告警"] = s_stop_loss_warnings
                self.save_pool("S级操作池", s_pool_data)
                print(f"[PoolManager] ⚠️ S级操作池共 {len(s_stop_loss_warnings)} 只触发止损告警")
                # 跌破止损的S级标的降级到边缘池
                for s in s_stocks:
                    if s.get("操作建议") == "⚠️ 已跌破止损，建议调出":
                        s_code = s.get("代码", s.get("股票代码", ""))
                        s_name = s.get("名称", "?")
                        self.add_stock("边缘池", {
                            "代码": s_code,
                            "名称": s_name,
                            "综合分": 65,
                            "纳入日期": datetime.now().strftime("%Y-%m-%d"),
                            "驱动来源": "S级操作池止损降级",
                            "核心逻辑": f"S级止损触发，现价{s.get('今日收盘', s.get('最新价', '?'))}<止损线{s_stop}",
                        })
                        s_pool_data["stocks"].remove(s)
                if s_pool_data["stocks"]:
                    self.save_pool("S级操作池", s_pool_data)
        # ────────────────────────────────────────────────────────

        return refreshed

    # ── P1：重点观察池信心度补填 ──────────────────────────
    def enrich_confidence_for_existing_stocks(self) -> int:
        """
        为重点观察池中信心度为空的存量股票推算信心度。
        规则（基于综合分）：
              - 80分+  → "高"
              - 70-79分 → "中高"
              - 60-74分 → "中"
              - <60分   → "低"

        Returns:
                补填的股票数量
        """
        data = self.load_pool("重点观察池")
        stocks = data.get("stocks", [])
        if not stocks:
            return 0

        updated = 0
        for stock in stocks:
            confidence = stock.get("信心度", "")
            if confidence and confidence.strip():
                continue  # 已有信心度，跳过
            score = stock.get("综合分")
            if score is None or score == "":
                continue
            try:
                score_val = float(score)
            except (TypeError, ValueError):
                continue

            if score_val >= 80:
                stock["信心度"] = "高"
            elif score_val >= 70:
                stock["信心度"] = "中高"
            elif score_val >= 60:
                stock["信心度"] = "中"
            else:
                stock["信心度"] = "低"
            updated += 1

        if updated > 0:
            self.save_pool("重点观察池", data)
            print(f"[PoolManager] ✅ 重点观察池信心度补填完成: {updated} 只股票")

        return updated

    # ── P1：持仓池浮盈刷新 ──────────────────────────────
    def refresh_holdings_prices(self) -> list:
        """
        刷新持仓池所有股票的最新价格及浮盈。
        读取持仓池 JSON -> 提取代码 -> fetch_quotes() 获取实时行情
        -> 计算盈亏额和盈亏比例 -> 更新字段 -> 写回 JSON。

        Returns:
            成功刷新的股票代码列表
        """
        from market_agent import fetch_quotes, to_api

        data = self.load_pool("持仓池")
        stocks = data.get("stocks", [])
        if not stocks:
            print("[PoolManager] 持仓池为空，无需刷新")
            return []

        codes = []
        for s in stocks:
            code = s.get("代码", "")
            if code:
                codes.append(code)

        if not codes:
            print("[PoolManager] 持仓池无有效股票代码")
            return []

        # 拉实时行情
        api_codes = [to_api(c) for c in codes]
        try:
            quotes = fetch_quotes(api_codes)
        except Exception as e:
            print(f"[PoolManager] 持仓池行情刷新失败: {e}")
            return []

        qmap = {q["代码"]: q for q in quotes if q.get("代码")}
        refreshed = []
        stop_loss_warnings = []

        for stock in stocks:
            code = stock.get("代码", "")
            q = qmap.get(code)
            if not q:
                continue
            cost = stock.get("成本价", 0)
            now_price = q.get("现价")
            chg_pct = q.get("涨跌幅", 0)
            if now_price is None:
                continue

            # 计算实际盈亏（总盈亏=每股盈亏×持仓股数）
            profit = round(now_price - cost, 2)
            shares = stock.get("买入股数", 0)
            if shares and shares > 0:
                total_profit = round(profit * shares, 2)
            else:
                total_profit = profit  # 无股数时使用每股盈亏作为兜底
            profit_pct = round((now_price - cost) / cost * 100, 2) if cost else 0

            stock["今日收盘"] = now_price
            stock["今日涨跌"] = f"{chg_pct:+.2f}%" if isinstance(chg_pct, float) else chg_pct
            stock["换手率"] = q.get("换手率", stock.get("换手率", 0))
            stock["量比"] = q.get("量比", stock.get("量比", 0))
            stock["成交量_手"] = q.get("成交量", stock.get("成交量_手", 0))
            stock["振幅"] = q.get("振幅", stock.get("振幅", 0))
            stock["盈亏额"] = total_profit
            stock["盈亏比例"] = profit_pct
            stock["更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            refreshed.append(code)

            # P0-2: 止损检查
            name = stock.get("名称", "?")
            stop_loss = stock.get("止损线", 0)
            if stop_loss > 0 and now_price < stop_loss:
                warning = f"⚠️ 止损警告：{name}({code}) 收盘价{now_price}已跌破止损线{stop_loss}（-{(stop_loss-now_price)/stop_loss*100:.1f}%）"
                print(f"[PoolManager] {warning}")
                stock["操作建议"] = "⚠️ 已跌破止损，建议执行止损"
                stop_loss_warnings.append(warning)

        if refreshed:
            # 更新统计
            data["统计"] = data.get("统计", {})
            data["统计"]["持仓数"] = len(stocks)
            data["统计"]["更新日期"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 同时更新总盈亏（从历史持仓计算）
            history = data.get("历史持仓", [])
            total_pnl = sum(h.get("盈亏额", 0) or 0 for h in history)
            data["统计"]["总盈亏"] = round(total_pnl, 2)
            data["统计"]["盈利次数"] = sum(1 for h in history if (h.get("盈亏额", 0) or 0) > 0)
            data["统计"]["亏损次数"] = sum(1 for h in history if (h.get("盈亏额", 0) or 0) < 0)
            # 同时更新资金配置中的持仓市值
            cfg = data.get("资金配置", {})
            if cfg:
                total_market_value = sum(
                    s.get("今日收盘", 0) * s.get("买入股数", 0)
                    for s in stocks if s.get("买入股数")
                )
                if total_market_value:
                    cfg["持仓市值"] = total_market_value
                    total_funds = cfg.get("总资金", 100000)
                    cfg["持仓比例"] = round(total_market_value / total_funds * 100, 2) if total_funds else 0
                    cfg["可用资金"] = round(total_funds - total_market_value, 2)

            # 如果存在止损警告，强制更新统计数据
            if stop_loss_warnings:
                data["统计"]["止损告警"] = stop_loss_warnings
                data["统计"]["止损告警时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # ── P0-2: 跌破止损的持仓股自动降级到边缘池 ────
            demoted = []
            for s in stocks[:]:  # 用副本遍历
                if s.get("操作建议", "") == "⚠️ 已跌破止损，建议执行止损":
                    code = s.get("代码", "")
                    name = s.get("名称", "")
                    edge_stock = {
                        "代码": code,
                        "名称": name,
                        "综合分": 65,
                        "纳入日期": datetime.now().strftime("%Y-%m-%d"),
                        "驱动来源": "持仓池止损降级",
                        "核心逻辑": f"持仓池止损触发，收盘价跌破止损线{s.get('止损线', 0)}",
                    }
                    # 止损降级时记录到历史持仓（防止交易数据丢失）
                    history_record = {
                        "代码": s.get("代码", ""),
                        "名称": s.get("名称", ""),
                        "市场": s.get("市场", ""),
                        "建仓日期": s.get("建仓日期", ""),
                        "建仓价": s.get("建仓价", s.get("成本价", 0)),
                        "卖出日期": datetime.now().strftime("%Y-%m-%d"),
                        "卖出价": s.get("今日收盘", 0),
                        "持仓股数": s.get("买入股数", s.get("持仓股数", 0)),
                        "买入金额": s.get("买入金额", 0),
                        "卖出金额": round(s.get("今日收盘", 0) * s.get("买入股数", s.get("持仓股数", 0)), 2),
                        "盈亏额": s.get("盈亏额", 0),
                        "盈亏比例": s.get("盈亏比例", 0),
                        "止损原因": "跌破止损线" + str(s.get("止损线", "")),
                        "卖出时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    }
                    if "历史持仓" not in data:
                        data["历史持仓"] = []
                    data["历史持仓"].append(history_record)
                    self.add_stock("边缘池", edge_stock)
                    stocks.remove(s)
                    demoted.append(f"{name}({code})")
                    print(f"[持仓降级] ⬇️ {name}({code}) → 边缘池（止损触发）")
            if demoted:
                print(f"[PoolManager] 🧹 持仓池止损降级：移除 {len(demoted)} 只")

            self.save_pool("持仓池", data)
            print(f"[PoolManager] ✅ 持仓池价格刷新完成: {len(refreshed)}/{len(stocks)} 只股票")
            if stop_loss_warnings:
                print(f"[PoolManager] ⚠️ 共 {len(stop_loss_warnings)} 只股票触发止损告警")
                for w in stop_loss_warnings:
                    print(f"  {w}")

        return refreshed

    # ── P1：快筛候选池价格刷新 ──────────────────────────
    def refresh_screen_candidate_prices(self) -> list:
        """
        刷新快筛候选池所有股票的实时价格。
        读取快筛候选池 JSON -> 提取代码 -> fetch_quotes() 获取实时行情
        -> 更新 今日收盘/今日涨跌/更新时间 -> 写回 JSON。

        Returns:
            成功刷新的股票代码列表
        """
        from market_agent import fetch_quotes, to_api

        data = self.load_pool("快筛候选池")
        stocks = data.get("stocks", [])
        if not stocks:
            print("[PoolManager] 快筛候选池为空，无需刷新")
            return []

        codes = []
        for s in stocks:
            code = s.get("代码", s.get("股票代码", ""))
            if code:
                codes.append(code)

        if not codes:
            print("[PoolManager] 快筛候选池无有效股票代码")
            return []

        # 拉实时行情
        api_codes = [to_api(c) for c in codes]
        try:
            quotes = fetch_quotes(api_codes)
        except Exception as e:
            print(f"[PoolManager] 快筛候选池行情刷新失败: {e}")
            return []

        qmap = {q["代码"]: q for q in quotes if q.get("代码")}
        refreshed = []

        for stock in stocks:
            code = stock.get("代码", stock.get("股票代码", ""))
            q = qmap.get(code)
            if not q:
                continue
            now_price = q.get("现价")
            chg_pct = q.get("涨跌幅", 0)
            if now_price is not None:
                stock["今日收盘"] = now_price
                stock["今日涨跌"] = f"{chg_pct:+.2f}%" if isinstance(chg_pct, float) else chg_pct
                stock["换手率"] = q.get("换手率", stock.get("换手率", 0))
                stock["量比"] = q.get("量比", stock.get("量比", 0))
                stock["成交量_手"] = q.get("成交量", stock.get("成交量_手", 0))
                stock["振幅"] = q.get("振幅", stock.get("振幅", 0))
                stock["更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                refreshed.append(code)

        # 更新统计（P0：即使行情刷新失败也执行降级扫描）
        data["统计"] = data.get("统计", {})
        data["统计"]["更新日期"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 扫描评分<65的存量股，自动降级
        self._scan_and_downgrade(data, "快筛候选池")

        if refreshed:
            print(f"[PoolManager] ✅ 快筛候选池价格刷新完成: {len(refreshed)}/{len(stocks)} 只股票")

        self.save_pool("快筛候选池", data)
        return refreshed

    # ── P1：S级操作池价格刷新 ──────────────────────────
    def refresh_s_operation_prices(self) -> list:
        """
        刷新S级操作池所有股票的实时价格。
        读取S级操作池 JSON -> 提取代码 -> fetch_quotes() 获取实时行情
        -> 更新 今日收盘/今日涨跌/更新时间 -> 写回 JSON。
        处理文件不存在的情况（空池/未创建）。

        Returns:
            成功刷新的股票代码列表
        """
        from market_agent import fetch_quotes, to_api

        data = self.load_pool("S级操作池")
        stocks = data.get("stocks", [])
        if not stocks:
            print("[PoolManager] S级操作池为空，无需刷新")
            return []

        codes = []
        for s in stocks:
            code = s.get("代码", s.get("股票代码", ""))
            if code:
                codes.append(code)

        if not codes:
            print("[PoolManager] S级操作池无有效股票代码")
            return []

        # 拉实时行情
        api_codes = [to_api(c) for c in codes]
        try:
            quotes = fetch_quotes(api_codes)
        except Exception as e:
            print(f"[PoolManager] S级操作池行情刷新失败: {e}")
            return []

        qmap = {q["代码"]: q for q in quotes if q.get("代码")}
        refreshed = []

        for stock in stocks:
            code = stock.get("代码", stock.get("股票代码", ""))
            q = qmap.get(code)
            if not q:
                continue
            now_price = q.get("现价")
            chg_pct = q.get("涨跌幅", 0)
            if now_price is not None:
                stock["今日收盘"] = now_price
                stock["今日涨跌"] = f"{chg_pct:+.2f}%" if isinstance(chg_pct, float) else chg_pct
                stock["换手率"] = q.get("换手率", stock.get("换手率", 0))
                stock["量比"] = q.get("量比", stock.get("量比", 0))
                stock["成交量_手"] = q.get("成交量", stock.get("成交量_手", 0))
                stock["振幅"] = q.get("振幅", stock.get("振幅", 0))
                stock["更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                # 止损检查
                stop_loss = stock.get("止损触发", stock.get("止损线", 0))
                if isinstance(stop_loss, (int, float)) and stop_loss > 0:
                    if now_price < stop_loss:
                        stock["操作建议"] = "已跌破止损，建议调出"
                        print(f"  [止损] {stock.get('名称','?')}({code}) 收盘{now_price}<止损{stop_loss}, 已标记")
                    else:
                        if stock.get("操作建议") == "已跌破止损，建议调出":
                            stock["操作建议"] = "正常"
                            print(f"  [止损解除] {stock.get('名称','?')}({code}) 收盘{now_price}>止损{stop_loss}, 标记清除")
                refreshed.append(code)

        if refreshed:
            data["统计"] = data.get("统计", {})
            data["统计"]["更新日期"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.save_pool("S级操作池", data)
            print(f"[PoolManager] ✅ S级操作池价格刷新完成: {len(refreshed)}/{len(stocks)} 只股票")

        return refreshed

    # 兼容方法：支持旧代码中的字段名
    def standardize_stock(self, stock: Dict[str, Any]) -> Dict[str, Any]:
        """
        标准化股票字段名
        
        Args:
            stock: 股票字典
            
        Returns:
            标准化后的字典
        """
        result = stock.copy()
        
        # 兼容旧字段名
        if "代码" in result and "股票代码" not in result:
            result["股票代码"] = result.pop("代码")
        if "名称" in result and "股票名称" not in result:
            result["股票名称"] = result.pop("名称")
        
        return result


# 便捷函数：创建全局PoolManager实例
_manager = None

def get_pool_manager() -> PoolManager:
    """获取全局PoolManager实例（单例）"""
    global _manager
    if _manager is None:
        _manager = PoolManager()
    return _manager


if __name__ == "__main__":
    # 测试PoolManager
    pm = PoolManager()
    
    print("=== PoolManager Test ===")
    
    # 获取摘要
    summary = pm.get_pool_summary()
    print("\\n池摘要:")
    for name, count in summary.items():
        print(f"  {name}: {count}只")
    
    # 测试添加股票
    test_stock = {
        "股票代码": "999999",
        "股票名称": "测试股票",
        "市场": "SH",
        "成本": 10.0,
        "建仓日期": datetime.now().strftime("%Y-%m-%d"),
        "备注": "PoolManager测试"
    }
    
    print(f"\\n测试添加股票: {test_stock}")
    
    # 测试获取所有池
    all_pools = pm.get_all_pools()
    print("\\n所有池:")
    for name, stocks in all_pools.items():
        print(f"  {name}: {len(stocks)}只")
    
    print("\\n✅ PoolManager 测试完成!")