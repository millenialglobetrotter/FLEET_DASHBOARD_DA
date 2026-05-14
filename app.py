from datetime import datetime, time, timezone
from pathlib import Path
import json

import pandas as pd
import streamlit as st

from fleet.dashboard import aggregate_trips, load_trips_from_run_folder, parse_trip_object
from fleet.pipeline import get_default_ist_window, run_pipeline


st.set_page_config(page_title="Fleet Analytics", layout="wide")
st.title("Fleet Analytics Automation")
st.caption("Run fetch pipeline and explore trip metrics in one place.")

st.sidebar.header("Pipeline Settings")
config_path = st.sidebar.text_input("Config path", value="config.json")
output_root = st.sidebar.text_input("Output root", value="data/runs")
make_filter = st.sidebar.text_input("Vehicle make filter", value="SML")
cutoff_date = st.sidebar.date_input("Subscription cutoff date", value=datetime(2026, 5, 9).date())

fetch_tab, dashboard_tab = st.tabs(["Fetch Data", "Dashboard"])

with fetch_tab:
    st.subheader("Run Data Fetch")
    defaults = get_default_ist_window()
    default_from = datetime.strptime(defaults["from_date"], "%Y-%m-%d %H:%M:%S")
    default_to = datetime.strptime(defaults["to_date"], "%Y-%m-%d %H:%M:%S")

    col1, col2 = st.columns(2)
    with col1:
        from_date = st.date_input("From date", value=default_from.date(), key="from_date")
        from_time = st.time_input("From time", value=default_from.time(), key="from_time")
    with col2:
        to_date = st.date_input("To date", value=default_to.date(), key="to_date")
        to_time = st.time_input("To time", value=default_to.time(), key="to_time")

    if st.button("Run pipeline", type="primary"):
        from_ts = datetime.combine(from_date, from_time).strftime("%Y-%m-%d %H:%M:%S")
        to_ts = datetime.combine(to_date, to_time).strftime("%Y-%m-%d %H:%M:%S")
        cutoff_dt = datetime.combine(cutoff_date, time.min, tzinfo=timezone.utc)

        try:
            with st.spinner("Fetching and processing vehicle data..."):
                report = run_pipeline(
                    config_path=config_path,
                    from_date=from_ts,
                    to_date=to_ts,
                    make=make_filter,
                    cutoff_date=cutoff_dt,
                    output_root=output_root,
                )

            st.success("Pipeline completed")
            metric_cols = st.columns(4)
            metric_cols[0].metric("Eligible", report["eligible_vehicle_count"])
            metric_cols[1].metric("Processed", report["processed_vehicle_count"])
            metric_cols[2].metric("Success", report["success_count"])
            metric_cols[3].metric("Failures", report["failure_count"])

            st.write("Output folder:", report["output_dir"])
            st.write("Summary file:", report["summary_file"])
        except Exception as exc:  # noqa: BLE001
            st.error(f"Pipeline failed: {exc}")

with dashboard_tab:
    st.subheader("Dashboard")
    source_mode = st.radio("Data source", ["Load run folder", "Upload JSON files"], horizontal=True)

    trips = []
    parse_errors = []

    if source_mode == "Load run folder":
        root = Path(output_root)
        run_folders = sorted([p for p in root.glob("*") if p.is_dir()], reverse=True)
        if not run_folders:
            st.info("No run folders found. Run pipeline first.")
        else:
            selected_run = st.selectbox("Select run folder", [str(p) for p in run_folders])
            trips, parse_errors = load_trips_from_run_folder(selected_run)
    else:
        uploaded = st.file_uploader("Upload trip JSON files", type=["json"], accept_multiple_files=True)
        if uploaded:
            for file in uploaded:
                try:
                    data = json.loads(file.getvalue().decode("utf-8"))
                    trip = parse_trip_object(data, file.name)
                    if trip:
                        trips.append(trip)
                except Exception as exc:  # noqa: BLE001
                    parse_errors.append(f"{file.name}: {exc}")

    if parse_errors:
        st.warning("Some files could not be parsed")
        st.dataframe(pd.DataFrame({"error": parse_errors}), use_container_width=True)

    if trips:
        summary = aggregate_trips(trips)

        kpi_cols = st.columns(5)
        kpi_cols[0].metric("Trips", summary["trip_count"])
        kpi_cols[1].metric("Distance", f"{summary['total_distance_km']:.1f} km")
        kpi_cols[2].metric("Fuel", f"{summary['total_fuel_l']:.1f} L")
        kpi_cols[3].metric("Avg Economy", f"{summary['avg_economy_kmpl']:.1f} km/L")
        kpi_cols[4].metric("Harsh Events", f"{summary['total_harsh_events']:.0f}")

        score_cols = st.columns(4)
        score_cols[0].metric("Avg Driver Score", f"{summary['avg_driver_score']:.2f}")
        score_cols[1].metric("Avg Fuel Score", f"{summary['avg_fuel_score']:.2f}")
        score_cols[2].metric("Max Speed", f"{summary['max_speed_kmh']:.0f} km/h")
        score_cols[3].metric("Idle Ratio", f"{summary['idle_ratio_percent']:.1f}%")

        st.markdown("### Fuel Waste Split")
        waste_df = pd.DataFrame(
            [{"source": key, "litres": val} for key, val in summary["waste_split_l"].items()]
        ).set_index("source")
        st.bar_chart(waste_df)

        board_cols = st.columns(2)
        with board_cols[0]:
            st.markdown("### Top Driver Scores")
            st.dataframe(
                pd.DataFrame(summary["top_driver"])[["source", "driver_score", "fuel_score", "distance_km"]],
                use_container_width=True,
            )
            st.markdown("### Bottom Driver Scores")
            st.dataframe(
                pd.DataFrame(summary["bottom_driver"])[["source", "driver_score", "fuel_score", "distance_km"]],
                use_container_width=True,
            )

        with board_cols[1]:
            st.markdown("### Top Fuel Scores")
            st.dataframe(
                pd.DataFrame(summary["top_fuel"])[["source", "fuel_score", "driver_score", "distance_km"]],
                use_container_width=True,
            )
            st.markdown("### Bottom Fuel Scores")
            st.dataframe(
                pd.DataFrame(summary["bottom_fuel"])[["source", "fuel_score", "driver_score", "distance_km"]],
                use_container_width=True,
            )

        st.markdown("### Engine Time")
        st.write(f"Engine ON total: {summary['engine_on_total']}")
        st.write(f"Idle total: {summary['idle_total']}")

        st.markdown("### Data Quality Signals")
        if summary["error_signals"]:
            st.dataframe(pd.DataFrame({"signal": summary["error_signals"]}), use_container_width=True)
        else:
            st.info("No error signals found in selected data.")
    elif source_mode == "Upload JSON files":
        st.info("Upload one or more trip JSON files to visualize metrics.")
