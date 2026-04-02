import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

import akshare as ak
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from pydantic import BaseModel, ConfigDict
import pandas as pd

logger = logging.getLogger(__name__)

# Pydantic å“åº”æ¨¡åž‹

class StockSpot(BaseModel):
    """ä¸ªè‚¡å®žæ—¶è¡Œæƒ…"""
    symbol: str
    name: str
    latest_price: float
    pct_change: float
    update_time: Optional[datetime] = None

class IndexKLine(BaseModel):
    """æŒ‡æ•°åŽ†å²æ—¥ K çº¿"""
    date: str
    close: float
    volume: float
    ma20: float
    below_ma20: bool

class StockKLine(BaseModel):
    """ä¸ªè‚¡åŽ†å²æ—¥ K çº¿"""
    date: str
    close: float
    ma5: float
    ma10: float
    ma20: float

class AkShareClient:
    """AkShare å¼‚æ­¥å°è£…ä¸Žé‡è¯•å®¢æˆ·ç«¯"""

    _trade_dates_cache = None
    _trade_dates_last_update: Optional[str] = None

    @classmethod
    async def is_trading_date(cls, target_date: Optional[datetime] = None) -> bool:
        """æ£€æŸ¥æŒ‡å®šæ—¥æœŸæ˜¯å¦ä¸ºçœŸå®žäº¤æ˜“æ—¥ï¼Œåˆ©ç”¨ç¼“å­˜é¿å…é¢‘ç¹è¯·æ±‚"""
        if not target_date:
            target_date = datetime.now()
            
        date_str = target_date.strftime("%Y%m%d")
        
        # ç®€å•ç¼“å­˜ç­–ç•¥ï¼šæ¯å¤©èŽ·å–ä¸€æ¬¡å³å¯
        if cls._trade_dates_cache is None or cls._trade_dates_last_update != target_date.strftime("%Y-%m"):
            loop = asyncio.get_running_loop()
            try:
                # èŽ·å–æ–°æµªçš„äº¤æ˜“æ—¥åŽ†ï¼Œè¿”å›žå¸¦æœ‰äº¤æ˜“æ—¥çš„ df
                df = await loop.run_in_executor(None, ak.tool_trade_date_hist_sina)
                cls._trade_dates_cache = set(df['trade_date'].astype(str).tolist())
                cls._trade_dates_last_update = target_date.strftime("%Y-%m")
            except Exception as e:
                logger.error(f"èŽ·å–äº¤æ˜“æ—¥åŽ†å¤±è´¥: {e}")
                # é™çº§ï¼šå¦‚æžœèŽ·å–å¤±è´¥ï¼Œå…ˆæŒ‰å¸¸è§„å·¥ä½œæ—¥å…è®¸è¿”å›ž true (æˆ– false)
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
        """èŽ·å– A è‚¡æŒ‡å®šè‚¡ç¥¨å®žæ—¶ç›˜å£å¿«ç…§"""
        if not symbol_list:
            return []
            
        logger.info(f"æ­£åœ¨èŽ·å–å®žæ—¶è¡Œæƒ…ï¼Œæ ‡çš„: {symbol_list}")
        # akshare åŽŸç”Ÿåªæœ‰é˜»å¡ž APIï¼Œç”¨çº¿ç¨‹æ± è¿è¡Œä»¥å…é˜»å¡ž FastAPI/Celery Worker
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, ak.stock_zh_a_spot_em)
        
        if df.empty:
            return []
            
        # ç®€å•è½¬æ¢åˆ—å
        # akshare.stock_zh_a_spot_em è¿”å›žåŒ…å«â€œä»£ç â€, â€œåç§°â€, â€œæœ€æ–°ä»·â€, â€œæ¶¨è·Œå¹…â€ ç­‰å­—æ®µ
        df.rename(columns={
            "ä»£ç ": "symbol",
            "åç§°": "name",
            "æœ€æ–°ä»·": "latest_price",
            "æ¶¨è·Œå¹…": "pct_change"
        }, inplace=True)
        
        # ç­›é€‰
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
        èŽ·å–æŒ‡æ•°åŽ†å²æ—¥ K çº¿å¹¶è®¡ç®— MA20
        é»˜è®¤ä¸Šè¯æŒ‡æ•° 000001
        """
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(
            None, 
            lambda: ak.index_zh_a_hist(symbol=symbol, period="daily")
        )
        if df.empty:
            return None
            
        # è®¡ç®— 20 æ—¥å‡çº¿
        df['MA20'] = df['æ”¶ç›˜'].rolling(window=20).mean()
        
        # æ‹¿æœ€åŽä¸€å¤©
        last_row = df.iloc[-1]
        close_price = float(last_row['æ”¶ç›˜'])
        ma20_val = float(last_row['MA20']) if not pd.isna(last_row['MA20']) else 0.0
        
        return IndexKLine(
            date=str(last_row['æ—¥æœŸ']),
            close=close_price,
            volume=float(last_row['æˆäº¤é‡']),
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
        èŽ·å–ä¸ªè‚¡åŽ†å²æ—¥ K çº¿ (å‰å¤æƒ)ï¼Œè®¡ç®— MA5/MA10/MA20
        """
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(
            None,
            lambda: ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
        )
        if df.empty or len(df) < 5:
            return None
            
        df['MA5'] = df['æ”¶ç›˜'].rolling(window=5).mean()
        df['MA10'] = df['æ”¶ç›˜'].rolling(window=10).mean()
        df['MA20'] = df['æ”¶ç›˜'].rolling(window=20).mean()
        
        last_row = df.iloc[-1]
        return StockKLine(
            date=str(last_row['æ—¥æœŸ']),
            close=float(last_row['æ”¶ç›˜']),
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
        èŽ·å–å…¨å¸‚åœº ST è‚¡ç¥¨åå•ä»£ç ï¼ˆæ¯å¤©èŽ·å–ä¸€æ¬¡å³å¯ï¼‰
        """
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, ak.stock_zh_a_st_em)
        if df.empty:
            return []
            
        return df['ä»£ç '].astype(str).tolist()


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
