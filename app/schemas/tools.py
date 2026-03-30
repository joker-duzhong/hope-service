"""
工具集相关的 Pydantic 模型
"""
from typing import Optional

from pydantic import BaseModel


class FormulaOCRRequest(BaseModel):
    """公式识别请求 - Base64"""
    image_base64: str


class FormulaOCRResponse(BaseModel):
    """公式识别响应"""
    status: bool
    res: Optional[dict] = None
    latex: Optional[str] = None
    error: Optional[str] = None
