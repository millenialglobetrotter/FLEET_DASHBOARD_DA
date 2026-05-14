from datetime import datetime, timezone
from typing import Any

import requests


def _parse_iso(dt_value: str) -> datetime:
    parsed = datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def get_filtered_vehicle_ids(
    config: dict[str, Any],
    token: str,
    make: str = "SML",
    cutoff_date: datetime | None = None,
    timeout: int = 15,
) -> list[str]:
    registry_config = config.get("vehicle_registry", {})
    base_url = registry_config.get("base_url")
    endpoint = registry_config.get("endpoint")

    if not base_url or not endpoint:
        raise ValueError("Missing vehicle_registry.base_url or vehicle_registry.endpoint")

    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}"}
    cutoff = cutoff_date or datetime(2026, 5, 9, tzinfo=timezone.utc)

    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()

    raw_list = response.json().get("data", {}).get("subscriptions", [])
    eligible_ids: list[str] = []

    for item in raw_list:
        vehicle = item.get("vehicle", {})
        vehicle_id = vehicle.get("vehicleId")
        vehicle_make = vehicle.get("make")
        start_str = item.get("subscriptionStartTime")

        if not vehicle_id or vehicle_make != make or not start_str:
            continue

        try:
            start_date = _parse_iso(start_str)
        except ValueError:
            continue

        if start_date > cutoff:
            eligible_ids.append(vehicle_id)

    return eligible_ids
