import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import calendar
from datetime import datetime
from azure.storage.blob import ContainerClient

# Page configuration
st.set_page_config(
    page_title="Fleet Dashboard - Vehicle Analytics", 
    layout="wide", 
    initial_sidebar_state="collapsed"
)

# Custom CSS for better styling and compact layout
st.markdown("""
    <style>
    /* Give content enough top padding so the header bar doesn't overlap it */
    .block-container {
        padding: 4rem 2rem 0.5rem 2rem !important;
        max-width: 100% !important;
    }
    [data-testid="stVerticalBlock"] {
        gap: 0.5rem !important;
    }
    footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

st.title("🚗 Fleet Level Dashboard")
st.caption("Real-time vehicle count analysis across hourly partitions")

# ============================================================================
# SIDEBAR CONFIGURATION
# ============================================================================
with st.sidebar:
    st.header("⚙️ Settings")
    
    sas_url = st.text_input(
        "SAS URL",
        value="https://condenseconnector.blob.core.windows.net/bosch-da?sp=racwl&st=2026-05-08T07:05:34Z&se=2026-06-24T15:20:34Z&spr=https&sv=2025-11-05&sr=c&sig=pGv2c9HD7iIfuC019S9ZNE7MSWjF5DWKCgKEVbBRG6g%3D",
        help="Container SAS URL"
    )
    
    container_name = st.text_input(
        "Container Name",
        value="bosch-da"
    )
    
    col1, col2 = st.columns(2)
    with col1:
        year = st.number_input("Year", value=2026, min_value=2020, max_value=2030)
    with col2:
        month = st.number_input("Month", value=5, min_value=1, max_value=12)
    
    st.divider()
    st.caption(
        "📊 Monitors vehicle counts across hourly data partitions in IST timezone"
    )

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

@st.cache_data
def count_vehicles_per_hour_for_month(sas_url: str, container_name: str, year: int, month: int):
    """
    Counts available vehicles inside each hourly partition path.
    """
    data = []
    
    try:
        container_client = ContainerClient.from_container_url(sas_url)
        now = datetime.now()

        if (year, month) > (now.year, now.month):
            st.warning(f"Target {year}-{month:02d} is in the future. No data to process.")
            return pd.DataFrame()

        with st.spinner(f"📡 Fetching vehicle data for {year}-{month:02d}..."):
            _, num_days = calendar.monthrange(year, month)
            last_day = now.day if (year == now.year and month == now.month) else num_days
            
            progress_bar = st.progress(0)
            total_iterations = last_day * 24

            for day in range(1, last_day + 1):
                end_hour = now.hour if (year == now.year and month == now.month and day == now.day) else 23
                
                for hour in range(end_hour + 1):
                    hour_path = f"raw-data/{year}/{month:02d}/{day:02d}/{hour:02d}/"

                    vehicles = set()
                    for blob in container_client.list_blobs(name_starts_with=hour_path):
                        suffix = blob.name[len(hour_path):]
                        if "/" in suffix:
                            vehicles.add(suffix.split("/", 1)[0])

                    vehicle_count = len(vehicles)
                    
                    data.append({
                        'day': day,
                        'hour': hour,
                        'vehicle_count': vehicle_count
                    })
                    
                    progress_bar.progress(min((day * 24 + hour) / total_iterations, 1.0))

        progress_bar.empty()
        return pd.DataFrame(data)

    except Exception as e:
        st.error(f"❌ Error fetching data: {e}")
        return pd.DataFrame()

# ============================================================================
# MAIN DASHBOARD
# ============================================================================

# Fetch data
df_results = count_vehicles_per_hour_for_month(sas_url, container_name, int(year), int(month))

if df_results.empty:
    st.warning("📭 No data available for the selected period.")
    st.stop()

# Convert UTC to IST
df_results_ist = df_results.copy()
df_results_ist['ist_hour'] = ((df_results_ist['hour'] + 5.5) % 24).astype(int)
df_results_ist['ist_day'] = df_results_ist['day'] + ((df_results_ist['hour'] + 5.5) // 24).astype(int)

# Get available days
available_days = sorted(df_results_ist['ist_day'].unique())

# Tabs
tab1, tab2 = st.tabs(["📊 Daily Drill-down", "🌡️ Heatmap"])

# ============================================================================
# TAB 1: DAILY DRILL-DOWN
# ============================================================================
with tab1:
    # Day selector + refresh button in one row
    col1, col2, col3 = st.columns([0.15, 0.6, 0.25])

    with col1:
        st.markdown("**Day:**")

    with col2:
        selected_day = st.selectbox(
            "Select Day (IST):",
            options=available_days,
            format_func=lambda x: f"Day {int(x)}",
            key="day_selector",
            label_visibility="collapsed"
        )

    with col3:
        if st.button("🔄 Refresh Data", use_container_width=True):
            count_vehicles_per_hour_for_month.clear()
            st.rerun()

    # Filter data for selected day
    day_data = df_results_ist[df_results_ist['ist_day'] == selected_day].copy()

    if not day_data.empty:
        # Group by hour
        hourly_data = day_data.groupby('hour')['vehicle_count'].sum().reset_index()
        hourly_data['ist_hour'] = ((hourly_data['hour'] + 5.5) % 24).astype(int)
        hourly_data = hourly_data.sort_values('ist_hour')

        fig = go.Figure()

        fig.add_trace(go.Bar(
            x=hourly_data['ist_hour'],
            y=hourly_data['vehicle_count'],
            marker=dict(color='steelblue'),
            text=hourly_data['vehicle_count'],
            textposition='outside',
            hovertemplate='<b>Hour:</b> %{x}:00 IST<br><b>Vehicles:</b> %{y}<extra></extra>'
        ))

        fig.update_layout(
            title=f"Vehicle Count by Hour - Day {int(selected_day)}, {int(year)}-{int(month):02d} (IST)",
            xaxis_title="Hour of Day (IST)",
            yaxis_title="Vehicle Count",
            hovermode='x unified',
            template='plotly_white',
            autosize=True,
            showlegend=False,
            xaxis=dict(tickmode='linear', tick0=0, dtick=1),
            margin=dict(l=50, r=90, t=50, b=50)
        )

        st.plotly_chart(fig, use_container_width=True, config={'responsive': True, 'displayModeBar': True})

    else:
        st.info(f"ℹ️ No data available for Day {int(selected_day)}")

# ============================================================================
# TAB 2: HEATMAP
# ============================================================================
with tab2:
    # Build pivot: rows = days, cols = IST hours
    pivot = df_results_ist.pivot_table(
        index='ist_day',
        columns='ist_hour',
        values='vehicle_count',
        fill_value=0,
        aggfunc='sum'
    )

    # Ensure all 24 hours are present as columns
    all_hours = list(range(24))
    for h in all_hours:
        if h not in pivot.columns:
            pivot[h] = 0
    pivot = pivot[all_hours]

    fig_heat = go.Figure(go.Heatmap(
        z=pivot.values,
        x=[f"{h:02d}:00" for h in all_hours],
        y=[f"Day {int(d)}" for d in pivot.index],
        colorscale='YlOrRd',
        text=pivot.values,
        texttemplate="%{text}",
        hovertemplate='<b>Day:</b> %{y}<br><b>Hour:</b> %{x} IST<br><b>Vehicles:</b> %{z}<extra></extra>',
        colorbar=dict(title="Vehicles")
    ))

    fig_heat.update_layout(
        title=f"Vehicle Count Heatmap - {int(year)}-{int(month):02d} (IST)",
        xaxis_title="Hour of Day (IST)",
        yaxis_title="Day",
        template='plotly_white',
        autosize=True,
        margin=dict(l=80, r=80, t=50, b=50),
        yaxis=dict(autorange='reversed')
    )

    st.plotly_chart(fig_heat, use_container_width=True, config={'responsive': True, 'displayModeBar': True})

