import calendar
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from azure.storage.blob import ContainerClient

st.set_page_config(
    page_title="Fleet Level Dashboard",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .block-container {
        padding: 3.5rem 1.5rem 0.75rem 1.5rem !important;
        max-width: 100% !important;
    }
    [data-testid="stVerticalBlock"] {
        gap: 0.5rem !important;
    }
    footer {visibility: hidden;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Fleet Level Dashboard")
st.caption("Vehicle count analytics across hourly partitions (IST)")

CACHE_DIR = Path("streamlit_deploy") / "data_cache"


def _secret_or_default(key: str, default_value):
    if key in st.secrets:
        return st.secrets[key]

    # Optional nested secrets support, e.g. [azure] sas_url = "..."
    if "azure" in st.secrets and key.lower() in st.secrets["azure"]:
        return st.secrets["azure"][key.lower()]

    return default_value


def _get_secret_value(section: str, key: str, default_value: str = "") -> str:
    if key in st.secrets:
        return str(st.secrets[key])

    if section in st.secrets and key.lower() in st.secrets[section]:
        return str(st.secrets[section][key.lower()])

    return default_value


def _load_local_config() -> dict:
    candidate_paths = ["config.json", str(Path(__file__).with_name("config.json"))]
    for path in candidate_paths:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def _join_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def _http_json(url: str, method: str = "GET", headers: dict | None = None, payload: dict | None = None, timeout: int = 15) -> dict:
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    req_data = None
    if payload is not None:
        req_data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    request_obj = urlrequest.Request(url=url, data=req_data, headers=req_headers, method=method)
    with urlrequest.urlopen(request_obj, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def _first_non_empty(source: dict, keys: list[str], default_value: str = ""):
    for key in keys:
        value = source.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return default_value


def _extract_registry_records(registry_response: dict) -> list[dict]:
    data = registry_response.get("data", registry_response)

    if isinstance(data, dict):
        if isinstance(data.get("subscriptions"), list):
            return data.get("subscriptions", [])
        if isinstance(data.get("customers"), list):
            records: list[dict] = []
            for customer in data.get("customers", []):
                subs = customer.get("subscriptions", []) if isinstance(customer, dict) else []
                if isinstance(subs, list):
                    records.extend(subs)
            return records
        if isinstance(data.get("vehicles"), list):
            return data.get("vehicles", [])

    if isinstance(data, list):
        return data

    return []


def fetch_onboarded_vehicle_summary(make_filter: str = "SML") -> dict:
    cfg = _load_local_config()
    auth_cfg = cfg.get("auth", {})
    registry_cfg = cfg.get("vehicle_registry", {})

    auth_base_url = _get_secret_value("auth", "AUTH_BASE_URL", auth_cfg.get("base_url", ""))
    auth_endpoint = _get_secret_value("auth", "AUTH_ENDPOINT", auth_cfg.get("endpoint", ""))
    auth_client_id = _get_secret_value("auth", "AUTH_CLIENT_ID", auth_cfg.get("client_id", ""))
    auth_client_secret = _get_secret_value("auth", "AUTH_CLIENT_SECRET", auth_cfg.get("client_secret", ""))

    registry_base_url = _get_secret_value(
        "vehicle_registry", "VEHICLE_REGISTRY_BASE_URL", registry_cfg.get("base_url", "")
    )
    registry_endpoint = _get_secret_value(
        "vehicle_registry", "VEHICLE_REGISTRY_ENDPOINT", registry_cfg.get("endpoint", "")
    )

    if not all([auth_base_url, auth_endpoint, auth_client_id, auth_client_secret, registry_base_url, registry_endpoint]):
        raise ValueError("Missing auth/vehicle registry configuration")

    auth_url = _join_url(auth_base_url, auth_endpoint)
    token_response = _http_json(
        auth_url,
        method="POST",
        payload={"clientId": auth_client_id, "clientSecret": auth_client_secret},
        timeout=15,
    )

    access_token = token_response.get("data", {}).get("accessToken")
    if not access_token:
        raise RuntimeError("Access token missing in auth response")

    registry_url = _join_url(registry_base_url, registry_endpoint)
    registry_response = _http_json(
        registry_url,
        method="GET",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )

    records = _extract_registry_records(registry_response)
    unique_vehicle_map: dict[str, dict] = {}

    for index, item in enumerate(records):
        vehicle = item.get("vehicle", item) if isinstance(item, dict) else {}
        if not isinstance(vehicle, dict):
            continue

        make = str(
            _first_non_empty(vehicle, ["make", "vehicleMake"], _first_non_empty(item, ["make", "vehicleMake"], ""))
        ).strip().upper()
        if make != make_filter.upper():
            continue

        vehicle_id = _first_non_empty(
            vehicle,
            ["vehicleId", "id", "vehicle_id", "registrationNumber", "vehicleNumber"],
            _first_non_empty(item, ["vehicleId", "id", "vehicle_id", "registrationNumber", "vehicleNumber"], ""),
        )
        vehicle_key = str(vehicle_id).strip() if str(vehicle_id).strip() else f"__unknown_{index}"

        model = str(
            _first_non_empty(
                vehicle,
                ["model", "vehicleModel"],
                _first_non_empty(item, ["model", "vehicleModel"], "Unknown"),
            )
        ).strip() or "Unknown"
        variant = str(
            _first_non_empty(
                vehicle,
                ["variant", "vehicleVariant", "variantName", "subModel", "modelVariant"],
                _first_non_empty(item, ["variant", "vehicleVariant", "variantName", "subModel", "modelVariant"], "Unknown"),
            )
        ).strip() or "Unknown"

        unique_vehicle_map[vehicle_key] = {"model": model, "variant": variant}

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

    return {
        "total": len(unique_vehicle_map),
        "model_df": model_df,
        "variant_df": variant_df,
        "vehicle_model_map": vehicle_model_map,
    }


def _safe_cache_key(container_name: str, year: int, month: int) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in container_name.lower()).strip("_")
    if not cleaned:
        cleaned = "default"
    return f"{cleaned}_{year}_{month:02d}"


def _cache_paths(container_name: str, year: int, month: int):
    key = _safe_cache_key(container_name, year, month)
    raw_path = CACHE_DIR / f"raw_{key}.csv"
    processed_path = CACHE_DIR / f"processed_{key}.csv"
    meta_path = CACHE_DIR / f"meta_{key}.json"
    return raw_path, processed_path, meta_path


def load_cached_datasets(container_name: str, year: int, month: int):
    raw_path, processed_path, meta_path = _cache_paths(container_name, year, month)
    raw_df = pd.DataFrame()
    processed_df = pd.DataFrame()
    cached_at = None

    if raw_path.exists():
        raw_df = pd.read_csv(raw_path)
    if processed_path.exists():
        processed_df = pd.read_csv(processed_path)
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as meta_file:
                meta = json.load(meta_file)
                cached_at = meta.get("cached_at")
        except (OSError, json.JSONDecodeError):
            cached_at = None

    return raw_df, processed_df, cached_at


def save_cached_datasets(container_name: str, year: int, month: int, raw_df: pd.DataFrame, processed_df: pd.DataFrame):
    raw_path, processed_path, meta_path = _cache_paths(container_name, year, month)
    os.makedirs(CACHE_DIR, exist_ok=True)

    raw_df.to_csv(raw_path, index=False)
    processed_df.to_csv(processed_path, index=False)

    with open(meta_path, "w", encoding="utf-8") as meta_file:
        json.dump({"cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, meta_file)


def is_cache_stale(cached_at: str, max_age_minutes: int = 15) -> bool:
    if not cached_at:
        return True
    try:
        cached_time = datetime.strptime(cached_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return datetime.now() - cached_time >= timedelta(minutes=max_age_minutes)


def count_processed_for_day(container_client: ContainerClient, year: int, month: int, day: int, end_hour: int) -> dict:
    unique_partitions = set()

    for hour in range(end_hour + 1):
        hour_path = f"result-data/{year}/{month:02d}/{day:02d}/{hour:02d}/"
        for blob in container_client.list_blobs(name_starts_with=hour_path):
            suffix = blob.name[len(hour_path):]
            if "/" in suffix:
                unique_partitions.add(suffix.split("/", 1)[0])

    return {"day": day, "processed_count": len(unique_partitions)}


def fetch_recent_processed_days(
    sas_url: str, container_name: str, year: int, month: int, lookback_hours: int = 24
) -> pd.DataFrame:
    if not sas_url or not container_name:
        return pd.DataFrame()

    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    if (year, month) != (now.year, now.month):
        return pd.DataFrame()

    start = now - timedelta(hours=max(lookback_hours - 1, 0))
    affected_days = sorted({t.day for t in [start, now] if t.year == year and t.month == month})
    if start.day != now.day and start.year == year and start.month == month and now.year == year and now.month == month:
        affected_days = list(range(start.day, now.day + 1))

    container_client = ContainerClient.from_container_url(sas_url)
    rows = []
    for day in affected_days:
        end_hour = now.hour if day == now.day else 23
        rows.append(count_processed_for_day(container_client, year, month, day, end_hour))

    return pd.DataFrame(rows)


def merge_daily_data(existing: pd.DataFrame, updates: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return updates.copy()
    if updates.empty:
        return existing.copy()

    key = ["day"]
    base = existing.set_index(key).copy()
    upd = updates.set_index(key)

    new_idx = upd.index.difference(base.index)
    base.update(upd)
    if len(new_idx) > 0:
        base = pd.concat([base, upd.loc[new_idx]])

    return base.reset_index().sort_values(key).reset_index(drop=True)


def _recent_days_for_lookback(year: int, month: int, lookback_hours: int = 24) -> list[int]:
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    if (year, month) != (now.year, now.month):
        return []

    start = now - timedelta(hours=max(lookback_hours - 1, 0))
    return sorted({t.day for t in [start, now] if t.year == year and t.month == month})


def fetch_onboarded_model_presence_for_days(
    sas_url: str,
    container_name: str,
    year: int,
    month: int,
    vehicle_model_map: dict[str, str],
    days: list[int],
) -> pd.DataFrame:
    if not sas_url or not container_name or not vehicle_model_map or not days:
        return pd.DataFrame(columns=["day", "model", "count"])

    container_client = ContainerClient.from_container_url(sas_url)
    now = datetime.now()
    rows = []

    for day in sorted(set(days)):
        if day < 1:
            continue
        end_hour = now.hour if (year == now.year and month == now.month and day == now.day) else 23

        # Count each onboarded vehicle only once per day even if present in multiple hours.
        seen_vehicle_ids = set()
        for hour in range(end_hour + 1):
            hour_path = f"raw-data/{year}/{month:02d}/{day:02d}/{hour:02d}/"
            for blob in container_client.list_blobs(name_starts_with=hour_path):
                suffix = blob.name[len(hour_path):]
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


@st.cache_data(show_spinner=False)
def fetch_onboarded_model_presence_for_month(
    sas_url: str,
    container_name: str,
    year: int,
    month: int,
    vehicle_model_map: dict[str, str],
) -> pd.DataFrame:
    if not sas_url or not container_name or not vehicle_model_map:
        return pd.DataFrame(columns=["day", "model", "count"])

    now = datetime.now()
    if (year, month) > (now.year, now.month):
        return pd.DataFrame(columns=["day", "model", "count"])

    _, num_days = calendar.monthrange(year, month)
    last_day = now.day if (year == now.year and month == now.month) else num_days
    all_days = list(range(1, last_day + 1))

    return fetch_onboarded_model_presence_for_days(
        sas_url,
        container_name,
        year,
        month,
        vehicle_model_map,
        all_days,
    )


def merge_model_daily_data(existing: pd.DataFrame, updates: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return updates.copy()
    if updates.empty:
        return existing.copy()

    keys = ["day", "model"]
    base = existing.set_index(keys).copy()
    upd = updates.set_index(keys)

    new_idx = upd.index.difference(base.index)
    base.update(upd)
    if len(new_idx) > 0:
        base = pd.concat([base, upd.loc[new_idx]])

    return base.reset_index().sort_values(keys).reset_index(drop=True)


with st.sidebar:
    st.header("Settings")

    default_sas = _secret_or_default("SAS_URL", "")
    default_container = _secret_or_default("CONTAINER_NAME", "")
    default_year = int(_secret_or_default("DEFAULT_YEAR", datetime.now().year))
    default_month = int(_secret_or_default("DEFAULT_MONTH", datetime.now().month))

    if "sas_url_input" not in st.session_state:
        st.session_state["sas_url_input"] = default_sas
    if "container_name_input" not in st.session_state:
        st.session_state["container_name_input"] = default_container
    if "year_input" not in st.session_state:
        st.session_state["year_input"] = default_year
    if "month_input" not in st.session_state:
        st.session_state["month_input"] = default_month

    sas_url = st.text_input("SAS URL", key="sas_url_input", help="Container SAS URL")
    container_name = st.text_input("Container Name", key="container_name_input")

    c1, c2 = st.columns(2)
    with c1:
        year = st.number_input("Year", key="year_input", min_value=2020, max_value=2035)
    with c2:
        month = st.number_input("Month", key="month_input", min_value=1, max_value=12)

    st.divider()
    st.caption("Shared cache is reused across users. Refresh updates recent data and saves for everyone.")
    if not default_sas or not default_container:
        st.caption("Tip: Set SAS_URL and CONTAINER_NAME in Streamlit secrets for permanent prefill.")


@st.cache_data(show_spinner=False)
def count_vehicles_per_hour_for_month(sas_url: str, container_name: str, year: int, month: int) -> pd.DataFrame:
    rows = []

    if not sas_url or not container_name:
        return pd.DataFrame()

    container_client = ContainerClient.from_container_url(sas_url)
    now = datetime.now()

    if (year, month) > (now.year, now.month):
        return pd.DataFrame()

    _, num_days = calendar.monthrange(year, month)
    last_day = now.day if (year == now.year and month == now.month) else num_days

    total_hours = sum(
        (now.hour + 1) if (year == now.year and month == now.month and day == now.day) else 24
        for day in range(1, last_day + 1)
    )
    processed = 0
    progress = st.progress(0.0)

    for day in range(1, last_day + 1):
        end_hour = now.hour if (year == now.year and month == now.month and day == now.day) else 23
        for hour in range(end_hour + 1):
            hour_path = f"raw-data/{year}/{month:02d}/{day:02d}/{hour:02d}/"
            vehicles = set()

            for blob in container_client.list_blobs(name_starts_with=hour_path):
                suffix = blob.name[len(hour_path):]
                if "/" in suffix:
                    vehicles.add(suffix.split("/", 1)[0])

            rows.append({"day": day, "hour": hour, "vehicle_count": len(vehicles)})
            processed += 1
            progress.progress(min(processed / max(total_hours, 1), 1.0))

    progress.empty()
    return pd.DataFrame(rows)


def count_vehicles_for_hour(container_client: ContainerClient, year: int, month: int, day: int, hour: int) -> dict:
    hour_path = f"raw-data/{year}/{month:02d}/{day:02d}/{hour:02d}/"
    vehicles = set()

    for blob in container_client.list_blobs(name_starts_with=hour_path):
        suffix = blob.name[len(hour_path):]
        if "/" in suffix:
            vehicles.add(suffix.split("/", 1)[0])

    return {"day": day, "hour": hour, "vehicle_count": len(vehicles)}


def fetch_recent_hours(sas_url: str, container_name: str, year: int, month: int, lookback_hours: int = 24) -> pd.DataFrame:
    if not sas_url or not container_name:
        return pd.DataFrame()

    now = datetime.now().replace(minute=0, second=0, microsecond=0)

    # For non-current months, incremental refresh does not add value.
    if (year, month) != (now.year, now.month):
        return pd.DataFrame()

    start = now - timedelta(hours=max(lookback_hours - 1, 0))
    container_client = ContainerClient.from_container_url(sas_url)

    rows = []
    t = start
    while t <= now:
        if t.year == year and t.month == month:
            rows.append(count_vehicles_for_hour(container_client, year, month, t.day, t.hour))
        t += timedelta(hours=1)

    return pd.DataFrame(rows)


def merge_hourly_data(existing: pd.DataFrame, updates: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return updates.copy()
    if updates.empty:
        return existing.copy()

    keys = ["day", "hour"]
    base = existing.set_index(keys).copy()
    upd = updates.set_index(keys)

    new_idx = upd.index.difference(base.index)
    base.update(upd)
    if len(new_idx) > 0:
        base = pd.concat([base, upd.loc[new_idx]])

    merged = base.reset_index().sort_values(keys).reset_index(drop=True)
    return merged


@st.cache_data(show_spinner=False)
def count_processed_vehicles_per_day(sas_url: str, container_name: str, year: int, month: int) -> pd.DataFrame:
    """Fetch unique sub-partition count from result-data path per day (aggregated across hours)."""
    rows = []

    if not sas_url or not container_name:
        return pd.DataFrame()

    container_client = ContainerClient.from_container_url(sas_url)
    now = datetime.now()

    if (year, month) > (now.year, now.month):
        return pd.DataFrame()

    _, num_days = calendar.monthrange(year, month)
    last_day = now.day if (year == now.year and month == now.month) else num_days

    total_hours = sum(
        (now.hour + 1) if (year == now.year and month == now.month and day == now.day) else 24
        for day in range(1, last_day + 1)
    )
    processed = 0
    progress = st.progress(0.0)

    for day in range(1, last_day + 1):
        unique_partitions = set()
        end_hour = now.hour if (year == now.year and month == now.month and day == now.day) else 23
        
        for hour in range(end_hour + 1):
            hour_path = f"result-data/{year}/{month:02d}/{day:02d}/{hour:02d}/"
            
            for blob in container_client.list_blobs(name_starts_with=hour_path):
                suffix = blob.name[len(hour_path):]
                if "/" in suffix:
                    unique_partitions.add(suffix.split("/", 1)[0])
            
            processed += 1
            progress.progress(min(processed / max(total_hours, 1), 1.0))

        rows.append({"day": day, "processed_count": len(unique_partitions)})

    progress.empty()
    return pd.DataFrame(rows)


current_key = (sas_url, container_name, int(year), int(month))
stored_key = st.session_state.get("dataset_key")

if stored_key != current_key or "df_results" not in st.session_state:
    with st.spinner("Loading shared cache..."):
        try:
            cached_raw, cached_processed, cached_at = load_cached_datasets(container_name, int(year), int(month))
            if not cached_raw.empty and not cached_processed.empty:
                st.session_state["df_results"] = cached_raw
                st.session_state["df_processed"] = cached_processed
                st.session_state["cache_loaded_at"] = cached_at

                now = datetime.now()
                # If cache is old for current month, one viewer updates and saves for everyone.
                if (int(year), int(month)) == (now.year, now.month) and is_cache_stale(cached_at, max_age_minutes=15):
                    recent_df = fetch_recent_hours(sas_url, container_name, int(year), int(month), lookback_hours=24)
                    recent_processed = fetch_recent_processed_days(
                        sas_url, container_name, int(year), int(month), lookback_hours=24
                    )
                    st.session_state["df_results"] = merge_hourly_data(st.session_state["df_results"], recent_df)
                    st.session_state["df_processed"] = merge_daily_data(
                        st.session_state["df_processed"], recent_processed
                    )
                    save_cached_datasets(
                        container_name,
                        int(year),
                        int(month),
                        st.session_state["df_results"],
                        st.session_state["df_processed"],
                    )
                    st.session_state["cache_loaded_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            else:
                st.session_state["df_results"] = count_vehicles_per_hour_for_month(
                    sas_url, container_name, int(year), int(month)
                )
                st.session_state["df_processed"] = count_processed_vehicles_per_day(
                    sas_url, container_name, int(year), int(month)
                )
                save_cached_datasets(
                    container_name,
                    int(year),
                    int(month),
                    st.session_state["df_results"],
                    st.session_state["df_processed"],
                )
                st.session_state["cache_loaded_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            st.session_state["dataset_key"] = current_key
        except Exception as exc:
            st.error(f"Unable to load data: {exc}")
            st.stop()

if "total_vehicles_onboarded" not in st.session_state:
    try:
        onboarded_summary = fetch_onboarded_vehicle_summary(make_filter="SML")
        st.session_state["total_vehicles_onboarded"] = onboarded_summary["total"]
        st.session_state["onboarded_model_counts"] = onboarded_summary["model_df"]
        st.session_state["onboarded_variant_counts"] = onboarded_summary["variant_df"]
        st.session_state["onboarded_vehicle_model_map"] = onboarded_summary.get("vehicle_model_map", {})
        st.session_state["onboarded_presence_df"] = fetch_onboarded_model_presence_for_month(
            sas_url,
            container_name,
            int(year),
            int(month),
            st.session_state["onboarded_vehicle_model_map"],
        )
        st.session_state["onboarded_last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.session_state.pop("onboarded_error", None)
    except (ValueError, RuntimeError, urlerror.URLError, urlerror.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        st.session_state["onboarded_error"] = str(exc)

# Auto-refresh onboarding drill-down with recent data once per month/session,
# so newly arrived day data appears even before manual refresh.
onboarded_auto_refresh_key = f"{int(year)}-{int(month):02d}"
if st.session_state.get("onboarded_presence_bootstrap_key") != onboarded_auto_refresh_key:
    try:
        recent_days = _recent_days_for_lookback(int(year), int(month), lookback_hours=24)
        presence_updates = fetch_onboarded_model_presence_for_days(
            sas_url,
            container_name,
            int(year),
            int(month),
            st.session_state.get("onboarded_vehicle_model_map", {}),
            recent_days,
        )
        existing_presence = st.session_state.get("onboarded_presence_df", pd.DataFrame())
        st.session_state["onboarded_presence_df"] = merge_model_daily_data(existing_presence, presence_updates)
        st.session_state["onboarded_last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.session_state["onboarded_presence_bootstrap_key"] = onboarded_auto_refresh_key
    except (ValueError, RuntimeError, urlerror.URLError, urlerror.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        st.session_state["onboarded_error"] = str(exc)

if st.button("Refresh Data", use_container_width=False):
    with st.spinner("Refreshing recent hours..."):
        try:
            recent_df = fetch_recent_hours(sas_url, container_name, int(year), int(month), lookback_hours=24)
            recent_processed = fetch_recent_processed_days(
                sas_url, container_name, int(year), int(month), lookback_hours=24
            )
            st.session_state["df_results"] = merge_hourly_data(st.session_state["df_results"], recent_df)
            st.session_state["df_processed"] = merge_daily_data(st.session_state["df_processed"], recent_processed)
            save_cached_datasets(
                container_name,
                int(year),
                int(month),
                st.session_state["df_results"],
                st.session_state["df_processed"],
            )
            st.session_state["cache_loaded_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            st.session_state["last_refresh"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            onboarded_summary = fetch_onboarded_vehicle_summary(make_filter="SML")
            st.session_state["total_vehicles_onboarded"] = onboarded_summary["total"]
            st.session_state["onboarded_model_counts"] = onboarded_summary["model_df"]
            st.session_state["onboarded_variant_counts"] = onboarded_summary["variant_df"]
            st.session_state["onboarded_vehicle_model_map"] = onboarded_summary.get("vehicle_model_map", {})
            recent_days = _recent_days_for_lookback(int(year), int(month), lookback_hours=24)
            presence_updates = fetch_onboarded_model_presence_for_days(
                sas_url,
                container_name,
                int(year),
                int(month),
                st.session_state["onboarded_vehicle_model_map"],
                recent_days,
            )
            existing_presence = st.session_state.get("onboarded_presence_df", pd.DataFrame())
            st.session_state["onboarded_presence_df"] = merge_model_daily_data(existing_presence, presence_updates)
            st.session_state["onboarded_last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            st.session_state.pop("onboarded_error", None)
        except (ValueError, RuntimeError, urlerror.URLError, urlerror.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            st.error(f"Unable to refresh recent hours: {exc}")
            st.session_state["onboarded_error"] = str(exc)

if "last_refresh" in st.session_state:
    st.caption(f"Last refresh: {st.session_state['last_refresh']} (last 24 hours)")
if "cache_loaded_at" in st.session_state:
    st.caption(f"Shared cache updated at: {st.session_state['cache_loaded_at']}")

info_col, metric_col = st.columns([0.65, 0.35])
with info_col:
    if "onboarded_last_updated" in st.session_state:
        st.caption(f"Onboarded count last updated at: {st.session_state['onboarded_last_updated']}")
    if "onboarded_error" in st.session_state:
        st.caption(f"Onboarded count error: {st.session_state['onboarded_error']}")
with metric_col:
    total_onboarded = st.session_state.get("total_vehicles_onboarded", "N/A")
    st.markdown(
        f"""
        <div style="text-align: right; background: #f2faf5; border: 1px solid #b8e0c9; border-radius: 10px; padding: 0.75rem 1rem;">
            <div style="font-size: 0.95rem; font-weight: 700; color: #1f6f52;">Total vehicles onboarded</div>
            <div style="font-size: 2rem; font-weight: 800; color: #0f5132; line-height: 1.1;">{total_onboarded}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

df_results = st.session_state["df_results"]

if df_results.empty:
    st.warning("No data available. Check credentials, month/year, and path format.")
    st.stop()

df_results_ist = df_results.copy()
df_results_ist["ist_hour"] = ((df_results_ist["hour"] + 5.5) % 24).astype(int)
df_results_ist["ist_day"] = df_results_ist["day"] + ((df_results_ist["hour"] + 5.5) // 24).astype(int)

available_days = sorted(df_results_ist["ist_day"].unique())

# Initialize active tab in session state
if "active_tab" not in st.session_state:
    st.session_state["active_tab"] = 0

# Tab selector using buttons
tab_cols = st.columns(4)
with tab_cols[0]:
    if st.button("📊 Daily Drill-down", use_container_width=True, key="tab_drill_down"):
        st.session_state["active_tab"] = 0
with tab_cols[1]:
    if st.button("🔥 Heatmap", use_container_width=True, key="tab_heatmap"):
        st.session_state["active_tab"] = 1
with tab_cols[2]:
    if st.button("✅ Vehicles Processed", use_container_width=True, key="tab_processed"):
        st.session_state["active_tab"] = 2
with tab_cols[3]:
    if st.button("🧭 Onboarded Drill-down", use_container_width=True, key="tab_onboarded"):
        st.session_state["active_tab"] = 3

st.divider()

# Tab 0: Daily Drill-down
if st.session_state["active_tab"] == 0:
    l, m, r = st.columns([0.15, 0.6, 0.25])
    with l:
        st.markdown("**Day**")
    with m:
        selected_day = st.selectbox(
            "Select Day (IST)",
            options=available_days,
            format_func=lambda x: f"Day {int(x)}",
            label_visibility="collapsed",
            key="selected_day_input",
        )

    day_data = df_results_ist[df_results_ist["ist_day"] == selected_day].copy()
    if day_data.empty:
        st.info(f"No data for Day {int(selected_day)}")
    else:
        hourly_data = day_data.groupby("hour", as_index=False)["vehicle_count"].sum()
        hourly_data["ist_hour"] = ((hourly_data["hour"] + 5.5) % 24).astype(int)
        hourly_data = hourly_data.sort_values("ist_hour")

        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=hourly_data["ist_hour"],
                y=hourly_data["vehicle_count"],
                marker={"color": "steelblue"},
                text=hourly_data["vehicle_count"],
                textposition="outside",
                hovertemplate="<b>Hour:</b> %{x}:00 IST<br><b>Vehicles:</b> %{y}<extra></extra>",
            )
        )
        fig.update_layout(
            title=f"Vehicle Count by Hour - Day {int(selected_day)}, {int(year)}-{int(month):02d} (IST)",
            xaxis_title="Hour of Day (IST)",
            yaxis_title="Vehicle Count",
            template="plotly_white",
            autosize=True,
            showlegend=False,
            xaxis={"tickmode": "linear", "tick0": 0, "dtick": 1},
            margin={"l": 50, "r": 40, "t": 50, "b": 50},
        )
        st.plotly_chart(fig, use_container_width=True)

# Tab 1: Heatmap
if st.session_state["active_tab"] == 1:
    pivot = df_results_ist.pivot_table(
        index="ist_day",
        columns="ist_hour",
        values="vehicle_count",
        fill_value=0,
        aggfunc="sum",
    )
    for hour in range(24):
        if hour not in pivot.columns:
            pivot[hour] = 0
    pivot = pivot[list(range(24))]

    fig_heat = go.Figure(
        go.Heatmap(
            z=pivot.values,
            x=[f"{h:02d}:00" for h in range(24)],
            y=[f"Day {int(d)}" for d in pivot.index],
            colorscale="YlOrRd",
            text=pivot.values,
            texttemplate="%{text}",
            hovertemplate="<b>Day:</b> %{y}<br><b>Hour:</b> %{x} IST<br><b>Vehicles:</b> %{z}<extra></extra>",
            colorbar={"title": "Vehicles"},
        )
    )
    fig_heat.update_layout(
        title=f"Vehicle Count Heatmap - {int(year)}-{int(month):02d} (IST)",
        xaxis_title="Hour of Day (IST)",
        yaxis_title="Day",
        template="plotly_white",
        autosize=True,
        margin={"l": 80, "r": 80, "t": 50, "b": 50},
        yaxis={"autorange": "reversed"},
    )
    st.plotly_chart(fig_heat, use_container_width=True)

# Tab 2: Vehicles Processed
if st.session_state["active_tab"] == 2:
    df_processed = st.session_state.get("df_processed", pd.DataFrame())
    
    if df_processed.empty:
        st.info("No processed data available. Check credentials and path format.")
    else:
        df_processed_sorted = df_processed.sort_values("day")
        
        fig_processed = go.Figure()
        fig_processed.add_trace(
            go.Bar(
                x=df_processed_sorted["day"],
                y=df_processed_sorted["processed_count"],
                marker={"color": "seagreen"},
                text=df_processed_sorted["processed_count"],
                textposition="outside",
                hovertemplate="<b>Day:</b> %{x}<br><b>Vehicles Processed:</b> %{y}<extra></extra>",
            )
        )
        fig_processed.update_layout(
            title=f"Vehicles Processed Per Day - {int(year)}-{int(month):02d}",
            xaxis_title="Day",
            yaxis_title="Unique Folders Count",
            template="plotly_white",
            autosize=True,
            showlegend=False,
            xaxis={"tickmode": "linear", "tick0": 1, "dtick": 1},
            margin={"l": 50, "r": 40, "t": 50, "b": 50},
        )
        st.plotly_chart(fig_processed, use_container_width=True)

# Tab 3: Onboarded Drill-down
if st.session_state["active_tab"] == 3:
    model_df = st.session_state.get("onboarded_model_counts", pd.DataFrame())
    variant_df = st.session_state.get("onboarded_variant_counts", pd.DataFrame())
    presence_df = st.session_state.get("onboarded_presence_df", pd.DataFrame())
    total_onboarded = st.session_state.get("total_vehicles_onboarded", 0)
    onboarded_error = st.session_state.get("onboarded_error", "")

    if model_df.empty and variant_df.empty:
        if onboarded_error:
            st.error(f"Unable to fetch onboarded breakdown: {onboarded_error}")
        elif total_onboarded == 0:
            st.info("No onboarded SML vehicles found from vehicle registry response.")
        else:
            st.info("Onboarded vehicles found, but model/variant fields are missing in the registry response.")
    else:
        left_col, right_col = st.columns(2)

        with left_col:
            if model_df.empty:
                st.info("No model-level onboarded data available.")
            else:
                fig_model = go.Figure()
                fig_model.add_trace(
                    go.Bar(
                        x=model_df["count"],
                        y=model_df["model"],
                        orientation="h",
                        marker={"color": "#2f7fdb"},
                        text=model_df["count"],
                        textposition="outside",
                        hovertemplate="<b>Model:</b> %{y}<br><b>Onboarded:</b> %{x}<extra></extra>",
                    )
                )
                fig_model.update_layout(
                    title="Onboarded Vehicles by Model",
                    xaxis_title="Vehicle Count",
                    yaxis_title="Model",
                    template="plotly_white",
                    autosize=True,
                    showlegend=False,
                    margin={"l": 80, "r": 40, "t": 50, "b": 50},
                )
                st.plotly_chart(fig_model, use_container_width=True)

        with right_col:
            if variant_df.empty:
                st.info("No variant-level onboarded data available.")
            else:
                variant_display = variant_df.head(25).copy()
                fig_variant = go.Figure()
                fig_variant.add_trace(
                    go.Bar(
                        x=variant_display["count"],
                        y=variant_display["model_variant"],
                        orientation="h",
                        marker={"color": "#1fa37a"},
                        text=variant_display["count"],
                        textposition="outside",
                        hovertemplate="<b>Model | Variant:</b> %{y}<br><b>Onboarded:</b> %{x}<extra></extra>",
                    )
                )
                fig_variant.update_layout(
                    title="Top 25 Onboarded Model | Variant",
                    xaxis_title="Vehicle Count",
                    yaxis_title="Model | Variant",
                    template="plotly_white",
                    autosize=True,
                    showlegend=False,
                    margin={"l": 80, "r": 40, "t": 50, "b": 50},
                )
                st.plotly_chart(fig_variant, use_container_width=True)

        st.divider()
        st.caption("Daily presence uses raw-data sub-partitions and counts each vehicle ID only once per day.")

        if presence_df.empty:
            st.info("No onboarded vehicle IDs were found in raw-data sub-partitions for the selected month.")
        else:
            available_presence_days = sorted(presence_df["day"].unique())
            selected_presence_day = st.selectbox(
                "Select Day for Onboarded Presence",
                options=available_presence_days,
                format_func=lambda x: f"Day {int(x)}",
                key="onboarded_presence_day_input",
            )

            day_presence = presence_df[presence_df["day"] == selected_presence_day].copy()
            day_presence = day_presence.sort_values("count", ascending=False)

            fig_presence = go.Figure()
            fig_presence.add_trace(
                go.Bar(
                    x=day_presence["model"],
                    y=day_presence["count"],
                    marker={"color": "#0f766e"},
                    text=day_presence["count"],
                    textposition="outside",
                    hovertemplate="<b>Model:</b> %{x}<br><b>Vehicles Present:</b> %{y}<extra></extra>",
                )
            )
            fig_presence.update_layout(
                title=f"Onboarded Vehicle IDs Present in Raw Data - Day {int(selected_presence_day)}",
                xaxis_title="Model",
                yaxis_title="Unique Vehicle IDs Present",
                template="plotly_white",
                autosize=True,
                showlegend=False,
                margin={"l": 50, "r": 40, "t": 50, "b": 80},
            )
            st.plotly_chart(fig_presence, use_container_width=True)
