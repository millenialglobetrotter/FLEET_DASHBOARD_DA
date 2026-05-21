"""Fleet Level Dashboard - Main application entry point and orchestration."""

import calendar
import base64
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

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
    http_json,
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
    initial_sidebar_state="expanded",
)

BOSCH_PRIMARY = "#007bc0"
BOSCH_PRIMARY_DARK = "#00629a"
BOSCH_TURQUOISE = "#18837e"
BOSCH_GREEN = "#00884a"
BOSCH_BG_MUTED = "#eff1f2"
BOSCH_BORDER = "#d0d4d8"

APP_ROOT = Path(__file__).resolve().parent
BRAND_LOGO_PATH = APP_ROOT / "assets" / "bosch_logo.svg"
SUPERGRAPHIC_PATH = APP_ROOT / "assets" / "bosch_supergraphic.svg"


def _svg_b64(path: Path) -> str:
    """Return a data URI for an SVG file (base64 encoded)."""
    try:
        data = base64.b64encode(path.read_bytes()).decode()
        return f"data:image/svg+xml;base64,{data}"
    except OSError:
        return ""

PLOTLY_BRAND_LAYOUT = {
    "template": "plotly_white",
    "paper_bgcolor": "#ffffff",
    "plot_bgcolor": "#ffffff",
    "font": {
        "family": "Bosch Sans, Helvetica Neue, Helvetica, Arial, sans-serif",
        "color": "#000000",
    },
}

st.markdown(
    """
    <style>
    :root {
        --bosch-blue-50: #007bc0;
        --bosch-blue-40: #00629a;
        --bosch-green-50: #00884a;
        --bosch-turquoise-50: #18837e;
        --bosch-gray-95: #eff1f2;
        --bosch-gray-85: #d0d4d8;
    }
    html, body, [class*="css"] {
        font-family: Bosch Sans, Helvetica Neue, Helvetica, Arial, sans-serif !important;
    }
    .block-container {
        padding: 0.75rem 1rem 0.5rem 1rem !important;
        max-width: 100% !important;
    }
    .brand-header {
        display: flex;
        flex-direction: column;
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        width: 100%;
        z-index: 1000000;
        background: #ffffff;
        padding: 0.2rem 1rem 0.3rem 1rem;
        box-sizing: border-box;
        border-bottom: 1px solid var(--bosch-gray-85);
    }
    .brand-supergraphic {
        width: 100vw;
        height: 8px;
        display: block;
        line-height: 0;
        font-size: 0;
        margin-bottom: 0.35rem;
        margin-left: calc(50% - 50vw);
    }
    .brand-supergraphic img {
        width: 100%;
        height: 8px;
        display: block;
        object-fit: cover;
        object-position: center top;
    }
    .brand-title-row {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        padding: 0;
    }
    .brand-header-spacer {
        height: 130px;
    }
    [data-testid="stSidebar"] > div:first-child {
        padding-top: 130px;
    }
    @media (max-width: 992px) {
        .block-container {
            padding: 0.6rem 0.8rem 0.4rem 0.8rem !important;
        }
        .brand-header {
            padding: 0.2rem 0.8rem 0.3rem 0.8rem;
        }
        .brand-logo {
            height: 50px;
            margin-left: 0.9rem;
        }
        .brand-title {
            font-size: 1.6rem;
        }
        .brand-subtitle {
            font-size: 0.88rem;
        }
        .brand-header-spacer {
            height: 110px;
        }
        [data-testid="stSidebar"] > div:first-child {
            padding-top: 110px;
        }
    }
    @media (max-width: 640px) {
        .block-container {
            padding: 0.5rem 0.65rem 0.35rem 0.65rem !important;
        }
        .brand-header {
            padding: 0.15rem 0.65rem 0.25rem 0.65rem;
        }
        .brand-title-row {
            align-items: center;
        }
        .brand-logo {
            height: 40px;
            margin-left: 0.65rem;
        }
        .brand-title {
            font-size: 1.25rem;
            line-height: 1.2;
        }
        .brand-subtitle {
            font-size: 0.78rem;
            margin-top: 0.1rem;
        }
        .brand-header-spacer {
            height: 90px;
        }
        [data-testid="stSidebar"] > div:first-child {
            padding-top: 90px;
        }
        .ux-subtle-box {
            margin-bottom: 0.45rem;
            padding: 0.5rem 0.6rem;
            min-height: 72px;
        }
        .onboarded-metric-card {
            padding: 0.25rem 0.4rem;
            min-height: 60px;
        }
        .onboarded-metric-title {
            font-size: 0.74rem;
        }
        .onboarded-metric-value {
            font-size: 1.45rem;
        }
    }
    .brand-text {
        flex: 1;
    }
    .brand-title {
        margin: 0;
        color: #0b1f2a;
        font-weight: 700;
        letter-spacing: 0.01em;
    }
    .brand-subtitle {
        margin: 0.2rem 0 0;
        color: #4e5256;
        font-size: 0.95rem;
    }
    .brand-logo {
        flex-shrink: 0;
        margin-left: 1.5rem;
        height: 60px;
        width: auto;
        display: block;
    }
    .onboarded-metric-card {
        text-align: right;
        background: transparent;
        border: 0;
        border-left: 2px solid var(--bosch-gray-85);
        border-radius: 0;
        padding: 0.3rem 0.7rem;
        width: 100%;
        box-sizing: border-box;
        margin: 0;
        min-height: 68px;
    }
    .onboarded-metric-title {
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.02em;
        text-transform: uppercase;
        color: #5b6268;
    }
    .onboarded-metric-value {
        font-size: 1.5rem;
        font-weight: 800;
        color: #0b1f2a;
        line-height: 1;
    }
    [data-testid="stAppViewContainer"] {
        background: linear-gradient(180deg, #ffffff 0%, #f7f9fa 100%);
    }
    [data-testid="stVerticalBlock"] {
        gap: 0.5rem !important;
    }
    p, label, [data-testid="stMarkdownContainer"] p {
        line-height: 1.45;
    }
    button:focus-visible,
    input:focus-visible,
    [role="combobox"]:focus-visible,
    [role="radiogroup"] label:focus-visible {
        outline: 2px solid var(--bosch-blue-50) !important;
        outline-offset: 2px !important;
    }
    .ux-subtle-box {
        border: 1px solid var(--bosch-gray-85);
        background: #ffffff;
        padding: 0.6rem 0.75rem;
        margin: 0.2rem 0 0.75rem 0;
        min-height: 88px;
        display: flex;
        flex-direction: column;
        justify-content: center;
    }
    .ux-subtle-title {
        font-size: 0.82rem;
        color: #4e5256;
        margin: 0;
    }
    .ux-subtle-value {
        font-size: 1.15rem;
        font-weight: 700;
        color: #0b1f2a;
        margin: 0.1rem 0 0;
    }
    .ux-inline-help {
        color: #4e5256;
        font-size: 0.85rem;
        margin-top: 0.2rem;
    }
    .ux-section-gap {
        height: 0.35rem;
    }
    .stButton button,
    .stDownloadButton button,
    .stFormSubmitButton button {
        border-radius: 0 !important;
        border: 0 !important;
    }
    .stButton button[kind="primary"],
    .stDownloadButton button[kind="primary"],
    .stFormSubmitButton button[kind="primaryFormSubmit"] {
        background-color: var(--bosch-blue-50) !important;
        color: #ffffff !important;
    }
    .stButton button[kind="primary"]:hover,
    .stDownloadButton button[kind="primary"]:hover,
    .stFormSubmitButton button[kind="primaryFormSubmit"]:hover {
        background-color: var(--bosch-blue-40) !important;
    }
    .stButton button[kind="secondary"] {
        color: var(--bosch-blue-50) !important;
    }
    .stButton button[kind="secondary"] p {
        color: var(--bosch-blue-50) !important;
    }
    .stButton button[kind="secondary"]:hover,
    .stButton button[kind="secondary"]:hover p {
        color: var(--bosch-blue-40) !important;
    }
    [data-baseweb="select"] > div,
    [data-testid="stNumberInputContainer"],
    [data-baseweb="input"] input {
        border-radius: 0 !important;
        background: var(--bosch-gray-95) !important;
    }
    [data-baseweb="input"] input,
    [data-testid="stNumberInputContainer"] {
        border-bottom: 1px solid var(--bosch-gray-85) !important;
    }
    [data-testid="stTabs"] button {
        border-radius: 0 !important;
    }
    .stAppHeader,
    header[data-testid="stHeader"] {
        display: none !important;
    }
    footer {visibility: hidden;}
    .metric-info-icon {
        display: inline-block;
        width: 18px;
        height: 18px;
        line-height: 18px;
        text-align: center;
        background-color: var(--bosch-blue-50);
        color: white;
        border-radius: 50%;
        font-weight: 700;
        font-size: 0.75rem;
        cursor: help;
        margin-left: 0.35rem;
        vertical-align: middle;
        transition: background-color 0.2s ease;
    }
    .metric-info-icon:hover {
        background-color: var(--bosch-blue-40);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

_sg_uri = _svg_b64(SUPERGRAPHIC_PATH)
_logo_uri = _svg_b64(BRAND_LOGO_PATH)
_sg_img = f'<img src="{_sg_uri}" alt="">' if _sg_uri else '<div style="background:#007bc0;width:100%;height:8px;"></div>'
_logo_img = f'<img class="brand-logo" src="{_logo_uri}" alt="Bosch">' if _logo_uri else '<span style="font-weight:700;font-size:1.2rem;">BOSCH</span>'

st.markdown(
    f"""
    <div class="brand-header">
        <div class="brand-supergraphic">{_sg_img}</div>
        <div class="brand-title-row">
            <div class="brand-text">
                <h1 class="brand-title">Fleet Level Dashboard</h1>
                <p class="brand-subtitle">Vehicle count analytics across hourly partitions (IST)</p>
            </div>
            {_logo_img}
        </div>
    </div>
    <div class="brand-header-spacer"></div>
    """,
    unsafe_allow_html=True,
)

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
        response = http_json(
            f"https://api.github.com/gists/{gist_id}",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
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
            req = urlrequest.Request(
                raw_url,
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            with urlrequest.urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8")
        st.session_state.pop("gist_cache_error", None)
        return file_info.get("content")
    except Exception as exc:
        st.session_state["gist_cache_error"] = f"Gist read failed: {exc}"
        return None


def _gist_save_files(gist_id: str, token: str, files: dict[str, str]) -> bool:
    """PATCH a GitHub Gist with the given {filename: content} dict. Returns True on success."""
    try:
        payload = {"files": {name: {"content": content} for name, content in files.items()}}
        http_json(
            f"https://api.github.com/gists/{gist_id}",
            method="PATCH",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            payload=payload,
            timeout=20,
        )
        st.session_state.pop("gist_cache_error", None)
        return True
    except Exception as exc:
        st.session_state["gist_cache_error"] = f"Gist write failed: {exc}"
        return False


def load_cached_datasets(container_name: str, year: int, month: int):
    key = _safe_cache_key(container_name, year, month)
    raw_df = pd.DataFrame()
    processed_df = pd.DataFrame()
    presence_df = pd.DataFrame()
    vehicle_hours_df = pd.DataFrame()
    cached_at = None

    def _safe_read_csv(content):
        try:
            return pd.read_csv(io.StringIO(content))
        except Exception:
            return pd.DataFrame()

    gist_id, gist_token = _gist_credentials()
    if not gist_id or not gist_token:
        return raw_df, processed_df, presence_df, vehicle_hours_df, cached_at

    raw_content = _gist_read_file(gist_id, gist_token, f"raw_{key}.csv")
    processed_content = _gist_read_file(gist_id, gist_token, f"processed_{key}.csv")
    presence_content = _gist_read_file(gist_id, gist_token, f"presence_{key}.csv")
    vehicle_hours_content = _gist_read_file(gist_id, gist_token, f"vehicle_hours_{key}.csv")
    meta_content = _gist_read_file(gist_id, gist_token, f"meta_{key}.json")
    if raw_content:
        raw_df = _safe_read_csv(raw_content)
    if processed_content:
        processed_df = _safe_read_csv(processed_content)
    if presence_content:
        presence_df = _safe_read_csv(presence_content)
    if vehicle_hours_content:
        vehicle_hours_df = _safe_read_csv(vehicle_hours_content)
    if meta_content:
        try:
            cached_at = json.loads(meta_content).get("cached_at")
        except Exception:
            cached_at = None
    return raw_df, processed_df, presence_df, vehicle_hours_df, cached_at


def save_cached_datasets(
    container_name: str,
    year: int,
    month: int,
    raw_df: pd.DataFrame,
    processed_df: pd.DataFrame,
    presence_df: pd.DataFrame | None = None,
    vehicle_hours_df: pd.DataFrame | None = None,
):
    key = _safe_cache_key(container_name, year, month)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    presence_df = presence_df if presence_df is not None else pd.DataFrame()
    vehicle_hours_df = vehicle_hours_df if vehicle_hours_df is not None else pd.DataFrame()

    gist_id, gist_token = _gist_credentials()
    if not gist_id or not gist_token:
        return

    files = {
        f"raw_{key}.csv": raw_df.to_csv(index=False),
        f"processed_{key}.csv": processed_df.to_csv(index=False),
        f"meta_{key}.json": json.dumps({"cached_at": now_str}),
    }
    if not presence_df.empty:
        files[f"presence_{key}.csv"] = presence_df.to_csv(index=False)
    if not vehicle_hours_df.empty:
        files[f"vehicle_hours_{key}.csv"] = vehicle_hours_df.to_csv(index=False)
    _gist_save_files(gist_id, gist_token, files)


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


def _expected_days_for_month(year: int, month: int) -> list[int]:
    now = datetime.now()
    if (year, month) > (now.year, now.month):
        return []
    _, num_days = calendar.monthrange(year, month)
    last_day = now.day if (year == now.year and month == now.month) else num_days
    return list(range(1, last_day + 1))


def _missing_presence_days(presence_df: pd.DataFrame, year: int, month: int) -> list[int]:
    expected_days = set(_expected_days_for_month(year, month))
    if not expected_days:
        return []
    if presence_df.empty or "day" not in presence_df.columns:
        return sorted(expected_days)

    present_days = {
        int(day)
        for day in presence_df["day"].dropna().unique().tolist()
        if str(day).strip() != ""
    }
    return sorted(expected_days.difference(present_days))


def _days_requiring_presence_refresh(presence_df: pd.DataFrame, year: int, month: int) -> list[int]:
    """Return days that still need a live fetch (missing or placeholder-only rows)."""
    expected_days = _expected_days_for_month(year, month)
    if not expected_days:
        return []
    if presence_df.empty or "day" not in presence_df.columns:
        return expected_days

    working = presence_df.copy()
    working["day_num"] = pd.to_numeric(working["day"], errors="coerce")
    if "model" in working.columns:
        working["model_norm"] = working["model"].astype(str).str.strip().str.lower()
    else:
        working["model_norm"] = ""
    if "count" in working.columns:
        working["count_num"] = pd.to_numeric(working["count"], errors="coerce").fillna(0)
    else:
        working["count_num"] = 0

    needs_refresh = []
    for day in expected_days:
        day_rows = working[working["day_num"] == int(day)]
        if day_rows.empty:
            needs_refresh.append(int(day))
            continue

        real_rows = day_rows[day_rows["model_norm"] != "no uploads"]
        if real_rows.empty:
            needs_refresh.append(int(day))
            continue

        if not (real_rows["count_num"] > 0).any():
            needs_refresh.append(int(day))

    return needs_refresh


def _drop_no_upload_placeholders_when_real_data_exists(presence_df: pd.DataFrame) -> pd.DataFrame:
    """Remove synthetic 'No Uploads' rows for days where real model rows now exist."""
    if presence_df.empty or "day" not in presence_df.columns or "model" not in presence_df.columns:
        return presence_df

    working = presence_df.copy()
    working["_day_num"] = pd.to_numeric(working["day"], errors="coerce")
    working["_model_norm"] = working["model"].astype(str).str.strip().str.lower()
    if "count" in working.columns:
        working["_count_num"] = pd.to_numeric(working["count"], errors="coerce").fillna(0)
    else:
        working["_count_num"] = 0

    real_days = set(
        working.loc[
            (working["_model_norm"] != "no uploads") & (working["_count_num"] > 0),
            "_day_num",
        ]
        .dropna()
        .astype(int)
        .tolist()
    )
    if not real_days:
        return presence_df

    drop_mask = working["_day_num"].isin(real_days) & (working["_model_norm"] == "no uploads")
    cleaned = working.loc[~drop_mask].drop(columns=["_day_num", "_model_norm", "_count_num"], errors="ignore")
    return cleaned.reset_index(drop=True)


def _ensure_presence_day_coverage(presence_df: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    """Guarantee one row per expected day so cache visibly covers all days (0 when no uploads)."""
    missing_days = _missing_presence_days(presence_df, year, month)
    if not missing_days:
        return presence_df

    filler = pd.DataFrame(
        [{"day": int(day), "model": "No Uploads", "count": 0} for day in missing_days]
    )
    if presence_df.empty:
        return filler.sort_values(["day", "model"]).reset_index(drop=True)
    return pd.concat([presence_df, filler], ignore_index=True).sort_values(["day", "model"]).reset_index(drop=True)


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


def refresh_onboarded_cache_for_month(
    sas_url: str,
    container_name: str,
    year: int,
    month: int,
    force_full: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    vehicle_model_map = st.session_state.get("onboarded_vehicle_model_map", {})
    vehicle_details_map = st.session_state.get("onboarded_vehicle_details_map", {})
    if not vehicle_model_map or not vehicle_details_map:
        return pd.DataFrame(), pd.DataFrame()

    current_presence = st.session_state.get("onboarded_presence_df", pd.DataFrame())
    if force_full or current_presence.empty:
        presence_df = fetch_onboarded_model_presence_for_month(
            sas_url,
            container_name,
            int(year),
            int(month),
            vehicle_model_map,
        )
    else:
        presence_df = current_presence.copy()

    missing_days = _missing_presence_days(presence_df, int(year), int(month))
    stale_placeholder_days = _days_requiring_presence_refresh(presence_df, int(year), int(month))
    days_to_fetch = sorted(set(missing_days).union(stale_placeholder_days))
    if days_to_fetch:
        missing_updates = fetch_onboarded_model_presence_for_days(
            sas_url,
            container_name,
            int(year),
            int(month),
            vehicle_model_map,
            days_to_fetch,
        )
        presence_df = merge_model_daily_data(presence_df, missing_updates)

    recent_days = _recent_days_for_lookback(int(year), int(month), lookback_hours=24)
    if recent_days:
        recent_updates = fetch_onboarded_model_presence_for_days(
            sas_url,
            container_name,
            int(year),
            int(month),
            vehicle_model_map,
            recent_days,
        )
        presence_df = merge_model_daily_data(presence_df, recent_updates)

    presence_df = _drop_no_upload_placeholders_when_real_data_exists(presence_df)

    # Materialize missing days as zero-count rows to keep Gist day coverage continuous.
    presence_df = _ensure_presence_day_coverage(presence_df, int(year), int(month))

    vehicle_hours_df = st.session_state.get("onboarded_vehicle_hours_df", pd.DataFrame())
    if force_full or vehicle_hours_df.empty:
        vehicle_hours_df = fetch_onboarded_vehicle_hours_for_month(
            sas_url,
            container_name,
            int(year),
            int(month),
            vehicle_details_map,
        )

    st.session_state["onboarded_presence_df"] = presence_df
    st.session_state["onboarded_vehicle_hours_df"] = vehicle_hours_df
    st.session_state["onboarded_tab_load_key"] = f"{int(year)}-{int(month):02d}"

    return presence_df, vehicle_hours_df


def aggregate_processed_vehicles_by_day_hour(
    sas_url: str,
    container_name: str,
    year: int,
    month: int,
    vehicle_details_map: dict[str, dict[str, str]],
) -> pd.DataFrame:
    """Aggregate processed vehicles by day and hour for the entire month (IST)."""
    if not sas_url or not container_name:
        return pd.DataFrame(columns=["ist_day", "ist_hour", "vehicle_count"])

    now = datetime.now()
    container_client = ContainerClient.from_container_url(sas_url)
    normalized_lookup: dict[str, str] = {
        _normalize_vehicle_id(vehicle_id): vehicle_id
        for vehicle_id in vehicle_details_map
    }
    month_prefix = f"result-data/{year}/{month:02d}/"
    slot_vehicle_ids: dict[tuple[int, int], set[str]] = {}

    # One pass through monthly blobs is significantly faster than listing each day/hour prefix.
    for blob in container_client.list_blobs(name_starts_with=month_prefix):
        parts = blob.name.split("/")
        if len(parts) < 6:
            continue

        try:
            utc_day = int(parts[3])
            utc_hour = int(parts[4])
        except ValueError:
            continue

        if (year, month, utc_day) > (now.year, now.month, now.day):
            continue
        if (year, month, utc_day) == (now.year, now.month, now.day) and utc_hour > now.hour:
            continue

        suffix = "/".join(parts[5:])
        vehicle_id = _extract_vehicle_id_from_suffix(suffix)
        if not vehicle_id:
            continue

        if vehicle_id not in vehicle_details_map:
            normalized_id = _normalize_vehicle_id(vehicle_id)
            vehicle_id = normalized_lookup.get(normalized_id, vehicle_id)

        utc_dt = datetime(year, month, utc_day, utc_hour)
        ist_dt = utc_dt + timedelta(hours=5, minutes=30)
        slot_key = (ist_dt.day, ist_dt.hour)
        if slot_key not in slot_vehicle_ids:
            slot_vehicle_ids[slot_key] = set()
        slot_vehicle_ids[slot_key].add(vehicle_id)

    if not slot_vehicle_ids:
        return pd.DataFrame(columns=["ist_day", "ist_hour", "vehicle_count"])

    rows = [
        {"ist_day": ist_day, "ist_hour": ist_hour, "vehicle_count": len(vehicle_ids)}
        for (ist_day, ist_hour), vehicle_ids in sorted(slot_vehicle_ids.items())
    ]
    return pd.DataFrame(rows)


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
    normalized_lookup: dict[str, str] = {
        _normalize_vehicle_id(vehicle_id): vehicle_id
        for vehicle_id in vehicle_details_map
    }

    for hour in range(end_hour + 1):
        hour_path = f"result-data/{year}/{month:02d}/{day:02d}/{hour:02d}/"
        model_variant_vehicle_ids: dict[tuple[str, str], set[str]] = {}

        for blob in container_client.list_blobs(name_starts_with=hour_path):
            suffix = blob.name[len(hour_path):]
            vehicle_id = _extract_vehicle_id_from_suffix(suffix)
            if not vehicle_id:
                continue

            if vehicle_id not in vehicle_details_map:
                normalized_id = _normalize_vehicle_id(vehicle_id)
                vehicle_id = normalized_lookup.get(normalized_id, vehicle_id)

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


def fetch_unprocessed_vehicles_for_day(
    sas_url: str,
    container_name: str,
    year: int,
    month: int,
    day: int,
    vehicle_details_map: dict[str, dict[str, str]],
) -> tuple[set[str], set[str]]:
    """Return (raw_ids, processed_ids) for the day (UTC day-level scan, IST offsets not needed here
    since we want coverage across the full calendar day in both partitions)."""
    if not sas_url or not container_name or day < 1:
        return set(), set()

    now = datetime.now()
    if (year, month, day) > (now.year, now.month, now.day):
        return set(), set()

    end_hour = now.hour if (year, month, day) == (now.year, now.month, now.day) else 23
    container_client = ContainerClient.from_container_url(sas_url)
    normalized_lookup: dict[str, str] = {
        _normalize_vehicle_id(vid): vid for vid in vehicle_details_map
    }

    def _resolve(vehicle_id: str) -> str:
        if vehicle_id not in vehicle_details_map:
            normalized_id = _normalize_vehicle_id(vehicle_id)
            return normalized_lookup.get(normalized_id, vehicle_id)
        return vehicle_id

    raw_ids: set[str] = set()
    processed_ids: set[str] = set()

    # Scan both partitions for all hours of the day in a single loop per partition.
    raw_prefix = f"raw-data/{year}/{month:02d}/{day:02d}/"
    result_prefix = f"result-data/{year}/{month:02d}/{day:02d}/"

    for blob in container_client.list_blobs(name_starts_with=raw_prefix):
        parts = blob.name[len(raw_prefix):].split("/")
        if len(parts) < 2:
            continue
        try:
            if int(parts[0]) > end_hour:
                continue
        except ValueError:
            continue
        suffix = "/".join(parts[1:])
        vid = _extract_vehicle_id_from_suffix(suffix)
        if vid:
            raw_ids.add(_resolve(vid))

    for blob in container_client.list_blobs(name_starts_with=result_prefix):
        parts = blob.name[len(result_prefix):].split("/")
        if len(parts) < 2:
            continue
        try:
            if int(parts[0]) > end_hour:
                continue
        except ValueError:
            continue
        suffix = "/".join(parts[1:])
        vid = _extract_vehicle_id_from_suffix(suffix)
        if vid:
            processed_ids.add(_resolve(vid))

    return raw_ids, processed_ids


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
    gist_id_dbg, gist_token_dbg = _gist_credentials()
    if gist_id_dbg and gist_token_dbg:
        st.caption("Gist cache: configured")
    else:
        st.caption("Gist cache: missing GITHUB_GIST_ID/GITHUB_GIST_TOKEN")
    if st.session_state.get("gist_cache_error"):
        st.caption(f"Gist cache error: {st.session_state['gist_cache_error']}")
    presence_dbg = st.session_state.get("onboarded_presence_df", pd.DataFrame())
    if not presence_dbg.empty and "day" in presence_dbg.columns:
        st.caption(f"Onboarded presence cached through day: {int(presence_dbg['day'].max())}")
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
            cached_raw, cached_processed, cached_presence, cached_vehicle_hours, cached_at = load_cached_datasets(container_name, int(year), int(month))
            if not cached_raw.empty and not cached_processed.empty:
                st.session_state["df_results"] = cached_raw
                st.session_state["df_processed"] = cached_processed
                st.session_state["cache_loaded_at"] = cached_at
                # Seed onboarded tab data from cache if available — avoids slow Azure fetch on first open.
                if not cached_presence.empty:
                    st.session_state["onboarded_presence_df"] = cached_presence
                    st.session_state["onboarded_tab_load_key"] = f"{int(year)}-{int(month):02d}"
                if not cached_vehicle_hours.empty:
                    st.session_state["onboarded_vehicle_hours_df"] = cached_vehicle_hours
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

# Always bootstrap recent data once per session/key for current month,
# so first open reflects latest processed/raw partitions without manual refresh.
recent_bootstrap_key = f"{container_name}|{int(year)}-{int(month):02d}"
if st.session_state.get("recent_data_bootstrap_key") != recent_bootstrap_key:
    try:
        now = datetime.now()
        if (int(year), int(month)) == (now.year, now.month):
            recent_df = fetch_recent_hours(sas_url, container_name, int(year), int(month), lookback_hours=12)
            recent_processed = fetch_recent_processed_days(
                sas_url,
                container_name,
                int(year),
                int(month),
                lookback_hours=12,
            )
            st.session_state["df_results"] = merge_hourly_data(
                st.session_state.get("df_results", pd.DataFrame()),
                recent_df,
            )
            st.session_state["df_processed"] = merge_daily_data(
                st.session_state.get("df_processed", pd.DataFrame()),
                recent_processed,
            )
        st.session_state["recent_data_bootstrap_key"] = recent_bootstrap_key
    except Exception as exc:
        st.error(f"Unable to load recent data on startup: {exc}")

# Load only lightweight onboarded summary at startup.
if "onboarded_vehicle_details_map" not in st.session_state:
    try:
        onboarded_summary = fetch_onboarded_vehicle_summary(make_filter="SML")
        st.session_state["total_vehicles_onboarded"] = onboarded_summary["total"]
        st.session_state["onboarded_model_counts"] = onboarded_summary["model_df"]
        st.session_state["onboarded_variant_counts"] = onboarded_summary["variant_df"]
        st.session_state["onboarded_vehicle_model_map"] = onboarded_summary.get("vehicle_model_map", {})
        st.session_state["onboarded_vehicle_details_map"] = onboarded_summary.get("vehicle_details_map", {})
        st.session_state["onboarded_last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.session_state.pop("onboarded_error", None)
    except (ValueError, RuntimeError, urlerror.URLError, urlerror.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        st.session_state["onboarded_error"] = str(exc)

# One-time month repair: backfill any missing onboarded presence days in cache.
onboarded_repair_key = f"{container_name}|{int(year)}-{int(month):02d}"
if st.session_state.get("onboarded_repair_key") != onboarded_repair_key:
    try:
        repaired_presence_df, repaired_vehicle_hours_df = refresh_onboarded_cache_for_month(
            sas_url,
            container_name,
            int(year),
            int(month),
            force_full=False,
        )
        if not repaired_presence_df.empty:
            save_cached_datasets(
                container_name,
                int(year),
                int(month),
                st.session_state.get("df_results", pd.DataFrame()),
                st.session_state.get("df_processed", pd.DataFrame()),
                repaired_presence_df,
                repaired_vehicle_hours_df,
            )
        st.session_state["onboarded_repair_key"] = onboarded_repair_key
    except (ValueError, RuntimeError, urlerror.URLError, urlerror.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        st.session_state["onboarded_error"] = str(exc)

st.markdown('<div style="height: 0.35rem;"></div>', unsafe_allow_html=True)

top_left_col, metric_col = st.columns([0.5, 0.5])
with top_left_col:
    action_col, status_col = st.columns([0.32, 0.68])
    with action_col:
        if st.button("Refresh Data", use_container_width=True, type="primary"):
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
                    refreshed_presence_df, refreshed_vehicle_hours_df = refresh_onboarded_cache_for_month(
                        sas_url,
                        container_name,
                        int(year),
                        int(month),
                        force_full=False,
                    )
                    st.session_state["onboarded_last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    st.session_state.pop("onboarded_error", None)
                    # Persist updated onboarded data to shared cache immediately.
                    save_cached_datasets(
                        container_name,
                        int(year),
                        int(month),
                        st.session_state["df_results"],
                        st.session_state["df_processed"],
                        refreshed_presence_df,
                        refreshed_vehicle_hours_df,
                    )

                    # Invalidate Tab 2 computed caches so next render reflects refreshed data.
                    processed_heatmap_cache_key = f"processed_heatmap_{int(year)}_{int(month):02d}"
                    st.session_state.pop(processed_heatmap_cache_key, None)
                    unprocessed_prefix = f"unprocessed_{int(year)}_{int(month):02d}_"
                    for _cache_key in list(st.session_state.keys()):
                        if _cache_key.startswith(unprocessed_prefix):
                            st.session_state.pop(_cache_key, None)
                except (ValueError, RuntimeError, urlerror.URLError, urlerror.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
                    st.error(f"Unable to refresh recent hours: {exc}")
                    st.session_state["onboarded_error"] = str(exc)

    with status_col:
        if "last_refresh" in st.session_state:
            st.caption(f"Last refresh: {st.session_state['last_refresh']} (24h sync)")
        if "cache_loaded_at" in st.session_state:
            st.caption(f"Shared cache: {st.session_state['cache_loaded_at']}")
        if "onboarded_last_updated" in st.session_state:
            st.caption(f"Onboarded count: {st.session_state['onboarded_last_updated']}")
        if "onboarded_error" in st.session_state:
            st.caption(f"Onboarded sync error: {st.session_state['onboarded_error']}")

with metric_col:
    total_onboarded = st.session_state.get("total_vehicles_onboarded", "N/A")
    uploading_today_value = "N/A"
    now_ist_kpi = datetime.now() + timedelta(hours=5, minutes=30)
    if (int(year), int(month)) == (now_ist_kpi.year, now_ist_kpi.month):
        presence_kpi_df = st.session_state.get("onboarded_presence_df", pd.DataFrame())
        if not presence_kpi_df.empty and "day" in presence_kpi_df.columns and "count" in presence_kpi_df.columns:
            day_rows = presence_kpi_df[presence_kpi_df["day"] == int(now_ist_kpi.day)]
            if not day_rows.empty:
                uploading_today_value = int(pd.to_numeric(day_rows["count"], errors="coerce").fillna(0).sum())

    metric_left, metric_right = st.columns(2)
    with metric_left:
        st.markdown(
            f"""
            <div class="onboarded-metric-card" style="border-left:0;">
                <div class="onboarded-metric-title">Total vehicles onboarded</div>
                <div class="onboarded-metric-value">{total_onboarded}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with metric_right:
        st.markdown(
            f"""
            <div class="onboarded-metric-card" style="border-left:0;">
                <div class="onboarded-metric-title">Total vehicles uploading data <span class="metric-info-icon" title="Total unique vehicles which have uploaded data today">ⓘ</span></div>
                <div class="onboarded-metric-value">{uploading_today_value}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.markdown('<div style="height:0.2rem;"></div>', unsafe_allow_html=True)

df_results = st.session_state["df_results"]

if df_results.empty:
    st.warning("No data available. Check credentials, month/year, and path format.")
    st.stop()

df_results_ist = df_results.copy()
df_results_ist["ist_hour"] = ((df_results_ist["hour"] + 5.5) % 24).astype(int)
df_results_ist["ist_day"] = df_results_ist["day"] + ((df_results_ist["hour"] + 5.5) // 24).astype(int)

available_days = sorted(df_results_ist["ist_day"].unique())
now_ist = datetime.now() + timedelta(hours=5, minutes=30)
if (int(year), int(month)) == (now_ist.year, now_ist.month):
    if now_ist.day not in available_days:
        available_days.append(now_ist.day)
        available_days = sorted(available_days)

# Initialize active tab in session state
if "active_tab" not in st.session_state:
    st.session_state["active_tab"] = 0

# Button-style tabs arranged in a 2x2 grid for better small-screen responsiveness.
tab_items = [
    ("📊 Daily Drill-down", 0, "tab_drill_down"),
    ("🔥 Vehicles Live/Hour Heatmap", 1, "tab_heatmap"),
    ("✅ Vehicles Result Processed", 2, "tab_processed"),
    ("🧭 Onboarded Vehicles Drill Down", 3, "tab_onboarded"),
]
tab_row_1 = st.columns(2)
tab_row_2 = st.columns(2)
_tab_switched = False
for tab_col, (tab_label, tab_idx, tab_key) in zip(tab_row_1, tab_items[:2]):
    with tab_col:
        if st.button(
            tab_label,
            use_container_width=True,
            key=tab_key,
            type="primary" if st.session_state["active_tab"] == tab_idx else "secondary",
        ):
            if st.session_state["active_tab"] != tab_idx:
                st.session_state["active_tab"] = tab_idx
                _tab_switched = True
for tab_col, (tab_label, tab_idx, tab_key) in zip(tab_row_2, tab_items[2:]):
    with tab_col:
        if st.button(
            tab_label,
            use_container_width=True,
            key=tab_key,
            type="primary" if st.session_state["active_tab"] == tab_idx else "secondary",
        ):
            if st.session_state["active_tab"] != tab_idx:
                st.session_state["active_tab"] = tab_idx
                _tab_switched = True
if _tab_switched:
    st.rerun()

st.divider()

# Tab 0: Daily Drill-down
if st.session_state["active_tab"] == 0:
    st.caption("Shows hourly live vehicle count trend for a selected IST day based on raw-data partitions.")
    if "selected_day_input" not in st.session_state or st.session_state["selected_day_input"] not in available_days:
        st.session_state["selected_day_input"] = available_days[0]

    day_label_col, day_select_col = st.columns([0.2, 0.8])
    with day_label_col:
        st.markdown("**Day**")
    with day_select_col:
        selected_day = st.selectbox(
            "Select Day (IST)",
            options=available_days,
            format_func=lambda x: f"Day {int(x)}",
            label_visibility="collapsed",
            key="selected_day_input",
        )

    selected_idx = available_days.index(st.session_state["selected_day_input"])
    day_prev_col, day_next_col = st.columns(2)
    with day_prev_col:
        prev_disabled = selected_idx == 0
        if st.button("Previous", key="day_prev_button", use_container_width=True, disabled=prev_disabled):
            st.session_state["selected_day_input"] = available_days[selected_idx - 1]
            st.rerun()
    with day_next_col:
        next_disabled = selected_idx == len(available_days) - 1
        if st.button("Next", key="day_next_button", use_container_width=True, disabled=next_disabled):
            st.session_state["selected_day_input"] = available_days[selected_idx + 1]
            st.rerun()

    st.markdown(
        f'<p class="ux-inline-help">Tip: use Previous/Next for faster day-by-day checks. ({selected_idx + 1} of {len(available_days)} days)</p>',
        unsafe_allow_html=True,
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
                marker={"color": BOSCH_PRIMARY},
                text=hourly_data["vehicle_count"],
                textposition="outside",
                hovertemplate="<b>Hour:</b> %{x}:00 IST<br><b>Vehicles:</b> %{y}<extra></extra>",
            )
        )
        fig.update_layout(
            title=f"Vehicle Count by Hour - Day {int(selected_day)}, {int(year)}-{int(month):02d} (IST)",
            xaxis_title="Hour of Day (IST)",
            yaxis_title="Vehicle Count",
            **PLOTLY_BRAND_LAYOUT,
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
            colorscale=[[0.0, "#e8f1ff"], [0.3, "#9dc9ff"], [0.6, "#007bc0"], [1.0, "#004975"]],
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
        **PLOTLY_BRAND_LAYOUT,
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
                marker={"color": BOSCH_GREEN},
                text=df_processed_sorted["processed_count"],
                textposition="outside",
                hovertemplate="<b>Day:</b> %{x}<br><b>Vehicles Processed:</b> %{y}<extra></extra>",
            )
        )
        fig_processed.update_layout(
            title=f"Vehicles Processed Per Day - {int(year)}-{int(month):02d}",
            xaxis_title="Day",
            yaxis_title="Unique Folders Count",
            **PLOTLY_BRAND_LAYOUT,
            autosize=True,
            showlegend=False,
            xaxis={"tickmode": "linear", "tick0": 1, "dtick": 1},
            margin={"l": 50, "r": 40, "t": 50, "b": 50},
        )
        st.plotly_chart(fig_processed, use_container_width=True)

        # Heatmap comparison: Live vehicles vs Processed vehicles
        st.markdown('<div style="height:0.3rem;"></div>', unsafe_allow_html=True)
        st.markdown("**Live Vehicles vs Processed Vehicles - Hourly Heatmap (IST)**")
        st.caption("Compare hourly vehicle counts: raw live data vs processed results side by side.")
        
        with st.spinner("Loading heatmap data..."):
            # Live vehicles heatmap
            pivot_live = df_results_ist.pivot_table(
                index="ist_day",
                columns="ist_hour",
                values="vehicle_count",
                fill_value=0,
                aggfunc="sum",
            )
            for hour in range(24):
                if hour not in pivot_live.columns:
                    pivot_live[hour] = 0

            # Processed vehicles heatmap (cached in session for current year-month).
            processed_heatmap_cache_key = f"processed_heatmap_{int(year)}_{int(month):02d}"
            processed_hourly_df = st.session_state.get(processed_heatmap_cache_key, pd.DataFrame())
            if processed_hourly_df.empty:
                processed_hourly_df = aggregate_processed_vehicles_by_day_hour(
                    sas_url,
                    container_name,
                    int(year),
                    int(month),
                    st.session_state.get("onboarded_vehicle_details_map", {}),
                )
                st.session_state[processed_heatmap_cache_key] = processed_hourly_df
            
            if not processed_hourly_df.empty:
                pivot_processed = processed_hourly_df.pivot_table(
                    index="ist_day",
                    columns="ist_hour",
                    values="vehicle_count",
                    fill_value=0,
                    aggfunc="sum",
                )
                for hour in range(24):
                    if hour not in pivot_processed.columns:
                        pivot_processed[hour] = 0
            else:
                pivot_processed = pd.DataFrame()

            # Keep both heatmaps on the same day axis for direct visual comparison.
            live_days = set(pivot_live.index.tolist())
            processed_days = set(pivot_processed.index.tolist()) if not pivot_processed.empty else set()
            all_days = sorted(live_days.union(processed_days))
            if all_days:
                pivot_live = pivot_live.reindex(index=all_days, fill_value=0)
                if not pivot_processed.empty:
                    pivot_processed = pivot_processed.reindex(index=all_days, fill_value=0)

            pivot_live = pivot_live.reindex(columns=list(range(24)), fill_value=0)
            if not pivot_processed.empty:
                pivot_processed = pivot_processed.reindex(columns=list(range(24)), fill_value=0)

        # Display heatmaps side by side
        heatmap_col1, heatmap_col2 = st.columns(2)
        
        with heatmap_col1:
            fig_heat_live = go.Figure(
                go.Heatmap(
                    z=pivot_live.values,
                    x=[f"{h:02d}:00" for h in range(24)],
                    y=[f"Day {int(d)}" for d in pivot_live.index],
                    colorscale=[[0.0, "#e8f1ff"], [0.3, "#9dc9ff"], [0.6, "#007bc0"], [1.0, "#004975"]],
                    hovertemplate="<b>Day:</b> %{y}<br><b>Hour:</b> %{x} IST<br><b>Vehicles:</b> %{z}<extra></extra>",
                    colorbar={"title": "Vehicles", "x": 1.02, "xanchor": "left", "thickness": 12, "len": 0.9},
                )
            )
            fig_heat_live.update_layout(
                title="Live Vehicles/Hour",
                xaxis_title="Hour of Day (IST)",
                yaxis_title="Day",
                **PLOTLY_BRAND_LAYOUT,
                autosize=True,
                margin={"l": 60, "r": 40, "t": 50, "b": 50},
                yaxis={"autorange": "reversed"},
                height=500,
            )
            st.plotly_chart(fig_heat_live, use_container_width=True)
        
        with heatmap_col2:
            if not pivot_processed.empty:
                fig_heat_processed = go.Figure(
                    go.Heatmap(
                        z=pivot_processed.values,
                        x=[f"{h:02d}:00" for h in range(24)],
                        y=[f"Day {int(d)}" for d in pivot_processed.index],
                        colorscale=[[0.0, "#e8f4e6"], [0.3, "#9ce6aa"], [0.6, "#00884a"], [1.0, "#005c2f"]],
                        hovertemplate="<b>Day:</b> %{y}<br><b>Hour:</b> %{x} IST<br><b>Processed:</b> %{z}<extra></extra>",
                        colorbar={"title": "Processed", "x": 1.02, "xanchor": "left", "thickness": 12, "len": 0.9},
                    )
                )
                fig_heat_processed.update_layout(
                    title="Processed Vehicles/Hour",
                    xaxis_title="Hour of Day (IST)",
                    yaxis_title="Day",
                    **PLOTLY_BRAND_LAYOUT,
                    autosize=True,
                    margin={"l": 60, "r": 40, "t": 50, "b": 50},
                    yaxis={"autorange": "reversed"},
                    height=500,
                )
                st.plotly_chart(fig_heat_processed, use_container_width=True)
            else:
                st.info("No processed vehicle data available for heatmap.")

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
            model_filter_options = sorted(hourly_model_totals["model"].dropna().unique())
            selected_processed_models = st.multiselect(
                "Filter models in hourly breakdown",
                options=model_filter_options,
                default=model_filter_options,
                key=f"processed_model_filter_{int(selected_processed_day)}",
            )

            if selected_processed_models:
                hourly_model_totals = hourly_model_totals[
                    hourly_model_totals["model"].isin(selected_processed_models)
                ]
                hourly_model_df = hourly_model_df[
                    hourly_model_df["model"].isin(selected_processed_models)
                ]

            if hourly_model_totals.empty:
                st.info("No rows match the selected model filter.")
            else:
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
                    **PLOTLY_BRAND_LAYOUT,
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
            with st.expander("View detailed processed vehicle IDs", expanded=False):
                st.download_button(
                    "Download processed breakdown CSV",
                    data=display_df.to_csv(index=False),
                    file_name=f"processed_breakdown_day_{int(selected_processed_day)}_{int(year)}_{int(month):02d}.csv",
                    mime="text/csv",
                    use_container_width=False,
                )
                st.dataframe(display_df, use_container_width=True, hide_index=True)

        # -- Unprocessed vehicles section --
        st.markdown('<div style="height:0.3rem;"></div>', unsafe_allow_html=True)
        st.markdown("**Vehicles Not Processed on This Day**")
        st.caption(
            "Vehicles present in raw-data partitions but absent from result-data for the selected day."
        )

        unproc_cache_key = f"unprocessed_{int(year)}_{int(month):02d}_{int(selected_processed_day)}"
        if unproc_cache_key not in st.session_state:
            with st.spinner("Comparing raw-data vs result-data vehicle IDs..."):
                raw_ids, processed_ids = fetch_unprocessed_vehicles_for_day(
                    sas_url,
                    container_name,
                    int(year),
                    int(month),
                    int(selected_processed_day),
                    st.session_state.get("onboarded_vehicle_details_map", {}),
                )
            st.session_state[unproc_cache_key] = (raw_ids, processed_ids)
        else:
            raw_ids, processed_ids = st.session_state[unproc_cache_key]

        unprocessed_ids = sorted(raw_ids - processed_ids)
        only_processed_ids = sorted(processed_ids - raw_ids)

        kpi1, kpi2, kpi3 = st.columns(3)
        with kpi1:
            st.markdown(
                f"""<div class="onboarded-metric-card" style="border-left:0;">
                    <div class="onboarded-metric-title">Vehicles in raw-data</div>
                    <div class="onboarded-metric-value">{len(raw_ids)}</div>
                </div>""",
                unsafe_allow_html=True,
            )
        with kpi2:
            st.markdown(
                f"""<div class="onboarded-metric-card" style="border-left:0;">
                    <div class="onboarded-metric-title">Vehicles in result-data</div>
                    <div class="onboarded-metric-value">{len(processed_ids)}</div>
                </div>""",
                unsafe_allow_html=True,
            )
        with kpi3:
            st.markdown(
                f"""<div class="onboarded-metric-card" style="border-left:0;">
                    <div class="onboarded-metric-title">Not processed (raw only)</div>
                    <div class="onboarded-metric-value" style="color:#c0392b;">{len(unprocessed_ids)}</div>
                </div>""",
                unsafe_allow_html=True,
            )

        if unprocessed_ids:
            vehicle_details_map_ui = st.session_state.get("onboarded_vehicle_details_map", {})
            unprocessed_rows = []
            for vid in unprocessed_ids:
                details = vehicle_details_map_ui.get(vid, {})
                unprocessed_rows.append(
                    {
                        "Vehicle ID": vid,
                        "Model": details.get("model", "Unknown") or "Unknown",
                        "Variant": details.get("variant", "Unknown") or "Unknown",
                    }
                )
            unprocessed_display_df = pd.DataFrame(unprocessed_rows)

            with st.expander(
                f"View {len(unprocessed_ids)} vehicle(s) not processed on Day {int(selected_processed_day)}",
                expanded=True,
            ):
                st.download_button(
                    "Download unprocessed vehicles CSV",
                    data=unprocessed_display_df.to_csv(index=False),
                    file_name=f"unprocessed_vehicles_day_{int(selected_processed_day)}_{int(year)}_{int(month):02d}.csv",
                    mime="text/csv",
                    use_container_width=False,
                )
                st.dataframe(unprocessed_display_df, use_container_width=True, hide_index=True)

            if only_processed_ids:
                with st.expander(
                    f"ⓘ {len(only_processed_ids)} vehicle(s) in result-data but not in raw-data",
                    expanded=False,
                ):
                    st.caption("These may be vehicles from a previous UTC day whose IST hour crosses midnight.")
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "Vehicle ID": vid,
                                    "Model": vehicle_details_map_ui.get(vid, {}).get("model", "Unknown") or "Unknown",
                                    "Variant": vehicle_details_map_ui.get(vid, {}).get("variant", "Unknown") or "Unknown",
                                }
                                for vid in only_processed_ids
                            ]
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )
        else:
            st.success(f"All vehicles in raw-data were processed on Day {int(selected_processed_day)}.")

# Tab 3: Onboarded Drill-down
if st.session_state["active_tab"] == 3:
    onboarded_tab_load_key = f"{int(year)}-{int(month):02d}"
    _has_cached_onboarded = (
        not st.session_state.get("onboarded_presence_df", pd.DataFrame()).empty
        and not st.session_state.get("onboarded_vehicle_hours_df", pd.DataFrame()).empty
    )
    _needs_live_fetch = st.session_state.get("onboarded_tab_load_key") != onboarded_tab_load_key

    if _needs_live_fetch and not _has_cached_onboarded:
        # No cache available — must fetch from Azure (first time for this month).
        with st.spinner("Loading onboarded vehicle data for the first time — this may take a minute…"):
            try:
                presence_df_month = fetch_onboarded_model_presence_for_month(
                    sas_url,
                    container_name,
                    int(year),
                    int(month),
                    st.session_state.get("onboarded_vehicle_model_map", {}),
                )
                recent_days = _recent_days_for_lookback(int(year), int(month), lookback_hours=24)
                presence_updates = fetch_onboarded_model_presence_for_days(
                    sas_url,
                    container_name,
                    int(year),
                    int(month),
                    st.session_state.get("onboarded_vehicle_model_map", {}),
                    recent_days,
                )
                st.session_state["onboarded_presence_df"] = merge_model_daily_data(presence_df_month, presence_updates)
                st.session_state["onboarded_vehicle_hours_df"] = fetch_onboarded_vehicle_hours_for_month(
                    sas_url,
                    container_name,
                    int(year),
                    int(month),
                    st.session_state.get("onboarded_vehicle_details_map", {}),
                )
                st.session_state["onboarded_tab_load_key"] = onboarded_tab_load_key
                st.session_state["onboarded_last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                st.session_state.pop("onboarded_error", None)
                # Save newly fetched data to shared cache.
                save_cached_datasets(
                    container_name,
                    int(year),
                    int(month),
                    st.session_state.get("df_results", pd.DataFrame()),
                    st.session_state.get("df_processed", pd.DataFrame()),
                    st.session_state["onboarded_presence_df"],
                    st.session_state["onboarded_vehicle_hours_df"],
                )
            except (ValueError, RuntimeError, urlerror.URLError, urlerror.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
                st.session_state["onboarded_error"] = str(exc)
    elif _needs_live_fetch and _has_cached_onboarded:
        # Cached data available — mark as loaded and silently accept stale data until next Refresh.
        st.session_state["onboarded_tab_load_key"] = onboarded_tab_load_key

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
        total_models_count = 0
        total_variants_count = 0
        if not model_df.empty and "model" in model_df.columns:
            total_models_count = int(model_df["model"].nunique())
        if not variant_df.empty and "model_variant" in variant_df.columns:
            total_variants_count = int(variant_df["model_variant"].nunique())

        summary_col1, summary_col2 = st.columns(2)
        with summary_col1:
            st.markdown(
                f"""
                <div class="onboarded-metric-card" style="border-left:0;">
                    <div class="onboarded-metric-title">Total models</div>
                    <div class="onboarded-metric-value">{total_models_count}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with summary_col2:
            st.markdown(
                f"""
                <div class="onboarded-metric-card" style="border-left:0;">
                    <div class="onboarded-metric-title">Total variants</div>
                    <div class="onboarded-metric-value">{total_variants_count}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

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
                        marker={"color": BOSCH_PRIMARY},
                        text=model_df["count"],
                        textposition="outside",
                        hovertemplate="<b>Model:</b> %{y}<br><b>Onboarded:</b> %{x}<extra></extra>",
                    )
                )
                fig_model.update_layout(
                    title="Onboarded Vehicles by Model",
                    xaxis_title="Vehicle Count",
                    yaxis_title="Model",
                    **PLOTLY_BRAND_LAYOUT,
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
                        marker={"color": BOSCH_TURQUOISE},
                        text=variant_display["count"],
                        textposition="outside",
                        hovertemplate="<b>Model | Variant:</b> %{y}<br><b>Onboarded:</b> %{x}<extra></extra>",
                    )
                )
                fig_variant.update_layout(
                    title="Top 25 Onboarded Model | Variant",
                    xaxis_title="Vehicle Count",
                    yaxis_title="Model | Variant",
                    **PLOTLY_BRAND_LAYOUT,
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
                    line={"color": BOSCH_PRIMARY, "width": 3},
                    marker={"size": 7},
                    hovertemplate="<b>Day:</b> %{x}<br><b>Vehicles Uploaded At Least Once:</b> %{y}<extra></extra>",
                )
            )
            fig_presence_line.update_layout(
                title="Daily Uploaded Vehicles (At Least Once)",
                xaxis_title="Day",
                yaxis_title="Unique Onboarded Vehicle IDs",
                **PLOTLY_BRAND_LAYOUT,
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
            _n_models = len(day_presence)
            _bar_height = max(420, _n_models * 28)
            # Use horizontal bars when there are many models so all labels are visible.
            if _n_models > 10:
                fig_presence = go.Figure()
                fig_presence.add_trace(
                    go.Bar(
                        x=day_presence["count"],
                        y=day_presence["model"],
                        orientation="h",
                        marker={"color": BOSCH_TURQUOISE},
                        text=day_presence["count"],
                        textposition="outside",
                        hovertemplate="<b>Model:</b> %{y}<br><b>Vehicles Present:</b> %{x}<extra></extra>",
                    )
                )
                fig_presence.update_layout(
                    title=f"Onboarded Vehicle IDs Present in Raw Data - Day {int(selected_presence_day)}",
                    xaxis_title="Unique Vehicle IDs Present",
                    yaxis_title="Model",
                    **PLOTLY_BRAND_LAYOUT,
                    height=_bar_height,
                    showlegend=False,
                    yaxis={"autorange": "reversed", "tickfont": {"size": 11}},
                    margin={"l": 160, "r": 60, "t": 50, "b": 50},
                )
            else:
                fig_presence = go.Figure()
                fig_presence.add_trace(
                    go.Bar(
                        x=day_presence["model"],
                        y=day_presence["count"],
                        marker={"color": BOSCH_TURQUOISE},
                        text=day_presence["count"],
                        textposition="outside",
                        hovertemplate="<b>Model:</b> %{x}<br><b>Vehicles Present:</b> %{y}<extra></extra>",
                    )
                )
                fig_presence.update_layout(
                    title=f"Onboarded Vehicle IDs Present in Raw Data - Day {int(selected_presence_day)}",
                    xaxis_title="Model",
                    yaxis_title="Unique Vehicle IDs Present",
                    **PLOTLY_BRAND_LAYOUT,
                    showlegend=False,
                    xaxis={"tickangle": -35, "tickfont": {"size": 11}},
                    margin={"l": 50, "r": 40, "t": 50, "b": 120},
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

                top_n_rows = st.slider(
                    "Rows to display",
                    min_value=10,
                    max_value=50,
                    value=20,
                    step=5,
                    key="onboarded_hours_topn",
                )

                model_vehicles = vehicle_hours_df[vehicle_hours_df["model"] == selected_hours_model].copy()
                model_vehicles = model_vehicles.sort_values(
                    ["operating_hours", "active_days"],
                    ascending=[False, False],
                ).head(top_n_rows)

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
                    with st.expander("View highest operating vehicles table", expanded=True):
                        st.download_button(
                            "Download operating-hours CSV",
                            data=display_df.to_csv(index=False),
                            file_name=f"onboarded_operating_hours_{selected_hours_model}_{int(year)}_{int(month):02d}.csv",
                            mime="text/csv",
                            use_container_width=False,
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
