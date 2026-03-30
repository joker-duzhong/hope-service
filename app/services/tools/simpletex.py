"""
SimpleTex 公式识别服务
API文档: https://simpletex.cn/api
"""
import base64
from typing import Optional

import httpx

from app.core.config import settings


class SimpleTexService:
    """SimpleTex 公式识别服务"""

    def __init__(self):
        self.base_url = settings.SIMPLETEX_API_URL
        self.api_token = settings.SIMPLETEX_API_TOKEN

    async def _make_request(
        self,
        endpoint: str,
        data: dict,
        files: Optional[dict] = None,
    ) -> dict:
        """发送请求到SimpleTex API"""
        headers = {"token": self.api_token} if self.api_token else {}

        async with httpx.AsyncClient(timeout=60.0) as client:
            if files:
                response = await client.post(
                    f"{self.base_url}{endpoint}",
                    headers=headers,
                    data=data,
                    files=files,
                )
            else:
                response = await client.post(
                    f"{self.base_url}{endpoint}",
                    headers=headers,
                    json=data,
                )

            response.raise_for_status()
            return response.json()

    async def latex_ocr(
        self,
        image_data: bytes,
        image_format: str = "png",
    ) -> dict:
        """
        图片公式识别

        Args:
            image_data: 图片二进制数据
            image_format: 图片格式 (png, jpg, jpeg)

        Returns:
            识别结果，包含 LaTeX 公式
        """
        files = {
            "file": (f"image.{image_format}", image_data, f"image/{image_format}")
        }
        data = {}

        return await self._make_request("/api/latex_ocr", data, files)

    async def latex_ocr_base64(
        self,
        image_base64: str,
    ) -> dict:
        """
        Base64图片公式识别

        Args:
            image_base64: Base64编码的图片

        Returns:
            识别结果
        """
        # 如果是 data:image/xxx;base64, 格式，需要提取纯base64
        if "base64," in image_base64:
            image_base64 = image_base64.split("base64,")[1]

        data = {"file": image_base64}

        return await self._make_request("/api/latex_ocr", data)

    async def mathpix_ocr(
        self,
        image_data: bytes,
        image_format: str = "png",
    ) -> dict:
        """
        使用Mathpix引擎识别（更精确）

        Args:
            image_data: 图片二进制数据
            image_format: 图片格式

        Returns:
            识别结果
        """
        files = {
            "file": (f"image.{image_format}", image_data, f"image/{image_format}")
        }
        data = {}

        return await self._make_request("/api/mathpix_ocr", data, files)


# 单例
simpletex_service = SimpleTexService()
