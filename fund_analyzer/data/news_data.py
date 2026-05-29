"""
新闻数据层 — 财经新闻聚合与重要性评分
来源：新浪财经多频道（最稳定），覆盖国内官方、国内财经、国际财经、基金数据、实时快讯
"""
import requests
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

logger = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
})

# ── 新浪财经频道定义 ──────────────────────────────
# 每条频道包含 lid、频道名、来源标签、来源类型
SINA_CHANNELS = [
    # ── 国内官方 ──
    ("150", "央行", "中国人民银行", "official"),
    ("151", "证监会", "中国证监会", "official"),
    ("2520", "政策", "国家发改委/政策", "official"),
    ("2510", "宏观", "宏观经济", "official"),
    ("2522", "数据", "国家统计局/数据", "official"),

    # ── 国内财经 ──
    ("2509", "财经", "综合财经", "domestic"),
    ("2511", "股市", "中国证券报/上证报/证券时报", "domestic"),
    ("2513", "公司", "公司新闻", "domestic"),
    ("2514", "地产", "地产财经", "domestic"),
    ("2516", "科技", "科技产业", "domestic"),
    ("21", "宏观", "宏观经济", "domestic"),
    ("26", "研报", "券商研报", "domestic"),
    ("31", "期货", "期货/大宗商品", "domestic"),
    ("30", "债券", "债券市场", "domestic"),
    ("35", "银行", "银行业", "domestic"),
    ("34", "保险", "保险业", "domestic"),

    # ── 国际财经 ──
    ("2518", "国际", "国际财经", "international"),
    ("106", "美股", "美股市场", "international"),
    ("108", "港股", "港股市场", "international"),
    ("4", "国际", "国际要闻", "international"),
    ("32", "外汇", "外汇/人民币", "international"),
    ("33", "黄金", "黄金/贵金属", "international"),

    # ── 基金数据 ──
    ("2512", "基金", "基金/天天基金", "fund"),
    ("29", "基金", "基金新闻", "fund"),
    ("37", "理财", "理财产品", "fund"),
    # ── 综合/快讯 ──
    ("109", "快讯", "24小时滚动快讯", "domestic"),
    ("107", "滚动", "全球财经滚动", "international"),
    ("2505", "要闻", "财经要闻", "domestic"),
    ("124", "自选", "自选股资讯", "domestic"),
    ("148", "港美股", "港美股资讯", "international"),
    ("28", "美股", "美股公司", "international"),
    ("167", "外汇", "外汇新闻", "international"),
    ("168", "商品", "大宗商品", "international"),
    ("184", "科技", "科技产业新闻", "domestic"),
    ("185", "汽车", "汽车产业", "domestic"),
    ("186", "医疗", "医疗健康", "domestic"),
]

SOURCE_TYPE_LABELS = {
    "official": "官方政策",
    "domestic": "国内财经",
    "international": "国际财经",
    "fund": "基金数据",
    "flash": "实时快讯",
}

# 新闻所属区域：国内 / 国际（用于顶层筛选）
SOURCE_REGION = {
    "official": "domestic",
    "domestic": "domestic",
    "fund": "domestic",
    "international": "international",
}

# 所有可用的标签列表
ALL_NEWS_TAGS = ["官方政策", "国内财经", "国际财经", "美联储", "海外机构",
                 "产业政策", "市场动态", "科技", "能源", "大宗商品", "国际贸易", "综合资讯"]

KEYWORD_WEIGHTS = {
    # ── 最高级别（5）：系统性风险、重大政策转向、危机事件 ──
    "加息": 5, "降息": 5, "联邦基金利率": 5, "FOMC": 5, "量化宽松": 5,
    "缩表": 5, "点阵图": 5, "降准": 5, "政治局": 5, "印花税": 5,
    "中央经济工作会议": 5, "经济衰退": 5, "金融危机": 5, "债务违约": 5,
    "熔断": 5, "崩盘": 5, "股灾": 5, "救市": 5, "黑天鹅": 5,
    "紧急": 5, "重磅": 5, "突发": 5, "重大利好": 5, "重大利空": 5,
    "战争": 5, "军事": 5, "核": 5, "全面制裁": 5,
    # ── 高影响（4）：重要政策、关键数据、地缘事件 ──
    "鲍威尔": 4, "美联储": 4, "央行": 4, "非农": 4, "CPI": 4,
    "PPI": 4, "GDP": 4, "PMI": 4, "ISM": 4, "美债": 4,
    "社融": 4, "M2": 4, "货币政策": 4, "LPR": 4, "MLF": 4,
    "国务院": 4, "证监会": 4, "银保监": 4, "国常会": 4, "两会": 4,
    "注册制": 4, "退市": 4, "再融资": 4, "新质生产力": 4,
    "人民币": 4, "汇率": 4, "关税": 4, "贸易战": 4, "制裁": 4,
    "俄乌": 4, "中东": 4, "地缘": 4, "财政": 4, "赤字率": 4,
    "专项债": 4, "房贷": 4, "首付": 4, "限购": 4,
    "原油": 4, "黄金": 4, "油价": 4, "金价": 4,
    "欧洲央行": 4, "日本央行": 4, "IMF": 4, "世界银行": 4,
    "英伟达": 4, "特斯拉": 4, "苹果": 4, "华为": 4,
    "万亿": 4, "千亿": 4, "历史新高": 4, "历史低位": 4,
    "破纪录": 4, "超预期": 4, "大幅": 4, "暴涨": 4, "暴跌": 4,
    "牛市": 4, "熊市": 4, "技术性": 4,
    # ── 中影响（3）：行业动态、公司事件、一般政策 ──
    "通胀": 3, "就业": 3, "初请失业金": 3, "零售销售": 3, "逆回购": 3,
    "SLF": 3, "发改委": 3, "T+0": 3, "IPO": 3, "科创板": 3,
    "北交所": 3, "新能源": 3, "半导体": 3, "芯片": 3, "光伏": 3,
    "锂电": 3, "人工智能": 3, "数字经济": 3, "碳中和": 3, "房地产": 3,
    "低空经济": 3, "人形机器人": 3, "量子计算": 3, "自动驾驶": 3,
    "设备更新": 3, "以旧换新": 3, "减税": 3, "消费券": 3,
    "保交楼": 3, "公积金": 3, "FDI": 3, "离岸": 3,
    "基金": 3, "ETF": 3, "公募": 3, "私募": 3, "分红": 3,
    "回购": 3, "增持": 3, "减持": 3, "举牌": 3, "定增": 3,
    "解禁": 3, "涨停": 3, "跌停": 3, "停牌": 3, "复牌": 3,
    "借壳": 3, "并购": 3, "收购": 3, "重组": 3, "私有化": 3,
    "要约收购": 3, "混改": 3, "国企改革": 3, "央企": 3,
    "AI": 3, "大模型": 3, "DeepSeek": 3, "ChatGPT": 3, "OpenAI": 3,
    "算力": 3, "光刻机": 3, "晶圆": 3, "GPU": 3, "昇腾": 3,
    "华为鸿蒙": 3, "麒麟": 3, "5G": 3, "6G": 3, "卫星": 3,
    "储能": 3, "固态电池": 3, "风电": 3, "氢能": 3, "钠离子": 3,
    "创新药": 3, "医疗器械": 3, "CXO": 3, "生物医药": 3,
    "消费电子": 3, "汽车": 3, "新能源车": 3, "电动车": 3,
    "机器人": 3, "无人机": 3, "商业航天": 3, "脑机": 3,
    "网络安全": 3, "数据要素": 3, "信创": 3, "东数西算": 3,
    "移动支付": 3, "数字货币": 3, "区块链": 3, "元宇宙": 3,
    "虚拟现实": 3, "增强现实": 3,
    "茅台": 3, "宁德时代": 3, "比亚迪": 3, "字节跳动": 3, "腾讯": 3,
    "阿里": 3, "美团": 3, "小米": 3, "拼多多": 3, "京东": 3,
    "百度": 3, "中芯国际": 3, "海思": 3, "台积电": 3, "三星": 3,
    "高通": 3, "英特尔": 3, "AMD": 3, "微软": 3, "谷歌": 3,
    "Meta": 3, "亚马逊": 3,
    "铜": 3, "铝": 3, "螺纹钢": 3, "铁矿石": 3, "煤炭": 3,
    "天然气": 3, "白银": 3, "有色金属": 3, "稀土": 3, "锂": 3,
    "钴": 3, "镍": 3, "硅": 3, "碳酸锂": 3,
    "大豆": 3, "玉米": 3, "棉花": 3, "白糖": 3, "生猪": 3,
    "粮食": 3, "农产品": 3, "猪周期": 3,
    # ── 低影响（2）：一般消息、常规报道 ──
    "分红": 2, "理财产品": 2, "定投": 2, "申购": 2, "赎回": 2,
    "净值": 2, "基金经理": 2, "债券": 2, "国债": 2, "可转债": 2,
    "保险": 2, "银行": 2, "证券": 2, "期货": 2, "信托": 2,
    "研报": 2, "评级": 2, "目标价": 2, "看好": 2, "跑赢": 2,
    "跑输": 2, "中性": 2, "增持": 2, "买入": 2, "卖出": 2,
    "财报": 2, "营收": 2, "利润": 2, "净利": 2, "业绩": 2,
    "股东": 2, "现金": 2, "股息": 2, "派息": 2,
}

AFFECTED_SECTORS_MAP = {
    "加息": ["银行", "地产", "科技"],
    "降息": ["地产", "科技", "消费"],
    "降准": ["银行", "地产", "券商"],
    "新能源": ["新能源", "光伏", "风电", "锂电"],
    "半导体": ["半导体", "芯片", "电子"],
    "人工智能": ["AI", "计算机", "传媒"],
    "房地产": ["地产", "建材", "家居"],
    "原油": ["石油", "化工", "航空"],
    "黄金": ["黄金", "有色", "珠宝"],
    "人民币": ["外贸", "航空", "造纸"],
    "关税": ["外贸", "制造", "农业"],
    "自动驾驶": ["汽车", "AI", "传感器"],
    "低空经济": ["航空", "无人机", "物流"],
    "设备更新": ["制造", "工业母机", "自动化"],
}

CACHE = {}
CACHE_TTL = 60


def _cache_get(key, fetcher, ttl=None):
    ttl = ttl or CACHE_TTL
    now = time.time()
    if key in CACHE and now - CACHE[key]["ts"] < ttl:
        return CACHE[key]["data"]
    data = fetcher()
    CACHE[key] = {"ts": now, "data": data}
    return data


def _score_news(title: str, content: str = ""):
    text = f"{title} {content}".lower()
    score = 1
    max_kw_score = 1
    for kw, weight in KEYWORD_WEIGHTS.items():
        if kw.lower() in text:
            max_kw_score = max(max_kw_score, weight)
            count = text.count(kw.lower())
            score += weight * min(count, 3)
    if max_kw_score >= 5:
        stars = 5
    elif score >= 14:
        stars = 5
    elif score >= 8:
        stars = 4
    elif score >= 4:
        stars = 3
    elif score >= 2:
        stars = 2
    else:
        stars = 1
    return stars


def _detect_sectors(title: str, content: str = ""):
    text = f"{title} {content}"
    sectors = set()
    for kw, secs in AFFECTED_SECTORS_MAP.items():
        if kw in text:
            sectors.update(secs)
    return list(sectors)[:5]


def _generate_tags(title: str, content: str = "", source_type: str = ""):
    """为一条新闻生成多个标签（基于来源类型 + 内容关键词匹配）"""
    text = f"{title} {content}".lower()
    tags = []

    # 来源类型→基础标签
    source_tag_map = {
        "official": "官方政策",
        "domestic": "国内财经",
        "international": "国际财经",
        "fund": "国内财经",
    }
    base_tag = source_tag_map.get(source_type)
    if base_tag and base_tag not in tags:
        tags.append(base_tag)

    # 内容关键词→标签
    if any(kw in text for kw in ["美联储", "fed", "加息", "降息", "非农",
                                   "cpi", "ppi", "鲍威尔", "fomc", "量化宽松",
                                   "缩表", "美债", "美元指数", "华尔街",
                                   "联邦基金", "点阵图"]):
        if "美联储" not in tags: tags.append("美联储")

    if any(kw in text for kw in ["央行", "中国人民银行", "降准", "lpr", "mlf", "slf",
                                   "逆回购", "国务院", "政治局", "证监会", "银保监",
                                   "发改委", "国常会", "社融", "m2", "货币政策",
                                   "财政", "专项债", "赤字率", "印花税", "中央经济",
                                   "两会", "政府工作", "减税降费", "转移支付",
                                   "国家金融", "金融监管", "存款准备", "利率调整",
                                   "宏观审慎", "深改委", "中央财经", "十四五",
                                   "人民银行", "银监会", "保监会", "统计局"]):
        if "官方政策" not in tags: tags.append("官方政策")

    if any(kw in text for kw in ["欧洲央行", "日本央行", "imf", "俄乌", "中东",
                                   "欧佩克", "路透", "彭博", "世界银行", "英国央行",
                                   "韩国央行", "澳洲联储", "印度央行", "北约",
                                   "贸易代表", "美中", "中美", "地缘",
                                   "opec", "东盟", "亚太", "欧盟委员"]):
        if "海外机构" not in tags: tags.append("海外机构")

    if any(kw in text for kw in ["新能源", "半导体", "芯片", "光伏", "锂电",
                                   "ai", "人工智能", "低空经济", "机器人",
                                   "自动驾驶", "量子", "新质生产力", "人形",
                                   "储能", "氢能", "风电", "电池", "固态电池",
                                   "生物制造", "商业航天", "新材料", "创新药",
                                   "医疗器械", "脑机", "大模型", "算力",
                                   "6g", "数据要素", "数字产业", "智能网联",
                                   "具身智能", "生物医药", "基因", "细胞治疗",
                                   "汽车", "电动车", "智能驾驶", "网络安全",
                                   "数字化", "工业互联网", "先进制造", "专精特新"]):
        if "产业政策" not in tags: tags.append("产业政策")

    if any(kw in text for kw in ["a股", "沪指", "深指", "创业板", "科创板",
                                   "北交所", "ipo", "退市", "注册制", "上证",
                                   "深证", "涨停", "跌停", "牛市", "熊市",
                                   "解禁", "大宗交易", "龙虎榜", "st股",
                                   "回购", "增持", "减持", "举牌", "定增",
                                   "配股", "可转债", "新股", "打新",
                                   "大市", "盘面", "收复", "失守",
                                   "涨超", "股市", "行情", "收盘",
                                   "私有化", "要约收购", "借壳"]):
        if "市场动态" not in tags: tags.append("市场动态")

    if any(kw in text for kw in ["ai", "人工智能", "芯片", "半导体", "机器人",
                                   "自动驾驶", "量子", "大模型", "算力",
                                   "数据要素", "数字化", "工业互联网", "先进制造",
                                   "专精特新", "人形", "脑机", "具身智能",
                                   "智能驾驶", "智能网联", "计算机", "软件",
                                   "消费电子", "华为", "苹果", "特斯拉", "英伟达",
                                   "云计算", "大数据", "物联网", "区块链", "无人机",
                                   "5g", "6g", "vr", "ar", "增强现实", "虚拟现实",
                                   "openai", "chatgpt", "aigc", "大语言模型",
                                   "gpu", "cpu", "光刻机", "晶圆", "封装",
                                   "服务器", "数据中心", "智能手机", "可穿戴",
                                   "网络安全", "操作系统", "数据库"]):
        if "科技" not in tags: tags.append("科技")

    if any(kw in text for kw in ["新能源", "光伏", "锂电", "储能", "氢能",
                                   "风电", "电池", "固态电池", "生物制造",
                                   "商业航天", "新材料", "新能源车", "电动车",
                                   "原油", "石油", "天然气", "油价", "成品油",
                                   "页岩油", "油气", "光伏组件", "硅料", "硅片",
                                   "逆变器", "风机", "海上风电", "陆上风电",
                                   "储能电站", "充电桩", "换电", "动力电池",
                                   "磷酸铁锂", "三元锂", "钠离子", "电解液",
                                   "正极材料", "负极材料", "电力", "电网",
                                   "特高压", "智能电网", "虚拟电厂",
                                   "可再生能源", "清洁能源", "碳中和", "碳达峰",
                                   "碳排放", "碳交易", "绿电", "绿证", "节能"]):
        if "能源" not in tags: tags.append("能源")

    if any(kw in text for kw in ["原油", "黄金", "铜", "铝", "螺纹钢",
                                   "铁矿石", "煤炭", "天然气", "油价", "金价",
                                   "白银", "有色金属", "稀土", "钢材", "焦煤",
                                   "焦炭", "动力煤", "液化天然气", "lng",
                                   "锂", "钴", "镍", "锡", "锌", "石油",
                                   "期货", "现货", "交易所库存", "交割",
                                   "能化", "农产", "大豆", "玉米", "棉花"]):
        if "大宗商品" not in tags: tags.append("大宗商品")

    if any(kw in text for kw in ["人民币", "汇率", "离岸", "外汇", "fdi",
                                   "关税", "贸易战", "制裁", "出口", "进口",
                                   "顺差", "逆差", "贸易", "商务部",
                                   "反倾销", "wto", "自贸", "rcep",
                                   "跨境", "外汇储备", "经常账户",
                                   "资本账户", "结汇", "售汇"]):
        if "国际贸易" not in tags: tags.append("国际贸易")

    # 财经相关性过滤
    finance_broad = [
        "经济", "金融", "银行", "保险", "证券", "股票", "基金", "期货",
        "债券", "信托", "理财", "投资", "融资", "上市", "收购", "并购",
        "美元", "美股", "a股", "港股", "指数", "涨", "跌", "市值",
        "财报", "营收", "利润", "净利", "业绩", "股东", "分红",
        "宏观", "政策", "监管", "改革", "部长", "书记", "总理", "主席",
        "公司", "企业", "产业", "行业", "制造", "消费", "零售",
        "科技", "芯片", "ai", "数据", "能源", "汽车", "医药",
        "货币", "利率", "债务", "赤字", "通胀", "gdp", "就业",
        "楼盘", "楼市", "地块", "土拍", "开盘", "成交",
        "供应链", "工厂", "产能", "物流",
        "万亿", "千亿", "百亿", "亿元",
        "工资", "收入", "消费", "物价", "税收",
        "研报", "机构", "评级", "目标价",
    ]
    if not tags and not any(kw in text for kw in finance_broad):
        return None

    if not tags:
        tags.append("综合资讯")

    return tags


def _fetch_sina_channel(lid: str, label: str, source_name: str, source_type: str):
    """从新浪财经单个频道获取新闻"""
    news = []
    try:
        resp = SESSION.get(
            "https://feed.mix.sina.com.cn/api/roll/get",
            params={
                "pageid": "153",
                "lid": lid,
                "k": "",
                "num": "30",
                "page": "1",
                "r": str(time.time()),
                "callback": "",
            },
            timeout=8,
        )
        data = resp.json()
        if data.get("result") and data["result"].get("data"):
            for item in data["result"]["data"]:
                title = item.get("title", "")
                if not title or len(title) < 4:
                    continue
                content = item.get("intro", "") or item.get("summary", "") or ""
                ctime = item.get("ctime", "")
                stars = _score_news(title, content)
                sectors = _detect_sectors(title, content)
                tags = _generate_tags(title, content, source_type)
                if tags is None:
                    continue
                region = SOURCE_REGION.get(source_type, "domestic")
                news.append({
                    "title": title,
                    "source": source_name,
                    "source_type": source_type,
                    "source_type_label": SOURCE_TYPE_LABELS.get(source_type, source_type),
                    "time": ctime,
                    "stars": stars,
                    "tags": tags,
                    "region": region,
                    "sectors": sectors,
                    "summary": content[:200] if content else "",
                })
    except Exception as e:
        logger.warning(f"新浪{label}(lid={lid})获取失败: {e}")
    return news


def get_financial_news():
    """聚合财经新闻 — 新浪多频道并行获取"""
    def _fetch():
        all_news = []
        seen_titles = set()

        # 并行获取所有频道
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {
                pool.submit(_fetch_sina_channel, lid, label, source_name, source_type): (lid, label)
                for lid, label, source_name, source_type in SINA_CHANNELS
            }
            for f in as_completed(futures):
                for n in f.result():
                    title_key = n["title"][:60]
                    if title_key not in seen_titles:
                        seen_titles.add(title_key)
                        all_news.append(n)

        all_news.sort(key=lambda x: -x["stars"])
        return all_news

    return _cache_get("financial_news", _fetch, ttl=60)


def filter_news_by_tag(tag: str = None):
    """按标签筛选新闻"""
    news = get_financial_news()
    if tag and tag != "全部":
        news = [n for n in news if tag in n.get("tags", [])]
    return news[:50]


def get_top_headlines(limit: int = 10):
    news = get_financial_news()
    headlines = [n for n in news if n["stars"] >= 4]
    if len(headlines) < limit:
        headlines = news[:limit]
    return headlines[:limit]


# ── 区域筛选选项（供前端使用） ──
def get_region_options():
    return [
        {"id": "all", "name": "全部资讯"},
        {"id": "domestic", "name": "国内资讯"},
        {"id": "international", "name": "国际资讯"},
    ]


# ── 标签列表（供前端筛选） ──
def get_tag_options():
    return [{"id": t, "name": t} for t in ALL_NEWS_TAGS]
