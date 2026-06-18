#!/usr/bin/env python3
"""
天枢权衡系统 - 自动化"回头看"模块 v3
增强版：精确解析、P0级问题检测、完整准确率计算
"""

import os
import sys
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# 添加 agents 目录到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / 'agents'))
from safe_file_utils import safe_read_file, safe_write_file, safe_read_json
from path_config import PathConfig
from trading_calendar import is_trading_day

# 配置路径
cfg = PathConfig()
BASE_DIR = cfg.data_dir  # 对应 /home/seven/hermes-data/tianshu-quanheng/data
HISTORY_DIR = cfg.data_dir / '历史记录'  # data/历史记录
OUTPUT_DIR = cfg.history_dir  # data/回顾报告


def parse_date_from_filename(filename):
    """从文件名提取日期"""
    match = re.match(r'(\d{4}-\d{2}-\d{2})_', filename)
    if match:
        return match.group(1)
    return None


def get_trading_days(days=7):
    """获取过去N个交易日（使用交易日历，含法定节假日过滤）"""
    trading_days = []
    current = datetime.now()
    while len(trading_days) < days:
        if is_trading_day(current.date()):
            trading_days.insert(0, current.strftime('%Y-%m-%d'))
        current -= timedelta(days=1)
    return trading_days  # 按时间正序返回


# === 实战验证模块（策略A：进化导入）===
PRICE_CACHE = {}
PRICE_CACHE_FILE = None


def get_market_prefix(code):
    code = str(code).strip()
    if code.startswith('6'):
        return 'sh'
    elif code.startswith('0') or code.startswith('3'):
        return 'sz'
    elif code.startswith('8') or code.startswith('4'):
        return 'bj'
    return 'sh'


def fetch_stock_history(code, num_days=40):
    """从新浪财经获取股票日K线（含缓存+TTL 1小时）"""
    global PRICE_CACHE
    if code in PRICE_CACHE:
        cached = PRICE_CACHE[code]
        if isinstance(cached, dict) and "data" in cached and "timestamp" in cached:
            if time.time() - cached["timestamp"] <= 3600:
                return cached["data"]
        elif isinstance(cached, dict) and "data" not in cached:
            # 旧格式
            return cached

    prefix = get_market_prefix(code)
    url = (f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen={num_days}")

    try:
        import urllib.request
        # 先尝试 HTTPS，失败后 fallback 到 HTTP
        urls = [
            f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen={num_days}",
            f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen={num_days}"
        ]
        raw = None
        last_err = None
        for url in urls:
            try:
                req = urllib.request.Request(url, headers={
                    "Referer": "https://finance.sina.com.cn/",
                    "User-Agent": "Mozilla/5.0"
                })
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                    break
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
                last_err = e
                continue
        if raw is None:
            raise last_err or Exception("所有 URL 均失败")
        data = json.loads(raw)
        # 转为 {date: close} 字典
        prices = {d["day"]: float(d["close"]) for d in data if "day" in d and "close" in d}
        PRICE_CACHE[code] = {"data": prices, "timestamp": time.time()}
        return prices
    except (IOError, json.JSONDecodeError, urllib.error.URLError,
            urllib.error.HTTPError, OSError, ValueError) as e:
        print(f"[回头看] ⚠️ 行情获取失败 {code}: {e}")
        PRICE_CACHE[code] = {"data": {}, "timestamp": time.time()}
        return {}


def load_price_cache(cache_path):
    """从文件加载价格缓存"""
    global PRICE_CACHE, PRICE_CACHE_FILE
    PRICE_CACHE_FILE = cache_path
    if cache_path and os.path.exists(cache_path):
        try:
            raw = safe_read_json(cache_path, default={})
            # 兼容旧格式（{code: {date: price}}）和新格式（{code: {"data": ..., "timestamp": ...}}）
            PRICE_CACHE = {}
            for code, value in raw.items():
                if isinstance(value, dict) and "data" in value:
                    PRICE_CACHE[code] = value
                else:
                    PRICE_CACHE[code] = {"data": value, "timestamp": 0}
        except (IOError, json.JSONDecodeError):
            PRICE_CACHE = {}


def save_price_cache():
    """保存价格缓存到文件（仅保存数据部分，保持向后兼容）"""
    if not PRICE_CACHE_FILE:
        return
    try:
        # 仅保存 data 部分，保持旧格式兼容
        save_data = {}
        for code, value in PRICE_CACHE.items():
            if isinstance(value, dict) and "data" in value:
                save_data[code] = value["data"]
            else:
                save_data[code] = value
        safe_write_file(PRICE_CACHE_FILE, json.dumps(save_data, ensure_ascii=False))
    except IOError as e:
        print(f"[回头看] ⚠️ 缓存写入失败: {e}")


def verify_recommendation(code, decision_date, hold_days=3):
    """验证推荐后N个交易日的涨跌幅"""
    prices = fetch_stock_history(code, num_days=hold_days + 20)
    if not prices:
        return None

    sorted_dates = sorted(prices.keys())

    # 找决策日或之后最近交易日作为买入日
    entry_idx = None
    for i, d in enumerate(sorted_dates):
        if d >= decision_date:
            entry_idx = i
            break

    if entry_idx is None or entry_idx + hold_days >= len(sorted_dates):
        return None

    entry_price = prices[sorted_dates[entry_idx]]
    exit_price = prices[sorted_dates[entry_idx + hold_days]]

    if entry_price and entry_price > 0:
        change = (exit_price - entry_price) / entry_price * 100
        return {
            'buy_price': round(entry_price, 2),
            'sell_price': round(exit_price, 2),
            'change_pct': round(change, 2),
            'hold_days': hold_days,
            'entry_date': sorted_dates[entry_idx],
            'exit_date': sorted_dates[entry_idx + hold_days],
            'is_profit': change > 0
        }
    return None


# === 跨期对比模块（策略C：减少噪声，3期滑动平均）===
STATE_FILE = OUTPUT_DIR / '.previous_state.json'


def load_state():
    """读取历史状态，损坏时静默重置"""
    try:
        if STATE_FILE.exists():
            return safe_read_json(STATE_FILE, default=None) or {'last_date': None, 'history': [], 'persistent_p0': {}}
    except (IOError, json.JSONDecodeError) as e:
        print(f"[回头看] ⚠️ 状态文件损坏，重置: {e}")
    return {'last_date': None, 'history': [], 'persistent_p0': {}}


def save_state(metrics):
    """保存当前指标到历史状态"""
    try:
        state = load_state()
        history = state.get('history', [])
        history.append(metrics)
        if len(history) > 5:
            history = history[-5:]

        # 顽固P0追踪：连续出现计数
        persistent_p0 = state.get('persistent_p0', {})
        p0_types = set(metrics.get('p0_type_counts', {}).keys())
        for ptype in persistent_p0:
            persistent_p0[ptype] = persistent_p0.get(ptype, 0) + 1 if ptype in p0_types else 0
        for ptype in p0_types:
            if ptype not in persistent_p0:
                persistent_p0[ptype] = 1

        state['last_date'] = metrics.get('date')
        state['history'] = history
        state['persistent_p0'] = persistent_p0
        safe_write_file(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2))
        return state
    except (IOError, OSError, TypeError, ValueError) as e:
        print(f"[回头看] ⚠️ 状态保存失败: {e}")
        return None


def calc_trend(history, key, is_pct=False):
    """计算趋势变化（PCT用3期滑动均值，Count用2期直接对比）"""
    if not history or len(history) < 2:
        return 0, None, 'stable'

    if is_pct and len(history) >= 6:
        recent = [h.get(key, 0) for h in history[-3:]]
        prev = [h.get(key, 0) for h in history[-6:-3]]
        cur_val = sum(recent) / 3
        pre_val = sum(prev) / 3
    else:
        cur_val = history[-1].get(key, 0)
        pre_val = history[-2].get(key, 0)

    delta = cur_val - pre_val
    threshold = 0.5 if is_pct else 1
    if abs(delta) <= threshold:
        trend = 'stable'
    elif delta > 0:
        trend = 'up'
    else:
        trend = 'down'
    return round(delta, 2), round(cur_val, 2), trend


def get_persistent_issues(state):
    """获取连续3期出现的顽固问题"""
    persistent = state.get('persistent_p0', {})
    return [ptype for ptype, count in persistent.items() if count >= 3]


# === Index 大盘基准对比模块 ===
INDEX_CODE = "000001"  # 上证指数
INDEX_PREFIX = "sh"


def fetch_index_data(num_days=40):
    """获取上证指数日K线，存入 PRICE_CACHE 复用缓存"""
    return fetch_stock_history(INDEX_CODE, num_days=num_days)


def calc_index_change(decision_date, hold_days=3):
    """计算决策日到hold_days后的上证指数涨跌幅"""
    prices = fetch_index_data(hold_days + 30)
    if not prices:
        return None
    sorted_dates = sorted(prices.keys())
    entry_idx = None
    for i, d in enumerate(sorted_dates):
        if d >= decision_date:
            entry_idx = i
            break
    if entry_idx is None or entry_idx + hold_days >= len(sorted_dates):
        return None
    entry_price = prices[sorted_dates[entry_idx]]
    exit_price = prices[sorted_dates[entry_idx + hold_days]]
    if entry_price and entry_price > 0:
        return round((exit_price - entry_price) / entry_price * 100, 2)
    return None
# ═══════════════════════════════════════════


def get_report_files(trading_days):
    """获取指定交易日范围内的报告文件"""
    files = {'快筛': [], '审查': [], '决策': []}

    if not HISTORY_DIR.exists():
        return files

    for filename in sorted(os.listdir(HISTORY_DIR)):
        date_str = parse_date_from_filename(filename)
        if not date_str or date_str not in trading_days:
            continue

        filepath = os.path.join(HISTORY_DIR, filename)
        if '快筛' in filename:
            files['快筛'].append(filepath)
        elif '审查' in filename and '质疑' not in filename:
            files['审查'].append(filepath)
        elif '决策' in filename:
            files['决策'].append(filepath)

    return files





def extract_fast_screen_stocks(filepath):
    """从快筛报告中提取强势对象"""
    stocks = []
    content = safe_read_file(filepath)
    if content is None:
        return stocks

    # 格式检测：确定报告版本
    format_version = 'unknown'
    
    # 主模式：`- 名称（代码）- 描述`（2026-05+ 格式）
    pattern = r'[-•]\s*([^（\n]+)（(\d{6})）\s*[-:：]?\s*([^\n]+)'
    matches = list(re.finditer(pattern, content))

    if matches:
        format_version = 'v1-standard'

    # 检查缩进过滤：有意义的股票行有缩进（`  - 名称`），标题行无缩进
    # 过滤掉可能误匹配的层级标题：一次扫描，利用 finditer 的位置信息
    if format_version == 'v1-standard':
        filtered = []
        for m in matches:
            name = m.group(1)
            code = m.group(2)
            desc = m.group(3)
            # 利用 finditer 位置信息：检查匹配前是否有缩进
            line_start = content.rfind('\n', 0, m.start()) + 1
            line_before = content[max(0, line_start - 5):line_start]
            # 如果前面有缩进（2+空格）或前面是列表上下文，保留
            if '\n  ' in line_before or '\n\t' in line_before:
                filtered.append((name, code, desc))
            else:
                # 无缩进也可能是有效行（第一级列表），保留
                filtered.append((name, code, desc))
        matches = filtered
    
    for name, code, desc in matches:
        stocks.append({
            'code': code.strip(),
            'name': name.strip(),
            'description': desc.strip(),
            'source_file': filepath,
            '_format': format_version,
            '_completeness': 'full'
        })
    
    return stocks


def extract_review_results(filepath):
    """从审查报告中提取审查结果 - 多格式自适应"""
    results = []
    content = safe_read_file(filepath)
    if content is None:
        return results

    format_version = 'unknown'
    completeness = 'empty'

    # === 策略：优先级匹配 ===
    # 优先尝试主格式（`## 代码 名称`），无结果再尝试备用格式

    # 格式1：`## 代码 名称`（2026-05+ 标准格式）
    blocks = re.split(r'##\s+(\d{6})\s+([^\n]+)\n', content)
    if len(blocks) >= 4:
        format_version = 'v1-standard'
    
    # 格式2：`### 代码 名称`（三级标题变体）
    if len(blocks) < 4 or not any(b.strip() for b in blocks[3:min(6, len(blocks))] if b.strip()):
        blocks2 = re.split(r'###\s+(\d{6})\s+([^\n]+)\n', content)
        if len(blocks2) >= 4:
            blocks = blocks2
            format_version = 'v1-fallback-1'
    
    # 格式3：行级匹配（旧格式 `• 名称（代码）：综合分XX |`）
    if len(blocks) < 4:
        line_results = []
        # 匹配 `• 名称（代码）：综合分XX` 或 `- 名称（代码）：综合分XX`
        line_pattern = r'[•\-]\s*([^（\n]+)（(\d{6})）[：:]\s*综合分(\d+)\s*\|?'
        for line_match in re.finditer(line_pattern, content):
            name = line_match.group(1).strip()
            code = line_match.group(2)
            score = int(line_match.group(3))
            
            # 从行中提取流转方向：`→ 升级XX` 或 `→ 降级XX`
            line_rest = content[line_match.end():line_match.end()+100]
            flow_match = re.search(r'→\s*(升级|降级|保留|排除)\s*(\S*)', line_rest)
            flow = flow_match.group(1) if flow_match else ''
            target_pool = flow_match.group(2).strip() if flow_match else ''
            
            line_results.append({
                'code': code, 'name': name, 'score': score,
                'flow': flow, 'target_pool': target_pool,
                'risk_tags': [],
                'source_file': filepath,
                'date': parse_date_from_filename(os.path.basename(filepath)),
                '_format': 'v1-fallback-2',
                '_completeness': 'partial'
            })
        
        if line_results:
            results = line_results
            format_version = 'v1-fallback-2'
            completeness = 'partial'
            # 如果行级匹配有结果，直接返回
            return results
    
    # 块格式解析（格式1或格式2）
    for i in range(1, len(blocks), 3):
        if i+2 >= len(blocks):
            break
        code = blocks[i].strip()
        name = blocks[i+1].strip()
        block_content = blocks[i+2]

        # 提取综合评分
        score_match = re.search(r'\*\*综合评分\*\*\s*\|\s*\*\*(\d+)\*\*\s*\|', block_content)
        score = int(score_match.group(1)) if score_match else 0

        # 提取流转方向和目标池（兼容双箭头和单箭头）
        flow = ''
        target_pool = ''
        # 先试双箭头 `→ X → Y`
        flow_match = re.search(r'→\s*(升级|降级|保留|排除)\s*→\s*([^\n]+)', block_content)
        if flow_match:
            flow = flow_match.group(1)
            target_pool = flow_match.group(2).strip()
        else:
            # 再试单箭头 `→ X Y`（旧格式）
            flow_match2 = re.search(r'→\s*(升级|降级|保留|排除)\s+(\S+)', block_content)
            if flow_match2:
                flow = flow_match2.group(1)
                target_pool = flow_match2.group(2).strip()

        # 提取风险标记
        risk_tags = []
        if '一票否决' in block_content and '无' not in block_content.split('一票否决')[0][-20:]:
            risk_tags.append('一票否决')
        loss_context = block_content
        if ('PE为负' in loss_context or '连续亏损' in loss_context) and '无亏损' not in loss_context:
            risk_tags.append('亏损')
        if ('估值泡沫' in loss_context) and '无' not in loss_context.split('PE')[0][-10:]:
            risk_tags.append('估值泡沫')

        completeness = 'full' if score > 0 else 'partial'
        results.append({
            'code': code,
            'name': name,
            'score': score,
            'flow': flow,
            'target_pool': target_pool,
            'risk_tags': risk_tags,
            'source_file': filepath,
            'date': parse_date_from_filename(os.path.basename(filepath)),
            '_format': format_version,
            '_completeness': completeness
        })
    
    return results


def extract_decision_results(filepath):
    """从决策报告中提取决策结果 - 多格式自适应"""
    content = safe_read_file(filepath)
    if content is None:
        return []
    
    result = {
        'date': parse_date_from_filename(os.path.basename(filepath)),
        'source_file': filepath,
        'is_empty': False,
        'main_stocks': [],
        'backup_stocks': [],
        'reason': '',
        'issues': [],
        '_format': 'unknown',
        '_completeness': 'empty'
    }
    
    # 检查空仓决策
    if re.search(r'(空仓|暂无.*股票|建议空仓|空仓等待)', content):
        result['is_empty'] = True
    
    # === 多格式主推提取 ===
    format_version = 'unknown'
    
    # 格式1: ### 【主推】名称(代码)（新格式）
    main_pattern1 = r'###\s*【主推】\s*([^\n（]+)[（(](\d{6})[）)]'
    mains = re.findall(main_pattern1, content)
    if mains:
        format_version = 'v1-standard'
    
    # 格式2: 【主推】名称(代码)（旧格式，无###前缀）
    if not mains:
        main_pattern2 = r'^【主推】\s*([^\n（]+)[（(](\d{6})[）)]'
        mains = re.findall(main_pattern2, content, re.MULTILINE)
        if mains:
            format_version = 'v1-fallback-1'
    
    for name, code in mains:
        #  在主推段落中找仓位：按 ### 分块后，在对应股票块内搜索
        main_section = content[:300]  # fallback
        # 找到该股票所在的 ### 块
        stock_tag = f'{code}'
        sections = re.split(r'(?m)^###\s+', content)
        for sec in sections:
            if stock_tag in sec:
                main_section = sec[:500]
                break
        pos_match = re.search(r'(?:单笔仓位|仓位)[：:]?\s*(\d+)%\s*(?!仓位|总|整体)', main_section)
        if pos_match:
            # 确认匹配段不包含涨幅、权重、百分比等干扰上下文
            match_start = max(0, pos_match.start() - 10)
            match_context = main_section[match_start:pos_match.end() + 10]
            if any(kw in match_context for kw in ['涨幅', '权重', '百分比']):
                pos_match = None
        pos = int(pos_match.group(1)) if pos_match else 15
        result['main_stocks'].append({'name': name.strip(), 'code': code, 'position': pos})
    
    # === 多格式备选提取 ===
    # 格式1: ### 【备选观察】名称(代码)
    backups = re.findall(r'###\s*【备选观察】\s*([^\n（]+)[（(](\d{6})[）)]', content)
    
    # 格式2: 【备选观察】名称(代码)（无###）
    if not backups:
        backups = re.findall(r'^【备选观察】\s*([^\n（]+)[（(](\d{6})[）)]', content, re.MULTILINE)
    
    # 格式3: 备选观察名称(代码)（无【】方括号）
    if not backups:
        backups = re.findall(r'备选观察\s*[:：]?\s*([^\n（]+)[（(](\d{6})[）)]', content)
    
    for name, code in backups:
        backup_section = content[:200]  # fallback
        stock_tag = f'{code}'
        sections = re.split(r'(?m)^###\s+', content)
        for sec in sections:
            if stock_tag in sec:
                backup_section = sec[:500]
                break
        pos_match = re.search(r'(?:单笔仓位|仓位)[：:]?\s*(\d+)%\s*(?!仓位|总|整体)', backup_section)
        if pos_match:
            # 确认匹配段不包含涨幅、权重、百分比等干扰上下文
            match_start = max(0, pos_match.start() - 10)
            match_context = backup_section[match_start:pos_match.end() + 10]
            if any(kw in match_context for kw in ['涨幅', '权重', '百分比']):
                pos_match = None
        pos = int(pos_match.group(1)) if pos_match else 0
        result['backup_stocks'].append({'name': name.strip(), 'code': code, 'position': pos})
    
    # 提取空仓理由（兼容###和##标题层级）
    reason = ''
    for prefix in ['###', '##']:
        reason_pattern = rf'{prefix}\s*空仓理由\n(.*?)(?:---|\n\n)'
        reason_match = re.search(reason_pattern, content, re.DOTALL)
        if reason_match:
            reason = reason_match.group(1).strip()[:200]
            break
    
    if reason:
        result['reason'] = reason
    
    # 标记格式完整性
    result['_format'] = format_version
    result['_completeness'] = 'full' if (result['main_stocks'] or result['is_empty']) else 'partial'
    
    return [result]


def detect_p0_issues(review_results, decision_results, fast_screen_stocks,
                     trading_days=None, performance_map=None):
    """检测P0级问题"""
    issues = []
    if performance_map is None:
        performance_map = {}

    # 0. 质疑报告缺失检测
    if trading_days:
        for date in trading_days:
            质疑_file = HISTORY_DIR / f"{date}_质疑审查报告.md"
            if not 质疑_file.exists():
                issues.append({
                    'type': 'P0-质疑报告缺失',
                    'date': date,
                    'code': '',
                    'name': '',
                    'score': 0,
                    'detail': f'决策前未提供质疑审查报告，违反DecisionAgent宪法'
                })
    
    # 1. 过热漏检检测
    for r in review_results:
        if r.get('flow') == '保留' and r.get('score', 0) >= 75:
            issues.append({
                'type': 'P0-过热漏检',
                'date': r['date'],
                'code': r['code'],
                'name': r['name'],
                'score': r.get('score', 0),
                'detail': '评分较高但未触发降级，存在过热风险'
            })
    
    # 2. 降级延迟检测 - 评分<60但未及时降级
    for r in review_results:
        if r.get('score', 100) < 60 and r.get('flow') != '降级':
            issues.append({
                'type': 'P0-降级延迟',
                'date': r['date'],
                'code': r['code'],
                'name': r['name'],
                'score': r.get('score', 0),
                'detail': f"评分{r.get('score', 0)}分但未降级，应降入边缘池"
            })
    
    # 3. 快筛漏检检测
    fast_screen_codes = {s['code'] for s in fast_screen_stocks}
    for r in review_results:
        if r.get('flow') == '升级' and r['code'] not in fast_screen_codes:
            issues.append({
                'type': 'P1-快筛漏检',
                'date': r['date'],
                'code': r['code'],
                'name': r['name'],
                'detail': '审查升级但快筛未覆盖，快筛覆盖不完整'
            })
    
    # 4. 决策与审查不一致检测
    for d in decision_results:
        if not d['is_empty'] and d['main_stocks']:
            decision_codes = {s['code'] for s in d['main_stocks']}
            review_passed_codes = {r['code'] for r in review_results if r.get('score', 0) >= 75}
            for stock in d['main_stocks']:
                if stock['code'] not in review_passed_codes:
                    issues.append({
                        'type': 'P1-决策越权',
                        'date': d['date'],
                        'code': stock['code'],
                        'detail': f"决策执行但审查未通过（评分<70）: {stock['name']}"
                    })
    
    # 5. P0-实盘亏损：推荐后连跌3个交易日
    for code, perf in performance_map.items():
        if perf and not perf.get('is_profit', True):
            # 尝试从review/decision中查找对应的名称
            code_clean = code.split('_')[0] if '_' in code else code
            matched_name = code_clean
            for r in review_results:
                if r['code'] == code_clean:
                    matched_name = r['name']
                    break
            for d in decision_results:
                for s in d.get('main_stocks', []):
                    if s['code'] == code_clean:
                        matched_name = s['name']
                        break
            issues.append({
                'type': 'P0-实盘亏损',
                'date': perf.get('entry_date', ''),
                'code': code_clean,
                'name': matched_name,
                'score': 0,
                'detail': (f"推荐后{perf.get('hold_days', 3)}个交易日跌幅"
                           f"{perf.get('change_pct', 0):.1f}%，入{perf.get('entry_date', '')}→出{perf.get('exit_date', '')}")
            })
    
    return issues


def calculate_fast_screen_accuracy(files, trading_days):
    """计算快筛层准确率"""
    if not files['快筛']:
        return None
    
    all_stocks = []
    for filepath in files['快筛']:
        stocks = extract_fast_screen_stocks(filepath)
        for s in stocks:
            s['date'] = parse_date_from_filename(os.path.basename(filepath))
        all_stocks.extend(stocks)
    
    # 统计各分类
    categories = defaultdict(list)
    for s in all_stocks:
        desc = s.get('description', '')
        if ('AI' in desc or '半导体' in desc or '算力' in desc or '光模块' in desc) and '智能家居' not in desc:
            categories['AI/半导体'].append(s)
        elif '医药' in desc or '医疗' in desc:
            categories['医药'].append(s)
        elif '军工' in desc or '国防' in desc:
            categories['军工'].append(s)
        elif '资源' in desc or '煤炭' in desc or '贵金属' in desc or '黄金' in desc:
            categories['资源品'].append(s)
        elif ('机器人' in desc or ('智能' in desc and not any(kw in desc for kw in ['家居', '家', '电网', '管理']))):
            categories['机器人/智能'].append(s)
        elif '油运' in desc or '航运' in desc:
            categories['油运/物流'].append(s)
        else:
            categories['其他'].append(s)
    
    return {
        'total_predictions': len(all_stocks),
        'by_category': {k: len(v) for k, v in categories.items()},
        'details': all_stocks
    }


def calculate_review_accuracy(files, trading_days, performance_map=None, all_review_results=None):
    """计算审查层准确率 - 修正版

    指标定义：
    - upgrade_market_accuracy: 升级标的后3日涨跌>0的比例（真实市场验证）
    - upgrade_persistence_rate: 升级标的在后续审查中日评分仍>=70的比例（跨日稳定性）
    - downgrade_accuracy: 降级的股票评分应<60（自洽性检查，暂无行情验证）
    """
    if not files['审查']:
        return None
    
    all_results = []
    upgrade_count = 0
    downgrade_count = 0
    hold_count = 0
    exclude_count = 0
    
    score_distribution = defaultdict(int)
    
    for filepath in files['审查']:
        results = extract_review_results(filepath)
        for r in results:
            all_results.append(r)
            flow = r.get('flow', '')
            score = r.get('score', 0)
            
            if flow == '升级':
                upgrade_count += 1
            elif flow == '降级':
                downgrade_count += 1
            elif flow == '保留':
                hold_count += 1
            elif flow == '排除':
                exclude_count += 1
            
            if score >= 80:
                score_distribution['优秀(80+)'] += 1
            elif score >= 70:
                score_distribution['良好(70-79)'] += 1
            elif score >= 60:
                score_distribution['一般(60-69)'] += 1
            else:
                score_distribution['较差(<60)'] += 1
    
    # ── A: 升级市场准确率（真实行情验证）───────────────────────
    # 替换原来的"升级的评分应>=70"自洽性检查
    upgrade_market_correct = 0
    upgrade_market_total = 0
    for r in all_results:
        if r.get('flow') == '升级':
            key = f"{r['code']}_{r['date']}"
            perf = performance_map.get(key) if performance_map else None
            if perf and perf.get('change_pct') is not None:
                upgrade_market_total += 1
                if perf['change_pct'] > 0:
                    upgrade_market_correct += 1
    upgrade_market_accuracy = round(upgrade_market_correct / upgrade_market_total * 100, 1) if upgrade_market_total > 0 else 0
    
    # ── B: 升级评分稳定性（跨日维持率）────────────────────────
    # 检查升级后的股票，在后续交易日审查中评分是否仍>=70
    upgrade_persist_numerator = 0
    upgrade_persist_denominator = 0
    
    # 建索引：code → [(date, score)]
    code_scores = defaultdict(list)
    for r in all_results:
        code_scores[r['code']].append((r['date'], r.get('score', 0)))
    # 按日期排序
    for code in code_scores:
        code_scores[code].sort(key=lambda x: x[0])
    
    for r in all_results:
        if r.get('flow') == '升级':
            code = r['code']
            upgrade_date = r['date']
            upgrade_score = r.get('score', 0)
            # 找该标的下一次审查记录的评分
            sorted_scores = code_scores.get(code, [])
            for d, s in sorted_scores:
                if d > upgrade_date:
                    upgrade_persist_denominator += 1
                    if s >= 70:
                        upgrade_persist_numerator += 1
                    break  # 只看下一次审查
    upgrade_persistence_rate = round(upgrade_persist_numerator / upgrade_persist_denominator * 100, 1) if upgrade_persist_denominator > 0 else None
    
    # 降级准确率：降级的股票评分应<60
    downgrade_correct = sum(1 for r in all_results if r.get('flow') == '降级' and r.get('score', 0) < 60)
    downgrade_accuracy = round(downgrade_correct / downgrade_count * 100, 1) if downgrade_count > 0 else 0
    
    return {
        'total_reviews': len(all_results),
        'upgrades': upgrade_count,
        'upgrade_market_correct': upgrade_market_correct,
        'upgrade_market_accuracy': upgrade_market_accuracy,
        'upgrade_persist_numerator': upgrade_persist_numerator,
        'upgrade_persist_denominator': upgrade_persist_denominator,
        'upgrade_persistence_rate': upgrade_persistence_rate,
        'downgrades': downgrade_count,
        'downgrade_correct': downgrade_correct,
        'downgrade_accuracy': downgrade_accuracy,
        'holds': hold_count,
        'excludes': exclude_count,
        'score_distribution': dict(score_distribution),
        'details': all_results
    }


def calculate_decision_accuracy(files, review_results, trading_days):
    """计算决策层准确率 - 修正版"""
    if not files['决策']:
        return None
    
    all_results = []
    empty_days = 0
    execute_days = 0
    empty_reasons = []
    
    for filepath in files['决策']:
        results = extract_decision_results(filepath)
        for r in results:
            all_results.append(r)
            if r['is_empty']:
                empty_days += 1
                empty_reasons.append(r.get('reason', '')[:100])
            else:
                execute_days += 1
    
    total_days = len(all_results)
    empty_accuracy = round(empty_days / total_days * 100, 1) if total_days > 0 else 0
    
    return {
        'total_days': total_days,
        'empty_days': empty_days,
        'execute_days': execute_days,
        'empty_accuracy': empty_accuracy,
        'empty_reasons': empty_reasons,
        'details': all_results
    }


def load_skeptic_blocked_codes(trading_days):
    """读取各交易日 SkepticGate 裁决，返回 {date: set(blocked_codes)}"""
    blocked_map = {}
    for date in trading_days:
        verdict_file = HISTORY_DIR / f"{date}_质疑审查裁决.json"
        if verdict_file.exists():
            try:
                data = safe_read_json(verdict_file, default={})
                blocked_list = data.get("blocked", [])
                if blocked_list:
                    blocked_map[date] = {
                        s.get("code", "") for s in blocked_list if s.get("code")
                    }
            except Exception:
                pass
    return blocked_map


def generate_report(days=7, output_file=None):
    """生成回头看报告"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    trading_days = get_trading_days(days)
    files = get_report_files(trading_days)
    
    # 获取所有数据
    fast_screen_stocks = []
    for f in files['快筛']:
        fast_screen_stocks.extend(extract_fast_screen_stocks(f))
    
    review_results = []
    for f in files['审查']:
        review_results.extend(extract_review_results(f))
    
    decision_results = []
    for f in files['决策']:
        decision_results.extend(extract_decision_results(f))
    
    # 计算各层指标
    fast_screen = calculate_fast_screen_accuracy(files, trading_days)
    decision = calculate_decision_accuracy(files, review_results, trading_days)

    # === 实战验证（策略A）===
    load_price_cache(str(OUTPUT_DIR / '.price_cache.json'))

    performance_map = {}
    verified_stocks = []

    # 验证审查层升级/保留标的
    for r in review_results:
        if r.get('flow') == '升级' and r.get('date'):
            perf = verify_recommendation(r['code'], r['date'], hold_days=3)
            if perf:
                r['_performance'] = perf
                verified_stocks.append({**r, '_perf': perf})
                key = f"{r['code']}_{r['date']}"
                performance_map[key] = perf

    # 加载 SkepticGate 阻塞数据（过滤 LLM 文本推荐中被质疑拦截的标的）
    blocked_map = load_skeptic_blocked_codes(trading_days)

    # 验证决策层主推标的（过滤被 SkepticGate 阻塞的标的）
    for d in decision_results:
        if not d['is_empty'] and d.get('date'):
            date = d['date']
            blocked_today = blocked_map.get(date, set())
            for s in d.get('main_stocks', []):
                code = s.get('code', '')
                if not code or code in blocked_today:
                    continue  # 被 SkepticGate 阻塞 → 不计为有效决策
                perf = verify_recommendation(code, date, hold_days=3)
                if perf:
                    key = f"{code}_{date}"
                    performance_map.setdefault(key, perf)

    save_price_cache()

    # 审查层准确率（传入 performance_map + review_results 做真实行情验证）
    review = calculate_review_accuracy(files, trading_days, performance_map=performance_map)

    # 计算实战准确率
    perf_profits = [p for p in performance_map.values() if p and p.get('is_profit')]
    perf_total = [p for p in performance_map.values() if p]
    actual_accuracy = round(len(perf_profits) / len(perf_total) * 100, 1) if perf_total else 0
    avg_return = round(sum(p['change_pct'] for p in perf_total) / len(perf_total), 2) if perf_total else 0

    # 检测P0级问题
    p0_issues = detect_p0_issues(review_results, decision_results, fast_screen_stocks,
                                  trading_days=trading_days, performance_map=performance_map)
    p0_count = sum(1 for i in p0_issues if i['type'].startswith('P0'))
    p1_count = sum(1 for i in p0_issues if i['type'].startswith('P1'))
    
    # === 跨期对比（策略C）===
    state = load_state()
    history = state.get('history', [])
    
    # 统计P0类型出现次数
    p0_type_counts = defaultdict(int)
    for issue in p0_issues:
        p0_type_counts[issue['type']] += 1
    
    # 构建当前指标
    current_metrics = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'fast_screen_count': fast_screen['total_predictions'] if fast_screen else 0,
        'review_total': review['total_reviews'] if review else 0,
        'upgrade_market_accuracy': review['upgrade_market_accuracy'] if review else 0,
        'upgrade_persistence_rate': review['upgrade_persistence_rate'] if review else 0,
        'downgrade_accuracy': review['downgrade_accuracy'] if review else 0,
        'p0_count': p0_count,
        'p1_count': p1_count,
        'actual_accuracy': actual_accuracy,
        'avg_return': avg_return,
        'perf_sample_count': len(perf_total),
        'p0_type_counts': dict(p0_type_counts)
    }
    
    # 计算各指标趋势
    trends = {}
    for key in ['p0_count', 'p1_count', 'fast_screen_count']:
        delta, val, trend = calc_trend(history, key, is_pct=False)
        trends[key] = {'delta': delta, 'trend': trend}
    for key in ['upgrade_market_accuracy', 'upgrade_persistence_rate', 'downgrade_accuracy', 'actual_accuracy', 'avg_return']:
        delta, val, trend = calc_trend(history, key, is_pct=True)
        trends[key] = {'delta': delta, 'trend': trend}
    
    # 顽固问题检测
    persistent_issues = get_persistent_issues(state)
    
    # 生成报告
    report_date = datetime.now().strftime('%Y-%m-%d')
    start_date = trading_days[-1] if trading_days else ''
    end_date = trading_days[0] if trading_days else ''
    
    report = f"""# 天枢"回头看"报告

**报告日期：** {report_date}  
**分析范围：** {start_date} 至 {end_date}（{days}个交易日）  
**报告类型：** 自动化准确率回顾  
**交易日列表：** {', '.join(trading_days)}

---

"""
    
    # P0级问题预警（如有）
    if p0_issues:
        if p0_count > 0:
            report += """## ⚠️ P0级问题预警

"""
            for issue in p0_issues:
                if issue['type'].startswith('P0'):
                    report += f"""### 🔴 {issue['type']}

| 项目 | 详情 |
|------|------|
| 日期 | {issue['date']} |
| 代码 | {issue['code']} |
| 名称 | {issue['name']} |
| 评分 | {issue.get('score', 'N/A')} |
| 说明 | {issue['detail']} |

"""

    # 📈 趋势对比（策略C）
    if history:
        trend_rows = [
            ('P0问题', 'p0_count', f'{p0_count}个', True),
            ('实战准确率', 'actual_accuracy', f'{actual_accuracy}%', False),
            ('升级市场准确率', 'upgrade_market_accuracy', f'{review["upgrade_market_accuracy"] if review else "N/A"}%', False),
            ('升级评分维持率', 'upgrade_persistence_rate', f'{review["upgrade_persistence_rate"]}%' if review and review["upgrade_persistence_rate"] is not None else 'N/A', False),
            ('降级准确率', 'downgrade_accuracy', f'{review["downgrade_accuracy"] if review else "N/A"}%', False),
            ('快筛数量', 'fast_screen_count', f'{fast_screen["total_predictions"] if fast_screen else 0}只', True),
            ('平均收益', 'avg_return', f'{avg_return:+.2f}%', False),
        ]

        report += """## 📈 趋势对比（进化检测）

| 指标 | 本期 | 趋势 |
|------|------|------|
"""
        for label, key, cur_val, reverse in trend_rows:
            t = trends.get(key, {})
            delta = t.get('delta', 0)
            trend_dir = t.get('trend', 'stable')

            if trend_dir == 'up':
                icon = '🟢' if reverse else '🔴'
            elif trend_dir == 'down':
                icon = '🔴' if reverse else '🟢'
            else:
                icon = '⚪'

            delta_str = f'{delta:+.1f}' if delta != 0 else '0'
            report += f"| {label} | {cur_val} | {icon} {delta_str} |\n"

        if persistent_issues:
            report += "\n### ⛔ 顽固问题（连续3期出现）\n\n"
            for ptype in persistent_issues:
                report += f"- **{ptype}** — 持续存在，建议深度排查\n"
        report += "\n---\n"

    # 一、快筛层回顾
    report += """## 📊 一、快筛层回顾

"""
    
    if fast_screen:
        report += f"""| 指标 | 数值 |
|------|------|
| 识别强势对象总数 | {fast_screen['total_predictions']}只 |
| 覆盖交易日 | {len(files['快筛'])}天 |

### 分类统计

| 分类 | 数量 |
|------|------|
"""
        for cat, count in sorted(fast_screen['by_category'].items(), key=lambda x: -x[1]):
            report += f"| {cat} | {count}只 |\n"
        
        report += f"""
### 详细记录

| 日期 | 代码 | 名称 | 描述摘要 |
|------|------|------|----------|
"""
        for d in fast_screen['details'][:30]:
            desc_short = d['description'][:50] + '...' if len(d['description']) > 50 else d['description']
            report += f"| {d['date']} | {d['code']} | {d['name']} | {desc_short} |\n"
    else:
        report += "⚠️ 未找到快筛报告数据\n"
    
    # 二、审查层回顾
    report += f"""
---

## 📊 二、审查层回顾

"""
    
    if review:
        persist_str = f"{review['upgrade_persistence_rate']}%" if review['upgrade_persistence_rate'] is not None else "N/A"
        report += f"""| 指标 | 数值 |
|------|------|
| 审查标的总数 | {review['total_reviews']}只 |
| 升级标的 | {review['upgrades']}只 |
| 升级市场准确率 | {review['upgrade_market_accuracy']}% (3日涨跌) |
| 升级评分维持率 | {persist_str} (跨日评分>=70) |
| 降级标的 | {review['downgrades']}只 |
| 降级准确率 | {review['downgrade_accuracy']}% |
| 保留标的 | {review['holds']}只 |
| 排除标的 | {review['excludes']}只 |

### 评分分布

| 等级 | 数量 |
|------|------|
"""
        for level, count in review['score_distribution'].items():
            report += f"| {level} | {count}只 |\n"
        
        report += f"""
### 详细记录

| 日期 | 代码 | 名称 | 评分 | 流转方向 | 目标池 |
|------|------|------|------|----------|--------|
"""
        for d in review['details'][:30]:
            flow = d.get('flow', 'N/A')
            target = d.get('target_pool', 'N/A')
            score = d.get('score', 'N/A')
            report += f"| {d['date']} | {d['code']} | {d['name']} | {score} | {flow} | {target} |\n"
    else:
        report += "⚠️ 未找到审查报告数据\n"
    
    # 三、决策层回顾
    report += f"""
---

## 📊 三、决策层回顾

"""
    
    if decision:
        report += f"""| 指标 | 数值 |
|------|------|
| 总交易日 | {decision['total_days']}天 |
| 空仓天数 | {decision['empty_days']}天 |
| 执行天数 | {decision['execute_days']}天 |
| 空仓准确率 | {decision['empty_accuracy']}% |

### 空仓理由摘要

"""
        for i, reason in enumerate(decision['empty_reasons'][:5], 1):
            report += f"{i}. {reason}\n"
        
        report += f"""
### 详细记录

| 日期 | 决策类型 | 主推标的 | 仓位 | 备选标的 |
|------|----------|----------|------|----------|
"""
        for d in decision['details']:
            decision_type = "空仓" if d['is_empty'] else "执行"
            main_str = ", ".join([f"{s['name']}({s['position']}%)" for s in d['main_stocks']]) or "无"
            backup_str = ", ".join([f"{s['name']}({s['position']}%)" for s in d['backup_stocks']]) or "无"
            # 标记被 SkepticGate 阻塞的主推标的
            date = d.get('date', '')
            blocked_today = blocked_map.get(date, set())
            main_str_filtered = []
            for s in d.get('main_stocks', []):
                code = s.get('code', '')
                if code in blocked_today:
                    main_str_filtered.append(f"{s['name']}({s['position']}%)🔒")
                else:
                    main_str_filtered.append(f"{s['name']}({s['position']}%)")
            main_display = ", ".join(main_str_filtered) or "无"
            report += f"| {d['date']} | {decision_type} | {main_display} | - | {backup_str} |\n"
    else:
        report += "⚠️ 未找到决策报告数据\n"
    
    # 四、实战回测（策略A：实战验证）
    # 计算大盘基准：获取每只验证股票对应时期的上证指数涨跌幅
    import urllib.request  # 确保导入
    index_changes = {}
    for r in verified_stocks:
        p = r.get('_perf', {})
        if p and p.get('entry_date'):
            idx_chg = calc_index_change(p['entry_date'], hold_days=3)
            if idx_chg is not None:
                index_changes[r['code']] = idx_chg

    # 计算相对收益
    relative_returns = []
    for r in verified_stocks:
        p = r.get('_perf', {})
        if p and p.get('change_pct') is not None:
            stock_chg = p['change_pct']
            idx_chg = index_changes.get(r['code'])
            if idx_chg is not None:
                relative_returns.append(stock_chg - idx_chg)

    avg_relative = round(sum(relative_returns) / len(relative_returns), 2) if relative_returns else 0

    # 计算大盘平均收益
    all_idx_chgs = [v for v in index_changes.values() if v is not None]
    avg_index_return = round(sum(all_idx_chgs) / len(all_idx_chgs), 2) if all_idx_chgs else 0

    report += f"""
|---

## 📊 四、实战回测（进化检测）

| 指标 | 数值 |
|------|------|
| 验证推荐总数 | {len(perf_total)}只 |
| 盈利标的 | {len(perf_profits)}只 |
| 亏损标的 | {len(perf_total) - len(perf_profits)}只 |
|| 实战准确率(3日后涨) | {actual_accuracy}% |
| 平均收益 | {avg_return:+.2f}% |
| 上证指数同期均值 | {avg_index_return:+.2f}% |
| 平均相对收益 | {avg_relative:+.2f}% |

### 各标的实战表现

| 日期 | 代码 | 名称 | 方向 | 买入价 | 卖出价 | 3日涨跌 | 上证同期 | 相对收益 |
|------|------|------|------|--------|--------|---------|---------|---------|
"""
    for r in verified_stocks:
        p = r.get('_perf', {})
        flow_mark = '✅' if p.get('is_profit') else '❌'
        idx_chg = index_changes.get(r['code'], 'N/A')
        idx_str = f"{idx_chg:+.2f}%" if isinstance(idx_chg, (int, float)) else "N/A"
        rel_str = f"{(p.get('change_pct', 0) - idx_chg):+.2f}%" if isinstance(idx_chg, (int, float)) else "N/A"
        report += (f"| {r.get('date', '')} | {r['code']} | {r['name']} | "
                   f"{r.get('flow', '')} | {p.get('buy_price', 'N/A')} | "
                   f"{p.get('sell_price', 'N/A')} | {flow_mark} {p.get('change_pct', 0):+.2f}% | "
                   f"{idx_str} | {rel_str} |\n")
    
    report += """
### 实战损伤报告

"""
    loss_stocks = [(k, v) for k, v in performance_map.items() if v and not v.get('is_profit')]
    if loss_stocks:
        for key, p in sorted(loss_stocks, key=lambda x: x[1].get('change_pct', 0)):
            report += f"- 🔴 {key.split('_')[0]} 亏损{p.get('change_pct', 0):+.2f}%（入{p.get('entry_date', '')}→出{p.get('exit_date', '')}）\n"
    else:
        report += "- ✅ 无亏损标的，所有验证均盈利\n"

    # 五、综合评估
    report += f"""
---

## 📊 五、综合评估

| 层级 | 关键指标 | 数值 | 评价 |
|------|----------|------|------|
| 快筛层 | 识别数量 | {fast_screen['total_predictions'] if fast_screen else 'N/A'}只 | {'充足' if fast_screen and fast_screen['total_predictions'] >= 15 else '偏少' if fast_screen else '无数据'} |
| 审查层 | 升级市场准确率 | {review['upgrade_market_accuracy'] if review else 'N/A'}% | {'优秀' if review and review['upgrade_market_accuracy'] >= 60 else '良好' if review and review['upgrade_market_accuracy'] >= 40 else '待优化' if review else '无数据'} |
| 审查层 | 升级评分维持率 | {review['upgrade_persistence_rate'] if review else 'N/A'}% | {'优秀' if review and review['upgrade_persistence_rate'] >= 80 else '良好' if review and review['upgrade_persistence_rate'] >= 60 else '待优化' if review else '无数据'} |
| 审查层 | 降级准确率 | {review['downgrade_accuracy'] if review else 'N/A'}% | {'优秀' if review and review['downgrade_accuracy'] >= 80 else '良好' if review and review['downgrade_accuracy'] >= 60 else '待优化' if review else '无数据'} |
| 决策层 | 空仓天数占比 | {decision['empty_accuracy'] if decision else 'N/A'}% | {'保守' if decision and decision['empty_accuracy'] >= 50 else '积极' if decision else '无数据'} |

### P0级问题统计

| 问题类型 | 数量 | 严重程度 |
|----------|------|----------|
"""
    
    # p0_count computed earlier
    # p1_count computed earlier
    report += f"| P0级问题 | {p0_count}个 | 🔴 紧急 |\n"
    report += f"| P1级问题 | {p1_count}个 | 🟡 重要 |\n"
    
    # P1级问题详情
    if p1_count > 0:
        report += f"""
### P1级问题详情

"""
        for issue in p0_issues:
            if issue['type'].startswith('P1'):
                name = issue.get('name', issue.get('code', '未知'))
                report += f"- **{issue['type']}** ({issue['date']}): {name} - {issue['detail']}\n"
    
    # 六、改进建议
    report += f"""
---

## 📊 六、改进建议

"""

    suggestions = []
    
    if p0_count > 0:
        suggestions.append(f"P0: 发现{p0_count}个P0级问题，需立即处理（过热漏检/降级延迟等）")
    
    if review and review['downgrade_accuracy'] < 80:
        suggestions.append("P1: 降级准确率偏低，建议优化降级触发条件，增加过热检测模块")
    
    if review and review['upgrade_market_accuracy'] < 40:
        suggestions.append(f"P2: 升级市场准确率仅{review['upgrade_market_accuracy']}%，升级推荐的市场验证效果不佳，建议审查升级标准")
    if review and review['upgrade_persistence_rate'] < 60:
        suggestions.append(f"P2: 升级评分维持率仅{review['upgrade_persistence_rate']}%，升级标的评分跨日稳定性不足，建议回顾评分一致性")
    
    if fast_screen and fast_screen['total_predictions'] < 10:
        suggestions.append("P1: 快筛识别数量偏少，建议扩大扫描范围或降低阈值")
    
    if decision and decision['empty_days'] > decision['execute_days']:
        suggestions.append("P2: 空仓天数较多，建议检查是否存在优质标的被遗漏")
    
    suggestions.append("P2: 建议延长回顾窗口，获取更多实战验证样本")
    suggestions.append("P2: 建议新增'黄色预警'状态，减少降级延迟")
    suggestions.append("P2: 建议建立快筛-审查-决策闭环追踪机制")
    
    # 实战验证相关建议
    if perf_total:
        if actual_accuracy < 60:
            suggestions.append(f"P1: 实战准确率仅{actual_accuracy}%，3日后仅{len(perf_profits)}/{len(perf_total)}只盈利，建议复盘审查评分标准")
        elif actual_accuracy < 80:
            suggestions.append(f"P2: 实战准确率{actual_accuracy}%（{len(perf_profits)}/{len(perf_total)}），有提升空间，建议结合行情回测优化")
        else:
            suggestions.append(f"P2: 实战准确率{actual_accuracy}%（{len(perf_profits)}/{len(perf_total)}）表现良好")
    
    # 顽固问题告警
    if persistent_issues:
        for ptype in persistent_issues:
            suggestions.append(f"P1: ⛔ {ptype} 已连续3期出现顽固问题，建议深度排查根因")
    
    for i, s in enumerate(suggestions, 1):
        report += f"{i}. {s}\n"
    
    report += f"""
---

## 📊 七、附录：原始数据摘要

### 快筛报告文件
"""
    for f in files['快筛']:
        report += f"- {os.path.basename(f)}\n"
    
    report += f"""
### 审查报告文件
"""
    for f in files['审查']:
        report += f"- {os.path.basename(f)}\n"
    
    report += f"""
### 决策报告文件
"""
    for f in files['决策']:
        report += f"- {os.path.basename(f)}\n"
    
    report += f"""
---

*报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*  
*生成工具：天枢回头看自动化模块 v3*  
*工作目录：{cfg.root}*
"""
    
    # Level-3：进化闭环 — 输出结构化 P0 摘要供 auto_heal 解析
    p0_severity = {"P0-过热漏检": 5, "P0-降级延迟": 4, "P0-实盘亏损": 3, "P0-质疑报告缺失": 4}
    top3_p0 = sorted([i for i in p0_issues if i['type'].startswith('P0')],
                     key=lambda x: p0_severity.get(x['type'], 1), reverse=True)[:3]
    top3_json = json.dumps([{
        "type": issue.get("type", ""),
        "date": issue.get("date", ""),
        "code": issue.get("code", ""),
        "name": issue.get("name", ""),
        "score": issue.get("score", 0),
        "description": issue.get("detail", ""),
    } for issue in top3_p0], ensure_ascii=False, indent=2)
    report += f"\n\n<!-- EVO-ISSUES -->\n```json\n{top3_json}\n```\n<!-- /EVO-ISSUES -->\n"
    
    # 保存报告
    if output_file:
        safe_write_file(output_file, report)
        print(f"✅ 报告已保存至: {output_file}")
    
    # 保存本期状态（策略C）
    save_state(current_metrics)
    
    return report


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='天枢回头看自动化模块 v3')
    parser.add_argument('--days', type=int, default=7, help='回顾天数（默认7天）')
    parser.add_argument('--output', action='store_true', help='保存报告到文件')
    parser.add_argument('--cron', action='store_true', help='Cron模式（无交互输出）')
    
    args = parser.parse_args()
    
    output_file = None
    if args.output:
        output_file = OUTPUT_DIR / f"{datetime.now().strftime('%Y-%m-%d')}_回头看报告_v3.md"
    
    report = generate_report(days=args.days, output_file=output_file)
    
    if not args.cron:
        print(report)


if __name__ == '__main__':
    main()