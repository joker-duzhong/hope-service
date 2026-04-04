import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

import akshare as ak
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from pydantic import BaseModel, ConfigDict
import pandas as pd

logger = logging.getLogger(__name__)

# Pydantic 响应模型

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

class AkShareClient:
    """AkShare 异步封装与重试客户端"""

    _trade_dates_cache = None
    _trade_dates_last_update: Optional[str] = None

    @classmethod
    async def is_trading_date(cls, target_date: Optional[datetime] = None) -> bool:
        """检查指定日期是否为真实交易日，利用缓存避免频繁请求"""
        if not target_date:
            target_date = datetime.now()

        date_str = target_date.strftime("%Y%m%d")

        # 简单缓存策略：每天获取一次即可
        if cls._trade_dates_cache is None or cls._trade_dates_last_update != target_date.strftime("%Y-%m"):
            loop = asyncio.get_running_loop()
            try:
                # 获取新浪的交易日历，返回带有交易日的 df
                df = await loop.run_in_executor(None, ak.tool_trade_date_hist_sina)
                cls._trade_dates_cache = set(df['trade_date'].astype(str).tolist())
                cls._trade_dates_last_update = target_date.strftime("%Y-%m")
            except Exception as e:
                logger.error(f"获取交易日历失败: {e}")
                # 降级：如果获取失败，先按常规工作日允许返回 true (或 false)
                return target_date.weekday() < 5

        return date_str in cls._trade_dates_cache

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_a_shares_spot(cls, symbol_list: List[str]) -> List[StockSpot]:
        """获取 A 股指定股票实时盘口快照"""
        if not symbol_list:
            return []

        logger.info(f"正在获取实时行情，标的: {symbol_list}")
        # akshare 原生只有阻塞 API，用线程池运行以免阻塞 FastAPI/Celery Worker
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, ak.stock_zh_a_spot_em)

        if df.empty:
            return []

        # 简单转换列名
        # akshare.stock_zh_a_spot_em 返回包含"代码", "名称", "最新价", "涨跌幅" 等字段
        df.rename(columns={
            "代码": "symbol",
            "名称": "name",
            "最新价": "latest_price",
            "涨跌幅": "pct_change"
        }, inplace=True)

        # 筛选
        filtered_df = df[df["symbol"].isin(symbol_list)].copy()
        now = datetime.now()

        results = []
        for _, row in filtered_df.iterrows():
            item = StockSpot(
                symbol=str(row.get("symbol", "")),
                name=str(row.get("name", "")),
                latest_price=float(row.get("latest_price", 0.0) or 0.0),
                pct_change=float(row.get("pct_change", 0.0) or 0.0),
                update_time=now
            )
            results.append(item)

        return results

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_index_kline(cls, symbol: str = "000001", days: int = 30) -> Optional[IndexKLine]:
        """
        获取指数历史日 K 线并计算 MA20
        默认上证指数 000001
        """
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(
            None,
            lambda: ak.index_zh_a_hist(symbol=symbol, period="daily")
        )
        if df.empty:
            return None

        # 计算 20 日均线
        df['MA20'] = df['收盘'].rolling(window=20).mean()

        # 拿最后一天
        last_row = df.iloc[-1]
        close_price = float(last_row['收盘'])
        ma20_val = float(last_row['MA20']) if not pd.isna(last_row['MA20']) else 0.0

        return IndexKLine(
            date=str(last_row['日期']),
            close=close_price,
            volume=float(last_row['成交量']),
            ma20=ma20_val,
            below_ma20=close_price < ma20_val if ma20_val > 0 else False
        )

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_stock_kline(cls, symbol: str, days: int = 60) -> Optional[StockKLine]:
        """
        获取个股历史日 K 线 (前复权)，计算 MA5/MA10/MA20
        """
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(
            None,
            lambda: ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
        )
        if df.empty or len(df) < 5:
            return None

        df['MA5'] = df['收盘'].rolling(window=5).mean()
        df['MA10'] = df['收盘'].rolling(window=10).mean()
        df['MA20'] = df['收盘'].rolling(window=20).mean()

        last_row = df.iloc[-1]
        return StockKLine(
            date=str(last_row['日期']),
            close=float(last_row['收盘']),
            ma5=float(last_row['MA5']) if not pd.isna(last_row['MA5']) else 0.0,
            ma10=float(last_row['MA10']) if not pd.isna(last_row['MA10']) else 0.0,
            ma20=float(last_row['MA20']) if not pd.isna(last_row['MA20']) else 0.0,
        )

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_all_st_stocks(cls) -> List[str]:
        """
        获取全市场 ST 股票名单代码（每天获取一次即可）
        """
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, ak.stock_zh_a_st_em)
        if df.empty:
            return []

        return df['代码'].astype(str).tolist()


    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_market_thermometer_data(cls) -> Dict[str, Any]:
        """获取全市场温度计数据，返回板块和全市场个股数据字典"""
        loop = asyncio.get_running_loop()
        df_spot = await loop.run_in_executor(None, ak.stock_zh_a_spot_em)
        if df_spot.empty:
            raise ValueError("全市场数据为空")
        df_board = await loop.run_in_executor(None, ak.stock_board_industry_name_em)
        return {"spot": df_spot, "board": df_board}

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_all_a_stock_info(cls) -> List[StockBasicInfo]:
        """
        获取全 A 股股票基本信息列表
        包含代码、名称、行业、上市日期、市值等
        """
        loop = asyncio.get_running_loop()
        try:
            # 获取A股股票列表
            df = await loop.run_in_executor(None, ak.stock_info_a_code_name)
            if df.empty:
                logger.warning("获取A股股票列表为空")
                return []

            results = []
            for _, row in df.iterrows():
                results.append(StockBasicInfo(
                    symbol=str(row.get('code', '')),
                    name=str(row.get('name', '')),
                    industry=None,
                    sector=None,
                    list_date=None,
                    total_market_value=None,
                    circulating_market_value=None,
                    is_st=False
                ))
            logger.info(f"成功获取 {len(results)} 只A股股票基本信息")
            return results
        except Exception as e:
            logger.error(f"获取A股股票基本信息失败: {e}")
            raise

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def get_stock_detail_info(cls, symbol: str) -> Optional[StockBasicInfo]:
        """
        获取单只股票的详细信息
        """
        loop = asyncio.get_running_loop()
        try:
            # 获取个股信息
            df = await loop.run_in_executor(
                None,
                lambda: ak.stock_individual_info_em(symbol=symbol)
            )
            if df.empty:
                return None

            # 解析返回的数据
            info_dict = {}
            for _, row in df.iterrows():
                item = row.get('item', '')
                value = row.get('value', '')
                info_dict[item] = value

            return StockBasicInfo(
                symbol=symbol,
                name=str(info_dict.get('股票简称', '')),
                industry=str(info_dict.get('行业', '')) if info_dict.get('行业') else None,
                sector=None,
                list_date=str(info_dict.get('上市时间', '')) if info_dict.get('上市时间') else None,
                total_market_value=float(info_dict.get('总市值', 0) or 0) if info_dict.get('总市值') else None,
                circulating_market_value=float(info_dict.get('流通市值', 0) or 0) if info_dict.get('流通市值') else None,
                is_st='ST' in str(info_dict.get('股票简称', ''))
            )
        except Exception as e:
            logger.error(f"获取股票 {symbol} 详细信息失败: {e}")
            return None
