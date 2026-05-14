import logging
from typing import Any

import requests


AUTH_TOKEN_CACHE: dict[str, str] = {}


def get_access_token(config: dict[str, Any], timeout: int = 10) -> str:
    if "token" in AUTH_TOKEN_CACHE:
        return AUTH_TOKEN_CACHE["token"]

    auth_config = config.get("auth", {})
    base_url = auth_config.get("base_url")
    endpoint = auth_config.get("endpoint")
    client_id = auth_config.get("client_id")
    client_secret = auth_config.get("client_secret")

    if not all([base_url, endpoint, client_id, client_secret]):
        raise ValueError("Missing auth configuration fields in config.")

    auth_url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    payload = {"clientId": client_id, "clientSecret": client_secret}

    logging.info("Fetching new access token")
    response = requests.post(auth_url, json=payload, timeout=timeout)
    response.raise_for_status()

    token_data = response.json().get("data", {})
    access_token = token_data.get("accessToken")
    if not access_token:
        raise RuntimeError("Access token missing in auth response.")

    AUTH_TOKEN_CACHE["token"] = access_token
    return access_token
