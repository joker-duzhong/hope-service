from copy import deepcopy
from typing import Any


AURAKEY_SYSTEM_CONFIG_KEY = "aurakey_system_config"

DEFAULT_AURAKEY_CONFIG: dict[str, Any] = {
    "register_reward_points": 10,
    "daily_sign_in_reward_points": 10,
    "invite_reward_points": 50,
    "default_vip_valid_days": 30,
    "default_point_pack_valid_days": None,
    "daily_free_points_reset_hour": 12,
    "custom": {},
}


def get_default_aurakey_config() -> dict[str, Any]:
    return deepcopy(DEFAULT_AURAKEY_CONFIG)


def merge_aurakey_config(value: dict[str, Any] | None) -> dict[str, Any]:
    config = get_default_aurakey_config()
    if value:
        config.update(value)
    if not isinstance(config.get("custom"), dict):
        config["custom"] = {}
    return config
