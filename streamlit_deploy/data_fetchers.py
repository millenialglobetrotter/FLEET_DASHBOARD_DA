"""Data fetching and processing functions for Azure blob storage operations."""

import calendar
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import streamlit as st
from azure.storage.blob import ContainerClient

from utils import (
    extract_registry_records,
    extract_vehicle_id_from_suffix,
    first_non_empty,
    get_secret_value,
    http_json,
    join_url,
    load_local_config,
    normalize_vehicle_id,
)


def fetch_onboarded_vehicle_summary(make_filter: str = "SML") -> dict:
    """Fetch onboarded vehicle summary from vehicle registry API.
    
    Args:
        make_filter: Vehicle make to filter by (e.g., 'SML')
    
    Returns:
        Dictionary with total count, model/variant dataframes, and mapping dicts
    """
    cfg = load_local_config()
    auth_cfg = cfg.get("auth", {})
    registry_cfg = cfg.get("vehicle_registry", {})

    auth_base_url = get_secret_value("auth", "AUTH_BASE_URL", auth_cfg.get("base_url", ""))
    auth_endpoint = get_secret_value("auth", "AUTH_ENDPOINT", auth_cfg.get("endpoint", ""))
    auth_client_id = get_secret_value("auth", "AUTH_CLIENT_ID", auth_cfg.get("client_id", ""))
    auth_client_secret = get_secret_value("auth", "AUTH_CLIENT_SECRET", auth_cfg.get("client_secret", ""))

    registry_base_url = get_secret_value(
        "vehicle_registry", "VEHICLE_REGISTRY_BASE_URL", registry_cfg.get("base_url", "")
    )
    registry_endpoint = get_secret_value(
        "vehicle_registry", "VEHICLE_REGISTRY_ENDPOINT", registry_cfg.get("endpoint", "")
    )

    if not all([auth_base_url, auth_endpoint, auth_client_id, auth_client_secret, registry_base_url, registry_endpoint]):
        raise ValueError("Missing auth/vehicle registry configuration")

    # Authenticate
    auth_url = join_url(auth_base_url, auth_endpoint)
    token_response = http_json(
        auth_url,
        method="POST",
        payload={"clientId": auth_client_id, "clientSecret": auth_client_secret},
        timeout=15,
    )

    access_token = token_response.get("data", {}).get("accessToken")
    if not access_token:
        raise RuntimeError("Access token missing in auth response")

    # Fetch registry
    registry_url = join_url(registry_base_url, registry_endpoint)
    registry_response = http_json(
        registry_url,
        method="GET",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )

    records = extract_registry_records(registry_response)
    unique_vehicle_map: dict[str, dict] = {}

    for index, item in enumerate(records):
        vehicle = item.get("vehicle", item) if isinstance(item, dict) else {}
        if not isinstance(vehicle, dict):
            continue

        make = str(
            first_non_empty(
                vehicle,
                ["make", "vehicleMake"],
                first_non_empty(item, ["make", "vehicleMake"], ""),
            )
        ).strip().upper()
        if make != make_filter.upper():
            continue

        vehicle_id = first_non_empty(
            vehicle,
            ["vehicleId", "id", "vehicle_id", "registrationNumber", "vehicleNumber"],
            first_non_empty(item, ["vehicleId", "id", "vehicle_id", "registrationNumber", "vehicleNumber"], ""),
        )
        vehicle_key = str(vehicle_id).strip() if str(vehicle_id).strip() else f"__unknown_{index}"

        model = str(
            first_non_empty(
                vehicle,
                ["model", "vehicleModel"],
                first_non_empty(item, ["model", "vehicleModel"], "Unknown"),
            )
        ).strip() or "Unknown"
        variant = str(
            first_non_empty(
                vehicle,
                ["variant", "vehicleVariant", "variantName", "subModel", "modelVariant"],
                first_non_empty(item, ["variant", "vehicleVariant", "variantName", "subModel", "modelVariant"], "Unknown"),
            )
        ).strip() or "Unknown"

        unique_vehicle_map[vehicle_key] = {"model": model, "variant": variant}

    # Aggregate counts
    model_counts: dict[str, int] = {}
    variant_counts: dict[str, int] = {}
    for details in unique_vehicle_map.values():
        model = details["model"]
        variant = details["variant"]
        model_variant = f"{model} | {variant}"
        model_counts[model] = model_counts.get(model, 0) + 1
        variant_counts[model_variant] = variant_counts.get(model_variant, 0) + 1

    model_df = pd.DataFrame(
        [{"model": key, "count": value} for key, value in model_counts.items()]
    ).sort_values("count", ascending=False, ignore_index=True)

    variant_df = pd.DataFrame(
        [{"model_variant": key, "count": value} for key, value in variant_counts.items()]
    ).sort_values("count", ascending=False, ignore_index=True)

    vehicle_model_map = {
        vehicle_id: details["model"]
        for vehicle_id, details in unique_vehicle_map.items()
        if not vehicle_id.startswith("__unknown_")
    }

    vehicle_details_map = {
        vehicle_id: {"model": details["model"], "variant": details["variant"]}
        for vehicle_id, details in unique_vehicle_map.items()
        if not vehicle_id.startswith("__unknown_")
    }

    return {
        "total": len(unique_vehicle_map),
        "model_df": model_df,
        "variant_df": variant_df,
        "vehicle_model_map": vehicle_model_map,
        "vehicle_details_map": vehicle_details_map,
    }


@st.cache_data(show_spinner=False)
def fetch_onboarded_vehicle_hours_for_month(
    sas_url: str,
    container_name: str,
    year: int,
    month: int,
    vehicle_details_map: dict[str, dict[str, str]],
) -> pd.DataFrame:
    """Count hourly raw-data appearances for onboarded vehicle IDs across the selected month.
    
    Args:
        sas_url: Azure container SAS URL
        container_name: Container name
        year: Year to analyze
        month: Month to analyze
        vehicle_details_map: Mapping of vehicle ID to model/variant details
    
    Returns:
        DataFrame with vehicle_id, model, variant, operating_hours, active_days
    """
    empty_df = pd.DataFrame(
        columns=["vehicle_id", "model", "variant", "operating_hours", "active_days"]
    )

    if not sas_url or not container_name or not vehicle_details_map:
        return empty_df

    now = datetime.now()
    if (year, month) > (now.year, now.month):
        return empty_df

    _, num_days = calendar.monthrange(year, month)
    last_day = now.day if (year == now.year and month == now.month) else num_days

    container_client = ContainerClient.from_container_url(sas_url)
    vehicle_day_hours: dict[str, set[tuple[int, int]]] = {}
    
    # Build normalized lookup for flexible vehicle ID matching
    normalized_lookup: dict[str, str] = {
        normalize_vehicle_id(vehicle_id): vehicle_id
        for vehicle_id in vehicle_details_map
    }

    # Scan all hourly partitions
    for day in range(1, last_day + 1):
        end_hour = (
            now.hour if (year == now.year and month == now.month and day == now.day) else 23
        )
        for hour in range(end_hour + 1):
            hour_path = f"raw-data/{year}/{month:02d}/{day:02d}/{hour:02d}/"
            seen_this_hour = set()

            for blob in container_client.list_blobs(name_starts_with=hour_path):
                suffix = blob.name[len(hour_path) :]
                vehicle_id = extract_vehicle_id_from_suffix(suffix)
                
                if not vehicle_id:
                    continue

                # Try exact match first
                if vehicle_id in vehicle_details_map:
                    seen_this_hour.add(vehicle_id)
                    continue

                # Try normalized match
                normalized_id = normalize_vehicle_id(vehicle_id)
                canonical_vehicle_id = normalized_lookup.get(normalized_id)
                if canonical_vehicle_id:
                    seen_this_hour.add(canonical_vehicle_id)

            # Record all vehicles found in this hour
            for vehicle_id in seen_this_hour:
                if vehicle_id not in vehicle_day_hours:
                    vehicle_day_hours[vehicle_id] = set()
                vehicle_day_hours[vehicle_id].add((day, hour))

    # Build result rows
    rows = []
    for vehicle_id, day_hour_pairs in vehicle_day_hours.items():
        details = vehicle_details_map.get(vehicle_id, {})
        model_name = details.get("model", "Unknown") or "Unknown"
        variant_name = details.get("variant", "Unknown") or "Unknown"
        rows.append(
            {
                "vehicle_id": vehicle_id,
                "model": model_name,
                "variant": variant_name,
                "operating_hours": len(day_hour_pairs),
                "active_days": len({day for day, _ in day_hour_pairs}),
            }
        )

    if not rows:
        return empty_df

    return pd.DataFrame(rows).sort_values(
        ["operating_hours", "active_days", "vehicle_id"],
        ascending=[False, False, True],
    ).reset_index(drop=True)


@st.cache_data(show_spinner=False)
def fetch_onboarded_model_presence_for_month(
    sas_url: str,
    container_name: str,
    year: int,
    month: int,
    vehicle_model_map: dict[str, str],
) -> pd.DataFrame:
    """Fetch onboarded vehicle presence by model for the month.
    
    Returns DataFrame with day, model, count columns.
    """
    if not sas_url or not container_name or not vehicle_model_map:
        return pd.DataFrame(columns=["day", "model", "count"])

    now = datetime.now()
    if (year, month) > (now.year, now.month):
        return pd.DataFrame(columns=["day", "model", "count"])

    _, num_days = calendar.monthrange(year, month)
    last_day = now.day if (year == now.year and month == now.month) else num_days
    all_days = list(range(1, last_day + 1))

    return _fetch_model_presence_for_days(
        sas_url,
        container_name,
        year,
        month,
        vehicle_model_map,
        all_days,
    )


@st.cache_data(show_spinner=False)
def _fetch_model_presence_for_days(
    sas_url: str,
    container_name: str,
    year: int,
    month: int,
    vehicle_model_map: dict[str, str],
    days: list[int],
) -> pd.DataFrame:
    """Fetch model presence for specific days.
    
    Counts each vehicle only once per day even if present in multiple hours.
    """
    if not sas_url or not container_name or not vehicle_model_map or not days:
        return pd.DataFrame(columns=["day", "model", "count"])

    container_client = ContainerClient.from_container_url(sas_url)
    now = datetime.now()
    rows = []

    for day in sorted(set(days)):
        if day < 1:
            continue
        end_hour = (
            now.hour if (year == now.year and month == now.month and day == now.day) else 23
        )

        seen_vehicle_ids = set()
        for hour in range(end_hour + 1):
            hour_path = f"raw-data/{year}/{month:02d}/{day:02d}/{hour:02d}/"
            for blob in container_client.list_blobs(name_starts_with=hour_path):
                suffix = blob.name[len(hour_path) :]
                if "/" in suffix:
                    vehicle_id = suffix.split("/", 1)[0]
                    if vehicle_id in vehicle_model_map:
                        seen_vehicle_ids.add(vehicle_id)

        model_counts: dict[str, int] = {}
        for vehicle_id in seen_vehicle_ids:
            model_name = vehicle_model_map.get(vehicle_id, "Unknown")
            model_counts[model_name] = model_counts.get(model_name, 0) + 1

        for model_name, count in model_counts.items():
            rows.append({"day": day, "model": model_name, "count": count})

    if not rows:
        return pd.DataFrame(columns=["day", "model", "count"])

    return pd.DataFrame(rows).sort_values(["day", "model"]).reset_index(drop=True)
