"""
飞书互动卡片模板构建器
"""
from typing import Dict, Any

def build_trade_alert_card(
    title: str,
    symbol: str, 
    name: str, 
    curr_price: float, 
    ref_price: float, 
    ref_price_label: str,
    desc: str, 
    color: str = "red"
) -> Dict[str, Any]:
    """
    构建飞书交易告警互动卡片
    :param title: 卡片标题
    :param symbol: 股票代码
    :param name: 股票名称
    :param curr_price: 当前价格
    :param ref_price: 参考价格 (成本价/高水位线)
    :param ref_price_label: 参考价格的说明 (例如 "持仓成本", "高水位线")
    :param desc: 警报正文说明
    :param color: 卡片标题颜色 (如 red, orange, blue, green)
    """
    return {
        "config": {
            "wide_screen_mode": True
        },
        "header": {
            "title": {
                "tag": "plain_text",
                "content": title
            },
            "template": color
        },
        "elements": [
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"**📈 交易标的：**\n{name} ({symbol})"
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"**💰 当前价格：**\n{curr_price} 元"
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"**🔖 {ref_price_label}：**\n{ref_price} 元"
                        }
                    }
                ]
            },
            {
                "tag": "hr"
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**🔔 告警说明：**\n{desc}"
                }
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "Trade Copilot 自动生成的盘中监控警报"
                    }
                ]
            }
        ]
    }

def build_market_status_card(
    title: str,
    status_color: str,
    sh_reason: str,
    sz_reason: str
) -> Dict[str, Any]:
    """
    构建飞书盘后结算红绿灯提醒卡片
    """
    color_map = {
        "red": "red",
        "orange": "orange",
        "green": "green"
    }
    
    return {
        "config": {
            "wide_screen_mode": True
        },
        "header": {
            "title": {
                "tag": "plain_text",
                "content": title
            },
            "template": color_map.get(status_color, "blue")
        },
        "elements": [
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": False,
                        "text": {
                            "tag": "lark_md",
                            "content": f"**🔴 上证指数：**\n{sh_reason}"
                        }
                    },
                    {
                        "is_short": False,
                        "text": {
                            "tag": "lark_md",
                            "content": f"**🔵 深证成指：**\n{sz_reason}"
                        }
                    }
                ]
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "Trade Copilot 每日盘后准点播报"
                    }
                ]
            }
        ]
    }

def build_sniper_radar_card(
    symbol: str, 
    name: str, 
    desc: str, 
    reason: str,
    ma_details: str,
    sizing_info: str = ""
) -> Dict[str, Any]:
    """
    构建飞书尾盘买点判定（狙击雷达）互动卡片
    """
    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**🎯 发现目标标的：**\n {name} ({symbol})\n\n**🧐 加入理由：**\n {reason}"      
            }
        },
        {
            "tag": "hr"
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**💡 技术面分析：**\n{ma_details}"
            }
        }
    ]

    if sizing_info:
        elements.extend([
            {
                "tag": "hr"
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**🧮 凯利仓位算盘：**\n{sizing_info}"
                }
            }
        ])

    elements.extend([
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**🔔 决策提示：**\n{desc}"
            }
        },
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "Trade Copilot 尾盘狙击雷达 14:45"
                }
            ]
        }
    ])

    return {
        "config": {
            "wide_screen_mode": True
        },
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "🟢 狙击信号：出现买点！"
            },
            "template": "green"
        },
        "elements": elements
    }
