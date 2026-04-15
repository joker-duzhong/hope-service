import asyncio
import logging
import re
from typing import List, Dict, Any, Optional
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import akshare as ak
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 通用 HTTP 请求工具：绕过系统代理 + transport 级重试
# ---------------------------------------------------------------------------
_retry_adapter = HTTPAdapter(
    max_retries=Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
)


def _http_get(url: str, params: dict = None, timeout: int = 15) -> requests.Response:
    """带重试、绕过代理的同步 GET 请求"""
    with requests.Session() as s:
        s.trust_env = False
        s.mount("http://", _retry_adapter)
        s.mount("https://", _retry_adapter)
        resp = s.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp


# ---------------------------------------------------------------------------
# secid 工具：东方财富 API 需要 "市场前缀.代码" 格式
#   上海: 6/9 开头 → 前缀 1
#   深圳: 0/3 开头 → 前缀 0
# ---------------------------------------------------------------------------
def _secid(symbol: str) -> str:
    if symbol.startswith(('6', '9')):
        return f"1.{symbol}"
    return f"0.{symbol}"


def _sina_prefix(symbol: str) -> str:
    """新浪行情前缀: sh / sz"""
    if symbol.startswith(('6', '9')):
        return f"sh{symbol}"
    return f"sz{symbol}"


# ---------------------------------------------------------------------------
# Monkey-patch akshare 请求层（仅用于仍依赖 akshare 的接口，如交易日历）
# ---------------------------------------------------------------------------
import random, time
from typing import Tuple


def _patched_request_with_retry(
    url: str, params: Dict = None, timeout: int = 15,
    max_retries: int = 3, base_delay: float = 1.0,
    random_delay_range: Tuple[float, float] = (0.5, 1.5),
) -> requests.Response:
    last_exception = None
    for attempt in range(max_retries):
        try:
            with requests.Session() as session:
                session.trust_env = False
                adapter = HTTPAdapter(
                    pool_connections=1, pool_maxsize=1,
                    max_retries=Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504]),
                )
                session.mount("http://", adapter)
                session.mount("https://", adapter)
                response = session.get(url, params=params, timeout=timeout)
                response.raise_for_status()
                return response
        except (requests.RequestException, ValueError) as e:
            last_exception = e
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(*random_delay_range)
                time.sleep(delay)
    raise last_exception


ak.utils.request.request_with_retry = _patched_request_with_retry
import akshare.utils.func as _ak_func  # noqa: E402
_ak_func.request_with_retry = _patched_request_with_retry
logger.info("已为 akshare 请求层注入 transport 级重试机制")


# ==================== Pydantic 响应模型 ====================

class StockSpot(BaseModel):
    """个股实时行情"""
    symbol: str
    name: str
    latest_price: float
    pct_change: float
    update_time: Optional[datetime] = None


class IndexKLine(BaseModel):
    """指数历史日 K 线"""
    date: str
    close: float
    volume: float
    ma20: float
    below_ma20: bool


class StockKLine(BaseModel):
    """个股历史日 K 线"""
    date: str
    close: float
    ma5: float
    ma10: float
    ma20: float


class StockBasicInfo(BaseModel):
    """A股股票基本信息"""
    symbol: str
    name: str
    industry: Optional[str] = None
    sector: Optional[str] = None
    list_date: Optional[str] = None
    total_market_value: Optional[float] = None
    circulating_market_value: Optional[float] = None
    is_st: bool = False


# ==================== 数据客户端 ====================

# 东方财富 push2his K线 API
PUSH2HIS_KLINE = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
# 东方财富 push2his 板块列表 API
PUSH2HIS_CLIST = "http://push2his.eastmoney.com/api/qt/clist/get"
# 新浪实时行情 API
SINA_HQ_URL = "http://hq.sinajs.cn/list"


class AkShareClient:
    """A股数据异步客户端 — 数据源: push2his(东方财富历史) + 新浪(实时) + akshare(交易日历等)"""

    _trade_dates_cache = None
    _trade_dates_last_update: Optional[str] = None

    # ---------- 交易日历 (akshare / Sina，可用) ----------

    @classmethod
    async def is_trading_date(cls, target_date: Optional[datetime] = None) -> bool:
        """检查指定日期是否为真实交易日，利用缓存避免频繁请求"""
        if not target_date:
            target_date = datetime.now()

        date_str = target_date.strftime("%Y-%m-%d")

        if cls._trade_dates_cache is None or cls._trade_dates_last_update != target_date.strftime("%Y-%m"):
            loop = asyncio.get_running_loop()
            try:
                df = await loop.run_in_executor(None, ak.tool_trade_date_hist_sina)
                cls._trade_dates_cache = set(df['trade_date'].astype(str).tolist())
                cls._trade_dates_last_update = target_date.strftime("%Y-%m")
            except Exception as e:
                logger.error(f"获取交易日历失败: {e}")
                return target_date.weekday() < 5

        return date_str in cls._trade_dates_cache

    # ---------- 实时行情 (新浪) ----------

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_a_shares_spot(cls, symbol_list: List[str]) -> List[StockSpot]:
        """通过新浪财经获取 A 股指定股票实时盘口快照"""
        if not symbol_list:
            return []

        logger.info(f"正在获取实时行情(新浪)，标的: {symbol_list}")

        # 新浪行情接口: hq.sinajs.cn/list=sh600519,sz000001,...
        sina_codes = ",".join(_sina_prefix(s) for s in symbol_list)
        loop = asyncio.get_running_loop()
        resp_text = await loop.run_in_executor(
            None, lambda: _http_get(SINA_HQ_URL, params={"list": sina_codes}).text
        )

        results = []
        now = datetime.now()
        # 解析: var hq_str_sh600519="字段0,字段1,...";
        for line in resp_text.strip().split("\n"):
            line = line.strip()
            if not line or '=""' in line:
                continue
            match = re.match(r'var hq_str_(s[hz])(\d+)="(.+)"', line)
            if not match:
                continue
            prefix, code, fields_str = match.groups()
            fields = fields_str.split(",")
            if len(fields) < 32:
                continue

            name = fields[0]
            yesterday_close = float(fields[2] or 0)
            current_price = float(fields[3] or 0)
            pct_change = 0.0
            if yesterday_close > 0:
                pct_change = round((current_price - yesterday_close) / yesterday_close * 100, 2)

            results.append(StockSpot(
                symbol=code,
                name=name,
                latest_price=current_price,
                pct_change=pct_change,
                update_time=now,
            ))

        return results

    # ---------- 指数 K 线 (push2his) ----------

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_index_kline(cls, symbol: str = "000001", days: int = 30) -> Optional[IndexKLine]:
        """
        通过东方财富 push2his 获取指数日 K 线并计算 MA20
        默认上证指数 000001
        """
        params = {
            "secid": _secid(symbol),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56",
            "klt": "101",       # 日K
            "fqt": "1",         # 前复权
            "end": "20500101",
            "lmt": str(max(days, 30)),
        }
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None, lambda: _http_get(PUSH2HIS_KLINE, params=params).json()
        )

        klines = data.get("data", {}).get("klines", [])
        if len(klines) < 20:
            return None

        # 解析最近 20 根K线的收盘价算 MA20
        # kline 格式: "日期,开盘,收盘,最高,最低,成交量"
        closes = [float(k.split(",")[2]) for k in klines]
        ma20 = sum(closes[-20:]) / 20
        last = klines[-1].split(",")
        close_price = float(last[2])

        return IndexKLine(
            date=last[0],
            close=close_price,
            volume=float(last[5]),
            ma20=round(ma20, 4),
            below_ma20=close_price < ma20 if ma20 > 0 else False,
        )

    # ---------- 个股 K 线 (push2his) ----------

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_stock_kline(cls, symbol: str, days: int = 60) -> Optional[StockKLine]:
        """
        通过东方财富 push2his 获取个股日 K 线 (前复权)，计算 MA5/MA10/MA20
        """
        params = {
            "secid": _secid(symbol),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56",
            "klt": "101",
            "fqt": "1",
            "end": "20500101",
            "lmt": str(max(days, 60)),
        }
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None, lambda: _http_get(PUSH2HIS_KLINE, params=params).json()
        )

        klines = data.get("data", {}).get("klines", [])
        if len(klines) < 5:
            return None

        closes = [float(k.split(",")[2]) for k in klines]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else 0.0
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else 0.0
        last = klines[-1].split(",")

        return StockKLine(
            date=last[0],
            close=float(last[2]),
            ma5=round(ma5, 4),
            ma10=round(ma10, 4),
            ma20=round(ma20, 4),
        )

    # ---------- ST 股票列表 (push2his clist) ----------

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_all_st_stocks(cls) -> List[str]:
        """
        通过东方财富 push2his 获取 ST 股票名单
        """
        params = {
            "pn": "1",
            "pz": "500",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "b:BK1153",   # ST 板块
            "fields": "f12,f14",
        }
        loop = asyncio.get_running_loop()
        try:
            data = await loop.run_in_executor(
                None, lambda: _http_get(PUSH2HIS_CLIST, params=params).json()
            )
            items = data.get("data", {}).get("diff", {})
            if isinstance(items, dict):
                return [v["f12"] for v in items.values() if "f12" in v]
            elif isinstance(items, list):
                return [v["f12"] for v in items if "f12" in v]
            return []
        except Exception as e:
            logger.error(f"获取 ST 股票列表失败: {e}")
            return []

    # ---------- 全市场温度计 (push2his clist) ----------

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_market_thermometer_data(cls) -> Dict[str, Any]:
        """通过 push2his 获取全市场个股数据和板块数据"""
        # 个股数据 (取全部 A 股涨跌幅)
        spot_params = {
            "pn": "1",
            "pz": "6000",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f2,f3,f12,f14",
        }
        # 板块数据
        board_params = {
            "pn": "1",
            "pz": "100",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:90+t:3",
            "fields": "f2,f3,f12,f14",
        }
        loop = asyncio.get_running_loop()
        spot_data = await loop.run_in_executor(
            None, lambda: _http_get(PUSH2HIS_CLIST, params=spot_params).json()
        )
        board_data = await loop.run_in_executor(
            None, lambda: _http_get(PUSH2HIS_CLIST, params=board_params).json()
        )
        return {"spot": spot_data.get("data", {}).get("diff", {}),
                "board": board_data.get("data", {}).get("diff", {})}

    # ---------- 全 A 股列表 (akshare，非 push2 接口) ----------

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_all_a_stock_info(cls) -> List[StockBasicInfo]:
        """获取全 A 股股票基本信息列表"""
        loop = asyncio.get_running_loop()
        try:
            df = await loop.run_in_executor(None, ak.stock_info_a_code_name)
            if df.empty:
                logger.warning("获取A股股票列表为空")
                return []

            results = []
            for _, row in df.iterrows():
                results.append(StockBasicInfo(
                    symbol=str(row.get('code', '')),
                    name=str(row.get('name', '')),
                ))
            logger.info(f"成功获取 {len(results)} 只A股股票基本信息")
            return results
        except Exception as e:
            logger.error(f"获取A股股票基本信息失败: {e}")
            raise

    # ---------- 个股详情 (akshare，非 push2 接口) ----------

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_stock_detail_info(cls, symbol: str) -> Optional[StockBasicInfo]:
        """获取单只股票的详细信息"""
        loop = asyncio.get_running_loop()
        try:
            df = await loop.run_in_executor(
                None, lambda: ak.stock_individual_info_em(symbol=symbol)
            )
            if df.empty:
                return None

            info_dict = {}
            for _, row in df.iterrows():
                info_dict[row.get('item', '')] = row.get('value', '')

            return StockBasicInfo(
                symbol=symbol,
                name=str(info_dict.get('股票简称', '')),
                industry=str(info_dict.get('行业', '')) if info_dict.get('行业') else None,
                list_date=str(info_dict.get('上市时间', '')) if info_dict.get('上市时间') else None,
                total_market_value=float(info_dict.get('总市值', 0) or 0) if info_dict.get('总市值') else None,
                circulating_market_value=float(info_dict.get('流通市值', 0) or 0) if info_dict.get('流通市值') else None,
                is_st='ST' in str(info_dict.get('股票简称', '')),
            )
        except Exception as e:
            logger.error(f"获取股票 {symbol} 详细信息失败: {e}")
            return None
