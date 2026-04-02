"""
具体的业务 APP 配置表
可通过该配置管控请求头中的 app 标识
该标识（key）与角色系统中的 scope 一一对应
"""
from typing import Dict
from pydantic import BaseModel

class AppConfig(BaseModel):
    key: str            # 应用唯一标识（对应前端请求头 app，以及 Role 表中的 scope）
    name: str           # 应用名称
    is_active: bool = True  # 应用状态
    created_at: str     # 接入时间
    description: str = ""   # 应用描述

# 手动维护的 APP 列表
REGISTERED_APPS: Dict[str, AppConfig] = {
    # 管理后台入口
    "admin_web": AppConfig(
        key="admin_web",
        name="统一管理后台",
        created_at="2026-04-02",
        description="系统超级管理员与运营人员入口"
    ),
    # 具体业务APP 1
    "hope_care": AppConfig(
        key="hope_care",
        name="Hope Care 产线APP",
        created_at="2026-04-02",
        description="健康关怀与服务应用"
    ),
    # 具体业务APP 2
    "hope_trade": AppConfig(
        key="hope_trade",
        name="Hope Trade 产线APP",
        created_at="2026-04-02",
        description="交易及分析助手应用"
    ),
}
