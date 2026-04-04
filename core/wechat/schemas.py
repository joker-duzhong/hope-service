from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

class WechatQRPollResponse(BaseModel):
    status: str
    token: Optional[str] = None
    userInfo: Optional[Dict[str, Any]] = None
