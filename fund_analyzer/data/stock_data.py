"""
股票数据层 — 直接请求新浪财经 / 东方财富 API（无 AKShare 依赖）
速度：ms 级 vs AKShare 的 4-6s
"""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
import numpy as np
import re
import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

CACHE = {}
CACHE_TTL = 30

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn/",
})
adapter = HTTPAdapter(pool_connections=20, pool_maxsize=30, max_retries=Retry(total=2, backoff_factor=0.1))
SESSION.mount("https://", adapter)
SESSION.mount("http://", adapter)


def _cache_get(key, fetcher, ttl=None, cache_empty=False):
    ttl = ttl or CACHE_TTL
    now = time.time()
    if key in CACHE and now - CACHE[key]["ts"] < ttl:
        return CACHE[key]["data"]
    data = fetcher()
    # DataFrame/Series 用 .empty，其他用 bool()
    has_data = not data.empty if isinstance(data, pd.DataFrame) else bool(data)
    if has_data or cache_empty:
        CACHE[key] = {"ts": now, "data": data}
    return data


# ── 市场指数（新浪财经，毫秒级） ─────────────────────────

SINA_INDEX_MAP = {
    "上证指数": "s_sh000001",
    "深证成指": "s_sz399001",
    "创业板指": "s_sz399006",
    "沪深300": "s_sh000300",
}


def get_market_index():
    """获取主要市场指数 — 从后台定时更新的缓存文件读取（毫秒级）"""
    from data.market_cache import read_market_index
    return read_market_index()


# ── A 股实时行情（东方财富，<1s） ────────────────────────

def get_stock_realtime(code: str):
    """获取单只股票实时行情"""
    def _fetch():
        try:
            clean_code = str(code).replace("sh", "").replace("sz", "")
            # 5位代码为港股，A股数据源不支持
            if len(clean_code) == 5:
                return None
            clean_code = clean_code.zfill(6)
            # 判断市场：3 开头为深圳创业板，0/2 开头为深圳，6 开头为上海
            if clean_code.startswith(("0", "2", "3")):
                secid = f"0.{clean_code}"
            else:
                secid = f"1.{clean_code}"
            resp = SESSION.get(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={
                    "secid": secid,
                    "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f57,f58,f60,f116,f117,f170",
                },
                timeout=5,
            )
            data = resp.json()
            if data.get("data"):
                d = data["data"]
                return {
                    "code": clean_code,
                    "name": d.get("f58", ""),
                    "price": d.get("f43", 0) / 100 if d.get("f43") else 0,
                    "change_pct": d.get("f170", 0) / 100 if d.get("f170") else 0,
                    "change_amount": d.get("f169", 0) / 100 if d.get("f169") else 0,
                    "volume": d.get("f47", 0),
                    "amount": d.get("f48", 0),
                    "high": d.get("f44", 0) / 100 if d.get("f44") else 0,
                    "low": d.get("f45", 0) / 100 if d.get("f45") else 0,
                    "open": d.get("f46", 0) / 100 if d.get("f46") else 0,
                    "pre_close": d.get("f60", 0) / 100 if d.get("f60") else 0,
                    "turnover": d.get("f168", 0) / 100 if d.get("f168") else 0,
                }
        except Exception as e:
            logger.warning(f"获取实时行情失败 {code}: {e}")
        return None

    return _cache_get(f"stock_rt_{code}", _fetch, ttl=15)


def get_stocks_realtime_batch(codes):
    """批量获取股票实时行情 — 新浪财经单次请求（避免东方财富限流）"""
    if not codes:
        return {}

    def _fetch():
        result = {}
        a_codes = []    # A股代码
        hk_codes = []   # 港股代码
        code_map = {}

        for code in codes:
            clean = str(code).replace("sh", "").replace("sz", "")
            if len(clean) == 5:
                hk_codes.append(clean)
                continue
            clean = clean.zfill(6)
            if clean.startswith(("0", "2", "3")):
                sc = f"sz{clean}"
            else:
                sc = f"sh{clean}"
            a_codes.append(sc)
            code_map[sc] = clean

        all_sina_codes = a_codes + [f"rt_hk{c}" for c in hk_codes]
        if not all_sina_codes:
            return result

        try:
            resp = SESSION.get(
                "http://hq.sinajs.cn/list=" + ",".join(all_sina_codes),
                headers={
                    "Referer": "https://finance.sina.com.cn/",
                    "User-Agent": "Mozilla/5.0",
                },
                timeout=8,
            )
            resp.encoding = "gbk"
            for line in resp.text.strip().split("\n"):
                if "=" not in line or '"' not in line:
                    continue
                var_name = line.split("=")[0].strip()
                data_str = line.split('"')[1] if '"' in line else ""
                parts = data_str.split(",")
                if len(parts) < 4:
                    continue

                var_key = var_name.replace("var hq_str_", "")

                # 港股：新浪格式 rt_hk00700
                if var_key.startswith("rt_hk"):
                    hk_code = var_key.replace("rt_hk", "")
                    try:
                        # HK format: [0]=英文, [1]=中文名, [2]=今开, [3]=昨收, [4]=最高, [5]=最低, [6]=现价, [7]=涨跌额, [8]=涨跌幅(%)
                        price = float(parts[6]) if len(parts) > 6 and parts[6] else 0
                        change_pct = float(parts[8]) if len(parts) > 8 and parts[8] else 0
                        result[hk_code] = {
                            "code": hk_code,
                            "name": parts[1] if len(parts) > 1 else parts[0],
                            "price": price,
                            "change_pct": round(change_pct, 2),
                            "open": float(parts[2]) if len(parts) > 2 and parts[2] else 0,
                            "high": float(parts[4]) if len(parts) > 4 and parts[4] else 0,
                            "low": float(parts[5]) if len(parts) > 5 and parts[5] else 0,
                            "pre_close": float(parts[3]) if len(parts) > 3 and parts[3] else 0,
                        }
                    except (ValueError, IndexError):
                        continue
                else:
                    # A股格式：[0]=名称, [1]=今开, [2]=昨收, [3]=现价, [4]=最高, [5]=最低
                    clean_code = code_map.get(var_key, "")
                    if not clean_code:
                        continue
                    try:
                        price = float(parts[3]) if parts[3] else 0
                        pre_close = float(parts[2]) if parts[2] else 0
                        change_pct = round((price - pre_close) / pre_close * 100, 2) if pre_close else 0
                        result[clean_code] = {
                            "code": clean_code,
                            "name": parts[0],
                            "price": price,
                            "change_pct": change_pct,
                            "open": float(parts[1]) if parts[1] else 0,
                            "high": float(parts[4]) if len(parts) > 4 and parts[4] else 0,
                            "low": float(parts[5]) if len(parts) > 5 and parts[5] else 0,
                            "pre_close": pre_close,
                        }
                    except (ValueError, IndexError):
                        continue
        except Exception as e:
            logger.warning(f"批量获取实时行情失败: {e}")
        return result

    cache_key = f"batch_rt_{'-'.join(sorted(codes)[:20])}"
    return _cache_get(cache_key, _fetch, ttl=15)


# ── 股票历史 K 线 ───────────────────────────────────────

def _fetch_history_sina(clean_code: str, days: int):
    """从新浪财经获取K线数据，更稳定"""
    if clean_code.startswith(("0", "2", "3")):
        symbol = f"sz{clean_code}"
    else:
        symbol = f"sh{clean_code}"

    resp = SESSION.get(
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData",
        params={"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(days)},
        timeout=10,
    )
    data = resp.json()
    if not isinstance(data, list) or len(data) == 0:
        return pd.DataFrame()

    rows = []
    for item in data:
        try:
            rows.append({
                "date": pd.to_datetime(item["day"]),
                "open": float(item["open"]),
                "close": float(item["close"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "volume": float(item["volume"]),
                "amount": 0,
            })
        except (ValueError, KeyError):
            continue
    return pd.DataFrame(rows)


def _fetch_history_eastmoney(clean_code: str, days: int, period: str):
    """从东方财富获取K线数据（备用）"""
    if clean_code.startswith(("0", "2", "3")):
        secid = f"0.{clean_code}"
    else:
        secid = f"1.{clean_code}"

    klt_map = {"daily": "101", "weekly": "102", "monthly": "103"}
    klt = klt_map.get(period, "101")

    resp = SESSION.get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": klt, "fqt": "1", "lmt": str(days),
            "end": "20500101",
        },
        timeout=10,
    )
    data = resp.json()
    if not data.get("data") or not data["data"].get("klines"):
        return pd.DataFrame()

    rows = []
    for line in data["data"]["klines"]:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        rows.append({
            "date": pd.to_datetime(parts[0]),
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "volume": float(parts[5]),
            "amount": float(parts[6]) if len(parts) > 6 else 0,
            "turnover": float(parts[10]) if len(parts) > 10 else 0,
            "change_pct": float(parts[8]) if len(parts) > 8 else 0,
            "change_amount": float(parts[9]) if len(parts) > 9 else 0,
        })
    return pd.DataFrame(rows)


def get_stock_history(code: str, days: int = 250, period: str = "daily", skip_cache: bool = False):
    """获取股票历史K线数据 — 新浪财经（主）+ 东方财富（备用）"""
    def _fetch():
        clean_code = str(code).replace("sh", "").replace("sz", "")
        # 5位代码为港股，A股数据源不支持
        if len(clean_code) == 5:
            return pd.DataFrame()
        clean_code = clean_code.zfill(6)

        # 优先使用新浪 API（更稳定）
        df = _fetch_history_sina(clean_code, days)
        if not df.empty and len(df) >= 30:
            return df

        # 新浪数据不足则回退到东方财富
        df = _fetch_history_eastmoney(clean_code, days, period)
        if not df.empty:
            return df

        logger.warning(f"获取历史K线失败 {code}: 所有数据源均不可用")
        return pd.DataFrame()

    if skip_cache:
        return _fetch()
    return _cache_get(f"stock_hist_{code}_{days}", _fetch, ttl=300)


def get_stock_history_with_retry(code: str, days: int = 250, max_retries: int = 2):
    """带指数退避重试的历史K线获取，跳过缓存直接请求"""
    import random
    for attempt in range(max_retries):
        # 首次尝试走缓存，重试时跳过缓存
        df = get_stock_history(code, days=days, skip_cache=(attempt > 0))
        if not df.empty:
            return df
        if attempt < max_retries - 1:
            wait = (2 ** attempt) + random.uniform(0.3, 1.0)
            time.sleep(wait)
    return pd.DataFrame()


# ── 行业/概念板块（新浪财经，实时数据） ──────────────

def _parse_sina_board_js(text, board_type):
    """解析新浪板块JS变量，返回结构化板块数据"""
    results = []
    # 匹配每一行：引号包裹的key和value
    for match in re.finditer(r'"([^"]+)":"([^"]*)"', text):
        key = match.group(1)
        value = match.group(2)
        if not value or key == "param":
            continue
        parts = value.split(",")
        if len(parts) < 8:
            continue
        try:
            name = parts[1] if len(parts) > 1 else ""
            stock_count = int(parts[2]) if len(parts) > 2 and parts[2] else 0
            avg_price = float(parts[3]) if len(parts) > 3 and parts[3] else 0
            change_amount = float(parts[4]) if len(parts) > 4 and parts[4] else 0
            change_pct = float(parts[5]) if len(parts) > 5 and parts[5] else 0
            volume = float(parts[6]) if len(parts) > 6 and parts[6] else 0
            amount = float(parts[7]) if len(parts) > 7 and parts[7] else 0
            lead_code = parts[8] if len(parts) > 8 else ""
            lead_change_pct = float(parts[9]) if len(parts) > 9 and parts[9] else 0
            lead_price = float(parts[10]) if len(parts) > 10 and parts[10] else 0
            lead_amount = float(parts[11]) if len(parts) > 11 and parts[11] else 0
            lead_name = parts[12] if len(parts) > 12 else ""

            if not name or stock_count == 0:
                continue

            results.append({
                "code": key,
                "name": name,
                "type": board_type,
                "stock_count": stock_count,
                "avg_price": round(avg_price, 2),
                "change_pct": round(change_pct, 2),
                "change_amount": round(change_amount, 2),
                "volume": int(volume),
                "amount": int(amount),
                "lead_stock": {
                    "code": lead_code,
                    "name": lead_name,
                    "change_pct": round(lead_change_pct, 2),
                    "price": round(lead_price, 2),
                },
            })
        except (ValueError, IndexError):
            continue
    return results


# 基金板块 → 新浪板块名称 手动映射（解决名称不匹配问题）
_FUND_SECTOR_SINA_MAP = {
    # 传统板块 — 精确匹配新浪行业/概念板块名称
    "港创新药": "创新药",
    "创新药": "创新药",
    "食品饮料": "酒、饮料和精制茶制造业",
    "白酒": "白酒概念",
    "黄金": "黄金概念",
    "煤炭": "煤炭开采和洗选业",
    "医药": "医药制造业",
    "房地产": "房地产业",
    "消费": "零售业",
    "银行": "货币金融服务",
    "证券保险": "资本市场服务",
    "交通运输": "道路运输业",
    "医疗": "医药制造业",
    "农林牧渔": "农业",
    "基建": "土木工程建筑业",
    "军工": "国防军工",
    "电力": "电力、热力生产和供应业",
    "钢铁": "黑色金属冶炼和压延加工业",
    "化工": "化学原料和化学制品制造业",
    "黄金股": "黄金概念",
    "汽车整车": "汽车制造业",
    # 科技板块 — 精确匹配新浪概念板块名称
    "光伏": "光伏概念",
    "机器人": "机器人概念",
    "固态电池": "固态电池",
    "锂矿": "锂电池",
    "通信": "5G概念",
    "新能源": "新能源",
    "金融科技": "互联金融",
    "传媒游戏": "网络游戏",
    "稀土永磁": "稀缺资源",
    "风电": "风能",
    "储能": "钠电池",
    "家用电器": "智能家居",
    "有色金属": "有色金属冶炼和压延加工业",
    "油气资源": "石油和天然气开采业",
    "人工智能": "智能机器",
    "消费电子": "无线耳机",
    "科创板": "科创50",
    "电网设备": "智能电网",
    # 代理映射（新浪无精确对应板块，用最接近的概念板块代替）
    "CPO": "5G概念",
    "半导体": "华为海思",
    "PCB": "华为海思",
    "可控核聚变": "核电核能",
    "脑机接口": "智能穿戴",
    "存储芯片": "华为海思",
    "北证": "三板精选",
    # 新增 — 补全所有缺失板块的指数代理
    "红利低波": "保险重仓",
    "港股红利": "含H股",
    "红利": "保险重仓",
    "沪港深消费": "奢侈品",
    "现金流": "基金重仓",
    "亚太": "东亚自贸",
    "蓝筹": "超大盘",
    "微盘股": "专精特新",
    "小微盘量化": "专精特新",
    "量化": "基金重仓",
    # 以下板块走市场指数兜底（见 _FUND_SECTOR_MARKET_MAP）
    # 债基、可转债、混债、中证500
}


def _fetch_fund_sector_quotes():
    """从新浪板块数据获取基金板块实时涨跌幅（缓存60秒）"""
    def _fetch():
        results = {}
        # 收集新浪所有行业+概念板块的 name→change_pct 映射
        sina_quotes = {}
        for url, btype in [
            ("https://money.finance.sina.com.cn/q/view/newFLJK.php?param=industry", "industry"),
            ("https://money.finance.sina.com.cn/q/view/newFLJK.php?param=class", "concept"),
        ]:
            try:
                resp = SESSION.get(url, timeout=10)
                resp.encoding = "gbk"
                boards = _parse_sina_board_js(resp.text, btype)
                for b in boards:
                    sina_quotes[b["name"]] = b["change_pct"]
            except Exception as e:
                logger.warning(f"获取新浪板块数据失败 ({btype}): {e}")

        # 匹配基金板块到新浪板块（优先手动映射，再模糊匹配）
        for sector in FUND_SECTORS_V1:
            name = sector["name"]
            # 1. 精确匹配
            if name in sina_quotes:
                results[name] = sina_quotes[name]
                continue
            # 2. 手动映射
            mapped = _FUND_SECTOR_SINA_MAP.get(name)
            if mapped and mapped in sina_quotes:
                results[name] = sina_quotes[mapped]
                continue
            # 3. 模糊匹配（子串包含）
            matched = False
            for sina_name, chg in sina_quotes.items():
                if name in sina_name or sina_name in name:
                    results[name] = chg
                    matched = True
                    break
            if matched:
                continue
            # 4. 部分重叠匹配（至少2个中文字符重叠，排除纯数字/字母匹配）
            if len(name) >= 2:
                for sina_name, chg in sina_quotes.items():
                    if len(sina_name) < 2:
                        continue
                    for i in range(len(name) - 1):
                        sub = name[i:i+2]
                        # 至少包含一个中文字符，避免纯数字/字母误匹配（如"50"匹配到"科创50"）
                        if sub in sina_name and any('一' <= c <= '鿿' for c in sub):
                            results[name] = chg
                            matched = True
                            break
                    if matched:
                        break

        logger.info(f"基金板块实时行情(新浪): {len(results)}/{len(FUND_SECTORS_V1)}个匹配成功")
        return results

    return _cache_get("fund_sector_quotes", _fetch, ttl=60)


# ── 基金板块分类（用户自定义，含基金数量） ──
FUND_SECTORS_V1 = [
    # 传统/价值板块
    {"name": "港创新药", "fund_count": 52, "group": "传统板块"},
    {"name": "电力", "fund_count": 11, "group": "传统板块"},
    {"name": "创新药", "fund_count": 16, "group": "传统板块"},
    {"name": "食品饮料", "fund_count": 5, "group": "传统板块"},
    {"name": "白酒", "fund_count": 15, "group": "传统板块"},
    {"name": "黄金", "fund_count": 15, "group": "传统板块"},
    {"name": "煤炭", "fund_count": 7, "group": "传统板块"},
    {"name": "医药", "fund_count": 29, "group": "传统板块"},
    {"name": "房地产", "fund_count": 20, "group": "传统板块"},
    {"name": "养老产业", "fund_count": 6, "group": "传统板块"},
    {"name": "红利低波", "fund_count": 17, "group": "传统板块"},
    {"name": "消费", "fund_count": 37, "group": "传统板块"},
    {"name": "银行", "fund_count": 13, "group": "传统板块"},
    {"name": "证券保险", "fund_count": 21, "group": "传统板块"},
    {"name": "港股红利", "fund_count": 27, "group": "传统板块"},
    {"name": "红利", "fund_count": 22, "group": "传统板块"},
    {"name": "交通运输", "fund_count": 7, "group": "传统板块"},
    {"name": "海外医药", "fund_count": 8, "group": "传统板块"},
    {"name": "医疗", "fund_count": 34, "group": "传统板块"},
    {"name": "恒生", "fund_count": 16, "group": "传统板块"},
    {"name": "恒生科技", "fund_count": 32, "group": "传统板块"},
    {"name": "沪港深消费", "fund_count": 23, "group": "传统板块"},
    {"name": "标普", "fund_count": 10, "group": "传统板块"},
    {"name": "农林牧渔", "fund_count": 13, "group": "传统板块"},
    {"name": "现金流", "fund_count": 0, "group": "传统板块"},
    {"name": "基建", "fund_count": 7, "group": "传统板块"},
    {"name": "上证50", "fund_count": 10, "group": "传统板块"},
    {"name": "亚太", "fund_count": 6, "group": "传统板块"},
    {"name": "蓝筹", "fund_count": 24, "group": "传统板块"},
    {"name": "债基", "fund_count": 58, "group": "传统板块"},
    {"name": "可转债", "fund_count": 14, "group": "传统板块"},
    {"name": "货币基金", "fund_count": 54, "group": "传统板块"},
    # 科技/成长板块
    {"name": "稀土永磁", "fund_count": 7, "group": "科技板块"},
    {"name": "军工", "fund_count": 20, "group": "科技板块"},
    {"name": "人工智能", "fund_count": 28, "group": "科技板块"},
    {"name": "算力租赁", "fund_count": 5, "group": "科技板块"},

    {"name": "电网设备", "fund_count": 9, "group": "科技板块"},
    {"name": "光伏", "fund_count": 26, "group": "科技板块"},
    {"name": "机器人", "fund_count": 40, "group": "科技板块"},
    {"name": "科创板", "fund_count": 24, "group": "科技板块"},
    {"name": "半导体", "fund_count": 41, "group": "科技板块"},
    {"name": "国产算力", "fund_count": 10, "group": "科技板块"},
    {"name": "半导体材料设备", "fund_count": 29, "group": "科技板块"},
    {"name": "商业航天", "fund_count": 0, "group": "科技板块"},
    {"name": "大科技", "fund_count": 58, "group": "科技板块"},
    {"name": "创业板", "fund_count": 25, "group": "科技板块"},
    {"name": "微盘股", "fund_count": 27, "group": "科技板块"},
    {"name": "固态电池", "fund_count": 16, "group": "科技板块"},
    {"name": "小微盘量化", "fund_count": 25, "group": "科技板块"},
    {"name": "存储芯片", "fund_count": 6, "group": "科技板块"},
    {"name": "先进制造", "fund_count": 14, "group": "科技板块"},
    {"name": "云计算", "fund_count": 14, "group": "科技板块"},
    {"name": "金融科技", "fund_count": 7, "group": "科技板块"},

    {"name": "北证", "fund_count": 33, "group": "科技板块"},
    {"name": "双创50", "fund_count": 12, "group": "科技板块"},
    {"name": "消费电子", "fund_count": 21, "group": "科技板块"},
    {"name": "家用电器", "fund_count": 8, "group": "科技板块"},
    {"name": "量化", "fund_count": 24, "group": "科技板块"},
    {"name": "有色金属", "fund_count": 28, "group": "科技板块"},
    {"name": "汽车整车", "fund_count": 3, "group": "科技板块"},
    {"name": "锂矿", "fund_count": 8, "group": "科技板块"},
    {"name": "PCB", "fund_count": 12, "group": "科技板块"},
    {"name": "油气资源", "fund_count": 10, "group": "科技板块"},
    {"name": "化工", "fund_count": 15, "group": "科技板块"},
    {"name": "中证500", "fund_count": 77, "group": "科技板块"},
    {"name": "AI应用", "fund_count": 25, "group": "科技板块"},
    {"name": "储能", "fund_count": 12, "group": "科技板块"},
    {"name": "通信", "fund_count": 24, "group": "科技板块"},
    {"name": "新能源", "fund_count": 46, "group": "科技板块"},
    {"name": "混债", "fund_count": 35, "group": "科技板块"},
    {"name": "传媒游戏", "fund_count": 10, "group": "科技板块"},
    {"name": "沪深300", "fund_count": 15, "group": "科技板块"},
    {"name": "钢铁", "fund_count": 4, "group": "科技板块"},
    {"name": "黄金股", "fund_count": 10, "group": "科技板块"},
    {"name": "CPO", "fund_count": 77, "group": "科技板块"},
]


# ── 基金板块 → 基金搜索关键词（用于从全市场基金列表中匹配）──
_SECTOR_FUND_KEYWORDS = {
    # 传统板块
    "港创新药": ["创新药", "港股创新药", "香港创新药"],
    "电力": ["电力", "公用事业"],
    "创新药": ["创新药"],
    "食品饮料": ["食品饮料", "食品"],
    "白酒": ["白酒", "酒"],
    "黄金": ["黄金", "贵金属"],
    "煤炭": ["煤炭", "能源"],
    "医药": ["医药", "医疗保健"],
    "房地产": ["房地产", "地产"],
    "养老产业": ["养老"],
    "红利低波": ["红利低波"],
    "消费": ["消费"],
    "银行": ["银行"],
    "证券保险": ["证券", "保险", "券商"],
    "港股红利": ["港股红利", "香港红利", "恒生红利"],
    "红利": ["红利"],
    "交通运输": ["交通运输", "交通", "运输"],
    "海外医药": ["海外医疗", "全球医疗", "全球医药", "海外医药"],
    "医疗": ["医疗"],
    "恒生": ["恒生"],
    "恒生科技": ["恒生科技", "港股科技"],
    "沪港深消费": ["沪港深消费", "港股消费"],
    "标普": ["标普", "SP500", "标准普尔"],
    "农林牧渔": ["农林牧渔", "农业", "农牧"],
    "现金流": ["现金流", "自由现金流"],
    "基建": ["基建", "基础设施", "工程建设"],
    "上证50": ["上证50"],
    "亚太": ["亚太"],
    "蓝筹": ["蓝筹"],
    "债基": ["纯债", "债券型"],
    "可转债": ["可转债", "可转换债券"],
    "货币基金": [":type:货币型"],
    # 科技板块
    "稀土永磁": ["稀土"],
    "军工": ["军工", "国防"],
    "人工智能": ["人工智能"],
    "算力租赁": ["云计算", "数据中心"],

    "电网设备": ["电网", "电力设备"],
    "光伏": ["光伏", "太阳能"],
    "机器人": ["机器人"],
    "科创板": ["科创50", "科创版"],
    "半导体": ["半导体", "芯片"],
    "国产算力": ["云计算", "大数据", "数字经济", "信息技术"],
    "半导体材料设备": ["半导体材料", "半导体设备"],
    "商业航天": ["商业航天", "航天"],
    "大科技": ["科技"],
    "创业板": ["创业板"],
    "微盘股": ["微盘", "小盘"],
    "固态电池": ["电池", "锂电池"],
    "小微盘量化": ["量化小盘", "量化"],
    "存储芯片": ["芯片"],
    "先进制造": ["先进制造", "高端制造"],
    "云计算": ["云计算"],
    "金融科技": ["金融科技"],

    "北证": ["北证50", "北交所"],
    "双创50": ["双创50", "科创创业50"],
    "消费电子": ["消费电子"],
    "家用电器": ["家电"],
    "量化": ["量化"],
    "有色金属": ["有色金属"],
    "汽车整车": ["汽车"],
    "锂矿": ["锂矿", "稀有金属", "锂"],
    "PCB": ["集成电路"],
    "油气资源": ["油气"],
    "化工": ["化工"],
    "中证500": ["中证500"],
    "AI应用": ["AI", "人工智能"],
    "储能": ["储能"],
    "通信": ["通信", "5G"],
    "新能源": ["新能源", "新能源车"],
    "混债": ["偏债", "固收", "稳固收益"],
    "传媒游戏": ["传媒", "游戏", "影视"],
    "沪深300": ["沪深300"],
    "钢铁": ["钢铁"],
    "黄金股": ["黄金股", "黄金"],
    "CPO": ["通信", "TMT", "信息技术"],
}

_sector_fund_cache = None


def _build_sector_fund_index(force=False):
    """构建板块→基金代码映射（单次遍历所有基金，高效匹配）

    关键词支持两种模式：
    - 普通关键词: 匹配基金名称字段
    - :type:前缀: 匹配基金类型字段，如 ":type:货币型" 匹配所有货币基金
    """
    global _sector_fund_cache
    if _sector_fund_cache is not None and not force:
        return _sector_fund_cache

    from data.fund_data import get_all_funds
    df = get_all_funds()
    if df.empty:
        return {}

    # 分离普通关键词和类型关键词
    name_kws = {}   # {sector: [kw_lower, ...]}
    type_kws = {}   # {sector: [type_kw_lower, ...]}
    for sector, kw_list in _SECTOR_FUND_KEYWORDS.items():
        name_kws[sector] = []
        type_kws[sector] = []
        for kw in kw_list:
            if kw.startswith(":type:"):
                type_kws[sector].append(kw[6:].lower())
            else:
                name_kws[sector].append(kw.lower())

    index = {sector: [] for sector in _SECTOR_FUND_KEYWORDS}

    # 单次遍历所有基金
    for _, row in df.iterrows():
        name = str(row.get("name", ""))
        code = str(row.get("code", "")).zfill(6)
        ftype = str(row.get("fund_type", ""))
        name_lower = name.lower()
        ftype_lower = ftype.lower()
        for sector in _SECTOR_FUND_KEYWORDS:
            # 检查名称关键词
            for kw in name_kws.get(sector, []):
                if kw in name_lower:
                    index[sector].append(code)
                    break
            else:
                # 检查类型关键词（仅当名称未匹配时）
                for tk in type_kws.get(sector, []):
                    if tk in ftype_lower:
                        index[sector].append(code)
                        break

    _sector_fund_cache = index
    logger.info(f"板块基金索引已构建: {len(index)}个板块, 总计{sum(len(v) for v in index.values())}条匹配")
    return index


def get_sector_fund_list(sector_name):
    """获取某个板块的实际基金列表"""
    index = _build_sector_fund_index()
    return index.get(sector_name, [])


# 基金板块 → 市场指数 映射（新浪板块无对应时用市场指数兜底）
_FUND_SECTOR_MARKET_MAP = {
    "上证50": "上证指数",
    "沪深300": "沪深300",
    "创业板": "创业板指",
    "恒生": "恒生指数",
    "标普": "标普500",
    "中证500": "中证500",
    "债基": "上证指数",
    "可转债": "上证指数",
    "混债": "上证指数",
}


def get_sina_sector_boards():
    """获取基金板块分类数据（含实时涨跌幅）"""
    quotes = _fetch_fund_sector_quotes()

    # 市场指数兜底：对仍未匹配的板块，尝试从市场指数缓存读取
    market_index = {}
    for name in _FUND_SECTOR_MARKET_MAP:
        if name not in quotes:
            if not market_index:  # 懒加载
                from data.market_cache import read_market_index
                market_index = read_market_index()
            mi_name = _FUND_SECTOR_MARKET_MAP[name]
            mi_data = market_index.get(mi_name, {})
            if mi_data:
                quotes[name] = round(mi_data.get("change_pct", 0), 2)

    results = []
    fund_index = _build_sector_fund_index()  # 预计算板块基金映射
    for item in FUND_SECTORS_V1:
        name = item["name"]
        chg = quotes.get(name, None)
        has_quote = chg is not None
        actual_count = len(fund_index.get(name, []))
        results.append({
            "code": f"sector_{name}",
            "name": name,
            "type": "fund_sector",
            "group": item["group"],
            "stock_count": actual_count,
            "fund_count": actual_count,
            "avg_price": 0,
            "change_pct": chg if has_quote else 0,
            "change_amount": 0,
            "volume": 0,
            "amount": 0,
            "lead_stock": {"code": "", "name": "", "change_pct": 0, "price": 0},
            "has_data": has_quote,
            "has_quote": has_quote,
        })
    return results


def get_board_indices():
    """获取行业板块涨跌排行 — 优先用新浪数据，东方财富备用"""
    boards = get_sina_sector_boards()
    if boards:
        industry = [b for b in boards if b["type"] == "industry"]
        return [
            {
                "name": b["name"],
                "change_pct": b["change_pct"],
                "lead_stock": b["lead_stock"].get("name", ""),
            }
            for b in industry[:30]
        ]
    # fallback to eastmoney (may fail on restricted networks)
    def _fetch():
        try:
            resp = SESSION.get(
                "https://push2.eastmoney.com/api/qt/clist/get",
                params={
                    "pn": "1", "pz": "30", "po": "1", "np": "1",
                    "fltt": "2", "invt": "2",
                    "fid": "f3",
                    "fs": "m:90+t:2",
                    "fields": "f2,f3,f4,f12,f14,f128",
                },
                timeout=10,
            )
            data = resp.json()
            result = []
            if data.get("data") and data["data"].get("diff"):
                for item in data["data"]["diff"]:
                    result.append({
                        "name": item.get("f14", ""),
                        "change_pct": item.get("f3", 0),
                        "lead_stock": item.get("f128", ""),
                    })
            return result
        except Exception as e:
            logger.warning(f"获取板块指数失败: {e}")
        return []
    return _cache_get("board_indices", _fetch, ttl=120)
