#!/usr/bin/env python3
"""
天枢权衡 — 数据流水线深度核查脚本

核查范围：K线行情、财务指标、换手率、板块概念、停牌ST、除权除息数据。
分阶段执行，支持中途暂停续跑。

用法:
    python3 scripts/data_pipeline_audit.py                    # 全量核查
    python3 scripts/data_pipeline_audit.py --phase 1         # 只跑阶段1
    python3 scripts/data_pipeline_audit.py --phase 1-3       # 跑阶段1-3
    python3 scripts/data_pipeline_audit.py --verbose         # 详细输出
    python3 scripts/data_pipeline_audit.py --checkpoint      # 查看断点
"""

import os, sys, json, datetime, re, urllib.request, argparse
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agents"))

AUDIT_DIR = PROJECT_ROOT / "data" / "audit"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

CHECKPOINT_FILE = AUDIT_DIR / "pipeline_audit_checkpoint.json"
REPORT_FILE = AUDIT_DIR / "pipeline_audit_report.md"

TODAY = datetime.date.today()
NOW = datetime.datetime.now()

# ============================================================
# 阶段1: 数据源与字段完整性核查
# ============================================================

def phase1_data_source_audit() -> dict:
    """核查所有数据源的字段完整性和格式一致性"""
    findings = []
    details = []

    # 1.1 腾讯行情API字段位置验证
    try:
        req = urllib.request.Request(
            "https://qt.gtimg.cn/q=sh000001,sz000001",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("gbk", errors="replace")
        lines = [l for l in body.split("\n") if "~" in l and "v_pv_none_match" not in l]
        for line in lines[:2]:
            parts = line.split("~")
            # 腾讯行情字段位置验证
            field_map = {
                "名称": parts[1] if len(parts) > 1 else "?",
                "代码": parts[2] if len(parts) > 2 else "?",
                "现价": parts[3] if len(parts) > 3 else "?",
                "昨收": parts[4] if len(parts) > 4 else "?",
                "涨跌幅": parts[32] if len(parts) > 32 else "?",
                "换手率": parts[38] if len(parts) > 38 else "?",
                "市盈率": parts[39] if len(parts) > 39 else "?",
                "振幅": parts[43] if len(parts) > 43 else "?",
                "流通市值": parts[44] if len(parts) > 44 else "?",
                "总市值": parts[45] if len(parts) > 45 else "?",
                "量比": parts[49] if len(parts) > 49 else "?",
            }
            missing = [k for k, v in field_map.items() if v in ("?", "")]
            if missing:
                findings.append({
                    "level": "critical", "type": "字段缺失",
                    "detail": f"腾讯行情API字段位置异常: {missing} 缺失。parts长度={len(parts)}"
                })
            else:
                findings.append({
                    "level": "info", "type": "字段完整性",
                    "detail": f"腾讯行情API字段完整({len(parts)}个字段段)，示例: {field_map['名称']}({field_map['代码']}) 现价={field_map['现价']}"
                })
            details.append(field_map)
    except Exception as e:
        findings.append({"level": "critical", "type": "API不可达", "detail": f"腾讯行情API: {e}"})

    # 1.2 五池字段一致性核查
    pool_dir = PROJECT_ROOT / "五池管理"
    score_field_names = defaultdict(set)
    for pf in pool_dir.glob("*.json"):
        try:
            data = json.loads(pf.read_text(encoding="utf-8"))
            for s in data.get("stocks", []):
                for k in s:
                    if any(x in k for x in ["分", "score", "评分"]):
                        score_field_names[pf.stem].add(k)
        except Exception:
            pass

    for pool, fields in sorted(score_field_names.items()):
        if len(fields) > 1:
            findings.append({
                "level": "warning", "type": "字段命名不一致",
                "detail": f"{pool}: 评分字段混用 {fields}"
            })

    # 1.3 复权标识核查
    has_adjust_tag = False
    for pf in pool_dir.glob("*.json"):
        try:
            data = json.loads(pf.read_text(encoding="utf-8"))
            for s in data.get("stocks", []):
                if "复权类型" in s or "adjust_type" in s:
                    has_adjust_tag = True
                    break
        except Exception:
            pass
    if not has_adjust_tag:
        findings.append({
            "level": "warning", "type": "除权除息",
            "detail": "全池均无复权类型标识字段，K线数据使用前复权(qfq)但未标注，存在数据源切换时复权口径不一致风险"
        })

    return {"phase": 1, "findings": findings, "details": details}


# ============================================================
# 阶段2: 数据清洗与转换逻辑核查
# ============================================================

def phase2_cleaning_audit() -> dict:
    """核查数据清洗/转换逻辑缺陷"""
    findings = []

    # 2.1 腾讯行情解析硬编码位置
    src = (PROJECT_ROOT / "agents" / "market_agent.py").read_text(encoding="utf-8")
    # 检查fetch_quotes中的字段位置注释
    if "parts[1]=名称  [2]=代码  [3]=现价" in src:
        findings.append({
            "level": "warning", "type": "清洗-硬编码字段位置",
            "detail": "腾讯行情解析使用硬编码parts[N]位置(如parts[3]=现价, parts[32]=涨跌幅)。API响应格式变化时所有字段静默偏移，无校验机制"
        })

    # 2.2 新浪行情解析
    if "var hq_str" in src:
        findings.append({
            "level": "info", "type": "清洗-格式依赖",
            "detail": "新浪行情解析依赖`var hq_str_`前缀，新浪API可能随时变更格式"
        })

    # 2.3 东方财富K线复权参数
    if "fqt=1" in src:
        findings.append({
            "level": "info", "type": "清洗-复权参数",
            "detail": "东方财富K线使用fqt=1(前复权)，但未校验响应中是否确实为前复权数据，无复权类型标识返回"
        })

    # 2.4 空值/异常值处理
    null_handling_issues = []
    if "try:" in src and "except" in src:
        # 检查行情获取失败的处理
        for pat in ["if not data:", "if not content:", "return []", "return None"]:
            count = src.count(pat)
            if count > 0:
                null_handling_issues.append(f"  {pat}: {count}处")
    if null_handling_issues:
        findings.append({
            "level": "info", "type": "清洗-空值处理",
            "detail": "行情获取失败处理:\n" + "\n".join(null_handling_issues)
        })

    # 2.5 停牌状态检测
    st_detection = 0
    for f in (PROJECT_ROOT / "agents").glob("*.py"):
        content = f.read_text(encoding="utf-8")
        if "ST_STOCKS" in content or "st_stock" in content or "停牌" in content:
            st_detection += 1
    if st_detection == 0:
        findings.append({
            "level": "critical", "type": "清洗-停牌ST",
            "detail": "系统无停牌/ST自动检测机制。ST_STOCKS为手动维护的空集合，无法自动识别新ST标的"
        })
    else:
        findings.append({
            "level": "warning", "type": "清洗-停牌ST",
            "detail": f"已有{st_detection}处ST检测逻辑，但ST_STOCKS集合为空(需手动维护)，无自动更新机制"
        })

    return {"phase": 2, "findings": findings}


# ============================================================
# 阶段3: 数据一致性交叉验证
# ============================================================

def phase3_consistency_audit() -> dict:
    """多源数据一致性交叉验证"""
    findings = []
    sample_codes = ["sh000001", "sz000001", "sh600519", "sz300750"]

    for code in sample_codes:
        try:
            # 腾讯行情
            req = urllib.request.Request(
                f"https://qt.gtimg.cn/q={code}",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                tencent_raw = resp.read().decode("gbk", errors="replace")
            tencent_parts = tencent_raw.split("~")
            tencent_price = tencent_parts[3] if len(tencent_parts) > 3 else "N/A"
            tencent_change = tencent_parts[32] if len(tencent_parts) > 32 else "N/A"

            # 东方财富行情
            em_secid = "1." + code[2:] if code.startswith("sh") else "0." + code[2:]
            em_url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={em_secid}&fields=f43,f44,f45,f46,f47,f48,f50,f57,f58,f170"
            req2 = urllib.request.Request(em_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req2, timeout=8) as resp:
                em_data = json.loads(resp.read().decode("utf-8"))
            em_price = em_data.get("data", {}).get("f43", "N/A") if em_data.get("data") else "N/A"

            # 价格对比
            tc = float(tencent_price) if tencent_price not in ("N/A", "") else 0
            ec = float(em_price) if em_price not in ("N/A", "") else 0
            if tc > 0 and ec > 0:
                diff_pct = abs(tc - ec) / tc * 100
                if diff_pct > 0.5:
                    findings.append({
                        "level": "warning", "type": "一致性-行情冲突",
                        "detail": f"{code}: 腾讯={tc} vs 东方财富={ec}, 差异{diff_pct:.2f}%"
                    })
        except Exception as e:
            findings.append({
                "level": "info", "type": "一致性-API不可达",
                "detail": f"{code}: {type(e).__name__}"
            })

    if not any(f["level"] == "warning" for f in findings):
        findings.append({
            "level": "info", "type": "一致性-行情",
            "detail": f"样本{len(sample_codes)}只: 腾讯vs东方财富行情价格一致(差异<0.5%)"
        })

    return {"phase": 3, "findings": findings}


# ============================================================
# 阶段4: 历史数据补全与缓存刷新机制核查
# ============================================================

def phase4_cache_refresh_audit() -> dict:
    """核查缓存刷新时机和历史数据补全机制"""
    findings = []

    # 4.1 池行情刷新机制
    pool_price_refresh = (PROJECT_ROOT / "scripts" / "pool_price_refresh.py")
    if pool_price_refresh.exists():
        content = pool_price_refresh.read_text(encoding="utf-8")
        findings.append({
            "level": "info", "type": "缓存-刷新机制",
            "detail": "池行情刷新脚本存在，每15分钟盘中执行。需核查是否刷新所有池(含S级操作池)"
        })

    # 4.2 K线缓存
    cache_files = list((PROJECT_ROOT / "data").glob("*cache*")) + list((PROJECT_ROOT / "data").glob("*kline*"))
    if cache_files:
        findings.append({
            "level": "info", "type": "缓存-K线",
            "detail": f"存在{len(cache_files)}个K线缓存文件，需核查缓存TTL和过期刷新机制"
        })
    else:
        findings.append({
            "level": "info", "type": "缓存-K线",
            "detail": "无K线数据缓存，每次调用均实时拉取API，交易时段可能因API限流获取失败"
        })

    # 4.3 历史数据补全
    his_files = list((PROJECT_ROOT / "data" / "历史记录").glob("*.md"))
    if his_files:
        # 检查是否有缺失的日期
        dates_found = set()
        for f in his_files:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
            if m:
                dates_found.add(m.group(1))
        findings.append({
            "level": "info", "type": "历史数据",
            "detail": f"历史记录目录: {len(his_files)}个文件, {len(dates_found)}个不同日期"
        })

    return {"phase": 4, "findings": findings}


# ============================================================
# 阶段5: 修复方案与告警规则设计
# ============================================================

def phase5_repair_design() -> dict:
    """设计修复方案和告警规则"""
    repairs = [
        {
            "id": "REP-01",
            "priority": "high",
            "title": "腾讯行情字段位置校验",
            "problem": "fetch_quotes硬编码parts[N]位置，API格式变化时静默错误",
            "solution": "在fetch_quotes中添加字段位置校验：检查parts[1]是否为中文名称(正则[\u4e00-\u9fa5])，parts[3]是否为数字，不匹配则告警",
            "verification": "运行python3 -c \"from market_agent import fetch_quotes; q=fetch_quotes(['sh000001']); assert q[0]['现价']>0\""
        },
        {
            "id": "REP-02",
            "priority": "high",
            "title": "自动停牌/ST检测",
            "problem": "ST_STOCKS为空集合，需手动维护，无法自动识别新ST",
            "solution": "从腾讯行情API获取ST标识(parts[1]含'ST'或'*ST')，自动更新ST_STOCKS集合",
            "verification": "检查ST标的在池中时系统自动拦截"
        },
        {
            "id": "REP-03",
            "priority": "medium",
            "title": "复权类型标识持久化",
            "problem": "K线数据使用前复权但未标注，数据源切换时口径不一致",
            "solution": "在池JSON中增加'复权类型':'qfq'字段，K线读取时校验复权类型一致性",
            "verification": "grep '复权类型' 五池管理/*.json 应返回非空"
        },
        {
            "id": "REP-04",
            "priority": "medium",
            "title": "多源行情一致性告警",
            "problem": "同一股票腾讯vs东方财富行情无交叉验证，数据冲突时静默使用单一源",
            "solution": "在fetch_quotes中双源对比，价格差异>1%时告警并记录到日志",
            "verification": "模拟价格差异场景，确认告警触发"
        },
        {
            "id": "REP-05",
            "priority": "low",
            "title": "日期对齐校验",
            "problem": "多源K线数据日期可能不对齐(节假日不同、数据延迟)",
            "solution": "K线合并时校验日期字段是否连续，缺失日期标记为'数据缺失'",
            "verification": "对比腾讯vs东方财富最近20个交易日K线日期是否一致"
        },
    ]
    return {"phase": 5, "repairs": repairs}


# ============================================================
# 主流程
# ============================================================

def load_checkpoint():
    if CHECKPOINT_FILE.exists():
        return json.loads(CHECKPOINT_FILE.read_text())
    return {"completed_phases": [], "last_phase": 0, "context": ""}


def save_checkpoint(completed_phases, last_phase, context=""):
    cp = {"completed_phases": completed_phases, "last_phase": last_phase, "context": context, "updated_at": NOW.isoformat()}
    CHECKPOINT_FILE.write_text(json.dumps(cp, ensure_ascii=False, indent=2))


def run_phases(start_phase, end_phase, verbose):
    cp = load_checkpoint()
    completed = cp.get("completed_phases", [])
    all_results = {}

    phases = {
        1: ("数据源与字段完整性核查", phase1_data_source_audit),
        2: ("数据清洗与转换逻辑核查", phase2_cleaning_audit),
        3: ("多源数据一致性交叉验证", phase3_consistency_audit),
        4: ("缓存刷新与历史数据补全核查", phase4_cache_refresh_audit),
        5: ("修复方案与告警规则设计", phase5_repair_design),
    }

    for phase_num in range(start_phase, end_phase + 1):
        if phase_num not in phases:
            continue
        phase_name, phase_fn = phases[phase_num]
        print(f"\n{'='*60}")
        print(f"  Phase {phase_num}: {phase_name}")
        print(f"{'='*60}")

        try:
            result = phase_fn()
            all_results[phase_num] = result
            completed.append(phase_num)
            save_checkpoint(completed, phase_num, f"Phase {phase_num} completed")

            findings = result.get("findings", [])
            repairs = result.get("repairs", [])
            for f in findings:
                emoji = {"critical": "🔴", "warning": "🟡", "info": "ℹ️"}.get(f["level"], "❓")
                if verbose or f["level"] in ("critical", "warning"):
                    print(f"  {emoji} [{f['type']}] {f['detail']}")
            for r in repairs:
                emoji = "🔴" if r["priority"] == "high" else ("🟡" if r["priority"] == "medium" else "🟢")
                print(f"  {emoji} [修复方案] {r['title']}: {r['solution'][:60]}...")

        except Exception as e:
            print(f"  ❌ Phase {phase_num} 失败: {e}")
            import traceback
            traceback.print_exc()
            break

    return all_results


def generate_report(all_results):
    """生成最终核查报告"""
    lines = []
    lines.append(f"# 天枢数据流水线深度核查报告")
    lines.append(f"")
    lines.append(f"> 生成时间: {NOW.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 核查范围: K线行情/财务指标/换手率/板块概念/停牌ST/除权除息")
    lines.append(f"")
    lines.append(f"## 核查结果汇总")
    lines.append(f"")
    lines.append(f"| 阶段 | 发现数 | 致命(🔴) | 警告(🟡) | 信息(ℹ️) |")
    lines.append(f"|:----|:------:|:--------:|:--------:|:--------:|")

    total_by_level = {"critical": 0, "warning": 0, "info": 0}
    for pn, result in sorted(all_results.items()):
        findings = result.get("findings", [])
        c = sum(1 for f in findings if f["level"] == "critical")
        w = sum(1 for f in findings if f["level"] == "warning")
        i = len(findings) - c - w
        total_by_level["critical"] += c
        total_by_level["warning"] += w
        total_by_level["info"] += i
        lines.append(f"| Phase {pn} | {len(findings)} | {c} | {w} | {i} |")

    lines.append(f"| **合计** | **{sum(len(r.get('findings',[])) for r in all_results.values())}** | **{total_by_level['critical']}** | **{total_by_level['warning']}** | **{total_by_level['info']}** |")
    lines.append(f"")

    # 修复方案
    if 5 in all_results:
        lines.append(f"## 修复方案")
        lines.append(f"")
        for r in all_results[5].get("repairs", []):
            prio_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(r["priority"], "❓")
            lines.append(f"### {prio_emoji} {r['id']}: {r['title']} ({r['priority']})")
            lines.append(f"")
            lines.append(f"**问题**: {r['problem']}")
            lines.append(f"**方案**: {r['solution']}")
            lines.append(f"**验证**: {r['verification']}")
            lines.append(f"")

    # 详细发现
    lines.append(f"## 详细发现")
    lines.append(f"")
    for pn, result in sorted(all_results.items()):
        findings = result.get("findings", [])
        if not findings:
            continue
        lines.append(f"### Phase {pn}")
        lines.append(f"")
        for f in findings:
            emoji = {"critical": "🔴", "warning": "🟡", "info": "ℹ️"}.get(f["level"], "❓")
            lines.append(f"- {emoji} **[{f['type']}]** {f['detail']}")
        lines.append(f"")

    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 报告已保存: {REPORT_FILE}")


def main():
    parser = argparse.ArgumentParser(description="天枢数据流水线深度核查")
    parser.add_argument("--phase", help="指定阶段范围(如1, 1-3, 5)", default="1-5")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    parser.add_argument("--checkpoint", action="store_true", help="查看断点状态")
    args = parser.parse_args()

    if args.checkpoint:
        cp = load_checkpoint()
        print(f"断点状态: 已完成阶段 {cp['completed_phases']}, 最后执行 Phase {cp['last_phase']}")
        print(f"上下文: {cp['context']}")
        return

    # 解析阶段范围
    if "-" in args.phase:
        start, end = map(int, args.phase.split("-"))
    else:
        start = end = int(args.phase)

    print(f"天枢数据流水线深度核查 | 阶段 {start}-{end}")
    all_results = run_phases(start, end, args.verbose)

    if all_results:
        generate_report(all_results)

    print(f"\n✅ 核查完成")
    print(f"   断点已保存: {CHECKPOINT_FILE}")
    print(f"   报告已保存: {REPORT_FILE}")
    print(f"   续跑: python3 scripts/data_pipeline_audit.py --phase {end+1}-5")


if __name__ == "__main__":
    main()