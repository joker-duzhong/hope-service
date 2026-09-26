"""
具体的业务 APP 配置表
路由注册时由后端绑定 app 标识，客户端不能指定
"""

from typing import Dict, List
from pydantic import BaseModel


class RouterConfig(BaseModel):
    module: str
    prefix: str
    tags: List[str]


class AppConfig(BaseModel):
    key: str  # 应用唯一标识（由后端路由注册绑定）
    name: str  # 应用名称
    is_active: bool = True  # 应用状态
    created_at: str  # 接入时间
    description: str = ""  # 应用描述
    router_modules: List[RouterConfig] = []
    task_modules: List[str] = []
    # AppID 和密钥由 WECHAT_APPS 环境变量维护；部署时可在此列出该业务允许使用的 AppID。
    wechat_appids: List[str] = []


# 手动维护的 APP 列表
REGISTERED_APPS: Dict[str, AppConfig] = {
    # 管理后台入口
    "admin_web": AppConfig(
        key="admin_web",
        name="统一管理后台",
        created_at="2026-04-02",
        description="系统超级管理员与运营人员入口",
    ),
    "hope_just_right": AppConfig(
        key="hope_just_right",
        name="Hope 恰好APP",
        created_at="2026-04-05",
        description="情侣互动应用，提供备忘录、愿望清单、纪念日等功能",
        router_modules=[RouterConfig(module="apps.just_right.router", prefix="/just-right", tags=["恰好"])],
        task_modules=["apps.just_right.tasks"],
    ),
    "hope_aurakey": AppConfig(
        key="hope_aurakey",
        name="Hope aurakey",
        created_at="2026-05-12",
        router_modules=[RouterConfig(module="apps.aurakey.router", prefix="/aurakey", tags=["AuraKey AI 绘画"])],
        task_modules=["apps.aurakey.tasks"],
    ),
    "hope_teacher_logbook": AppConfig(
        key="hope_teacher_logbook",
        name="Hope 班主任工作台",
        created_at="2026-08-27",
        router_modules=[RouterConfig(module="apps.teacher_logbook.router", prefix="/teacher-logbook", tags=["班主任工作台"])],
    ),
    "hope_ledger_mate": AppConfig(
        key="hope_ledger_mate",
        name="Hope 账伴",
        created_at="2026-08-27",
        router_modules=[RouterConfig(module="apps.ledger_mate.router", prefix="/ledger-mate", tags=["账伴"])],
    ),

    # 下面的应用暂时下线，后续可能会重新上线
    "hope_nest_talk": AppConfig(
        key="hope_nest_talk",
        name="Hope 语筑APP",
        created_at="2026-04-02",
        description="语筑APP，提供房源监听、房源分析、AI智能对话等功能",
        router_modules=[RouterConfig(module="apps.nest_talk.router", prefix="/nest-talk", tags=["语筑"])],
        task_modules=["apps.nest_talk.tasks"],
        is_active=False  # 暂时下线，后续可能会重新上线
    ),
    "hope_trade_copilot": AppConfig(
        key="hope_trade_copilot",
        name="Hope Trade 产线APP",
        created_at="2026-04-02",
        description="交易及分析助手应用",
        router_modules=[RouterConfig(module="apps.trade_copilot.router", prefix="/trade-copilot", tags=["交易助手"])],
        task_modules=["apps.trade_copilot.tasks"],
        is_active=False  # 暂时下线，后续可能会重新上线
    ),
    "hope_time_library": AppConfig(
        key="hope_time_library",
        name="Hope 时间图书馆APP",
        created_at="2026-04-09",
        description="Travel Through Time. Read the World. 时间图书馆，提供历史事件查询、名人传记、文化百科等功能",
        router_modules=[
            RouterConfig(module="apps.time_library.router", prefix="/time-library", tags=["时空图书馆"]),
            RouterConfig(module="apps.time_library.admin_router", prefix="/time-library/admin", tags=["时空图书馆-管理端"]),
        ],
        is_active=False  # 暂时下线，后续可能会重新上线
    ),
    "hope_sisyphus": AppConfig(
        key="hope_sisyphus",
        name="Hope 西西弗斯认知引擎",
        created_at="2026-04-15",
        description="基于合意困难与渐隐式支架的生成式学习引擎",
        router_modules=[RouterConfig(module="apps.project_sisyphus.router", prefix="/sisyphus", tags=["西西弗斯认知引擎"])],
        task_modules=["apps.project_sisyphus.tasks"],
        is_active=False  # 暂时下线，后续可能会重新上线
    ),
    "hope_ai_gateway": AppConfig(
        key="hope_ai_gateway",
        name="Hope AI 对话网关",
        created_at="2026-08-27",
        router_modules=[RouterConfig(module="apps.ai_gateway.router", prefix="/ai", tags=["AI对话网关"])],
        is_active=False  # 暂时下线，后续可能会重新上线
    ),
    "hope_zaiwen_gaokao": AppConfig(
        key="hope_zaiwen_gaokao",
        name="Hope 在线高考",
        created_at="2026-08-27",
        router_modules=[RouterConfig(module="apps.zaiwen_gaokao.router", prefix="/zaiwen-gaokao", tags=["在线高考"])],
        task_modules=["apps.zaiwen_gaokao.tasks"],
        is_active=False  # 暂时下线，后续可能会重新上线
    ),
    "hope_shadow_board": AppConfig(
        key="hope_shadow_board",
        name="Hope 影子董事会",
        created_at="2026-08-27",
        router_modules=[RouterConfig(module="apps.shadow_board.router", prefix="/shadow-board", tags=["影子董事会"])],
        task_modules=["apps.shadow_board.tasks"],
        is_active=False  # 暂时下线，后续可能会重新上线
    ),
    "hope_typo_craft": AppConfig(
        key="hope_typo_craft",
        name="Hope 言图引擎",
        created_at="2026-08-27",
        router_modules=[RouterConfig(module="apps.typo_craft.router", prefix="/typo-craft", tags=["言图引擎"])],
        task_modules=["apps.typo_craft.tasks"],
        is_active=False  # 暂时下线，后续可能会重新上线
    ),
}
