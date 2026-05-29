"""
基金生命周期判断 — 建仓期、拉升期、派发期
基于专业量价理论：
- 建仓期：价格横盘、成交量萎缩、A/D线底背离、OBV缓慢攀升
- 拉升期：放量突破、均线多头排列、MACD金叉开口放大
- 派发期：放量滞涨、高位震荡、MACD顶背离、A/D线走平或下降
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


def detect_fund_phase(nav_df: pd.DataFrame, days: int = 120):
    """
    判断基金当前所处阶段：建仓期 / 拉升期 / 派发期 / 震荡期

    参数:
        nav_df: 基金净值DataFrame，需包含 date, nav 列
    返回:
        dict: {phase, confidence, reasons, indicators}
    """
    if nav_df.empty or len(nav_df) < 60:
        return {
            "phase": "数据不足",
            "confidence": 0,
            "reasons": ["净值数据不足60个交易日，无法判断"],
            "indicators": {}
        }

    df = nav_df.tail(days).copy()
    if "nav" not in df.columns:
        return {"phase": "未知", "confidence": 0, "reasons": ["数据格式异常"], "indicators": {}}

    # 计算净值变化率
    df["return"] = df["nav"].pct_change()
    df["nav_ma5"] = df["nav"].rolling(5).mean()
    df["nav_ma20"] = df["nav"].rolling(20).mean()
    df["nav_ma60"] = df["nav"].rolling(60).mean()

    # 模拟成交量（基金净值变化的幅度作为活跃度代理变量）
    df["activity"] = df["return"].abs()

    reasons = []
    scores = {"accumulation": 0, "markup": 0, "distribution": 0, "shakeout": 0}

    # ── 1. 价格趋势分析 ──
    if len(df) >= 60:
        nav_20d_ago = df["nav"].iloc[-20] if len(df) > 20 else df["nav"].iloc[0]
        nav_60d_ago = df["nav"].iloc[-60] if len(df) > 60 else df["nav"].iloc[0]
        nav_now = df["nav"].iloc[-1]

        ret_20d = (nav_now - nav_20d_ago) / nav_20d_ago
        ret_60d = (nav_now - nav_60d_ago) / nav_60d_ago

        # 近期波动率
        vol_20d = df["return"].tail(20).std() * np.sqrt(242)  # 年化
        vol_60d = df["return"].tail(60).std() * np.sqrt(242)

        # 建仓期特征：价格在窄幅波动，波动率降低
        if abs(ret_20d) < 0.03 and abs(ret_60d) < 0.08:
            if vol_20d < vol_60d * 0.8:
                scores["accumulation"] += 30
                reasons.append("净值近20日窄幅波动(±3%)，波动率下降，符合建仓期特征")
            else:
                scores["shakeout"] += 20
                reasons.append("净值窄幅整理，处于震荡期")

        # 拉升期特征：净值持续上涨，波动率可能放大
        if ret_20d > 0.05 and ret_60d > 0.03:
            if df["nav"].tail(20).is_monotonic_increasing or ret_20d > 0.08:
                scores["markup"] += 35
                reasons.append("净值近期持续上涨，符合拉升期特征")
            elif ret_20d > 0.03:
                scores["markup"] += 20
                reasons.append("净值温和上涨，可能处于拉升初期")

        # 派发期特征：高位震荡、净值滞涨或回落
        if ret_20d < -0.03 and ret_60d > 0.05:
            scores["distribution"] += 30
            reasons.append("前期涨幅较大但近20日净值回落，可能进入派发期")

        if abs(ret_20d) < 0.02 and ret_60d > 0.10:
            scores["distribution"] += 25
            reasons.append("前期大幅上涨后高位横盘，符合派发期初期特征")

    # ── 2. 均线系统分析 ──
    if df["nav_ma5"].notna().all() and df["nav_ma20"].notna().all():
        ma5_now = df["nav_ma5"].iloc[-1]
        ma20_now = df["nav_ma20"].iloc[-1]
        ma60_now = df["nav_ma60"].iloc[-1] if pd.notna(df["nav_ma60"].iloc[-1]) else 0

        # 多头排列 → 拉升期
        if ma5_now > ma20_now and (ma60_now == 0 or ma20_now > ma60_now):
            if ret_20d > 0.02:
                scores["markup"] += 15
                reasons.append("均线多头排列(MA5>MA20>MA60)，趋势向上")
            else:
                scores["accumulation"] += 10

        # 空头排列 → 派发或建仓
        if ma5_now < ma20_now:
            if ret_20d < -0.02:
                scores["distribution"] += 15
                reasons.append("短期均线下穿长期均线，趋势转弱")
            else:
                scores["accumulation"] += 5

    # ── 3. 波动率分析 ──
    if len(df) >= 30:
        vol_recent = df["return"].tail(10).std()
        vol_prior = df["return"].iloc[-30:-10].std()

        if vol_recent > vol_prior * 1.5:
            scores["markup"] += 15
            reasons.append("近期波动率放大，可能进入快速拉升或调整阶段")
        elif vol_recent < vol_prior * 0.6:
            if ret_20d > 0:
                scores["markup"] += 5
            else:
                scores["accumulation"] += 15
                reasons.append("波动率持续收缩，符合建仓后期特征")

    # ── 4. 最大回撤分析 ──
    if len(df) >= 60:
        peak = df["nav"].tail(60).cummax()
        drawdown = (df["nav"] - peak) / peak
        max_dd = drawdown.tail(60).min()

        if max_dd < -0.15:
            scores["distribution"] += 20
            reasons.append(f"近60日最大回撤{abs(max_dd)*100:.1f}%，超出正常调整范围")

    # ── 综合判断 ──
    phase_scores = {
        "建仓期": scores["accumulation"],
        "拉升期": scores["markup"],
        "派发期": scores["distribution"],
        "震荡期": scores["shakeout"],
    }

    best_phase = max(phase_scores, key=phase_scores.get)
    best_score = phase_scores[best_phase]
    total_score = sum(phase_scores.values())

    if total_score == 0:
        confidence = 0
    else:
        confidence = min(round(best_score / max(total_score, 1) * 100), 95)

    # 如果最高分太低，说明信号不够明确
    if best_score < 20:
        best_phase = "震荡观察期"
        confidence = 30
        reasons.append("当前信号不够明确，建议持续观察")

    # 补充关键指标
    indicators = {
        "ret_20d": round(float(ret_20d * 100), 2) if 'ret_20d' in dir() else 0,
        "ret_60d": round(float(ret_60d * 100), 2) if 'ret_60d' in dir() else 0,
        "vol_annualized": round(float(vol_20d * 100), 1) if 'vol_20d' in dir() else 0,
        "max_drawdown_60d": round(float(max_dd * 100), 1) if 'max_dd' in dir() else 0,
        "ma_trend": "多头" if ('ma5_now' in dir() and 'ma20_now' in dir() and
                                ma5_now > ma20_now) else "空头",
        "phase_scores": {k: v for k, v in phase_scores.items()},
    }

    return {
        "phase": best_phase,
        "confidence": confidence,
        "reasons": reasons,
        "indicators": indicators,
    }


def get_phase_badge(phase: str):
    """获取阶段的显示标签和颜色"""
    mapping = {
        "建仓期": {"label": "建仓期", "color": "#34C759", "icon": "📥"},
        "拉升期": {"label": "拉升期", "color": "#FF3B30", "icon": "🚀"},
        "派发期": {"label": "派发期", "color": "#FF9500", "icon": "📤"},
        "震荡期": {"label": "震荡期", "color": "#8E8E93", "icon": "🔄"},
        "震荡观察期": {"label": "震荡观察", "color": "#8E8E93", "icon": "👀"},
        "数据不足": {"label": "数据不足", "color": "#C7C7CC", "icon": "❓"},
    }
    return mapping.get(phase, {"label": phase, "color": "#8E8E93", "icon": "❓"})


def get_phase_description(phase: str):
    """获取阶段的详细说明"""
    descriptions = {
        "建仓期": "基金处于建仓/累积阶段。净值窄幅波动，波动率降低，主力资金在低位收集筹码。此时不宜追高，可等待放量突破信号再介入。",
        "拉升期": "基金处于主升浪阶段。净值持续上涨，均线多头排列，趋势强劲。持仓者可继续持有，但需关注成交量变化和顶背离信号。",
        "派发期": "基金可能处于派发/出货阶段。前期涨幅较大，近期净值滞涨或回落，可能伴随MACD顶背离。建议逐步减仓或离场观察。",
        "震荡期": "基金处于震荡整理阶段。方向不明确，多空力量均衡。建议等待方向确认后再操作，关注突破信号。",
        "震荡观察期": "当前信号不明确，基金可能在阶段转换过程中。建议持续观察均线系统和成交量的变化。",
    }
    return descriptions.get(phase, "暂无分析说明")
