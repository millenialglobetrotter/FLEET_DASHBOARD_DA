"""Fleet Level Dashboard - Main application entry point and orchestration."""

import calendar
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
from urllib import error as urlerror

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from azure.storage.blob import ContainerClient

# Import refactored modules
from utils import (
    secret_or_default,
    get_secret_value,
    load_local_config,
    first_non_empty,
    get_secret_keys,
)
from data_fetchers import (
    fetch_onboarded_vehicle_summary,
    fetch_onboarded_vehicle_hours_for_month,
    fetch_onboarded_model_presence_for_month,
    _fetch_model_presence_for_days,
)

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


# ============================================================================
# AUTHENTICATION & CONFIGURATION
# ============================================================================


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


def _gist_credentials() -> tuple[str, str]:
    """Return (gist_id, github_token) from secrets or config, empty strings if not configured."""
    cfg = load_local_config().get("github_gist", {})
    gist_id = get_secret_value("github_gist", "GITHUB_GIST_ID", cfg.get("gist_id", ""))
    token = get_secret_value("github_gist", "GITHUB_GIST_TOKEN", cfg.get("token", ""))
    return gist_id.strip(), token.strip()


def _gist_read_file(gist_id: str, token: str, filename: str) -> str | None:
    """Fetch a single file's content from a GitHub Gist. Returns None on any failure."""
    try:
        response = _http_json(
            f"https://api.github.com/gists/{gist_id}",
            headers={"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"},
            timeout=15,
        )
        file_info = response.get("files", {}).get(filename)
        if not file_info:
            return None
        # For files >1 MB GitHub truncates content and provides raw_url instead.
        if file_info.get("truncated"):
            raw_url = file_info.get("raw_url", "")
            if not raw_url:
                return None
            req = urlrequest.Request(raw_url, headers={"Authorization": f"Bearer {token}"})
            with urlrequest.urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8")
        return file_info.get("content")
    except Exception:
        return None


def _gist_save_files(gist_id: str, token: str, files: dict[str, str]) -> bool:
    """PATCH a GitHub Gist with the given {filename: content} dict. Returns True on success."""
    try:
        payload = {"files": {name: {"content": content} for name, content in files.items()}}
        _http_json(
            f"https://api.github.com/gists/{gist_id}",
            method="PATCH",
            headers={"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"},
            payload=payload,
            timeout=20,
        )
        return True
    except Exception:
        return False


def load_cached_datasets(container_name: str, year: int, month: int):
    key = _safe_cache_key(container_name, year, month)
    raw_df = pd.DataFrame()
    processed_df = pd.DataFrame()
    cached_at = None

    gist_id, gist_token = _gist_credentials()
    if gist_id and gist_token:
        raw_content = _gist_read_file(gist_id, gist_token, f"raw_{key}.csv")
        processed_content = _gist_read_file(gist_id, gist_token, f"processed_{key}.csv")
        meta_content = _gist_read_file(gist_id, gist_token, f"meta_{key}.json")
        if raw_content:
            try:
                raw_df = pd.read_csv(io.StringIO(raw_content))
            except Exception:
                raw_df = pd.DataFrame()
        if processed_content:
            try:
                processed_df = pd.read_csv(io.StringIO(processed_content))
            except Exception:
                processed_df = pd.DataFrame()
        if meta_content:
            try:
                cached_at = json.loads(meta_content).get("cached_at")
            except Exception:
                cached_at = None
        return raw_df, processed_df, cached_at

    # Fallback: local file cache
    raw_path, processed_path, meta_path = _cache_paths(container_name, year, month)
    if raw_path.exists():
        raw_df = pd.read_csv(raw_path)
    if processed_path.exists():
        processed_df = pd.read_csv(processed_path)
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as meta_file:
                cached_at = json.load(meta_file).get("cached_at")
        except (OSError, json.JSONDecodeError):
            cached_at = None
    return raw_df, processed_df, cached_at


def save_cached_datasets(container_name: str, year: int, month: int, raw_df: pd.DataFrame, processed_df: pd.DataFrame):
    key = _safe_cache_key(container_name, year, month)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    gist_id, gist_token = _gist_credentials()
    if gist_id and gist_token:
        _gist_save_files(gist_id, gist_token, {
            f"raw_{key}.csv": raw_df.to_csv(index=False),
            f"processed_{key}.csv": processed_df.to_csv(index=False),
            f"meta_{key}.json": json.dumps({"cached_at": now_str}),
        })
        return

    # Fallback: local file cache
    raw_path, processed_path, meta_path = _cache_paths(container_name, year, month)
    os.makedirs(CACHE_DIR, exist_ok=True)
    raw_df.to_csv(raw_path, index=False)
    processed_df.to_csv(processed_path, index=False)
    with open(meta_path, "w", encoding="utf-8") as meta_file:
        json.dump({"cached_at": now_str}, meta_file)


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


def _normalize_vehicle_id(value: str) -> str:
    return "".join(ch for ch in str(value).upper().strip() if ch.isalnum())


def _extract_vehicle_id_from_suffix(suffix: str) -> str:
    if not suffix:
        return ""

    first_segment = suffix.split("/", 1)[0].split("?", 1)[0].strip()
    if first_segment.lower().endswith(".json"):
        first_segment = first_segment[:-5]
    return first_segment.strip()


@st.cache_data(show_spinner=False)
def fetch_onboarded_vehicle_hours_for_month(
    sas_url: str,
    container_name: str,
    year: int,
    month: int,
    vehicle_details_map: dict[str, dict[str, str]],
) -> pd.DataFrame:
    """Count hourly raw-data appearances for onboarded vehicle IDs across the selected month."""
    empty_df = pd.DataFrame(columns=["vehicle_id", "model", "variant", "operating_hours", "active_days"])

    if not sas_url or not container_name or not vehicle_details_map:
        return empty_df

    now = datetime.now()
    if (year, month) > (now.year, now.month):
        return empty_df

    _, num_days = calendar.monthrange(year, month)
    last_day = now.day if (year == now.year and month == now.month) else num_days

    container_client = ContainerClient.from_container_url(sas_url)
    vehicle_day_hours: dict[str, set[tuple[int, int]]] = {}
    normalized_lookup: dict[str, str] = {
        _normalize_vehicle_id(vehicle_id): vehicle_id
        for vehicle_id in vehicle_details_map
    }

    for day in range(1, last_day + 1):
        end_hour = now.hour if (year == now.year and month == now.month and day == now.day) else 23
        for hour in range(end_hour + 1):
            hour_path = f"raw-data/{year}/{month:02d}/{day:02d}/{hour:02d}/"
            seen_this_hour = set()

            for blob in container_client.list_blobs(name_starts_with=hour_path):
                suffix = blob.name[len(hour_path):]
                vehicle_id = _extract_vehicle_id_from_suffix(suffix)
                if not vehicle_id:
                    continue

                if vehicle_id in vehicle_details_map:
                    seen_this_hour.add(vehicle_id)
                    continue

                normalized_id = _normalize_vehicle_id(vehicle_id)
                canonical_vehicle_id = normalized_lookup.get(normalized_id)
                if canonical_vehicle_id:
                    seen_this_hour.add(canonical_vehicle_id)

            for vehicle_id in seen_this_hour:
                if vehicle_id not in vehicle_day_hours:
                    vehicle_day_hours[vehicle_id] = set()
                vehicle_day_hours[vehicle_id].add((day, hour))

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


@st.cache_data(show_spinner=False)
def fetch_processed_model_vehicleids_for_day(
    sas_url: str,
    container_name: str,
    year: int,
    month: int,
    day: int,
    vehicle_details_map: dict[str, dict[str, str]],
) -> pd.DataFrame:
    if not sas_url or not container_name or day < 1:
        return pd.DataFrame(columns=["day", "hour", "ist_day", "ist_hour", "model", "variant", "vehicle_count", "vehicle_ids"])

    now = datetime.now()
    if (year, month, day) > (now.year, now.month, now.day):
        return pd.DataFrame(columns=["day", "hour", "ist_day", "ist_hour", "model", "variant", "vehicle_count", "vehicle_ids"])

    end_hour = now.hour if (year, month, day) == (now.year, now.month, now.day) else 23
    container_client = ContainerClient.from_container_url(sas_url)
    rows = []

    for hour in range(end_hour + 1):
        hour_path = f"result-data/{year}/{month:02d}/{day:02d}/{hour:02d}/"
        model_variant_vehicle_ids: dict[tuple[str, str], set[str]] = {}

        for blob in container_client.list_blobs(name_starts_with=hour_path):
            suffix = blob.name[len(hour_path):]
            if "/" not in suffix:
                continue

            vehicle_id = suffix.split("/", 1)[0]
            details = vehicle_details_map.get(vehicle_id, {})

            model_name = details.get("model", "Unknown") or "Unknown"
            variant_name = details.get("variant", "Unknown") or "Unknown"
            key = (model_name, variant_name)
            if key not in model_variant_vehicle_ids:
                model_variant_vehicle_ids[key] = set()
            model_variant_vehicle_ids[key].add(vehicle_id)

        for (model_name, variant_name), ids in model_variant_vehicle_ids.items():
            sorted_ids = sorted(ids)
            utc_dt = datetime(year, month, day, hour)
            ist_dt = utc_dt + timedelta(hours=5, minutes=30)
            rows.append(
                {
                    "day": day,
                    "hour": hour,
                    "ist_day": ist_dt.day,
                    "ist_hour": ist_dt.hour,
                    "model": model_name,
                    "variant": variant_name,
                    "vehicle_count": len(sorted_ids),
                    "vehicle_ids": ", ".join(sorted_ids),
                }
            )

    if not rows:
        return pd.DataFrame(columns=["day", "hour", "ist_day", "ist_hour", "model", "variant", "vehicle_count", "vehicle_ids"])

    return pd.DataFrame(rows).sort_values(["ist_day", "ist_hour", "vehicle_count", "model", "variant"], ascending=[True, True, False, True, True]).reset_index(drop=True)


with st.sidebar:
    st.header("Settings")

    default_sas = secret_or_default("SAS_URL", "")
    default_container = secret_or_default("CONTAINER_NAME", "")
    default_year = int(secret_or_default("DEFAULT_YEAR", datetime.now().year))
    default_month = int(secret_or_default("DEFAULT_MONTH", datetime.now().month))

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
        st.warning("⚠️ SAS_URL and/or CONTAINER_NAME not configured in secrets. Please enter them above.")
        st.caption("Tip: Set SAS_URL and CONTAINER_NAME in Streamlit secrets for permanent prefill.")
        
        # Show available secrets for debugging
        available_keys = get_secret_keys()
        if available_keys:
            with st.expander("ℹ️ Available secrets (debug info)"):
                st.write("Keys found in secrets:")
                for key in available_keys:
                    st.text(f"  • {key}")
        else:
            st.caption("ℹ️ No secrets configured. Create a .streamlit/secrets.toml file with SAS_URL and CONTAINER_NAME.")
    else:
        st.success("✓ Credentials loaded from secrets")


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
        st.session_state["onboarded_vehicle_details_map"] = onboarded_summary.get("vehicle_details_map", {})
        st.session_state["onboarded_presence_df"] = fetch_onboarded_model_presence_for_month(
            sas_url,
            container_name,
            int(year),
            int(month),
            st.session_state["onboarded_vehicle_model_map"],
        )
        st.session_state["onboarded_vehicle_hours_df"] = fetch_onboarded_vehicle_hours_for_month(
            sas_url,
            container_name,
            int(year),
            int(month),
            st.session_state["onboarded_vehicle_details_map"],
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
        st.session_state["onboarded_vehicle_hours_df"] = fetch_onboarded_vehicle_hours_for_month(
            sas_url,
            container_name,
            int(year),
            int(month),
            st.session_state.get("onboarded_vehicle_details_map", {}),
        )
        st.session_state["onboarded_last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.session_state["onboarded_presence_bootstrap_key"] = onboarded_auto_refresh_key
    except (ValueError, RuntimeError, urlerror.URLError, urlerror.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        st.session_state["onboarded_error"] = str(exc)

if (
    "onboarded_vehicle_hours_df" not in st.session_state
    or (
        st.session_state.get("onboarded_vehicle_hours_df", pd.DataFrame()).empty
        and st.session_state.get("onboarded_vehicle_details_map")
    )
):
    try:
        st.session_state["onboarded_vehicle_hours_df"] = fetch_onboarded_vehicle_hours_for_month(
            sas_url,
            container_name,
            int(year),
            int(month),
            st.session_state.get("onboarded_vehicle_details_map", {}),
        )
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
            st.session_state["onboarded_vehicle_details_map"] = onboarded_summary.get("vehicle_details_map", {})
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
            st.session_state["onboarded_vehicle_hours_df"] = fetch_onboarded_vehicle_hours_for_month(
                sas_url,
                container_name,
                int(year),
                int(month),
                st.session_state["onboarded_vehicle_details_map"],
            )
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
    if st.button("🔥 Vehicles Live/Hour Heatmap", use_container_width=True, key="tab_heatmap"):
        st.session_state["active_tab"] = 1
with tab_cols[2]:
    if st.button("✅ Vehicles Result Processed", use_container_width=True, key="tab_processed"):
        st.session_state["active_tab"] = 2
with tab_cols[3]:
    if st.button("🧭 Onboarded Vehicles Drill Down", use_container_width=True, key="tab_onboarded"):
        st.session_state["active_tab"] = 3

st.divider()

# Tab 0: Daily Drill-down
if st.session_state["active_tab"] == 0:
    st.caption("Shows hourly live vehicle count trend for a selected IST day based on raw-data partitions.")
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
    st.caption("Shows daily vs hourly (IST) density of live vehicles; warmer cells indicate higher live vehicle counts.")
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
        title=f"Vehicles Live/Hour Heatmap - {int(year)}-{int(month):02d} (IST)",
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
    st.caption("Shows how many unique vehicle folders were processed in result-data each day.")
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

        st.divider()
        st.markdown("**Processed Vehicle IDs by Model and Hour**")
        st.caption("Shows per-hour processed vehicle counts by model and variant in IST, mapped using vehicle registry data.")

        available_processed_days = sorted(df_processed_sorted["day"].unique())
        selected_processed_day = st.selectbox(
            "Select Day for Hourly Processed Breakdown",
            options=available_processed_days,
            format_func=lambda x: f"Day {int(x)}",
            key="processed_breakdown_day_input",
        )

        onboarded_vehicle_details_map = st.session_state.get("onboarded_vehicle_details_map", {})
        with st.spinner("Loading processed model/hour breakdown..."):
            hourly_model_df = fetch_processed_model_vehicleids_for_day(
                sas_url,
                container_name,
                int(year),
                int(month),
                int(selected_processed_day),
                onboarded_vehicle_details_map,
            )

        if hourly_model_df.empty:
            st.info("No processed vehicle IDs found for the selected day.")
        else:
            hourly_model_totals = (
                hourly_model_df.groupby(["ist_hour", "model"], as_index=False)["vehicle_count"]
                .sum()
                .sort_values(["ist_hour", "model"])
            )
            fig_hourly_breakdown = go.Figure()
            for model_name in sorted(hourly_model_totals["model"].unique()):
                model_rows = hourly_model_totals[hourly_model_totals["model"] == model_name].copy()
                model_rows["ist_hour_label"] = model_rows["ist_hour"].map(lambda h: f"{int(h):02d}:00")
                fig_hourly_breakdown.add_trace(
                    go.Bar(
                        x=model_rows["ist_hour_label"],
                        y=model_rows["vehicle_count"],
                        name=str(model_name),
                        hovertemplate="<b>IST Hour:</b> %{x}<br><b>Model:</b> %{fullData.name}<br><b>Processed Vehicle IDs:</b> %{y}<extra></extra>",
                    )
                )
            fig_hourly_breakdown.update_layout(
                title=f"Processed Vehicle IDs by Model and Hour (IST) - Day {int(selected_processed_day)}",
                xaxis_title="Hour of Day (IST)",
                yaxis_title="Processed Vehicle IDs",
                template="plotly_white",
                autosize=True,
                barmode="group",
                xaxis={"categoryorder": "array", "categoryarray": [f"{h:02d}:00" for h in range(24)]},
                margin={"l": 50, "r": 40, "t": 50, "b": 50},
            )
            st.plotly_chart(fig_hourly_breakdown, use_container_width=True)

            display_df = hourly_model_df.copy()
            display_df["ist_hour"] = display_df["ist_hour"].map(lambda h: f"{int(h):02d}:00")
            display_df = display_df.rename(
                columns={
                    "ist_day": "IST Day",
                    "ist_hour": "IST Hour",
                    "model": "Model",
                    "variant": "Variant",
                    "vehicle_count": "Vehicle ID Count",
                    "vehicle_ids": "Vehicle IDs",
                }
            )
            display_df = display_df[["IST Day", "IST Hour", "Model", "Variant", "Vehicle ID Count", "Vehicle IDs"]]
            st.dataframe(display_df, use_container_width=True, hide_index=True)

# Tab 3: Onboarded Drill-down
if st.session_state["active_tab"] == 3:
    st.caption("Shows onboarded fleet composition and daily raw-data upload presence using vehicle registry mapping.")
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
                st.caption("Distribution of total onboarded vehicles by model.")
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
                st.caption("Top 25 onboarded model-variant combinations by vehicle count.")
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
            daily_presence_totals = (
                presence_df.groupby("day", as_index=False)["count"]
                .sum()
                .sort_values("day")
            )
            st.caption("Line graph: total onboarded vehicles that uploaded raw data at least once on each day.")
            fig_presence_line = go.Figure()
            fig_presence_line.add_trace(
                go.Scatter(
                    x=daily_presence_totals["day"],
                    y=daily_presence_totals["count"],
                    mode="lines+markers",
                    line={"color": "#2563eb", "width": 3},
                    marker={"size": 7},
                    hovertemplate="<b>Day:</b> %{x}<br><b>Vehicles Uploaded At Least Once:</b> %{y}<extra></extra>",
                )
            )
            fig_presence_line.update_layout(
                title="Daily Uploaded Vehicles (At Least Once)",
                xaxis_title="Day",
                yaxis_title="Unique Onboarded Vehicle IDs",
                template="plotly_white",
                autosize=True,
                showlegend=False,
                xaxis={"tickmode": "linear", "tick0": 1, "dtick": 1},
                margin={"l": 50, "r": 40, "t": 50, "b": 50},
            )
            st.plotly_chart(fig_presence_line, use_container_width=True)

            available_presence_days = sorted(presence_df["day"].unique())
            selected_presence_day = st.selectbox(
                "Select Day for Onboarded Presence",
                options=available_presence_days,
                format_func=lambda x: f"Day {int(x)}",
                key="onboarded_presence_day_input",
            )

            day_presence = presence_df[presence_df["day"] == selected_presence_day].copy()
            day_presence = day_presence.sort_values("count", ascending=False)

            st.caption("Bar chart: model-wise onboarded vehicle IDs that uploaded at least once on selected day.")
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

            st.divider()
            st.markdown("**Highest Operating Vehicles by Model**")
            st.caption("Counts hourly raw-data partition appearances for onboarded vehicle IDs across the selected month.")

            vehicle_hours_df = st.session_state.get("onboarded_vehicle_hours_df", pd.DataFrame())
            if vehicle_hours_df.empty:
                st.info("No operating-hours data available for onboarded vehicles in the selected month.")
            else:
                available_models = sorted(vehicle_hours_df["model"].dropna().unique())
                selected_hours_model = st.selectbox(
                    "Select Model to View Highest Operating Vehicles",
                    options=available_models,
                    key="onboarded_hours_model_input",
                )

                model_vehicles = vehicle_hours_df[vehicle_hours_df["model"] == selected_hours_model].copy()
                model_vehicles = model_vehicles.sort_values(["operating_hours", "active_days"], ascending=[False, False]).head(20)

                if model_vehicles.empty:
                    st.info(f"No operating-hours data available for model: {selected_hours_model}")
                else:
                    display_df = model_vehicles[["vehicle_id", "variant", "operating_hours", "active_days"]].copy()
                    display_df = display_df.rename(
                        columns={
                            "vehicle_id": "Vehicle ID",
                            "variant": "Variant",
                            "operating_hours": "Operating Hours",
                            "active_days": "Active Days",
                        }
                    )
                    st.dataframe(
                        display_df,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Vehicle ID": st.column_config.TextColumn("Vehicle ID", width="medium"),
                            "Variant": st.column_config.TextColumn("Variant", width="medium"),
                            "Operating Hours": st.column_config.NumberColumn("Operating Hours", format="%d"),
                            "Active Days": st.column_config.NumberColumn("Active Days", format="%d"),
                        },
                    )
