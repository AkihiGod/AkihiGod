"""
基金数据层 — 直接请求天天基金 / 东方财富 API（无 AKShare 依赖）
"""
import requests
import pandas as pd
import numpy as np
import re
import json
import time
import logging

logger = logging.getLogger(__name__)

CACHE = {}
CACHE_TTL = 60

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://fund.eastmoney.com/",
})


def _cache_get(key, fetcher, ttl=None):
    ttl = ttl or CACHE_TTL
    now = time.time()
    if key in CACHE and now - CACHE[key]["ts"] < ttl:
        return CACHE[key]["data"]
    data = fetcher()
    has_data = not data.empty if isinstance(data, pd.DataFrame) else bool(data)
    if has_data:
        CACHE[key] = {"ts": now, "data": data}
    return data


FUND_BOARDS = [
    {"id": "all", "name": "全部基金", "type": "all"},
    {"id": "stock", "name": "股票型基金", "type": "stock"},
    {"id": "mix", "name": "混合型基金", "type": "mix"},
    {"id": "bond", "name": "债券型基金", "type": "bond"},
    {"id": "index", "name": "指数型基金", "type": "index"},
    {"id": "QDII", "name": "QDII基金", "type": "QDII"},
    {"id": "money", "name": "货币型基金", "type": "money"},
    {"id": "ETF", "name": "ETF基金", "type": "ETF"},
    {"id": "LOF", "name": "LOF基金", "type": "LOF"},
    {"id": "short_term", "name": "短期理财基金", "type": "short_term"},
    {"id": "FOF", "name": "FOF基金", "type": "FOF"},
]

BOARD_INDICES = {
    "stock": "BK0477", "mix": "BK0478", "bond": "BK0480",
    "index": "BK0476", "QDII": "BK0483", "ETF": "BK0484", "LOF": "BK0485",
}


# ── 全市场基金列表（天天基金 JSONP，缓存 4 小时） ──────

def _parse_fund_list():
    """解析天天基金 JS 文件获取基金列表"""
    try:
        resp = SESSION.get(
            "https://fund.eastmoney.com/js/fundcode_search.js",
            headers={"Referer": "https://fund.eastmoney.com/"},
            timeout=10,
        )
        match = re.search(r"\[.*\]", resp.text, re.DOTALL)
        if not match:
            logger.error("基金列表 JSONP 解析失败")
            return pd.DataFrame()
        data = json.loads(match.group())
        # 格式：[code, pinyin, name, fund_type, pinyin_full]
        rows = []
        for item in data:
            if len(item) >= 4:
                rows.append({
                    "code": str(item[0]).zfill(6),
                    "name": str(item[2]),
                    "fund_type": str(item[3]),
                })
        return pd.DataFrame(rows)
    except Exception as e:
        logger.error(f"获取基金列表失败: {e}")
        return pd.DataFrame()


def get_all_funds():
    df = _cache_get("all_funds", _parse_fund_list, ttl=14400)  # 缓存4小时
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()


# ── 基金净值走势 ────────────────────────────────────────

def get_fund_nav_history(code: str, days: int = 120):
    def _fetch():
        try:
            page_size = 20
            # 约250个交易日/年，days天对应 days*0.7 个交易日，每页20条
            est_trading_days = int(days * 0.7)
            pages = min((est_trading_days // page_size) + 1, 50)
            all_rows = []
            for page in range(1, pages + 1):
                resp = SESSION.get(
                    "https://api.fund.eastmoney.com/f10/lsjz",
                    params={
                        "fundCode": str(code).zfill(6),
                        "pageIndex": str(page),
                        "pageSize": str(page_size),
                    },
                    headers={
                        "Referer": "https://fundf10.eastmoney.com/",
                    },
                    timeout=10,
                )
                data = resp.json()
                records = data.get("Data", {}).get("LSJZList", [])
                if not records:
                    break
                for item in records:
                    all_rows.append({
                        "date": pd.to_datetime(item.get("FSRQ", "")),
                        "nav": float(item.get("DWJZ", 0)) if item.get("DWJZ") else None,
                    })
                if len(records) < page_size:
                    break
                time.sleep(0.05)  # 翻页间隔（单IP 20req/s 安全）
            df = pd.DataFrame(all_rows)
            return df.dropna(subset=["nav"]).sort_values("date") if len(df) else pd.DataFrame()
        except Exception as e:
            logger.warning(f"获取基金净值失败 {code}: {e}")
            return pd.DataFrame()

    return _cache_get(f"fund_nav_{code}_{days}", _fetch, ttl=300)


# ── 基金持仓 ────────────────────────────────────────────

def get_fund_holdings(code: str):
    def _fetch():
        try:
            # 通过 fundf10 页面接口获取持仓 HTML
            resp = SESSION.get(
                "https://fundf10.eastmoney.com/FundArchivesDatas.aspx",
                params={
                    "type": "jjcc",
                    "code": str(code).zfill(6),
                    "topline": "10",
                    "year": "",
                    "month": "",
                    "rt": str(time.time()),
                },
                headers={
                    "Referer": f"https://fundf10.eastmoney.com/ccmx_{str(code).zfill(6)}.html",
                },
                timeout=10,
            )
            html = resp.text  # 东方财富已改用 UTF-8，trust requests 自动检测

            if "暂无数据" in html and "基金" not in html:
                return pd.DataFrame()

            # 提取所有 td 内容（包括嵌套 HTML）
            tds = re.findall(r"<td[^>]*>(.*?)</td>", html)
            if len(tds) < 20 and "没有找到" in html:
                return pd.DataFrame()

            # 表头在<th>里，<td>全都是数据行
            # td[0]=序号, td[1]=股票代码, td[2]=股票名称, ...
            # td[6]=占净值比例(如"9.91%")
            records = []
            row_size = 9
            for i in range(0, len(tds) - row_size + 1, row_size):
                try:
                    stock_code_td = tds[i + 1]
                    stock_name_td = tds[i + 2]
                    weight_td = tds[i + 6]

                    # 提取纯文本（去除HTML标签）
                    stock_code = re.sub(r"<[^>]+>", "", stock_code_td).strip()
                    stock_name = re.sub(r"<[^>]+>", "", stock_name_td).strip()
                    weight_str = re.sub(r"<[^>]+>", "", weight_td).strip().replace("%", "")

                    if not stock_code or not re.match(r"^\d{5,6}$", stock_code):
                        continue

                    records.append({
                        "stock_code": stock_code,
                        "stock_name": stock_name,
                        "weight": float(weight_str) if weight_str else 0,
                    })
                except (ValueError, IndexError):
                    continue

            if records:
                return pd.DataFrame(records)
        except Exception as e:
            logger.warning(f"获取基金持仓失败 {code}: {e}")
        return pd.DataFrame()

    return _cache_get(f"fund_hold_{code}", _fetch, ttl=3600)


# ── 基金日收益排行 ──────────────────────────────────────

def get_fund_daily_rank():
    def _fetch():
        try:
            # 使用东方财富 clist API 获取基金日涨跌排行
            resp = SESSION.get(
                "https://push2.eastmoney.com/api/qt/clist/get",
                params={
                    "pn": "1", "pz": "500", "po": "1", "np": "1",
                    "fltt": "2", "invt": "2",
                    "fid": "f3",
                    "fs": "b:MK0021,b:MK0022,b:MK0024,b:MK0025,b:MK0026,b:MK0027,b:MK0028,b:MK0029",
                    "fields": "f2,f3,f12,f14",
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("data") and data["data"].get("diff"):
                rows = []
                for item in data["data"]["diff"]:
                    rows.append({
                        "基金代码": str(item.get("f12", "")).zfill(6),
                        "日增长率": item.get("f3", 0),
                        "单位净值": item.get("f2", 0),
                    })
                return pd.DataFrame(rows)
        except Exception as e:
            logger.warning(f"获取基金排行失败: {e}")
        return pd.DataFrame()

    return _cache_get("fund_daily_rank", _fetch, ttl=300)


# ── ETF 行情 ────────────────────────────────────────────

def get_etf_spot():
    def _fetch():
        try:
            resp = SESSION.get(
                "https://push2.eastmoney.com/api/qt/clist/get",
                params={
                    "pn": "1", "pz": "100", "po": "1", "np": "1",
                    "fltt": "2", "invt": "2",
                    "fid": "f3",
                    "fs": "b:MK0021",
                    "fields": "f2,f3,f12,f14",
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("data") and data["data"].get("diff"):
                return pd.DataFrame(data["data"]["diff"])
        except Exception as e:
            logger.warning(f"获取ETF行情失败: {e}")
        return pd.DataFrame()

    return _cache_get("etf_spot", _fetch, ttl=30)


# ── 搜索基金 ────────────────────────────────────────────

def search_funds(keyword: str):
    df = get_all_funds()
    if df.empty:
        return []
    keyword = str(keyword).strip()
    mask = df["code"].str.contains(keyword) | df["name"].str.contains(keyword, na=False)
    results = df[mask].head(20)
    return results.to_dict(orient="records")


# ── 基金详情 ────────────────────────────────────────────

def get_fund_info(code: str):
    df = get_all_funds()
    if df.empty:
        return None
    match = df[df["code"] == str(code).zfill(6)]
    if match.empty:
        return None
    row = match.iloc[0]
    return {
        "code": str(row["code"]),
        "name": str(row.get("name", "")),
        "fund_type": str(row.get("fund_type", "")),
        "establish_date": str(row.get("establish_date", "")) if "establish_date" in row else "",
        "manager": str(row.get("manager", "")) if "manager" in row else "",
        "scale": str(row.get("scale", "")) if "scale" in row else "",
        "company": str(row.get("company", "")) if "company" in row else "",
    }
