"""
FundRadar — 基金雷达
三层架构：基金板块总览 → 持仓深度分析 → 财经新闻聚合
数据来源：AKShare（东方财富/新浪财经/财联社）
"""
import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, Response
import pandas as pd
import numpy as np

from data.fund_data import (
    get_all_funds, get_fund_nav_history, get_fund_holdings,
    search_funds, get_fund_info, get_fund_daily_rank,
    get_etf_spot, FUND_BOARDS,
)
from data.stock_data import (
    get_stock_realtime, get_stock_history, get_stocks_realtime_batch,
    get_stock_history_with_retry, get_board_indices, get_market_index,
    get_sina_sector_boards, get_sector_fund_list,
)
from data.news_data import get_financial_news, filter_news_by_tag, get_top_headlines, get_region_options, get_tag_options
from data.ai_analysis import get_ai_analysis, analyze_fund_holdings
from analysis.technical import (
    calc_all_indicators, detect_golden_cross, detect_death_cross,
    detect_support_resistance, detect_macd_divergence,
    detect_head_shoulders, analyze_stock_state,
)
from analysis.fund_phase import detect_fund_phase, get_phase_badge, get_phase_description

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False


# 重写 Flask JSON 序列化，确保 numpy 类型被转换为原生 Python 类型
from flask.json.provider import DefaultJSONProvider

class _NumpyJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            val = float(o)
            # 浏览器 JSON.parse 不兼容 NaN/Infinity，转为 null
            return None if (np.isnan(o) or np.isinf(o)) else val
        if isinstance(o, (np.bool_,)):
            return bool(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return super().default(o)

    def encode(self, o):
        """递归清理所有 float 中的 NaN/Inf"""
        cleaned = _sanitize_floats(o)
        return super().encode(cleaned)


def _sanitize_floats(obj):
    """将 NaN/Infinity 转为 None，浏览器可安全解析"""
    if isinstance(obj, (float, np.floating)):
        val = float(obj)
        import math
        return None if math.isnan(val) or math.isinf(val) else val
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_floats(v) for v in obj]
    return obj


class _NumpyJSONProvider(DefaultJSONProvider):
    ensure_ascii = False

    def dumps(self, obj, **kwargs):
        kwargs.setdefault("cls", _NumpyJSONEncoder)
        kwargs.setdefault("ensure_ascii", self.ensure_ascii)
        kwargs.setdefault("allow_nan", False)  # 拒绝 NaN/Inf，配合 sanitize 保证干净输出
        return json.dumps(obj, **kwargs)


# Flask 3.1 在 __init__ 中已缓存 json provider，直接替换实例
app.__dict__["json"] = _NumpyJSONProvider(app)


# ──────────────────────────────────────────────
# 页面路由
# ──────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", boards=FUND_BOARDS)


# ──────────────────────────────────────────────
# API: 第一层 — 基金板块 & 基金列表
# ──────────────────────────────────────────────

@app.route("/api/boards")
def api_boards():
    """获取所有基金板块及涨跌幅"""
    try:
        market = get_market_index()
        board_indices = get_board_indices()
        return jsonify({
            "success": True,
            "boards": FUND_BOARDS,
            "market_index": market,
            "board_indices": board_indices,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        logger.error(f"API boards error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/funds")
def api_funds():
    """获取全市场基金列表（分页）"""
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 50))
        fund_type = request.args.get("type", "all")

        df = get_all_funds()
        if df.empty:
            return jsonify({"success": False, "error": "无法获取基金数据，请稍后重试"}), 500

        # 按类型筛选
        if fund_type and fund_type != "all":
            type_map = {
                "stock": "股票型", "mix": "混合型", "bond": "债券型",
                "index": "指数型", "QDII": "QDII", "money": "货币型",
                "ETF": "ETF", "LOF": "LOF", "short_term": "短期理财", "FOF": "FOF",
            }
            type_name = type_map.get(fund_type)
            if type_name:
                df = df[df["fund_type"].str.contains(type_name, na=False)]

        total = len(df)
        start = (page - 1) * per_page
        end = start + per_page
        funds = df.iloc[start:end].to_dict(orient="records")

        # 尝试获取日收益排行数据
        daily_rank = get_fund_daily_rank()
        rank_dict = {}
        if not daily_rank.empty:
            for _, row in daily_rank.head(500).iterrows():
                code = str(row.get("基金代码", ""))
                try:
                    rank_dict[code] = {
                        "daily_return": float(row.get("日增长率", 0)) if pd.notna(row.get("日增长率")) else 0,
                        "nav": float(row.get("单位净值", 0)) if pd.notna(row.get("单位净值")) else 0,
                    }
                except (ValueError, TypeError):
                    pass

        # 合并日收益
        for f in funds:
            code = str(f.get("code", ""))
            if code in rank_dict:
                f["daily_return"] = rank_dict[code]["daily_return"]
                f["nav"] = rank_dict[code]["nav"]
            else:
                f["daily_return"] = None
                f["nav"] = None

        return jsonify({
            "success": True,
            "funds": funds,
            "total": total,
            "page": page,
            "per_page": per_page,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        logger.error(f"API funds error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/funds/search")
def api_funds_search():
    """搜索基金"""
    keyword = request.args.get("q", "")
    if len(keyword) < 2:
        return jsonify({"success": False, "error": "请输入至少2位代码或关键词"}), 400
    try:
        results = search_funds(keyword)
        return jsonify({"success": True, "funds": results, "count": len(results)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/watchlist/analyze")
def api_watchlist_analyze():
    """分析自选基金列表中各基金所处阶段"""
    codes = request.args.get("codes", "")
    if not codes:
        return jsonify({"success": False, "error": "请提供基金代码"}), 400

    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    results = []
    for code in code_list:
        try:
            info = get_fund_info(code)
            nav_df = get_fund_nav_history(code, days=120)
            phase_result = detect_fund_phase(nav_df)
            badge = get_phase_badge(phase_result["phase"])
            results.append({
                "code": code,
                "name": info["name"] if info else code,
                "fund_type": info["fund_type"] if info else "",
                "phase": phase_result["phase"],
                "confidence": phase_result["confidence"],
                "badge": badge,
                "reasons": phase_result["reasons"][:3],
                "description": get_phase_description(phase_result["phase"]),
                "indicators": phase_result.get("indicators", {}),
            })
        except Exception as e:
            logger.warning(f"分析基金 {code} 失败: {e}")
            results.append({"code": code, "name": code, "phase": "分析失败", "confidence": 0,
                            "badge": {"label": "失败", "color": "#C7C7CC", "icon": "⚠️"},
                            "reasons": [str(e)]})

    return jsonify({"success": True, "results": results})


# ──────────────────────────────────────────────
# API: 第二层 — 基金持仓深度分析
# ──────────────────────────────────────────────

FUND_DETAIL_CACHE = {}

@app.route("/api/fund/<code>/detail")
def api_fund_detail(code):
    """获取基金详情 + 重仓股分析"""
    days = int(request.args.get("days", 365))
    cache_key = f"{code}_{days}"
    now = time.time()
    if cache_key in FUND_DETAIL_CACHE and now - FUND_DETAIL_CACHE[cache_key]["ts"] < 60:
        return FUND_DETAIL_CACHE[cache_key]["data"]

    try:
        info = get_fund_info(code)
        holdings = get_fund_holdings(code)
        fetch_days = max(days, 120)  # 保证阶段判断至少有120日数据
        nav_df = get_fund_nav_history(code, days=fetch_days)

        # 基金阶段判断
        phase_result = detect_fund_phase(nav_df, days=fetch_days)
        badge = get_phase_badge(phase_result["phase"])

        # 净值数据用于图表（只取用户选择的时间范围）
        nav_data = []
        if not nav_df.empty:
            for _, row in nav_df.tail(days).iterrows():
                nav_data.append({
                    "date": row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else str(row["date"]),
                    "nav": float(row["nav"]) if pd.notna(row["nav"]) else 0,
                })

        # 计算图表周期内的最大回撤（含日期标记）
        chart_max_dd = None
        if not nav_df.empty and len(nav_df) >= 5:
            chart_df = nav_df.tail(days).copy()
            peak = chart_df["nav"].cummax()
            dd_series = (chart_df["nav"] - peak) / peak
            max_dd_value = dd_series.min()
            if max_dd_value < 0:
                trough_idx = dd_series.idxmin()
                peak_subset = chart_df.loc[:trough_idx]
                if len(peak_subset) > 0:
                    peak_idx = peak_subset["nav"].idxmax()
                    chart_max_dd = {
                        "value": round(float(max_dd_value * 100), 1),
                        "peak_date": chart_df.loc[peak_idx, "date"].strftime("%Y-%m-%d"),
                        "trough_date": chart_df.loc[trough_idx, "date"].strftime("%Y-%m-%d"),
                        "peak_value": round(float(chart_df.loc[peak_idx, "nav"]), 4),
                        "trough_value": round(float(chart_df.loc[trough_idx, "nav"]), 4),
                    }

        # 重仓股分析
        holdings_analysis = []
        if not holdings.empty:
            # 批量获取所有重仓股实时行情（单次请求）
            all_codes = [str(row.get("stock_code", "")) for _, row in holdings.iterrows()]
            batch_realtime = get_stocks_realtime_batch(all_codes)

            # 并行获取所有A股历史K线（港股直接跳过）
            def _fetch_hist(code):
                if len(code) == 5:
                    return code, pd.DataFrame()
                return code, get_stock_history_with_retry(code, days=120)

            history_map = {}
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = {pool.submit(_fetch_hist, c): c for c in all_codes}
                for f in as_completed(futures):
                    code, df = f.result()
                    history_map[code] = df

            for _, row in holdings.iterrows():
                stock_code = str(row.get("stock_code", ""))
                stock_name = str(row.get("stock_name", ""))
                weight = float(row.get("weight", 0)) if pd.notna(row.get("weight")) else 0

                realtime = batch_realtime.get(stock_code) or get_stock_realtime(stock_code)
                hist_df = history_map.get(stock_code, pd.DataFrame())

                stock_analysis = {
                    "code": stock_code,
                    "name": stock_name,
                    "weight": round(weight, 2),
                    "realtime": realtime,
                }

                if not hist_df.empty and len(hist_df) >= 50:
                    # 技术分析
                    hist_df = calc_all_indicators(hist_df)
                    state_result = analyze_stock_state(hist_df)
                    sr_levels = detect_support_resistance(hist_df)
                    hs_pattern = detect_head_shoulders(hist_df)
                    divergence = detect_macd_divergence(hist_df)

                    stock_analysis["state"] = state_result["state"]
                    stock_analysis["indicators"] = state_result.get("indicators", [])
                    stock_analysis["golden_cross"] = state_result.get("golden_crosses", [])
                    stock_analysis["death_cross"] = state_result.get("death_crosses", [])
                    stock_analysis["support_resistance"] = sr_levels
                    stock_analysis["head_shoulders"] = hs_pattern
                    stock_analysis["divergence"] = divergence
                else:
                    if len(stock_code) == 5:
                        stock_analysis["state"] = "港股"
                        stock_analysis["indicators"] = ["港股暂不支持历史K线数据，仅展示实时行情"]
                    else:
                        stock_analysis["state"] = "数据不足"
                        stock_analysis["indicators"] = ["历史数据不足（需>50个交易日），无法进行技术分析"]

                holdings_analysis.append(stock_analysis)

        response_data = {
            "success": True,
            "fund_info": info,
            "phase": phase_result,
            "badge": badge,
            "phase_description": get_phase_description(phase_result["phase"]),
            "nav_data": nav_data,
            "chart_max_drawdown": chart_max_dd,
            "holdings": holdings_analysis,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            body = json.dumps(response_data, cls=_NumpyJSONEncoder, ensure_ascii=False)
            resp = Response(body, mimetype="application/json")
            FUND_DETAIL_CACHE[cache_key] = {"ts": time.time(), "data": resp}
            return resp
        except TypeError as te:
            # 定位具体哪个字段导致序列化失败
            def _find_bad(obj, path=""):
                from flask.json.provider import DefaultJSONProvider as DP
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        try:
                            json.dumps(v, cls=_NumpyJSONEncoder, ensure_ascii=False)
                        except TypeError:
                            return _find_bad(v, f"{path}.{k}")
                elif isinstance(obj, list):
                    for i, v in enumerate(obj):
                        try:
                            json.dumps(v, cls=_NumpyJSONEncoder, ensure_ascii=False)
                        except TypeError:
                            return _find_bad(v, f"{path}[{i}]")
                else:
                    return f"BAD at {path}: type={type(obj).__module__}.{type(obj).__name__}, value={repr(obj)[:200]}"
                return f"Unknown at {path}"
            detail = _find_bad(response_data)
            logger.error(f"JSON serialization error detail: {detail}")
            return jsonify({"success": False, "error": f"JSON error: {detail}"}), 500
    except Exception as e:
        logger.error(f"API fund detail error for {code}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/fund/<code>/holdings-ai")
def api_fund_holdings_ai(code):
    """AI分析基金重仓股，返回持仓诊断和投资建议"""
    try:
        force = request.args.get("force", "0") == "1"
        result = analyze_fund_holdings(code, force=force)
        if result.get("success"):
            return jsonify(result)
        return jsonify(result), 503
    except Exception as e:
        logger.error(f"API holdings AI error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/stock/<code>/kline")
def api_stock_kline(code):
    """获取单只股票K线数据 + 智能分析"""
    try:
        days = int(request.args.get("days", 250))
        hist_df = get_stock_history(code, days=days)
        if hist_df.empty:
            return jsonify({"success": False, "error": "无法获取数据"}), 404

        hist_df = calc_all_indicators(hist_df)
        kline = []
        for _, r in hist_df.iterrows():
            kline.append({
                "date": r["date"].strftime("%Y-%m-%d") if hasattr(r["date"], "strftime") else str(r["date"]),
                "open": float(r["open"]) if pd.notna(r.get("open")) else 0,
                "close": float(r["close"]) if pd.notna(r.get("close")) else 0,
                "low": float(r["low"]) if pd.notna(r.get("low")) else 0,
                "high": float(r["high"]) if pd.notna(r.get("high")) else 0,
                "volume": int(r["volume"]) if pd.notna(r.get("volume")) else 0,
            })

        # 指标
        indicators = {}
        for col in ["MA5", "MA10", "MA20", "MA60", "BOLL_UP", "BOLL_MID", "BOLL_DN",
                      "DIF", "DEA", "MACD", "RSI", "K", "D", "J"]:
            if col in hist_df.columns:
                indicators[col] = [
                    round(float(v), 2) if pd.notna(v) else None
                    for v in hist_df[col].tolist()
                ]

        # 智能分析
        state_result = analyze_stock_state(hist_df)
        sr_levels = detect_support_resistance(hist_df)
        hs_pattern = detect_head_shoulders(hist_df)
        divergence = detect_macd_divergence(hist_df)

        dates = [r["date"] for r in kline]

        return jsonify({
            "success": True,
            "dates": dates,
            "kline": kline,
            "indicators": indicators,
            "analysis": {
                "state": state_result.get("state", ""),
                "summary": state_result.get("indicators", []),
                "support": sr_levels.get("support", []),
                "resistance": sr_levels.get("resistance", []),
                "ma_supports": sr_levels.get("ma_supports", []),
                "head_shoulders": hs_pattern,
                "divergence": divergence,
            },
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ──────────────────────────────────────────────
# API: 第三层 — 新闻聚合
# ──────────────────────────────────────────────

@app.route("/api/news")
def api_news():
    """获取财经新闻"""
    try:
        region = request.args.get("region", None)
        tag = request.args.get("tag", None)
        news = get_financial_news()

        if region and region != "all":
            news = [n for n in news if n.get("region") == region]
        if tag and tag != "全部":
            news = [n for n in news if tag in n.get("tags", [])]

        return jsonify({
            "success": True,
            "news": news,
            "regions": get_region_options(),
            "tags": get_tag_options(),
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        logger.error(f"API news error: {e}")
        return jsonify({"success": False, "error": str(e), "news": []}), 200


@app.route("/api/ai/analysis")
def api_ai_analysis():
    """AI 智能分析 — 综合当日新闻、板块、指数给出投资建议"""
    try:
        force = request.args.get("force", "0") == "1"
        result = get_ai_analysis(force=force)
        if result.get("success"):
            return jsonify(result)
        return jsonify(result), 503
    except Exception as e:
        logger.error(f"API AI analysis error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/news/headlines")
def api_headlines():
    """获取头条要闻"""
    try:
        headlines = get_top_headlines(10)
        return jsonify({"success": True, "headlines": headlines})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 200


@app.route("/api/sectors")
def api_sectors():
    """获取所有行业+概念板块实时数据"""
    try:
        boards = get_sina_sector_boards()
        sector_type = request.args.get("type", "all")
        if sector_type == "concept":
            boards = [b for b in boards if b["type"] == "concept"]
        elif sector_type == "industry":
            boards = [b for b in boards if b["type"] == "industry"]

        return jsonify({
            "success": True,
            "sectors": boards,
            "total": len(boards),
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        logger.error(f"API sectors error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/sector/<name>/funds")
def api_sector_funds(name):
    """获取某个板块的实际基金列表"""
    try:
        codes = get_sector_fund_list(name)
        if not codes:
            return jsonify({"success": True, "funds": [], "count": 0})

        # 获取基金基本信息
        from data.fund_data import get_all_funds
        df = get_all_funds()
        fund_map = {}
        if not df.empty:
            for _, row in df.iterrows():
                code = str(row.get("code", "")).zfill(6)
                if code in set(codes):
                    fund_map[code] = {
                        "code": code,
                        "name": str(row.get("name", "")),
                        "fund_type": str(row.get("fund_type", "")),
                    }

        # 保持与codes相同的顺序
        funds = [fund_map.get(c, {"code": c, "name": c, "fund_type": ""}) for c in codes]

        return jsonify({
            "success": True,
            "sector_name": name,
            "funds": funds,
            "count": len(funds),
        })
    except Exception as e:
        logger.error(f"API sector funds error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/market/index")
def api_market_index():
    """获取大盘指数"""
    try:
        market = get_market_index()
        return jsonify({"success": True, "index": market,
                        "update_time": datetime.now().strftime("%H:%M:%S")})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/refresh")
def api_refresh():
    """一键刷新所有数据"""
    try:
        # 清缓存
        from data.fund_data import CACHE as fund_cache
        from data.stock_data import CACHE as stock_cache
        fund_cache.clear()
        stock_cache.clear()
        return jsonify({
            "success": True,
            "message": "数据缓存已清除，正在重新获取最新数据",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ──────────────────────────────────────────────
# 启动
# ──────────────────────────────────────────────

def _warmup_cache():
    """后台预热缓存，减少首次请求等待时间"""
    import threading
    import time as _time
    def _warm():
        _time.sleep(3)  # 等待 Flask 完全启动
        logger.info("正在预热数据缓存...")
        try:
            from data.fund_data import get_all_funds, get_fund_daily_rank
            from data.stock_data import get_market_index, _build_sector_fund_index
            logger.info("预热: 获取基金列表...")
            get_all_funds()
            _time.sleep(1)
            logger.info("预热: 构建板块基金索引...")
            _build_sector_fund_index(force=True)
            _time.sleep(1)
            logger.info("预热: 获取大盘指数...")
            get_market_index()
            _time.sleep(1)
            logger.info("预热: 获取基金日排名...")
            get_fund_daily_rank()
            logger.info("缓存预热完成")
        except Exception as e:
            logger.warning(f"缓存预热部分失败（不影响使用）: {e}")
    t = threading.Thread(target=_warm, daemon=True)
    t.start()

if __name__ == "__main__":
    print("=" * 60)
    print("  FundRadar 基金雷达 v1.0")
    print("  数据来源：东方财富 / 新浪财经 / 财联社")
    print(f"  启动时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    from data.market_cache import start_market_updater
    start_market_updater(interval=30)
    _warmup_cache()
    app.run(host="0.0.0.0", port=5002, debug=False)
