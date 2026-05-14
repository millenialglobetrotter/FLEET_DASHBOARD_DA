import calendar
from datetime import datetime, timedelta
import json
import os
from pathlib import Path

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
    sas_url: str, container_name: str, year: int, month: int, lookback_hours: int = 6
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


def fetch_recent_hours(sas_url: str, container_name: str, year: int, month: int, lookback_hours: int = 6) -> pd.DataFrame:
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
                    recent_df = fetch_recent_hours(sas_url, container_name, int(year), int(month), lookback_hours=6)
                    recent_processed = fetch_recent_processed_days(
                        sas_url, container_name, int(year), int(month), lookback_hours=6
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

if st.button("Refresh Data", use_container_width=False):
    with st.spinner("Refreshing recent hours..."):
        try:
            recent_df = fetch_recent_hours(sas_url, container_name, int(year), int(month), lookback_hours=6)
            recent_processed = fetch_recent_processed_days(
                sas_url, container_name, int(year), int(month), lookback_hours=6
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
        except Exception as exc:
            st.error(f"Unable to refresh recent hours: {exc}")
    st.rerun()

if "last_refresh" in st.session_state:
    st.caption(f"Last refresh: {st.session_state['last_refresh']} (recent hours only)")
if "cache_loaded_at" in st.session_state:
    st.caption(f"Shared cache updated at: {st.session_state['cache_loaded_at']}")

df_results = st.session_state["df_results"]

if df_results.empty:
    st.warning("No data available. Check credentials, month/year, and path format.")
    st.stop()

df_results_ist = df_results.copy()
df_results_ist["ist_hour"] = ((df_results_ist["hour"] + 5.5) % 24).astype(int)
df_results_ist["ist_day"] = df_results_ist["day"] + ((df_results_ist["hour"] + 5.5) // 24).astype(int)

available_days = sorted(df_results_ist["ist_day"].unique())

tab1, tab2, tab3 = st.tabs(["Daily Drill-down", "Heatmap", "Vehicles Processed"])

with tab1:
    l, m, r = st.columns([0.15, 0.6, 0.25])
    with l:
        st.markdown("**Day**")
    with m:
        selected_day = st.selectbox(
            "Select Day (IST)",
            options=available_days,
            format_func=lambda x: f"Day {int(x)}",
            label_visibility="collapsed",
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

with tab2:
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

with tab3:
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
