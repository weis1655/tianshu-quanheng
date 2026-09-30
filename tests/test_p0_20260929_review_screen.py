#!/usr/bin/env python3
"""2026-09-29 三项修复的回归测试（纯 unittest，不依赖 pytest）。

运行：python3 tests/test_p0_20260929_review_screen.py
      python3 -m unittest tests/test_p0_20260929_review_screen.py -v

覆盖：
  ① raw 为空 → 诚实返回 no_candidates，不空转调用 LLM
  ② 新浪 fallback 字段全为 None 时仍能选出涨停标的（修复前恒为 0 只）
  ③ 边缘池回补遇 综合分=None 不抛 TypeError、不早退
"""

import sys
import json
import logging
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents"))

from review_agent import ReviewAgent          # noqa: E402
from screen_agent import ScreenAgent          # noqa: E402


TODAY = datetime.now().strftime("%Y-%m-%d")


# ═══════════════════════ ② 新浪 fallback ═══════════════════════

def _sina(code, name, chg, turnover=None, vr=None):
    """构造一条新浪涨幅榜记录（该接口实测换手/量比/振幅均为 None）。"""
    return {"code": code, "name": name, "changepercent": chg,
            "turnover": turnover, "volume_ratio": vr, "amplitude": None}


class TestSinaFallback(unittest.TestCase):
    """新浪涨幅榜筛选（_filter_sina_fallback）。"""

    def test_none_fields_still_select_limitup(self):
        """核心回归：字段全 None 时不再判死（修复前恒为 0 只）。"""
        data = [_sina("688685", "迈信林", 20.0),
                _sina("688655", "迅捷兴", 20.0)]
        out = ScreenAgent._filter_sina_fallback(data, set())
        self.assertEqual(len(out), 2, "换手率缺失时涨停标的不该被四重阈值判死")
        self.assertEqual({s["name"] for s in out}, {"迈信林", "迅捷兴"})

    def test_20cm_not_treated_as_limitup_board(self):
        """20cm 股涨停线是 20%，涨 13.7% 未封板可买——不能用 9.9% 判封板。"""
        out = ScreenAgent._filter_sina_fallback([_sina("301041", "金百泽", 13.7)], set())
        self.assertEqual(len(out), 1, "13.7% 的 20cm 股未封板，应可入选")

    def test_new_stock_first_day_excluded(self):
        """N/C 新股首日无量价限制（如 N鸿富诚 654%），且封板买不到。"""
        out = ScreenAgent._filter_sina_fallback(
            [_sina("301716", "N鸿富诚", 654.6), _sina("920202", "C安达", 289.5)], set())
        self.assertEqual(out, [])

    def test_st_excluded(self):
        out = ScreenAgent._filter_sina_fallback(
            [_sina("300147", "*ST香雪", 11.7), _sina("600000", "ST浦发", 10.0)], set())
        self.assertEqual(out, [])

    def test_low_change_excluded(self):
        f = ScreenAgent._filter_sina_fallback
        self.assertEqual(f([_sina("600000", "浦发银行", 3.2)], set()), [])
        self.assertEqual(f([_sina("600000", "浦发银行", 4.99)], set()), [])
        self.assertEqual(len(f([_sina("600000", "浦发银行", 5.0)], set())), 1,
                         "边界：恰好 5% 应入选")

    def test_known_turnover_still_filtering(self):
        """字段有值时仍作为软门槛生效（不是无脑全收）。"""
        f = ScreenAgent._filter_sina_fallback
        self.assertEqual(f([_sina("600000", "浦发银行", 8.0, turnover=1.5)], set()), [])
        self.assertEqual(f([_sina("600000", "浦发银行", 8.0, vr=1.0)], set()), [])
        self.assertEqual(len(f([_sina("600000", "浦发银行", 8.0,
                                     turnover=4.0, vr=2.0)], set())), 1)

    def test_pooled_codes_excluded(self):
        out = ScreenAgent._filter_sina_fallback(
            [_sina("600584", "长电科技", 15.0)], {"600584"})
        self.assertEqual(out, [])

    def test_bad_changepercent_skipped(self):
        """非数字涨幅不抛异常（原代码 float(None) 会崩）。"""
        f = ScreenAgent._filter_sina_fallback
        self.assertEqual(f([dict(_sina("600000", "浦发银行", None))], set()), [])
        self.assertEqual(f([dict(_sina("600000", "浦发银行", "abc"))], set()), [])

    def test_no_truncation_inside_method(self):
        """方法本身不截断（截断在调用方 cand[:5]），确认不在此处丢数据。"""
        data = [_sina(str(600000 + i), "股票%d" % i, 9.0) for i in range(8)]
        self.assertEqual(len(ScreenAgent._filter_sina_fallback(data, set())), 8)


# ═══════════════════════ ③ 边缘池回补 None 分数 ═══════════════════════

class TestDowngradeScoreNone(unittest.TestCase):
    """复现 review_agent.py 边缘池回补的评分读取逻辑。"""

    @staticmethod
    def _scan(stocks):
        """与 review_agent.py:233-238 一致的扫描逻辑。"""
        raw, existing = [], set()
        for es in stocks:
            es_code = str(es.get("代码", es.get("股票代码", "")))
            es_score = es.get("综合分", es.get("综合评分", es.get("score", 0)))
            if es_score is None:          # ← 2026-09-29 新增守卫
                continue
            if es_code and es_score >= 60 and es_code not in existing:
                raw.append(es)
                existing.add(es_code)
        return raw

    def test_none_score_does_not_crash(self):
        """兆易创新/比亚迪 综合分=None 不得抛 TypeError 中断整段回补。"""
        stocks = [{"代码": "600584", "名称": "长电科技", "综合分": 65.0},
                  {"代码": "603986", "名称": "兆易创新", "综合分": None},
                  {"代码": "002594", "名称": "比亚迪", "综合评分": None},
                  {"代码": "600028", "名称": "中国石化", "综合分": 72.0}]
        out = self._scan(stocks)           # 修复前在此抛 TypeError
        self.assertEqual([s["名称"] for s in out], ["长电科技", "中国石化"])

    def test_candidates_before_none_not_lost(self):
        """关键：None 出现在中间时，它之前的合格标的不能因循环中断而丢失。"""
        stocks = [{"代码": "601127", "名称": "赛力斯", "综合分": 80.0},
                  {"代码": "603986", "名称": "兆易创新", "综合分": None},
                  {"代码": "600584", "名称": "长电科技", "综合分": 66.0}]
        self.assertEqual([s["名称"] for s in self._scan(stocks)],
                         ["赛力斯", "长电科技"])

    def test_both_none_still_scan_rest(self):
        stocks = [{"代码": "603986", "名称": "兆易创新",
                   "综合分": None, "综合评分": None},
                  {"代码": "002594", "名称": "比亚迪",
                   "综合分": None, "综合评分": None},
                  {"代码": "600547", "名称": "山东黄金", "综合分": 61.0}]
        self.assertEqual([s["名称"] for s in self._scan(stocks)], ["山东黄金"])

    def test_all_none_returns_empty(self):
        self.assertEqual(self._scan([{"代码": "603986", "综合分": None}]), [])

    def test_missing_key_uses_default_zero(self):
        """键不存在走默认值 0（<60 不入选），键存在为 None 走 continue，两条路径都安全。"""
        self.assertEqual(
            self._scan([{"代码": "600000", "名称": "浦发银行"}]), [])

    def test_regression_no_none_still_works(self):
        """无 None 时行为与修复前完全一致。"""
        stocks = [{"代码": "600584", "综合分": 50.0},
                  {"代码": "601127", "综合分": 80.0}]
        self.assertEqual([s["代码"] for s in self._scan(stocks)], ["601127"])


# ═══════════════════════ ① 空候选诚实返回 ═══════════════════════

class TestEmptyCandidates(unittest.TestCase):
    """raw 为空时：不空转调用 LLM，返回 success=True + reason=no_candidates。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.agent = None          # _restore_logger 依赖，需先置空
        import logger as _logger_mod
        self._old_handlers = []
        self._old_propagate = True

        # ── 日志隔离（2026-09-29 第三次修正）─────────────────────────────
        # 有【两条】独立写入路径指向生产日志 logs/{今天}.log：
        #   ① plog() → root logger 的 FileHandler（硬编码 LOG_DIR）
        #   ② StructuredLogger.__init__（logger.py:52-54）构造时自己 addHandler
        #     一个 FileHandler 到 LOG_DIR，与 root 无关。
        # 位置要求：本段必须在 self.agent = ReviewAgent() 【之前】。
        #   ReviewAgent.__init__ 内部就构造 PoolManager，其 _init_capacity_limits
        #   会 plog 输出「池容量已加载」，若此时 root 仍指生产目录即产生写入。
        #   实测漏写 4 行。
        # 手法（照 tests/test_coverage_f06.py::test_plog 既有范式，并补齐 ②）：
        #   重置 _ROOT_LOGGER_SETUP 一次性守卫后 setup_root_logger(log_dir=tmp)。
        #   不重置则守卫为 True 直接 return，log_dir 参数被忽略——这是关键坑。
        #   注意：test_coverage_f06.py:109 未重置守卫，在 setup_root_logger()
        #   已被其他代码调用过的进程里它其实不生效；此处必须重置。
        # 还原顺序：addCleanup 为 LIFO，先登记清理、后登记还原 → 还原先执行。
        self._tmp_log = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_log.cleanup)
        self.addCleanup(self._restore_logger)
        self._old_setup_flag = _logger_mod._ROOT_LOGGER_SETUP
        self._old_root_handlers = list(logging.getLogger().handlers)
        _logger_mod._ROOT_LOGGER_SETUP = False
        _logger_mod.setup_root_logger(level="INFO", log_dir=self._tmp_log.name)

        # ── 构造与路径重定向 ──
        # 用真实 __init__ 拿到全部属性（stats/logger/pool_manager 等），
        # 再把路径切到临时目录，避免污染真实的五池管理和历史记录。
        # （曾试用 ReviewAgent.__new__ 手工补属性，越补越多——漏 pool_dir、stats
        #   等，且补不全 BaseAgent 状态，故改回真实构造。）
        self.agent = ReviewAgent()
        self.agent.root = self.tmp
        self.agent.history_dir = self.tmp / "data" / "历史记录"
        self.agent.history_dir.mkdir(parents=True, exist_ok=True)
        self.agent.pool_dir = self.tmp / "五池管理"
        self.agent.pool_dir.mkdir(parents=True, exist_ok=True)
        from pool_manager import PoolManager
        self.agent.pool_manager = PoolManager(self.agent.pool_dir)

        # ② 清掉 agent.logger 自己在【构造时】挂上的生产 FileHandler，并断开冒泡。
        #   此时 root 已指临时目录，构造期间 plog 写入已进临时目录（预期无害）。
        self._old_handlers = [
            (h, getattr(h, "formatter", None))
            for h in self.agent.logger.logger.handlers]
        self._old_propagate = self.agent.logger.logger.propagate
        self.agent.logger.logger.handlers.clear()
        self.agent.logger.logger.propagate = False

    def _restore_logger(self):
        """还原 root logger 与 agent.logger，避免污染同进程内后续测试。"""
        import logger as _logger_mod
        # ① 还原 root 到真实 LOG_DIR（必须重置守卫，否则参数被忽略）
        _logger_mod._ROOT_LOGGER_SETUP = False
        _logger_mod.setup_root_logger(level="INFO", log_dir=str(_logger_mod.LOG_DIR))
        # 清掉指向已删除临时目录的 handler，还原真实 LOG_DIR handler
        root = logging.getLogger()
        root.handlers.clear()
        for h in self._old_root_handlers:
            root.addHandler(h)
        # ② 还原 agent.logger 的 handlers 与 propagate
        if self.agent is not None:
            lg = self.agent.logger.logger
            lg.propagate = self._old_propagate
            for h, f in self._old_handlers:
                if f is not None:
                    h.setFormatter(f)
                lg.addHandler(h)

    def _make_pools(self, screen_stocks="[]"):
        """池文件必须写在 pool_dir（= root/五池管理）下，不是 root。"""
        self.agent.pool_dir.mkdir(parents=True, exist_ok=True)
        (self.agent.pool_dir / "快筛候选池.json").write_text(
            json.dumps({"stocks": json.loads(screen_stocks)}, ensure_ascii=False),
            encoding="utf-8")
        (self.agent.pool_dir / "边缘池.json").write_text(
            '{"stocks": []}', encoding="utf-8")
        # cron 每次运行必写快筛报告（哪怕筛出 0 只），审查阶段会读它做报告文本回退
        (self.agent.history_dir / f"{TODAY}_快筛报告.md").write_text(
            "## 结论\n\n快筛未筛选出技术面达标标的。\n", encoding="utf-8")

    def test_empty_pool_returns_no_candidates(self):
        self._make_pools()
        with patch.object(self.agent, "_call_llm_review_batched",
                          side_effect=AssertionError("raw 为空时不得调用 LLM")):
            r = self.agent._run_impl()
        self.assertTrue(r["success"])
        self.assertEqual(r["reason"], "no_candidates")
        self.assertEqual(r["stocks"], [])
        self.assertEqual(r["high_risk_count"], 0)

    def test_empty_pool_report_text_fallback_not_treated_as_candidates(self):
        """报告文本含标的时（LLM 幻觉输出）必须被过滤，不得当作候选喂给 LLM。

        09-29 链条正是如此：快筛 0 只，但报告文本里的 工商银行/腾讯 被抽成候选，
        让守卫误判"有 2 只候选"而放行，随后 LLM 空转输出误导结论。
        """
        self._make_pools()
        (self.agent.history_dir / f"{TODAY}_快筛报告.md").write_text(
            "### 技术面达标标的\n- 601398 工商银行（涨幅 8.5%）\n- 00700 腾讯控股\n",
            encoding="utf-8")
        with patch.object(self.agent, "_call_llm_review_batched",
                          side_effect=AssertionError("报告文本回退不得当作候选喂给 LLM")):
            r = self.agent._run_impl()
        self.assertEqual(r["reason"], "no_candidates",
                         "报告文本回退的标的不应绕过空池早退")

    def test_empty_candidates_not_reported_as_failure(self):
        """候选池为空≠审查失败：不得 success=False，否则 main.py 会级联终止。"""
        self._make_pools()
        with patch.object(self.agent, "_call_llm_review_batched",
                          side_effect=AssertionError("raw 为空时不得调用 LLM")):
            r = self.agent._run_impl()
        self.assertTrue(r["success"], "不应 success=False，否则 main.py 级联终止后续阶段")

    def test_empty_pool_report_written(self):
        self._make_pools()
        with patch.object(self.agent, "_call_llm_review_batched",
                          side_effect=AssertionError("raw 为空时不得调用 LLM")):
            r = self.agent._run_impl()
        self.assertEqual(r.get("reason"), "no_candidates")
        f = self.agent.history_dir / f"{TODAY}_审查报告.md"
        self.assertTrue(f.exists(), f"报告未写盘: {f}")
        self.assertIn("候选池现有：0 只", f.read_text(encoding="utf-8"))
        self.assertIn("无审查标的，今日不产生池流转", f.read_text(encoding="utf-8"))

    def test_nonempty_pool_not_short_circuited(self):
        """有候选时不得走 no_candidates 早退（防过度修复）。"""
        self._make_pools('[{"代码": "600584", "名称": "长电科技", "综合分": 80}]')
        called = []

        def _fake_llm(**kw):
            called.append(1)
            return "", []        # 与真实返回一致：(result, batch_meta)

        with patch.object(self.agent, "_get_market_state",
                          return_value={"state": "震荡", "sh_chg": 0.0}), \
                patch.object(self.agent, "_call_llm_review_batched", _fake_llm):
            r = self.agent._run_impl()
        self.assertNotEqual(r.get("reason"), "no_candidates", "有候选时必须走正常审查")
        self.assertEqual(len(called), 1, "有候选时应真正调用 LLM")


if __name__ == "__main__":
    unittest.main(verbosity=2)
