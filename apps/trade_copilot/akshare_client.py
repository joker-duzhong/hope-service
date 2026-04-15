import asyncio
import json
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
# 通用 HTTP 请求工具
# ---------------------------------------------------------------------------
_retry_adapter = HTTPAdapter(
    max_retries=Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
)


_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn",
}


def _http_get(url: str, params: dict = None, timeout: int = 15) -> requests.Response:
    """带重试、绕过代理的同步 GET 请求

    注意：新浪行情接口的 list 参数包含逗号，不能被 URL 编码为 %2C，
    因此对包含逗号的值需要手动拼接到 URL 中而非走 params 序列化。
    """
    with requests.Session() as s:
        s.trust_env = False
        s.mount("http://", _retry_adapter)
        s.mount("https://", _retry_adapter)
        if params:
            # 新浪 API 要求逗号原样传递，不能被 percent-encode
            parts = []
            for k, v in params.items():
                if isinstance(v, str) and ',' in v:
                    # 含逗号的值直接拼接到 URL，避免 requests 编码为 %2C
                    from urllib.parse import quote
                    parts.append(f"{quote(k, safe='')}={v}")
                else:
                    from urllib.parse import urlencode
                    parts.append(urlencode({k: v}))
            sep = '&' if '?' in url else '?'
            url = f"{url}{sep}{'&'.join(parts)}"
            resp = s.get(url, timeout=timeout, headers=_HEADERS)
        else:
            resp = s.get(url, timeout=timeout, headers=_HEADERS)
        resp.raise_for_status()
        return resp


def _sina_prefix(symbol: str) -> str:
    """新浪行情代码: 6/9开头→sh, 其余→sz"""
    if symbol.startswith(('6', '9')):
        return f"sh{symbol}"
    return f"sz{symbol}"


# ---------------------------------------------------------------------------
# Monkey-patch akshare 请求层（用于交易日历等仍走 akshare 的接口）
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
    symbol: str
    name: str
    latest_price: float
    pct_change: float
    update_time: Optional[datetime] = None


class IndexKLine(BaseModel):
    date: str
    close: float
    volume: float
    ma20: float
    below_ma20: bool


class StockKLine(BaseModel):
    date: str
    close: float
    ma5: float
    ma10: float
    ma20: float


class StockBasicInfo(BaseModel):
    symbol: str
    name: str
    industry: Optional[str] = None
    sector: Optional[str] = None
    list_date: Optional[str] = None
    total_market_value: Optional[float] = None
    circulating_market_value: Optional[float] = None
    is_st: bool = False


# ==================== 数据客户端 ====================

SINA_KLINE = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
SINA_HQ_URL = "http://hq.sinajs.cn/list"


class AkShareClient:
    """A股数据异步客户端 — 全部走新浪数据源 (Docker 兼容)"""

    _trade_dates_cache = None
    _trade_dates_last_update: Optional[str] = None

    # ==================== 交易日历 ====================

    @classmethod
    async def is_trading_date(cls, target_date: Optional[datetime] = None) -> bool:
        """检查指定日期是否为真实交易日"""
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

    # ==================== 实时行情 (新浪) ====================

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_a_shares_spot(cls, symbol_list: List[str]) -> List[StockSpot]:
        """通过新浪获取实时行情"""
        if not symbol_list:
            return []

        logger.info(f"正在获取实时行情(新浪)，标的: {symbol_list}")

        sina_codes = ",".join(_sina_prefix(s) for s in symbol_list)
        loop = asyncio.get_running_loop()
        resp_text = await loop.run_in_executor(
            None, lambda: _http_get(SINA_HQ_URL, params={"list": sina_codes}).text
        )

        results = []
        now = datetime.now()
        for line in resp_text.strip().split("\n"):
            line = line.strip()
            if not line or '=""' in line:
                continue
            match = re.match(r'var hq_str_s([hz])(\d+)="(.+)"', line)
            if not match:
                continue
            _, code, fields_str = match.groups()
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
                symbol=code, name=name,
                latest_price=current_price, pct_change=pct_change,
                update_time=now,
            ))
        return results

    # ==================== K线数据 (新浪) ====================

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_index_kline(cls, symbol: str = "000001", days: int = 30) -> Optional[IndexKLine]:
        """
        通过新浪获取指数日 K 线并计算 MA20
        默认上证指数 000001
        """
        params = {
            "symbol": _sina_prefix(symbol),
            "scale": "240",   # 240分钟=日线
            "ma": "no",
            "datalen": str(max(days, 30)),
        }
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(
            None, lambda: _http_get(SINA_KLINE, params=params).json()
        )

        if not raw or len(raw) < 20:
            return None

        closes = [float(item["close"]) for item in raw]
        ma20 = sum(closes[-20:]) / 20
        last = raw[-1]

        return IndexKLine(
            date=last["day"],
            close=float(last["close"]),
            volume=float(last["volume"]),
            ma20=round(ma20, 4),
            below_ma20=float(last["close"]) < ma20 if ma20 > 0 else False,
        )

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_stock_kline(cls, symbol: str, days: int = 60) -> Optional[StockKLine]:
        """通过新浪获取个股日 K 线，计算 MA5/MA10/MA20"""
        params = {
            "symbol": _sina_prefix(symbol),
            "scale": "240",
            "ma": "no",
            "datalen": str(max(days, 60)),
        }
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(
            None, lambda: _http_get(SINA_KLINE, params=params).json()
        )

        if not raw or len(raw) < 5:
            return None

        closes = [float(item["close"]) for item in raw]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else 0.0
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else 0.0
        last = raw[-1]

        return StockKLine(
            date=last["day"],
            close=float(last["close"]),
            ma5=round(ma5, 4),
            ma10=round(ma10, 4),
            ma20=round(ma20, 4),
        )

    # ==================== ST 股票列表 ====================

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_all_st_stocks(cls) -> List[str]:
        """
        获取 ST 股票名单 — 从全 A 股列表中筛选名称含 ST 的股票
        """
        loop = asyncio.get_running_loop()
        try:
            df = await loop.run_in_executor(None, ak.stock_info_a_code_name)
            if df.empty:
                return []
            st_df = df[df['name'].str.contains('ST', case=False, na=False)]
            codes = st_df['code'].astype(str).tolist()
            logger.info(f"从股票列表中筛选到 {len(codes)} 只 ST 股票")
            return codes
        except Exception as e:
            logger.error(f"获取 ST 股票列表失败: {e}")
            return []

    # ==================== 全市场温度计 ====================

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_market_thermometer_data(cls) -> Dict[str, Any]:
        """
        获取全市场温度计数据 — 批量拉取新浪实时行情
        返回 {"spot": [{symbol, name, pct_change}, ...], "board": []}
        新浪单次最多约 800 只，分批拉取
        """
        loop = asyncio.get_running_loop()

        # 1. 获取全部股票列表
        df = await loop.run_in_executor(None, ak.stock_info_a_code_name)
        if df.empty:
            raise ValueError("获取股票列表为空")

        all_codes = [(str(row['code']), _sina_prefix(str(row['code']))) for _, row in df.iterrows()]

        # 2. 分批请求新浪行情 (每批 200，加延时防反爬)
        batch_size = 200
        all_items = []
        total_batches = (len(all_codes) + batch_size - 1) // batch_size
        for i in range(0, len(all_codes), batch_size):
            batch_idx = i // batch_size + 1
            batch = all_codes[i:i + batch_size]
            sina_codes = ",".join(c[1] for c in batch)
            try:
                resp_text = await loop.run_in_executor(
                    None, lambda sc=sina_codes: _http_get(SINA_HQ_URL, params={"list": sc}).text
                )
                batch_count = 0
                for line in resp_text.strip().split("\n"):
                    line = line.strip()
                    if not line or '=""' in line:
                        continue
                    match = re.match(r'var hq_str_s([hz])(\d+)="(.+)"', line)
                    if not match:
                        continue
                    _, code, fields_str = match.groups()
                    fields = fields_str.split(",")
                    if len(fields) < 10:
                        continue
                    try:
                        yesterday_close = float(fields[2] or 0)
                        current_price = float(fields[3] or 0)
                        pct = round((current_price - yesterday_close) / yesterday_close * 100, 2) if yesterday_close > 0 else 0.0
                        all_items.append({
                            "f12": code, "f14": fields[0],
                            "f2": current_price, "f3": pct,
                            # 额外字段供 StockInfo 每日更新使用
                            "open": float(fields[1] or 0),
                            "yesterday_close": yesterday_close,
                            "high": float(fields[4] or 0),
                            "low": float(fields[5] or 0),
                            "volume": float(fields[7] or 0),
                            "amount": float(fields[8] or 0),
                        })
                        batch_count += 1
                    except (ValueError, TypeError):
                        continue
                if batch_idx <= 2 or batch_count == 0:
                    logger.info(f"温度计第 {batch_idx}/{total_batches} 批: 获取 {batch_count} 只, 响应长度 {len(resp_text)}")
            except Exception as e:
                logger.warning(f"批量获取行情第 {batch_idx} 批失败: {e}")
            # 每批之间休眠 0.5s，避免触发反爬
            if i + batch_size < len(all_codes):
                import time
                await loop.run_in_executor(None, lambda: time.sleep(0.5))

        logger.info(f"市场温度计: 共获取到 {len(all_items)} 只股票行情")
        return {"spot": all_items, "board": []}

    # ==================== 全 A 股列表 ====================

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

    # ==================== 个股详情 ====================

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_stock_detail_info(cls, symbol: str) -> Optional[StockBasicInfo]:
        """获取单只股票详细信息 (通过新浪实时行情)"""
        loop = asyncio.get_running_loop()
        try:
            sina_code = _sina_prefix(symbol)
            resp_text = await loop.run_in_executor(
                None, lambda: _http_get(SINA_HQ_URL, params={"list": sina_code}).text
            )
            for line in resp_text.strip().split("\n"):
                line = line.strip()
                if not line or '=""' in line:
                    continue
                match = re.match(r'var hq_str_s[hz]\d+="(.+)"', line)
                if not match:
                    continue
                fields = match.group(1).split(",")
                if len(fields) < 32:
                    continue
                name = fields[0]
                return StockBasicInfo(
                    symbol=symbol,
                    name=name,
                    is_st='ST' in name.upper(),
                )
            return None
        except Exception as e:
            logger.error(f"获取股票 {symbol} 详细信息失败: {e}")
            return None
