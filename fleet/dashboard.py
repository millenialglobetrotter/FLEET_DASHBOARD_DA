import json
from pathlib import Path
from typing import Any


def _get_val(obj: dict[str, Any], key: str, fallback: float | dict[str, float] = 0) -> Any:
    if not obj or key not in obj:
        return fallback
    item = obj[key]
    if isinstance(item, dict) and "value" in item:
        return item["value"]
    return fallback


def _parse_time_to_seconds(value: str) -> int:
    if not value:
        return 0
    parts = [int(x) for x in value.split(":")]
    if len(parts) != 3:
        return 0
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def _seconds_to_clock(seconds: float) -> str:
    total = int(round(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours}h:{minutes}m:{secs}s"


def parse_trip_object(data: dict[str, Any], source_name: str) -> dict[str, Any] | None:
    insights = data.get("Score_And_Insights", {})
    metrics = data.get("Metrics_Data", {})
    errors = data.get("ErrorValues", {})

    if isinstance(insights, list) and insights and insights[0] is False:
        return None

    gear_data = _get_val(metrics, "Gear_Detection", {})
    if not isinstance(gear_data, dict):
        gear_data = {}

    return {
        "source": source_name,
        "driver_score": float(insights.get("Driver_Score", 0) or 0),
        "fuel_score": float(insights.get("Fuel_Score", 0) or 0),
        "distance_km": float(_get_val(metrics, "Distance_Travelled", 0) or 0),
        "avg_speed": float(_get_val(metrics, "Average_Speed", 0) or 0),
        "max_speed": float(_get_val(metrics, "Maximum_Speed", 0) or 0),
        "total_fuel_l": float(_get_val(metrics, "Total_Fuel_Consumed", 0) or 0),
        "fuel_economy": float(_get_val(metrics, "Fuel_Economy", 0) or 0),
        "harsh_acc": float(_get_val(metrics, "Harsh_Acceleration", 0) or 0),
        "harsh_brake": float(_get_val(metrics, "Harsh_Braking", 0) or 0),
        "harsh_corner": float(_get_val(metrics, "Harsh_Cornering", 0) or 0),
        "mod_brake": float(_get_val(metrics, "Moderate_Braking", 0) or 0),
        "wrong_gear_km": float(_get_val(metrics, "Distance_Travelled_in_Wrong_Gear", 0) or 0),
        "overspeed_km": float(_get_val(metrics, "Overspeeding_Distance", 0) or 0),
        "coasting_km": float(_get_val(metrics, "Coasting_Distance", 0) or 0),
        "half_clutch_km": float(_get_val(metrics, "Distance_Travelled_With_Half_Clutch", 0) or 0),
        "idle_fuel_l": float(_get_val(metrics, "Additional_Fuel_Consumed_During_Engine_Idling", 0) or 0),
        "overspeed_fuel_l": float(_get_val(metrics, "Additional_Fuel_Consumed_During_Overspeed", 0) or 0),
        "overrev_fuel_l": float(_get_val(metrics, "Additional_Fuel_Consumed_During_Engine_Overreving", 0) or 0),
        "mil_error_km": float(_get_val(metrics, "MIL_Error", 0) or 0),
        "idle_time": str(_get_val(metrics, "Engine_Idling_Duration", "00:00:00") or "00:00:00"),
        "engine_on": str(_get_val(metrics, "Engine_ON_Time", "00:00:00") or "00:00:00"),
        "engine_off": str(_get_val(metrics, "Engine_OFF_Time", "00:00:00") or "00:00:00"),
        "data_loss": str(_get_val(metrics, "Data_Loss_Duration", "00:00:00") or "00:00:00"),
        "start_count": float(_get_val(metrics, "Engine_Start_Count", 0) or 0),
        "stop_count": float(_get_val(metrics, "Engine_Stop_Count", 0) or 0),
        "gear_dist": gear_data,
        "error_signals": list(errors.keys()),
    }


def load_trips_from_run_folder(run_folder: str) -> tuple[list[dict[str, Any]], list[str]]:
    base = Path(run_folder)
    if not base.exists() or not base.is_dir():
        return [], [f"Run folder does not exist: {run_folder}"]

    trips: list[dict[str, Any]] = []
    parse_errors: list[str] = []

    skip_files = {"vehicle_results.json", "no_data_vehicles.json"}
    for path in sorted(base.glob("*.json")):
        if path.name in skip_files:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            trip = parse_trip_object(data, path.name)
            if trip:
                trips.append(trip)
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"{path.name}: {exc}")

    return trips, parse_errors


def aggregate_trips(trips: list[dict[str, Any]]) -> dict[str, Any]:
    if not trips:
        return {}

    total_distance = sum(item["distance_km"] for item in trips)
    total_fuel = sum(item["total_fuel_l"] for item in trips)
    avg_economy = (total_distance / total_fuel) if total_fuel > 0 else 0

    total_harsh_brake = sum(item["harsh_brake"] for item in trips)
    total_harsh_acc = sum(item["harsh_acc"] for item in trips)
    total_harsh_corner = sum(item["harsh_corner"] for item in trips)
    total_harsh = total_harsh_brake + total_harsh_acc + total_harsh_corner

    idle_fuel = sum(item["idle_fuel_l"] for item in trips)
    overspeed_fuel = sum(item["overspeed_fuel_l"] for item in trips)
    overrev_fuel = sum(item["overrev_fuel_l"] for item in trips)
    total_waste = idle_fuel + overspeed_fuel + overrev_fuel

    avg_driver_score = sum(item["driver_score"] for item in trips) / len(trips)
    avg_fuel_score = sum(item["fuel_score"] for item in trips) / len(trips)

    avg_speed = sum(item["avg_speed"] for item in trips) / len(trips)
    max_speed = max(item["max_speed"] for item in trips)

    total_engine_on_sec = sum(_parse_time_to_seconds(item["engine_on"]) for item in trips)
    total_idle_sec = sum(_parse_time_to_seconds(item["idle_time"]) for item in trips)
    idle_ratio = (total_idle_sec / total_engine_on_sec * 100) if total_engine_on_sec > 0 else 0

    top_driver = sorted(trips, key=lambda x: x["driver_score"], reverse=True)[:3]
    bottom_driver = sorted(trips, key=lambda x: x["driver_score"])[:3]
    top_fuel = sorted(trips, key=lambda x: x["fuel_score"], reverse=True)[:3]
    bottom_fuel = sorted(trips, key=lambda x: x["fuel_score"])[:3]

    combined_errors = sorted({signal for trip in trips for signal in trip["error_signals"]})

    return {
        "trip_count": len(trips),
        "total_distance_km": total_distance,
        "total_fuel_l": total_fuel,
        "avg_economy_kmpl": avg_economy,
        "avg_driver_score": avg_driver_score,
        "avg_fuel_score": avg_fuel_score,
        "avg_speed_kmh": avg_speed,
        "max_speed_kmh": max_speed,
        "total_harsh_events": total_harsh,
        "idle_ratio_percent": idle_ratio,
        "total_waste_l": total_waste,
        "waste_split_l": {
            "Idle": idle_fuel,
            "Overspeed": overspeed_fuel,
            "Overrev": overrev_fuel,
        },
        "engine_on_total": _seconds_to_clock(total_engine_on_sec),
        "idle_total": _seconds_to_clock(total_idle_sec),
        "top_driver": top_driver,
        "bottom_driver": bottom_driver,
        "top_fuel": top_fuel,
        "bottom_fuel": bottom_fuel,
        "error_signals": combined_errors,
    }
