from typing import Any

import requests


def fetch_prediction(
    config: dict[str, Any],
    token: str,
    vehicle_id: str,
    from_date: str,
    to_date: str,
    timeout: int = 15,
) -> requests.Response:
    prediction_config = config.get("prediction_service", {})
    base_url = prediction_config.get("base_url")
    url_template = prediction_config.get("url_template")

    if not base_url or not url_template:
        raise ValueError("Missing prediction_service.base_url or prediction_service.url_template")

    path_only = url_template.split("?")[0]
    request_url = f"{base_url.rstrip('/')}/{path_only.lstrip('/')}".format(id=vehicle_id)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "*/*",
    }
    params = {"from": from_date, "to": to_date}

    return requests.get(request_url, headers=headers, params=params, timeout=timeout)
