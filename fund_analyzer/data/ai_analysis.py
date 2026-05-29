"""
AI 智能分析模块 — 调用 DeepSeek API，综合当日新闻、板块表现、市场指数，
经过深度推理后给出投资建议和风险提示。
"""
import json
import time
import logging
import requests

logger = logging.getLogger(__name__)

DEEPSEEK_API_KEY = "sk-a3cb6853e84442cf8f51ab696b67273f"
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-pro"

CACHE = {}
CACHE_TTL = 600  # 分析结果缓存10分钟
HOLDINGS_AI_CACHE = {}


def _build_analysis_context():
    """收集当日全部可用数据，构建分析上下文"""
    from data.market_cache import read_market_index
    from data.news_data import get_financial_news
    from data.fund_data import get_fund_daily_rank

    ctx = {}

    # 市场指数
    market = read_market_index()
    ctx["market"] = market

    # 板块数据（从新浪实时快照 + 基金板块映射）
    try:
        from data.stock_data import get_sina_sector_boards
        sectors = get_sina_sector_boards()
        # 按涨跌幅排序，取前10和后10
        sorted_sectors = sorted(
            [s for s in sectors if s.get("has_quote") and s["fund_count"] > 0],
            key=lambda x: x.get("change_pct", 0),
            reverse=True,
        )
        ctx["top_sectors"] = sorted_sectors[:10]
        ctx["bottom_sectors"] = sorted_sectors[-10:]
        ctx["all_sectors"] = sectors
    except Exception as e:
        logger.warning(f"板块数据采集失败: {e}")
        ctx["top_sectors"] = []
        ctx["bottom_sectors"] = []

    # 今日新闻
    try:
        all_news = get_financial_news()
        ctx["news"] = all_news[:30]  # 取前30条
    except Exception as e:
        logger.warning(f"新闻数据采集失败: {e}")
        ctx["news"] = []

    # 基金日排名（前20热门）
    try:
        rank_df = get_fund_daily_rank()
        if not rank_df.empty:
            top_funds = []
            for _, row in rank_df.head(20).iterrows():
                top_funds.append({
                    "code": str(row.get("基金代码", "")),
                    "name": str(row.get("基金简称", "")),
                    "daily_return": float(row.get("日增长率", 0)) if row.get("日增长率") is not None else 0,
                })
            ctx["top_funds"] = top_funds
        else:
            ctx["top_funds"] = []
    except Exception:
        ctx["top_funds"] = []

    return ctx


def _build_prompt(ctx):
    """根据上下文数据构建深度分析提示词"""
    # 市场指数摘要
    market_lines = []
    for name, info in ctx["market"].items():
        chg = info.get("change_pct", 0)
        sign = "+" if chg >= 0 else ""
        market_lines.append(f"  - {name}: {info.get('price', '?')} ({sign}{chg:.2f}%)")
    market_summary = "\n".join(market_lines) if market_lines else "暂无数据"

    # 板块涨跌
    top_lines = []
    for s in ctx["top_sectors"]:
        chg = s.get("change_pct", 0)
        sign = "+" if chg >= 0 else ""
        top_lines.append(f"  - {s['name']}: {sign}{chg:.2f}% ({s.get('fund_count', 0)}只基金)")
    top_summary = "\n".join(top_lines[:8]) if top_lines else "暂无数据"

    bottom_lines = []
    for s in ctx["bottom_sectors"]:
        chg = s.get("change_pct", 0)
        sign = "+" if chg >= 0 else ""
        bottom_lines.append(f"  - {s['name']}: {sign}{chg:.2f}% ({s.get('fund_count', 0)}只基金)")
    bottom_summary = "\n".join(bottom_lines[:8]) if bottom_lines else "暂无数据"

    # 新闻摘要
    news_lines = []
    for n in ctx["news"][:20]:
        title = n.get("title", "")
        cat = n.get("category", "")
        news_lines.append(f"  - [{cat}] {title}")
    news_summary = "\n".join(news_lines) if news_lines else "暂无数据"

    # 热门基金
    fund_lines = []
    for f in ctx.get("top_funds", [])[:10]:
        ret = f.get("daily_return", 0)
        sign = "+" if ret >= 0 else ""
        fund_lines.append(f"  - {f['name']}({f['code']}): {sign}{ret:.2f}%")
    fund_summary = "\n".join(fund_lines) if fund_lines else "暂无数据"

    prompt = f"""你是一位资深金融分析师，拥有20年A股和基金投资经验。请基于以下**当日实时数据**，经过缜密分析后给出投资建议。

## 一、大盘指数
{market_summary}

## 二、涨幅领先板块（TOP 8）
{top_summary}

## 三、跌幅靠前板块（BOTTOM 8）
{bottom_summary}

## 四、今日重要财经新闻
{news_summary}

## 五、日涨幅靠前基金（TOP 10）
{fund_summary}

---

请从以下维度进行深度分析（使用 Markdown 格式输出）：

### 1. 市场整体研判（2-3句话）
综合指数、板块、新闻，判断今日市场基调（进攻/防御/震荡），指出核心驱动因素。

### 2. 重点板块深度分析（选2-3个最值得关注的板块）
对每个板块：
- 涨跌原因（结合新闻面和资金面）
- 短期趋势判断（延续/反转/震荡）
- 操作建议层级：**强烈关注** / **逢低布局** / **逢高减仓** / **观望**

### 3. 明日操作策略
- 推荐关注的方向（1-2个）及具体逻辑
- 需要回避的方向（1个）及风险原因
- 仓位建议（如：维持xx成仓位，可适当xx）

### 4. 风险提示（必须）
- 列出2-3个需要警惕的宏观/微观风险点
- 特别提示：以上分析仅基于今日截面数据，不构成投资建议，投资有风险，决策需谨慎

---

**重要要求：**
- 分析必须基于上方提供的实际数据，不可凭空编造
- 语气专业审慎，避免绝对化表述（如"必将大涨""绝对不会跌"等）
- 每个结论都要有数据支撑
- 控制在600-800字以内，精炼有力"""
    return prompt


def get_ai_analysis(force=False):
    """获取 AI 智能分析（缓存10分钟）"""
    global CACHE
    now = time.time()
    if not force and "analysis" in CACHE and now - CACHE["ts"] < CACHE_TTL:
        return CACHE["analysis"]

    try:
        ctx = _build_analysis_context()
        prompt = _build_prompt(ctx)

        resp = requests.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": "你是一位资深金融分析师，擅长基于实时数据进行深度市场研判和投资策略分析。你的回答必须基于数据、逻辑严密、措辞审慎。永远在最后附上风险提示。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 2048,
            },
            timeout=120,
        )

        if resp.status_code != 200:
            logger.error(f"DeepSeek API error: {resp.status_code} {resp.text[:200]}")
            return {"success": False, "error": f"AI分析服务暂时不可用（{resp.status_code}）"}

        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        result = {
            "success": True,
            "analysis": content,
            "model": DEEPSEEK_MODEL,
            "update_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "data_summary": {
                "market_indices": len(ctx["market"]),
                "sectors_analyzed": len(ctx.get("all_sectors", [])),
                "news_analyzed": len(ctx["news"]),
                "funds_analyzed": len(ctx.get("top_funds", [])),
            },
        }

        CACHE["analysis"] = result
        CACHE["ts"] = now
        return result

    except requests.exceptions.Timeout:
        logger.error("DeepSeek API timeout")
        return {"success": False, "error": "AI分析请求超时，请稍后重试"}
    except Exception as e:
        logger.error(f"AI analysis error: {e}")
        return {"success": False, "error": f"AI分析出错: {str(e)[:100]}"}


def _build_holdings_prompt(code, fund_info, stocks_analysis):
    """根据基金持仓数据构建重仓股分析提示词"""
    stock_lines = []
    total_weight = 0
    for s in stocks_analysis:
        w = s.get("weight", 0)
        total_weight += w
        rt = s.get("realtime", {}) or {}
        price = rt.get("price", 0) or 0
        chg = rt.get("change_pct", 0) or 0
        sign = "+" if chg >= 0 else ""
        state = s.get("state", "未知")

        indicators = s.get("indicators", [])
        ind_summary = "、".join(indicators[:3]) if indicators else "无特殊信号"

        sr = s.get("support_resistance", {}) or {}
        support = ", ".join([str(x) for x in sr.get("support", [])[:2]]) if sr.get("support") else "暂无"
        resistance = ", ".join([str(x) for x in sr.get("resistance", [])[:2]]) if sr.get("resistance") else "暂无"

        golden = s.get("golden_cross", []) or []
        death = s.get("death_cross", []) or []
        cross_signals = []
        if golden:
            cross_signals.append(f"金叉({golden[-1].get('date', '?')})")
        if death:
            cross_signals.append(f"死叉({death[-1].get('date', '?')})")

        div = s.get("divergence", {}) or {}
        if div.get("top_divergence"):
            cross_signals.append("顶背离(看跌)")
        if div.get("bottom_divergence"):
            cross_signals.append("底背离(看涨)")

        hs = s.get("head_shoulders", {}) or {}
        if hs.get("head_shoulders_top"):
            cross_signals.append("头肩顶(看跌)")
        if hs.get("head_shoulders_bottom"):
            cross_signals.append("头肩底(看涨)")

        signal_text = "、".join(cross_signals) if cross_signals else "无特殊信号"

        stock_lines.append(
            f"### {s['name']} ({s['code']}) — 权重 {w}%\n"
            f"- 现价: {price} ({sign}{chg:.2f}%)\n"
            f"- 技术状态: {state}\n"
            f"- 关键指标: {ind_summary}\n"
            f"- 支撑位: {support} / 阻力位: {resistance}\n"
            f"- 技术信号: {signal_text}"
        )

    stock_text = "\n\n".join(stock_lines)

    prompt = f"""你是一位资深基金经理助理，拥有丰富的持仓诊断和风险管理经验。请基于以下基金的重仓股数据和技术面分析，经过深度思考后给出诊断意见。

## 基金信息
- 代码: {code}
- 名称: {fund_info.get('name', '未知')}
- 类型: {fund_info.get('fund_type', '未知')}
- 经理: {fund_info.get('manager', '未知')}
- 规模: {fund_info.get('scale', '未知')}
- 前十大重仓股总占比: {total_weight:.1f}%

## 重仓股逐一数据

{stock_text}

---

请从以下维度进行深度分析（使用 Markdown 格式输出）：

### 1. 重仓股逐一点评
对每只重仓股给出简短判断（强势 / 中性 / 弱势），结合技术指标、形态、支撑阻力等数据，点出最关键的1-2个信号。格式：**股票名**：判断 + 一句话理由。

### 2. 组合结构评估
- 持仓集中度分析（是否过度集中在某几只股票）
- 整体技术面健康度（多少只处于多头趋势、多少只偏弱）
- 行业/风格暴露的潜在风险

### 3. 基金投资建议
基于以上分析，给出该基金的明确操作建议：
- **综合评级**（四选一）：积极关注 / 谨慎持有 / 观望等待 / 建议减仓
- **核心理由**（2-3条，每条一句话）
- **操作建议**（具体可执行的指导）

### 4. 风险提示
列出2-3个持有该基金需要关注的风险点，以及特别提示。

---

**重要要求：**
- 分析必须严格基于上方提供的实际数据，不可凭空编造
- 语气专业审慎，避免绝对化表述（如"必涨""绝对不会跌"等）
- 每个结论都要有数据支撑
- 控制字数在500-700字以内，精炼有力
- 风险提示必须包含"以上分析基于历史数据和技术指标，不构成投资建议\""""

    return prompt


def analyze_fund_holdings(code, force=False):
    """AI分析基金重仓股 — 调用 DeepSeek API 诊断持仓结构并给出投资建议"""
    global HOLDINGS_AI_CACHE
    cache_key = f"holdings_{code}"
    now = time.time()
    if not force and cache_key in HOLDINGS_AI_CACHE and now - HOLDINGS_AI_CACHE.get(cache_key + "_ts", 0) < CACHE_TTL:
        return HOLDINGS_AI_CACHE[cache_key]

    try:
        from data.fund_data import get_fund_info, get_fund_holdings
        from data.stock_data import get_stocks_realtime_batch, get_stock_history_with_retry
        from analysis.technical import (
            calc_all_indicators, analyze_stock_state,
            detect_support_resistance, detect_head_shoulders, detect_macd_divergence,
        )
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import pandas as pd

        info = get_fund_info(code)
        if not info:
            return {"success": False, "error": "未找到该基金信息"}

        holdings = get_fund_holdings(code)
        if holdings.empty:
            return {"success": False, "error": "该基金暂无重仓股数据"}

        all_codes = [str(row.get("stock_code", "")) for _, row in holdings.iterrows()]
        batch_realtime = get_stocks_realtime_batch(all_codes)

        def _fetch_hist(c):
            if len(c) == 5:
                return c, pd.DataFrame()
            return c, get_stock_history_with_retry(c, days=120)

        history_map = {}
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(_fetch_hist, c): c for c in all_codes}
            for f in as_completed(futures):
                c, df = f.result()
                history_map[c] = df

        stocks_analysis = []
        for _, row in holdings.iterrows():
            stock_code = str(row.get("stock_code", ""))
            stock_name = str(row.get("stock_name", ""))
            weight = float(row.get("weight", 0)) if pd.notna(row.get("weight")) else 0

            realtime = batch_realtime.get(stock_code) or {}
            hist_df = history_map.get(stock_code, pd.DataFrame())

            sa = {
                "code": stock_code,
                "name": stock_name,
                "weight": round(weight, 2),
                "realtime": realtime,
            }

            if not hist_df.empty and len(hist_df) >= 50:
                hist_df = calc_all_indicators(hist_df)
                state_result = analyze_stock_state(hist_df)
                sa["state"] = state_result["state"]
                sa["indicators"] = state_result.get("indicators", [])
                sa["golden_cross"] = state_result.get("golden_crosses", [])
                sa["death_cross"] = state_result.get("death_crosses", [])
                sa["support_resistance"] = detect_support_resistance(hist_df)
                sa["head_shoulders"] = detect_head_shoulders(hist_df)
                sa["divergence"] = detect_macd_divergence(hist_df)
            else:
                sa["state"] = "港股" if len(stock_code) == 5 else "数据不足"
                sa["indicators"] = ["数据不足，仅展示实时行情"] if len(stock_code) == 5 else ["历史数据不足"]
                sa["golden_cross"] = []
                sa["death_cross"] = []
                sa["support_resistance"] = {}
                sa["head_shoulders"] = {}
                sa["divergence"] = {}

            stocks_analysis.append(sa)

        prompt = _build_holdings_prompt(code, info, stocks_analysis)

        resp = requests.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": "你是一位资深基金经理助理，擅长基于持仓结构和技术面数据进行基金诊断。你的分析必须基于数据、逻辑严密、措辞审慎。永远在最后附上风险提示。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 2048,
            },
            timeout=120,
        )

        if resp.status_code != 200:
            logger.error(f"DeepSeek API error for holdings: {resp.status_code} {resp.text[:200]}")
            return {"success": False, "error": f"AI分析服务暂时不可用（{resp.status_code}）"}

        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        result = {
            "success": True,
            "analysis": content,
            "model": DEEPSEEK_MODEL,
            "code": code,
            "fund_name": info.get("name", ""),
            "stock_count": len(stocks_analysis),
            "update_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        HOLDINGS_AI_CACHE[cache_key] = result
        HOLDINGS_AI_CACHE[cache_key + "_ts"] = now
        return result

    except requests.exceptions.Timeout:
        logger.error("DeepSeek API timeout for holdings")
        return {"success": False, "error": "AI分析请求超时，请稍后重试"}
    except Exception as e:
        logger.error(f"Holdings AI analysis error: {e}")
        return {"success": False, "error": f"AI分析出错: {str(e)[:100]}"}
