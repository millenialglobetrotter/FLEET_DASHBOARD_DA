import calendar
from datetime import datetime

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


def _secret_or_default(key: str, default_value):
    return st.secrets[key] if key in st.secrets else default_value


with st.sidebar:
    st.header("Settings")

    default_sas = _secret_or_default("SAS_URL", "")
    default_container = _secret_or_default("CONTAINER_NAME", "")
    default_year = int(_secret_or_default("DEFAULT_YEAR", datetime.now().year))
    default_month = int(_secret_or_default("DEFAULT_MONTH", datetime.now().month))

    sas_url = st.text_input("SAS URL", value=default_sas, help="Container SAS URL")
    container_name = st.text_input("Container Name", value=default_container)

    c1, c2 = st.columns(2)
    with c1:
        year = st.number_input("Year", value=default_year, min_value=2020, max_value=2035)
    with c2:
        month = st.number_input("Month", value=default_month, min_value=1, max_value=12)

    st.divider()
    st.caption("Use Refresh Data to clear cache and fetch latest data from Azure")


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


if st.button("Refresh Data", use_container_width=False):
    count_vehicles_per_hour_for_month.clear()
    st.rerun()

with st.spinner("Fetching data..."):
    try:
        df_results = count_vehicles_per_hour_for_month(sas_url, container_name, int(year), int(month))
    except Exception as exc:
        st.error(f"Unable to fetch data: {exc}")
        st.stop()

if df_results.empty:
    st.warning("No data available. Check credentials, month/year, and path format.")
    st.stop()

df_results_ist = df_results.copy()
df_results_ist["ist_hour"] = ((df_results_ist["hour"] + 5.5) % 24).astype(int)
df_results_ist["ist_day"] = df_results_ist["day"] + ((df_results_ist["hour"] + 5.5) // 24).astype(int)

available_days = sorted(df_results_ist["ist_day"].unique())

tab1, tab2 = st.tabs(["Daily Drill-down", "Heatmap"])

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
