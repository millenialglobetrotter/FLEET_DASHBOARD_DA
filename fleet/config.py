import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


ENV_OVERRIDES = {
    "auth.client_id": "FLEET_AUTH_CLIENT_ID",
    "auth.client_secret": "FLEET_AUTH_CLIENT_SECRET",
    "decryption_service.client_id": "FLEET_DECRYPT_CLIENT_ID",
    "decryption_service.client_secret": "FLEET_DECRYPT_CLIENT_SECRET",
}


def _set_nested(config: dict[str, Any], dotted_key: str, value: Any) -> None:
    keys = dotted_key.split(".")
    target = config
    for key in keys[:-1]:
        target = target.setdefault(key, {})
    target[keys[-1]] = value


def load_config(config_path: str = "config.json") -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    merged = deepcopy(config)
    for dotted_key, env_name in ENV_OVERRIDES.items():
        env_value = os.getenv(env_name)
        if env_value:
            _set_nested(merged, dotted_key, env_value)

    return merged
