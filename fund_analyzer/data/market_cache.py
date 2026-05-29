"""
市场指数缓存 — 后台线程定时从新浪获取，写入 JSON 文件
Flask API 直接读文件，避免线程/网络问题，保证毫秒级响应
"""
import json
import os
import time
import logging
import threading
import requests

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
MARKET_CACHE_FILE = os.path.join(CACHE_DIR, "_market_index.json")
FUND_RANK_CACHE_FILE = os.path.join(CACHE_DIR, "_fund_rank.json")

SINA_INDEX_ALL = {
    "上证指数": "s_sh000001",
    "深证成指": "s_sz399001",
    "创业板指": "s_sz399006",
    "沪深300": "s_sh000300",
    "中证500": "s_sh000905",
    "恒生指数": "int_hangseng",
    "恒生科技": "hkHSTECH",
    "道琼斯": "int_dji",
    "纳斯达克": "int_nasdaq",
    "标普500": "int_sp500",
}


def _fetch_sina_indices():
    """从新浪获取所有市场指数（A股 + 全球）"""
    result = {}
    codes = ",".join(SINA_INDEX_ALL.values())
    try:
        resp = requests.get(
            "http://hq.sinajs.cn/list=" + codes,
            headers={
                "Referer": "https://finance.sina.com.cn/",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=10,
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
            for name, code in SINA_INDEX_ALL.items():
                if code in var_name:
                    try:
                        if code.startswith("hk"):
                            # 港股格式: parts[2]=现价, parts[8]=涨跌幅%
                            price = float(parts[2])
                            change_pct = float(parts[8])
                        else:
                            price = float(parts[1])
                            change_pct = float(parts[3])
                        result[name] = {
                            "price": price,
                            "change_pct": change_pct,
                        }
                    except (ValueError, IndexError):
                        pass
                    break
    except Exception as e:
        logger.warning(f"获取市场指数失败: {e}")
    return result


def fetch_market_index():
    """获取所有市场指数（A股 + 全球）— 统一从新浪获取"""
    return _fetch_sina_indices()


def read_market_index():
    """读取缓存的市场指数（毫秒级）"""
    try:
        if os.path.exists(MARKET_CACHE_FILE):
            with open(MARKET_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 检查是否过期（60秒）
                if time.time() - data.get("_ts", 0) < 120:
                    return data.get("index", {})
    except Exception:
        pass
    return {}


def write_market_index(index_data):
    """写入市场指数缓存"""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(MARKET_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "index": index_data,
                "_ts": time.time(),
            }, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"写入指数缓存失败: {e}")


def start_market_updater(interval=30):
    """启动后台定时更新器"""
    def _update_loop():
        logger.info("市场指数后台更新器已启动")
        while True:
            try:
                data = fetch_market_index()
                if data:
                    write_market_index(data)
                    logger.info(f"指数更新: {list(data.keys())}")
            except Exception as e:
                logger.warning(f"指数后台更新异常: {e}")
            time.sleep(interval)

    t = threading.Thread(target=_update_loop, daemon=True)
    t.start()
    return t
