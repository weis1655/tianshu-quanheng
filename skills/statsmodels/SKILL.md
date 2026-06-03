---
name: statsmodels
description: "时间序列分析、统计建模、量化金融分析的 Statsmodels 使用指南"
version: 1.0.0
author: K-Dense-AI (adapted for Hermes Agent)
tags: [statsmodels, time-series, statistics, quantitative-finance]
---

# Statsmodels Skill

**来源:** K-Dense-AI/scientific-agent-skills (BSD-3-Clause)
**适配:** Hermes Agent Skill 格式
**适用场景:** 时间序列分析、统计建模、量化金融分析

---

## 何时使用

- 时间序列建模：ARIMA、SARIMAX、VAR、指数平滑
- 回归分析：OLS、WLS、GLS、分位数回归
- 广义线性模型：Logistic、Poisson、Gamma
- 统计检验与诊断：异方差、自相关、正态性检验
- 模型比较：AIC/BIC、似然比检验
- 因果效应估计
- 发表级统计表格与推断

---

## 快速上手

### 线性回归 (OLS)

```python
import statsmodels.api as sm
import numpy as np
import pandas as pd

# 准备数据 - 必须添加常数项(截距)
X = sm.add_constant(X_data)

# 拟合OLS模型
model = sm.OLS(y, X)
results = model.fit()

# 查看综合结果
print(results.summary())

# 关键指标
print(f"R-squared: {results.rsquared:.4f}")
print(f"Coefficients:\n{results.params}")
print(f"P-values:\n{results.pvalues}")

# 带置信区间的预测
predictions = results.get_prediction(X_new)
pred_summary = predictions.summary_frame()
print(pred_summary)  # 包含均值、CI、预测区间

# 诊断
from statsmodels.stats.diagnostic import het_breuschpagan
bp_test = het_breuschpagan(results.resid, X)
print(f"Breusch-Pagan p-value: {bp_test[1]:.4f}")
```

### 时间序列 (ARIMA)

```python
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.graphics.tsaplots import plot_acf, plot_pacf
from statsmodels.tsa.stattools import adfuller

# 检查平稳性
adf_result = adfuller(y_series)
print(f"ADF p-value: {adf_result[1]:.4f}")

if adf_result[1] > 0.05:
    # 非平稳，差分处理
    y_diff = y_series.diff().dropna()
    
    # 绘制ACF/PACF识别p, q
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))
    plot_acf(y_diff, lags=40, ax=ax1)
    plot_pacf(y_diff, lags=40, ax=ax2)
    plt.show()

# 拟合ARIMA(p,d,q)
model = ARIMA(y_series, order=(1, 1, 1))
results = model.fit()
print(results.summary())

# 预测
forecast = results.forecast(steps=10)
forecast_obj = results.get_forecast(steps=10)
forecast_df = forecast_obj.summary_frame()
print(forecast_df)  # 包含均值和置信区间

# 残差诊断
results.plot_diagnostics(figsize=(12, 8))
plt.show()
```

### 逻辑回归 (分类)

```python
from statsmodels.discrete.discrete_model import Logit

X = sm.add_constant(X_data)
model = Logit(y_binary, X)
results = model.fit()
print(results.summary())

# 优势比
odds_ratios = np.exp(results.params)
print("Odds ratios:\n", odds_ratios)

# 预测概率
probs = results.predict(X)
predictions = (probs > 0.5).astype(int)

# 模型评估
from sklearn.metrics import classification_report, roc_auc_score
print(classification_report(y_binary, predictions))
print(f"AUC: {roc_auc_score(y_binary, probs):.4f}")
```

### 广义线性模型 (GLM)

```python
import statsmodels.api as sm

# Poisson回归(计数数据)
X = sm.add_constant(X_data)
model = sm.GLM(y_counts, X, family=sm.families.Poisson())
results = model.fit()
print(results.summary())

# 率比
rate_ratios = np.exp(results.params)
print("Rate ratios:\n", rate_ratios)

# 检查过离散
overdispersion = results.pearson_chi2 / results.df_resid
print(f"Overdispersion: {overdispersion:.2f}")

if overdispersion > 1.5:
    # 改用负二项
    from statsmodels.discrete.count_model import NegativeBinomial
    nb_model = NegativeBinomial(y_counts, X)
    nb_results = nb_model.fit()
    print(nb_results.summary())
```

---

## 核心能力清单

### 1. 线性回归模型
- OLS: 标准线性回归(独立同分布误差)
- WLS: 加权最小二乘(异方差误差)
- GLS: 广义最小二乘(任意协方差结构)
- GLSAR: 带自回归误差的GLS
- 分位数回归: 条件分位数(对异常值稳健)
- 混合效应: 层次/多层模型

### 2. 时间序列分析
- ARIMA/SARIMAX: 自回归积分滑动平均
- VAR: 向量自回归(多变量)
- 指数平滑: Holt-Winters
- 状态空间模型: 卡尔曼滤波
- 协整分析: Engle-Granger、Johansen检验
- 谱分析: 周期图、Welch方法

### 3. 统计检验
- 单位根检验: ADF、KPSS、PP
- 异方差检验: Breusch-Pagan、White
- 自相关检验: Durbin-Watson、Ljung-Box
- 正态性检验: Jarque-Bera、Shapiro-Wilk
- 格兰杰因果检验
- 结构突变检验: Chow、CUSUM

### 4. 模型诊断
- 残差分析: Q-Q图、残差直方图
- 影响点检测: Cook距离、DFBETAS
- 多重共线性: VIF
- 异方差稳健标准误: HC0-HC3
- 自相关稳健标准误: Newey-West

---

## 量化金融常用工作流

### 收益率建模

```python
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.stattools import adfuller, acf, pacf

# 1. 收益率计算
returns = df['close'].pct_change().dropna()

# 2. 平稳性检验
adf_result = adfuller(returns)
print(f"ADF统计量: {adf_result[0]:.4f}, p值: {adf_result[1]:.4f}")

# 3. 自相关分析
acf_vals = acf(returns, lags=20)
pacf_vals = pacf(returns, lags=20)

# 4. GARCH效应检验(使用arch包)
from arch import arch_model
garch = arch_model(returns, vol='GARCH', p=1, q=1)
garch_result = garch.fit(disp='off')
print(garch_result.summary())
```

### 因子模型 (Fama-French)

```python
# 多因子回归
factors = ['market', 'SMB', 'HML', 'MOM']
X = sm.add_constant(ff_factors[factors])
model = sm.OLS(returns, X)
results = model.fit()

# 因子显著性
print(results.summary())

# 因子暴露
factor_exposures = results.params
print(f"市场因子暴露: {factor_exposures['market']:.4f}")
print(f"SMB暴露: {factor_exposures['SMB']:.4f}")
```

### 风险价值 (VaR) 估计

```python
import numpy as np
from scipy import stats

# 历史模拟法
var_95 = np.percentile(returns, 5)
print(f"95% VaR (历史模拟): {var_95:.4f}")

# 参数法 (假设正态)
mu = returns.mean()
sigma = returns.std()
var_95_param = stats.norm.ppf(0.05, mu, sigma)
print(f"95% VaR (参数法): {var_95_param:.4f}")

# 条件VaR (Expected Shortfall)
es_95 = returns[returns <= var_95].mean()
print(f"95% ES: {es_95:.4f}")
```

---

## 注意事项

⚠️ **重要提示:**
1. 线性回归必须 `sm.add_constant(X)` 添加截距项
2. 时间序列先做平稳性检验(ADF)，非平稳需差分
3. AIC/BIC越小越好，用于模型选择
4. p值<0.05视为统计显著
5. 多重共线性时VIF>10需警惕
6. 金融时间序列常有GARCH效应，残差可能非独立

---

## 依赖安装

```bash
# 方案一（推荐，使用 uv）：
uv pip install statsmodels pandas numpy scipy matplotlib
# 方案二（无 uv 时回退）：
# pip install statsmodels pandas numpy scipy matplotlib
# 金融分析额外需要
# uv pip install arch yfinance
# pip install arch yfinance
```
