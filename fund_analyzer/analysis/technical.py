"""
技术分析引擎 — MA、MACD、RSI、KDJ、布林带、金叉死叉、支撑位阻力位
基于专业金融理论，参考《期货市场技术分析》(John Murphy)及中国A股实战经验
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


def calc_ma(df: pd.DataFrame, periods: list = None):
    """计算移动平均线"""
    periods = periods or [5, 10, 20, 60, 120, 250]
    for p in periods:
        if len(df) >= p:
            df[f"MA{p}"] = df["close"].rolling(window=p).mean()
    return df


def calc_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9):
    """计算MACD指标"""
    if len(df) < slow:
        return df
    df["EMA12"] = df["close"].ewm(span=fast, adjust=False).mean()
    df["EMA26"] = df["close"].ewm(span=slow, adjust=False).mean()
    df["DIF"] = df["EMA12"] - df["EMA26"]
    df["DEA"] = df["DIF"].ewm(span=signal, adjust=False).mean()
    df["MACD"] = 2 * (df["DIF"] - df["DEA"])
    return df


def calc_rsi(df: pd.DataFrame, period: int = 14):
    """计算RSI相对强弱指标"""
    if len(df) < period:
        return df
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))
    return df


def calc_kdj(df: pd.DataFrame, n: int = 9):
    """计算KDJ随机指标"""
    if len(df) < n:
        return df
    low_min = df["low"].rolling(window=n).min()
    high_max = df["high"].rolling(window=n).max()
    rsv = (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan) * 100
    df["K"] = rsv.ewm(com=2, adjust=False).mean()
    df["D"] = df["K"].ewm(com=2, adjust=False).mean()
    df["J"] = 3 * df["K"] - 2 * df["D"]
    return df


def calc_bollinger(df: pd.DataFrame, period: int = 20, std: int = 2):
    """计算布林带"""
    if len(df) < period:
        return df
    df["BOLL_MID"] = df["close"].rolling(window=period).mean()
    std_val = df["close"].rolling(window=period).std()
    df["BOLL_UP"] = df["BOLL_MID"] + std * std_val
    df["BOLL_DN"] = df["BOLL_MID"] - std * std_val
    df["BOLL_WIDTH"] = (df["BOLL_UP"] - df["BOLL_DN"]) / df["BOLL_MID"] * 100
    return df


def calc_obv(df: pd.DataFrame):
    """计算OBV能量潮"""
    if "volume" not in df.columns:
        return df
    df["price_change"] = df["close"].diff()
    df["OBV"] = 0.0
    for i in range(1, len(df)):
        if df["price_change"].iloc[i] > 0:
            df.loc[df.index[i], "OBV"] = df["OBV"].iloc[i - 1] + df["volume"].iloc[i]
        elif df["price_change"].iloc[i] < 0:
            df.loc[df.index[i], "OBV"] = df["OBV"].iloc[i - 1] - df["volume"].iloc[i]
        else:
            df.loc[df.index[i], "OBV"] = df["OBV"].iloc[i - 1]
    return df


def calc_ad_line(df: pd.DataFrame):
    """计算累积/派发线 (A/D Line) — 判断建仓/派发期的核心指标"""
    if "volume" not in df.columns or "high" not in df.columns or "low" not in df.columns:
        return df
    high_low = df["high"] - df["low"]
    high_low = high_low.replace(0, np.nan)
    clv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / high_low
    money_flow = clv * df["volume"]
    df["AD"] = money_flow.cumsum()
    return df


def calc_all_indicators(df: pd.DataFrame):
    """一次性计算全部技术指标"""
    if df.empty or len(df) < 5:
        return df
    df = calc_ma(df)
    df = calc_macd(df)
    df = calc_rsi(df)
    df = calc_kdj(df)
    df = calc_bollinger(df)
    df = calc_obv(df)
    df = calc_ad_line(df)
    return df


# ── 形态检测 ────────────────────────────────────────────


def detect_golden_cross(df: pd.DataFrame, fast_ma: str = "MA5", slow_ma: str = "MA20"):
    """检测金叉 — 短期均线上穿长期均线，看涨信号"""
    if fast_ma not in df.columns or slow_ma not in df.columns:
        return []
    crosses = []
    for i in range(1, len(df)):
        if (df[fast_ma].iloc[i] > df[slow_ma].iloc[i] and
                df[fast_ma].iloc[i - 1] <= df[slow_ma].iloc[i - 1]):
            crosses.append({
                "date": df["date"].iloc[i].strftime("%Y-%m-%d"),
                "type": "golden_cross",
                "fast_ma": fast_ma,
                "slow_ma": slow_ma,
                "price": float(df["close"].iloc[i]),
            })
    return crosses


def detect_death_cross(df: pd.DataFrame, fast_ma: str = "MA5", slow_ma: str = "MA20"):
    """检测死叉 — 短期均线下穿长期均线，看跌信号"""
    if fast_ma not in df.columns or slow_ma not in df.columns:
        return []
    crosses = []
    for i in range(1, len(df)):
        if (df[fast_ma].iloc[i] < df[slow_ma].iloc[i] and
                df[fast_ma].iloc[i - 1] >= df[slow_ma].iloc[i - 1]):
            crosses.append({
                "date": df["date"].iloc[i].strftime("%Y-%m-%d"),
                "type": "death_cross",
                "fast_ma": fast_ma,
                "slow_ma": slow_ma,
                "price": float(df["close"].iloc[i]),
            })
    return crosses


def detect_support_resistance(df: pd.DataFrame, lookback: int = 60):
    """检测支撑位和阻力位 — 基于近期高点和低点"""
    if len(df) < lookback:
        return {"support": [], "resistance": []}
    recent = df.tail(lookback)
    # 支撑位：近期低点
    lows = recent["low"].nsmallest(3).tolist()
    # 阻力位：近期高点
    highs = recent["high"].nlargest(3).tolist()
    # 均线支撑
    ma_supports = []
    for ma_col in ["MA20", "MA60", "MA120"]:
        if ma_col in recent.columns:
            val = recent[ma_col].iloc[-1]
            if pd.notna(val):
                ma_supports.append({"ma": ma_col, "value": round(float(val), 2)})

    current_price = float(df["close"].iloc[-1])

    return {
        "support": [round(float(x), 2) for x in sorted(lows)[:3]],
        "resistance": [round(float(x), 2) for x in sorted(highs, reverse=True)[:3]],
        "ma_supports": ma_supports,
        "current_price": round(current_price, 2),
    }


def detect_macd_divergence(df: pd.DataFrame, lookback: int = 60):
    """检测MACD顶背离/底背离"""
    if "DIF" not in df.columns or len(df) < lookback:
        return {"top_divergence": False, "bottom_divergence": False}

    recent = df.tail(lookback)
    price_high = recent["close"].max()
    price_high_idx = recent["close"].idxmax()
    price_low = recent["close"].min()
    price_low_idx = recent["close"].idxmin()

    dif_max = recent["DIF"].max()
    dif_max_idx = recent["DIF"].idxmax()
    dif_min = recent["DIF"].min()
    dif_min_idx = recent["DIF"].idxmin()

    # 顶背离：价格创新高但DIF不创新高
    top_div = (price_high_idx > dif_max_idx and
               recent["close"].iloc[-1] >= price_high * 0.95 and
               recent["DIF"].iloc[-1] < dif_max * 0.8)

    # 底背离：价格创新低但DIF不创新低
    bottom_div = (price_low_idx > dif_min_idx and
                  recent["close"].iloc[-1] <= price_low * 1.05 and
                  recent["DIF"].iloc[-1] > dif_min * 1.2)

    return {"top_divergence": bool(top_div), "bottom_divergence": bool(bottom_div)}


def detect_head_shoulders(df: pd.DataFrame, lookback: int = 120):
    """检测头肩顶/头肩底形态 — 基于局部极值点模式匹配"""
    if len(df) < lookback:
        return {"head_shoulders_top": False, "head_shoulders_bottom": False,
                "confidence": 0, "description": "数据不足"}

    recent = df.tail(lookback)
    highs = recent["high"].values
    lows = recent["low"].values

    # 寻找局部极值点
    peaks = []
    troughs = []
    for i in range(2, len(recent) - 2):
        if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and \
           highs[i] > highs[i + 1] and highs[i] > highs[i + 2]:
            peaks.append((i, highs[i]))
        if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and \
           lows[i] < lows[i + 1] and lows[i] < lows[i + 2]:
            troughs.append((i, lows[i]))

    # 头肩顶检测：三个峰，中间最高
    hs_top = False
    hs_bottom = False
    confidence = 0
    desc = ""

    if len(peaks) >= 3:
        last_three = peaks[-3:]
        p1, p2, p3 = last_three[0][1], last_three[1][1], last_three[2][1]
        # 中间峰最高(头部)，两侧峰较低(肩部)，且头>左肩>右肩
        if p2 > p1 and p2 > p3 and abs(p1 - p3) / max(p1, p3) < 0.15:
            hs_top = True
            confidence = 70
            desc = "检测到疑似头肩顶形态：三个连续高点，中间(头部)最高，左右肩高度接近。如跌破颈线则确认看跌信号。"
        # 头比肩高20%以上额外加分
        if p2 > p1 * 1.05 and p2 > p3 * 1.05:
            confidence = 85
            desc = "检测到头肩顶形态（置信度较高）：头部显著高于双肩，若跌破颈线位则确认中期顶部，建议关注。"
    elif len(peaks) >= 2 and len(troughs) >= 2:
        # 尝试简化检测
        last_peak = peaks[-1][1]
        prev_peak = peaks[-2][1]
        if prev_peak > last_peak * 1.03:
            hs_top = True
            confidence = 55
            desc = "检测到下降高点结构（右肩低于左肩），可能是头肩顶的肩部形成中，需继续观察。"

    # 头肩底检测：三个谷，中间最低
    if len(troughs) >= 3:
        last_three = troughs[-3:]
        t1, t2, t3 = last_three[0][1], last_three[1][1], last_three[2][1]
        if t2 < t1 and t2 < t3 and abs(t1 - t3) / max(t1, t3) < 0.15:
            hs_bottom = True
            confidence = max(confidence, 70)
            desc = "检测到疑似头肩底形态：三个连续低点，中间(头部)最低，左右肩接近。如突破颈线则确认看涨信号。"
        if t2 < t1 * 0.95 and t2 < t3 * 0.95:
            confidence = max(confidence, 85)
            desc = "检测到头肩底形态（置信度较高）：头部显著低于双肩，若突破颈线位则确认中期底部，建议关注反转机会。"

    return {
        "head_shoulders_top": bool(hs_top),
        "head_shoulders_bottom": bool(hs_bottom),
        "confidence": int(confidence),
        "description": desc or "未检测到明显的头肩形态",
    }


# ── 综合状态判断 ──────────────────────────────────────


def analyze_stock_state(df: pd.DataFrame):
    """
    综合判断股票当前状态：
    - 横盘震荡 (sideways/consolidation)
    - 破位下跌 (breakdown)
    - 低位缩量 (low_volume_bottom)
    - 强势上涨 (strong_uptrend)
    - 弱势下跌 (weak_downtrend)
    """
    if df.empty or len(df) < 20:
        return {"state": "数据不足", "indicators": []}

    if "MA5" not in df.columns or "MACD" not in df.columns:
        df = calc_all_indicators(df)
    recent_20 = df.tail(20)
    recent_5 = df.tail(5)
    latest = df.iloc[-1]

    indicators = []
    state = "震荡整理"

    # 1. 横盘震荡判断
    if len(recent_20) >= 10:
        high_range = recent_20["high"].max()
        low_range = recent_20["low"].min()
        price_range_pct = (high_range - low_range) / low_range * 100

        # 20日波动小于8%视为横盘
        if price_range_pct < 8:
            state = "横盘震荡"
            indicators.append(f"近20日振幅仅{price_range_pct:.1f}%，处于窄幅横盘状态")

            # 判断是低位还是高位横盘
            ma60 = latest.get("MA60", 0) if pd.notna(latest.get("MA60")) else 0
            if ma60 > 0:
                if latest["close"] < ma60 * 0.95:
                    state = "低位横盘"
                    indicators.append("价格位于MA60下方，属低位横盘，关注放量突破信号")

    # 2. 破位下跌判断
    # 破位 = 跌破近期支撑位（20日低点或MA60）
    support_20 = recent_20["low"].min()
    ma60_val = latest.get("MA60", 0) if pd.notna(latest.get("MA60")) else 0
    if latest["close"] < support_20 * 1.02 and latest["close"] < support_20:
        vol_ratio = recent_5["volume"].mean() / recent_20["volume"].mean() if recent_20["volume"].mean() > 0 else 1
        if vol_ratio > 1.5:
            state = "放量破位下跌"
            indicators.append("跌破20日低点且成交量放大，属放量破位，风险较大")
        else:
            state = "缩量破位下跌"
            indicators.append("跌破20日低点但缩量，可能为诱空洗盘")

    if ma60_val > 0 and latest["close"] < ma60_val * 0.97 and latest["close"] < recent_5["close"].iloc[-2] if len(recent_5) >= 2 else False:
        if "破位" not in state:
            state = "破位下跌"
            indicators.append("跌破MA60重要支撑位，中期趋势转弱")

    # 3. 低位缩量判断
    if len(df) >= 60:
        recent_60 = df.tail(60)
        price_percentile = (latest["close"] - recent_60["low"].min()) / \
                           (recent_60["high"].max() - recent_60["low"].min()) * 100

        # 价格在近60日低位25%以内
        if price_percentile < 25:
            avg_vol_20 = recent_20["volume"].mean()
            avg_vol_60 = recent_60["volume"].mean()
            vol_decline = avg_vol_20 / avg_vol_60 if avg_vol_60 > 0 else 1

            if vol_decline < 0.6:
                if "低位" not in state:
                    state = "低位缩量"
                indicators.append(f"成交量萎缩至60日均量的{vol_decline*100:.0f}%，杀跌动能枯竭，关注放量反弹信号")

            # 低量柱检测（5日最低量）
            if len(recent_5) >= 3:
                vol_5min = recent_5["volume"].min()
                if latest["volume"] <= vol_5min * 1.05:
                    indicators.append("出现阶段性地量柱，多空力量趋于平衡")

    # 4. 强势/弱势判断
    if "MA5" in df.columns and "MA20" in df.columns:
        ma5 = latest.get("MA5", 0) if pd.notna(latest.get("MA5")) else 0
        ma20 = latest.get("MA20", 0) if pd.notna(latest.get("MA20")) else 0
        if ma5 > ma20 > 0:
            if latest["close"] > ma5:
                if "破位" not in state and "横盘" not in state:
                    state = "多头排列"
                    indicators.append("MA5>MA20，短期多头趋势")

    # 5. 金叉死叉检测
    golden = detect_golden_cross(df)
    death = detect_death_cross(df)
    recent_golden = [g for g in golden if g["date"] >= (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")]
    recent_death = [d for d in death if d["date"] >= (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")]

    if recent_golden:
        indicators.append(f"近日出现MA5/MA20金叉({recent_golden[-1]['date']})，短期看涨")
    if recent_death:
        indicators.append(f"近日出现MA5/MA20死叉({recent_death[-1]['date']})，短期看跌")

    # 6. MACD背离
    divergence = detect_macd_divergence(df)
    if divergence["top_divergence"]:
        indicators.append("MACD顶背离：价格高位但动能减弱，注意回调风险")
    if divergence["bottom_divergence"]:
        indicators.append("MACD底背离：价格低位但动能增强，关注反弹机会")

    return {
        "state": state,
        "indicators": indicators,
        "golden_crosses": golden[-3:],
        "death_crosses": death[-3:],
        "divergence": divergence,
    }
