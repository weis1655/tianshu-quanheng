#!/usr/bin/env python3
"""数据质量监控脚本（每日运行，输出异常告警）"""
import json, sys
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
POOL = ROOT / "五池管理"
TODAY = datetime.now()

issues = []

def check_pool(name):
    try:
        d=json.load(open(POOL/f"{name}.json"))
        stocks=d.get('stocks',[])
        # 完整性
        if not stocks and name not in ('持仓池','S级操作池'):
            issues.append(f"⚠️ {name}: 空池异常")
        # 重复代码
        codes=[s.get('代码') for s in stocks]
        if len(codes)!=len(set(codes)):
            issues.append(f"❌ {name}: 存在重复代码")
        # 时效性
        ut=d.get('统计',{}).get('更新日期','')
        if ut:
            try:
                dt=datetime.strptime(ut[:10],'%Y-%m-%d')
                if (TODAY-dt).days>3:
                    issues.append(f"⏰ {name}: 统计未更新{(TODAY-dt).days}天")
            except:
                pass
        return len(stocks)
    except Exception as e:
        issues.append(f"❌ {name}: 加载失败 {e}")
        return 0

# 五池检查
pools = ['快筛候选池','重点观察池','边缘池','持仓池','S级操作池','重点观察池_历史池']
for p in pools:
    check_pool(p)

# 决策日志
try:
    dl=json.load(open(DATA/"decision_log.json"))
    if isinstance(dl,list):
        zero=sum(1 for x in dl if x.get('actual_pnl') in (0,'0',None))
        if len(dl)>0 and zero/len(dl)>0.8:
            issues.append(f"⚠️ 决策日志: {zero}/{len(dl)} 无盈亏数据({zero/len(dl)*100:.0f}%)")
except: pass

# 盟主持仓
try:
    hp=json.load(open(DATA/"盟主持仓.json"))
    holdings=hp.get('持仓',[])
    if len(holdings)==0:
        issues.append("ℹ️ 盟主持仓为空")
except: pass

# 熔断器
try:
    cb=json.load(open(DATA/"circuit_breaker_state.json"))
    if cb.get('state')=='open':
        issues.append(f"🔥 熔断器OPEN: {cb.get('consecutive_failures')}次连续失败")
except: pass

if issues:
    print("📊 数据质量监控报告", datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("="*40)
    for i in issues:
        print(i)
else:
    print("✅ 数据质量正常")
