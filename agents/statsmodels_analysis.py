#!/usr/bin/env python3
"""
statsmodels 时间序列分析模块
对重点观察池股票进行ARIMA建模、平稳性检验、趋势预测

用法：
  python agents/statsmodels_analysis.py              # 分析重点观察池全部股票
  python agents/statsmodels_analysis.py --code 600941  # 分析单只股票
  python agents/statsmodels_analysis.py --output report.md  # 指定输出文件
"""

import sys
import json
import argparse
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from safe_file_utils import safe_read_json
from logger import plog

import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
from path_config import ensure_agent_paths; ensure_agent_paths()

try:
    import statsmodels.api as sm
    from statsmodels.tsa.arima.model import ARIMA
    from statsmodels.tsa.stattools import adfuller, acf, pacf, kpss
    from statsmodels.graphics.tsaplots import plot_acf, plot_pacf
    import matplotlib
    matplotlib.use('Agg')  # 非交互式后端
    import matplotlib.pyplot as plt
    import yfinance as yf
except ImportError as e:
    plog("INFO", f"缺少依赖: {e}")
    plog("INFO", "请运行: uv pip install statsmodels pandas numpy matplotlib yfinance")
    sys.exit(1)


def fetch_stock_history(code: str, period: str = "6mo") -> pd.DataFrame:
    """
    获取股票历史数据
    code: 股票代码(如 600941.SS, 000001.SZ)
    """
    # 处理A股代码
    if code.startswith(('6', '5', '9')):
        api_code = f"{code}.SS"  # 上交所
    else:
        api_code = f"{code}.SZ"  # 深交所
    
    try:
        df = yf.download(api_code, period=period, progress=False)
        if df.empty or len(df) < 30:
            return None
        df = df[['Close', 'Volume']].dropna()
        df.columns = ['close', 'volume']
        df['returns'] = df['close'].pct_change().dropna()
        df['log_returns'] = np.log(df['close'] / df['close'].shift(1)).dropna()
        return df
    except Exception as e:
        plog("INFO", f"  获取 {code} 数据失败: {e}")
        return None


def check_stationarity(series: pd.Series, name: str = "序列") -> dict:
    """
    平稳性检验：ADF检验 + KPSS检验
    """
    results = {"name": name, "adf": {}, "kpss": {}}
    
    # ADF检验 (原假设: 非平稳)
    try:
        adf_result = adfuller(series.dropna())
        results["adf"] = {
            "statistic": round(adf_result[0], 4),
            "p_value": round(adf_result[1], 4),
            "critical_values": {k: round(v, 4) for k, v in adf_result[4].items()},
            "is_stationary": adf_result[1] < 0.05,
        }
    except Exception:
        results["adf"]["error"] = "检验失败"
    
    # KPSS检验 (原假设: 平稳)
    try:
        kpss_result = kpss(series.dropna(), regression='c')
        results["kpss"] = {
            "statistic": round(kpss_result[0], 4),
            "p_value": round(kpss_result[1], 4),
            "is_stationary": kpss_result[1] > 0.05,
        }
    except Exception:
        results["kpss"]["error"] = "检验失败"
    
    # 综合判断
    adf_ok = results["adf"].get("is_stationary", False)
    kpss_ok = results["kpss"].get("is_stationary", False)
    results["conclusion"] = "平稳" if (adf_ok and kpss_ok) else "非平稳" if (not adf_ok and not kpss_ok) else "不确定"
    
    return results


def fit_arima_model(series: pd.Series, order: tuple = (1, 1, 1)) -> dict:
    """
    拟合ARIMA模型并返回关键参数
    """
    results = {"order": order, "params": {}, "diagnostics": {}}
    
    try:
        model = ARIMA(series.dropna(), order=order)
        fitted = model.fit()
        
        results["params"] = {
            "aic": round(fitted.aic, 2),
            "bic": round(fitted.bic, 2),
            "hqic": round(fitted.hqic, 2),
            "coef_ar": [round(c, 4) for c in fitted.params[:len(order[0])] if not np.isnan(c)],
            "coef_ma": [round(c, 4) for c in fitted.params[len(order[0]):len(order[0])+len(order[2])] if not np.isnan(c)],
            "sigma2": round(fitted.sigma2, 6),
        }
        
        # 残差诊断
        resid = fitted.resid
        results["diagnostics"] = {
            "resid_mean": round(resid.mean(), 6),
            "resid_std": round(resid.std(), 6),
            "resid_skew": round(resid.skew(), 4),
            "resid_kurtosis": round(resid.kurtosis(), 4),
        }
        
        # 残差平稳性
        resid_adf = adfuller(resid.dropna())
        results["diagnostics"]["resid_adf_p"] = round(resid_adf[1], 4)
        results["diagnostics"]["resid_is_white_noise"] = resid_adf[1] < 0.05
        
        results["success"] = True
    except Exception as e:
        results["success"] = False
        results["error"] = str(e)
    
    return results


def forecast_arima(fitted_model, steps: int = 5) -> dict:
    """
    ARIMA预测
    """
    try:
        forecast_result = fitted_model.forecast(steps=steps)
        conf_int = fitted_model.get_forecast(steps=steps).summary_frame()
        
        return {
            "predictions": [round(float(p), 2) for p in forecast_result],
            "lower_ci": [round(float(conf_int["mean_ci_lower"]), 2)] if "mean_ci_lower" in conf_int.columns else None,
            "upper_ci": [round(float(conf_int["mean_ci_upper"]), 2)] if "mean_ci_upper" in conf_int.columns else None,
        }
    except Exception as e:
        return {"error": str(e)}


def analyze_stock(code: str, name: str = "", period: str = "6mo") -> dict:
    """
    对单只股票进行完整的时间序列分析
    """
    plog("INFO", f"\n📊 分析 {code} {name} ...")
    
    # 1. 获取数据
    df = fetch_stock_history(code, period)
    if df is None or len(df) < 30:
        return {"code": code, "name": name, "error": "数据不足或获取失败"}
    
    result = {
        "code": code,
        "name": name,
        "period": period,
        "data_points": len(df),
        "current_price": round(df['close'].iloc[-1], 2),
        "data": {
            "start_date": df.index[0].strftime('%Y-%m-%d'),
            "end_date": df.index[-1].strftime('%Y-%m-%d'),
        }
    }
    
    close_series = df['close']
    returns_series = df['returns'].dropna()
    
    # 2. 平稳性检验
    result["stationarity"] = check_stationarity(close_series, "收盘价")
    result["returns_stationarity"] = check_stationarity(returns_series, "收益率")
    
    # 3. 自相关分析
    try:
        acf_vals = acf(returns_series, lags=20)
        pacf_vals = pacf(returns_series, lags=20)
        result["acf"] = [round(v, 4) for v in acf_vals[:10]]
        result["pacf"] = [round(v, 4) for v in pacf_vals[:10]]
        
        # 判断AR/MA阶数
        # PACF在lag 1后截尾 -> AR(1)
        # ACF在lag 1后截尾 -> MA(1)
        if abs(pacf_vals[1]) > 0.2 and len([v for v in pacf_vals[2:] if abs(v) > 0.2]) <= 1:
            result["suggested_ar"] = 1
        if abs(acf_vals[1]) > 0.2 and len([v for v in acf_vals[2:] if abs(v) > 0.2]) <= 1:
            result["suggested_ma"] = 1
    except Exception:
        result["acf"] = []
        result["pacf"] = []
    
    # 4. 拟合ARIMA模型
    # 先尝试自动选择d（差分阶数）
    d = 0 if result["stationarity"]["conclusion"] == "平稳" else 1
    
    # 尝试不同(p,d,q)组合，选AIC最小的
    best_aic = float('inf')
    best_order = (1, d, 1)
    best_model_result = {"success": False, "error": "未找到有效模型"}
    
    for p in [0, 1, 2]:
        for q in [0, 1, 2]:
            order = (p, d, q)
            model_result = fit_arima_model(close_series, order)
            if model_result.get("success") and model_result["params"].get("aic", float('inf')) < best_aic:
                best_aic = model_result["params"]["aic"]
                best_order = order
                best_model_result = model_result
    
    result["best_arima"] = best_model_result
    result["best_order"] = best_order
    
    # 5. 预测
    if best_model_result.get("success"):
        try:
            model = ARIMA(close_series.dropna(), order=best_order)
            fitted = model.fit()
            forecast_result = forecast_arima(fitted, steps=5)
            result["forecast_5d"] = forecast_result
        except Exception as e:
            result["forecast_5d"] = {"error": str(e)}
    
    # 6. 趋势判断
    recent_5d = close_series.tail(5)
    recent_10d = close_series.tail(10)
    change_5d = round((recent_5d.iloc[-1] / recent_5d.iloc[0] - 1) * 100, 2)
    change_10d = round((recent_10d.iloc[-1] / recent_10d.iloc[0] - 1) * 100, 2)
    result["trend"] = {
        "5d_change_pct": change_5d,
        "10d_change_pct": change_10d,
        "5d_trend": "上升" if change_5d > 2 else "下降" if change_5d < -2 else "震荡",
        "10d_trend": "上升" if change_10d > 5 else "下降" if change_10d < -5 else "震荡",
    }
    
    # 7. 波动率分析
    result["volatility"] = {
        "daily_std": round(returns_series.std() * 100, 2),
        "annualized_vol": round(returns_series.std() * np.sqrt(252) * 100, 2),
        "max_drawdown_pct": round((close_series / close_series.expanding().max() - 1).min() * 100, 2),
    }
    
    return result


def generate_report(results: list, output_path: str = None) -> str:
    """
    生成分析报告
    """
    lines = []
    today = datetime.now().strftime("%Y-%m-%d")
    
    lines.append(f"# 【时间序列分析报告】{today}")
    lines.append("")
    lines.append("━━━")
    lines.append("")
    lines.append("## 分析说明")
    lines.append("")
    lines.append("使用statsmodels对重点观察池股票进行ARIMA时间序列建模分析。")
    lines.append("包含：平稳性检验、自相关分析、模型拟合、短期预测。")
    lines.append("")
    
    # 汇总统计
    valid = [r for r in results if "error" not in r]
    lines.append("## 📊 分析汇总")
    lines.append("")
    lines.append(f"• 分析股票数：{len(results)}只")
    lines.append(f"• 成功建模：{len(valid)}只")
    lines.append(f"• 数据不足/失败：{len(results) - len(valid)}只")
    lines.append("")
    
    # 各股票详细分析
    for r in results:
        if "error" in r:
            lines.append(f"## {r['code']} {r.get('name', '')}")
            lines.append("")
            lines.append(f"❌ {r['error']}")
            lines.append("")
            continue
        
        lines.append(f"## {r['code']} {r['name']}")
        lines.append("")
        lines.append(f"• 当前价格：{r['current_price']}元")
        lines.append(f"• 数据区间：{r['data']['start_date']} ~ {r['data']['end_date']}")
        lines.append(f"• 数据点数：{r['data_points']}个")
        lines.append("")
        
        # 平稳性
        stat = r.get("stationarity", {})
        lines.append(f"### 平稳性检验")
        lines.append("")
        lines.append(f"• ADF检验 p值：{stat.get('adf', {}).get('p_value', 'N/A')} → {'平稳' if stat.get('adf', {}).get('is_stationary') else '非平稳'}")
        ret_stat = r.get("returns_stationarity", {})
        lines.append(f"• 收益率平稳性：{ret_stat.get('conclusion', 'N/A')}")
        lines.append("")
        
        # 趋势
        trend = r.get("trend", {})
        lines.append(f"### 趋势分析")
        lines.append("")
        lines.append(f"• 5日变化：{trend.get('5d_change_pct', 0):+.2f}% ({trend.get('5d_trend', 'N/A')})")
        lines.append(f"• 10日变化：{trend.get('10d_change_pct', 0):+.2f}% ({trend.get('10d_trend', 'N/A')})")
        lines.append("")
        
        # 波动率
        vol = r.get("volatility", {})
        lines.append(f"### 波动率")
        lines.append("")
        lines.append(f"• 日波动率：{vol.get('daily_std', 0):.2f}%")
        lines.append(f"• 年化波动率：{vol.get('annualized_vol', 0):.2f}%")
        lines.append(f"• 最大回撤：{vol.get('max_drawdown_pct', 0):.2f}%")
        lines.append("")
        
        # ARIMA模型
        arima = r.get("best_arima", {})
        if arima.get("success"):
            lines.append(f"### ARIMA模型 ({r.get('best_order', 'N/A')})")
            lines.append("")
            params = arima.get("params", {})
            lines.append(f"• AIC：{params.get('aic', 'N/A')}")
            lines.append(f"• BIC：{params.get('bic', 'N/A')}")
            lines.append(f"• 残差白噪声检验p值：{arima.get('diagnostics', {}).get('resid_adf_p', 'N/A')} → {'通过' if arima.get('diagnostics', {}).get('resid_is_white_noise') else '未通过'}")
            lines.append("")
        
        # 预测
        forecast = r.get("forecast_5d", {})
        if "predictions" in forecast:
            lines.append(f"### 5日预测")
            lines.append("")
            preds = forecast["predictions"]
            lines.append(f"• 预测价格：{', '.join([f'{p}元' for p in preds])}")
            lines.append("")
        
        lines.append("---")
        lines.append("")
    
    report = "\n".join(lines)
    
    if output_path:
        Path(output_path).write_text(report, encoding="utf-8")
        plog("INFO", f"📄 报告已保存: {output_path}")
    
    return report


def main():
    parser = argparse.ArgumentParser(description="statsmodels 时间序列分析")
    parser.add_argument("--code", type=str, help="单只股票代码(如600941)")
    parser.add_argument("--name", type=str, help="股票名称")
    parser.add_argument("--period", type=str, default="6mo", help="数据周期(1mo/3mo/6mo/1y)")
    parser.add_argument("--output", type=str, help="输出报告路径")
    parser.add_argument("--pool", type=str, default="重点观察池", help="池子名称")
    args = parser.parse_args()
    
    plog("INFO", "=" * 50)
    plog("INFO", "📊 statsmodels 时间序列分析")
    plog("INFO", "=" * 50)
    
    # 确定要分析的代码列表
    if args.code:
        codes = [(args.code, args.name or "")]
    else:
        # 从池文件读取
        pool_file = PROJECT_ROOT / "五池管理" / f"{args.pool}.json"
        if pool_file.exists():
            data = json.loads(pool_file.read_text(encoding="utf-8"))
            stocks = data.get("stocks", [])
            codes = [(s.get("代码", ""), s.get("名称", "")) for s in stocks if s.get("代码")]
        else:
            plog("INFO", f"❌ 未找到池文件: {pool_file}")
            sys.exit(1)
    
    codes = [(c, n) for c, n in codes if c]
    plog("INFO", f"待分析股票：{len(codes)}只")
    for c, n in codes[:5]:
        plog("INFO", f"  • {c} {n}")
    if len(codes) > 5:
        plog("INFO", f"  ... 等{len(codes)}只")
    
    # 逐个分析
    results = []
    for code, name in codes:
        result = analyze_stock(code, name, args.period)
        results.append(result)
    
    # 生成报告
    output_path = args.output or str(PROJECT_ROOT / "data" / "历史记录" / f"{datetime.now().strftime('%Y-%m-%d')}_时间序列分析.md")
    report = generate_report(results, output_path)
    
    plog("INFO", "\n" + "=" * 50)
    plog("INFO", "✅ 分析完成")
    plog("INFO", "=" * 50)
    
    # 打印摘要
    valid = [r for r in results if "error" not in r]
    plog("INFO", f"\n📊 摘要：")
    plog("INFO", f"  成功建模：{len(valid)}/{len(results)}只")
    
    for r in valid:
        trend = r.get("trend", {})
        vol = r.get("volatility", {})
        plog("INFO", f"  • {r['code']} {r['name']}: 5日{trend.get('5d_trend','N/A')}({trend.get('5d_change_pct',0):+.1f}%), 年化波动{vol.get('annualized_vol',0):.1f}%")
    
    return results


if __name__ == "__main__":
    main()
