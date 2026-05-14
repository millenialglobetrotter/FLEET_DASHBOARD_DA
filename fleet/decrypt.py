import ast
import json
from typing import Any

import requests


def _parse_payload(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(value)
            except (SyntaxError, ValueError):
                return value
    return value


def decrypt_prediction_data(config: dict[str, Any], encrypted_data: str, timeout: int = 10) -> Any:
    decrypt_config = config.get("decryption_service", {})
    base_url = decrypt_config.get("base_url")
    endpoint = decrypt_config.get("endpoint")
    client_id = decrypt_config.get("client_id")
    client_secret = decrypt_config.get("client_secret")

    if not all([base_url, endpoint, client_id, client_secret]):
        raise ValueError("Missing decryption service configuration fields.")

    decrypt_url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    payload = {
        "clientId": client_id,
        "clientSecret": client_secret,
        "data": encrypted_data,
    }

    response = requests.post(decrypt_url, json=payload, timeout=timeout)
    response.raise_for_status()

    raw = response.json()
    if isinstance(raw, dict):
        candidate = raw.get("Data") or raw.get("data") or raw
    else:
        candidate = raw

    return _parse_payload(candidate)
