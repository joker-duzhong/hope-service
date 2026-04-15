"""
Beike House Crawler - 贝壳房源爬虫
"""
import logging
import asyncio
import time
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
import httpx

logger = logging.getLogger(__name__)


class BeikeCrawler:
    """贝壳房源爬虫"""

    BASE_URL = "https://cd.ke.com"  # 成都
    REGIONS = {
        "高新区": "gaoxinqu",
        "天府新区": "tianfuxinqu",
        "锦江区": "jinjiangqu",
        "青羊区": "qingyangqu",
        "武侯区": "wuhouqu",
        "成华区": "chenghuaqu",
        "金牛区": "jinniuqu",
        "双流区": "shuangliuqu",
        "温江区": "wenjiangqu",
        "郫都区": "piduqu",
        "龙泉驿区": "longquanyiqu",
        "新都区": "xinduqu",
    }

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://cd.ke.com/ershoufang/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    @staticmethod
    async def crawl_region(region_name: str, page: int = 1) -> List[Dict[str, Any]]:
        """
        爬取指定区域的房源列表

        Args:
            region_name: 区域名称 (如 "高新区")
            page: 页码 (从1开始)

        Returns:
            房源列表，每个房源包含:
            - house_id: 房源唯一ID
            - title: 房源标题
            - total_price: 总价(万元)
            - unit_price: 单价(元/㎡)
            - area: 面积(㎡)
            - rooms: 居室数
            - floor: 所在楼层
            - total_floors: 总楼层
            - orientation: 朝向
            - community_name: 小区名称
            - region_name: 区域名称
            - url: 房源链接
            - image_url: 房源图片
        """
        if region_name not in BeikeCrawler.REGIONS:
            logger.warning(f"Region {region_name} not supported")
            return []

        region_pinyin = BeikeCrawler.REGIONS[region_name]
        url = f"{BeikeCrawler.BASE_URL}/ershoufang/{region_pinyin}/pg{page}/"

        try:
            async with httpx.AsyncClient(headers=BeikeCrawler.HEADERS, timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "lxml")
            houses = []

            # 查找房源列表容器
            house_items = soup.find_all("li", class_="sellListItem")

            for item in house_items:
                try:
                    house_data = BeikeCrawler._parse_house_item(item, region_name)
                    if house_data:
                        houses.append(house_data)
                except Exception as e:
                    logger.debug(f"Failed to parse house item: {e}")
                    continue

            logger.info(f"Crawled {len(houses)} houses from {region_name} page {page}")

            # 延迟避免被封
            await asyncio.sleep(0.5)

            return houses

        except Exception as e:
            logger.error(f"Failed to crawl region {region_name} page {page}: {e}")
            return []

    @staticmethod
    def _parse_house_item(item, region_name: str) -> Optional[Dict[str, Any]]:
        """解析单个房源项"""
        try:
            # 房源链接和ID
            link_elem = item.find("a", class_="titleXl")
            if not link_elem:
                return None

            url = link_elem.get("href", "")
            if not url.startswith("http"):
                url = BeikeCrawler.BASE_URL + url

            # 从URL提取house_id
            house_id = url.split("/")[-2] if url else None
            if not house_id:
                return None

            title = link_elem.get_text(strip=True)

            # 价格信息
            price_elem = item.find("span", class_="totalPrice")
            total_price_text = price_elem.get_text(strip=True) if price_elem else "0"
            total_price = float(total_price_text.replace("万", "").strip() or 0)

            unit_price_elem = item.find("span", class_="unitPrice")
            unit_price_text = unit_price_elem.get_text(strip=True) if unit_price_elem else "0"
            unit_price = float(unit_price_text.replace("元/㎡", "").strip() or 0)

            # 房源信息
            info_text = item.find("div", class_="positionInfo").get_text(strip=True) if item.find("div", class_="positionInfo") else ""
            info_parts = [p.strip() for p in info_text.split("|")]

            # 解析房型、面积、楼层等
            rooms = None
            area = None
            floor = None
            total_floors = None
            orientation = None
            community_name = None

            if len(info_parts) > 0:
                # 第一部分通常是房型 (如 "3室2厅1卫")
                layout = info_parts[0]
                # 提取居室数
                if "室" in layout:
                    rooms_str = layout.split("室")[0]
                    try:
                        rooms = int(rooms_str)
                    except ValueError:
                        pass

            if len(info_parts) > 1:
                # 第二部分通常是面积 (如 "120㎡")
                area_str = info_parts[1].replace("㎡", "").strip()
                try:
                    area = float(area_str)
                except ValueError:
                    pass

            if len(info_parts) > 2:
                # 第三部分通常是楼层 (如 "5/18层")
                floor_str = info_parts[2]
                if "/" in floor_str:
                    try:
                        floor_parts = floor_str.replace("层", "").split("/")
                        floor = int(floor_parts[0])
                        total_floors = int(floor_parts[1])
                    except (ValueError, IndexError):
                        pass

            if len(info_parts) > 3:
                # 第四部分通常是朝向 (如 "南北")
                orientation = info_parts[3]

            # 小区名称
            community_elem = item.find("a", class_="communityName")
            community_name = community_elem.get_text(strip=True) if community_elem else None

            # 图片
            img_elem = item.find("img", class_="lj-lazy")
            image_url = img_elem.get("data-src", "") if img_elem else None

            return {
                "house_id": house_id,
                "title": title,
                "total_price": total_price,
                "unit_price": unit_price,
                "area": area,
                "rooms": rooms,
                "floor": floor,
                "total_floors": total_floors,
                "orientation": orientation,
                "community_name": community_name,
                "region_name": region_name,
                "url": url,
                "image_url": image_url,
                "source": "beike",
            }

        except Exception as e:
            logger.debug(f"Error parsing house item: {e}")
            return None

    @staticmethod
    async def crawl_all_regions(regions: List[str], max_pages: int = 3) -> List[Dict[str, Any]]:
        """
        并发爬取多个区域

        Args:
            regions: 区域名称列表
            max_pages: 每个区域最多爬取的页数

        Returns:
            所有房源列表
        """
        all_houses = []

        for region in regions:
            logger.info(f"Starting to crawl region: {region}")
            for page in range(1, max_pages + 1):
                houses = await BeikeCrawler.crawl_region(region, page)
                all_houses.extend(houses)

                if not houses:
                    # 如果某页没有房源，说明已到最后一页
                    break

        logger.info(f"Total crawled {len(all_houses)} houses from {len(regions)} regions")
        return all_houses
