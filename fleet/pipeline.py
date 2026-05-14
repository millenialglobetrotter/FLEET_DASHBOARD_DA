import json
import logging
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .auth import get_access_token
from .config import load_config
from .decrypt import decrypt_prediction_data
from .prediction import fetch_prediction
from .registry import get_filtered_vehicle_ids


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def get_default_ist_window(now: datetime | None = None) -> dict[str, str]:
    """Return yesterday 12:00:00 to today 08:00:00 in IST."""
    ist = timezone(timedelta(hours=5, minutes=30))
    current = now.astimezone(ist) if now else datetime.now(ist)

    yesterday = current - timedelta(days=1)
    start = yesterday.replace(hour=12, minute=0, second=0, microsecond=0)
    end = datetime.combine(current.date(), time(8, 0, 0), tzinfo=ist)

    return {
        "from_date": start.strftime("%Y-%m-%d %H:%M:%S"),
        "to_date": end.strftime("%Y-%m-%d %H:%M:%S"),
        "folder_date": yesterday.strftime("%Y-%m-%d"),
    }


def run_pipeline(
    config_path: str = "config.json",
    from_date: str | None = None,
    to_date: str | None = None,
    make: str = "SML",
    cutoff_date: datetime | None = None,
    output_root: str = "data/runs",
) -> dict[str, Any]:
    config = load_config(config_path)
    window = get_default_ist_window()

    from_ts = from_date or window["from_date"]
    to_ts = to_date or window["to_date"]

    try:
        folder_date = datetime.strptime(from_ts, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
    except ValueError:
        folder_date = window["folder_date"]

    output_dir = Path(output_root) / folder_date
    output_dir.mkdir(parents=True, exist_ok=True)

    token = get_access_token(config)
    vehicle_ids = get_filtered_vehicle_ids(
        config=config,
        token=token,
        make=make,
        cutoff_date=cutoff_date,
    )

    results: list[dict[str, Any]] = []
    no_data: list[dict[str, str]] = []

    for vehicle_id in vehicle_ids:
        entry: dict[str, Any] = {
            "vehicle_id": vehicle_id,
            "prediction_status": None,
            "decrypted_data": None,
            "error": None,
        }

        try:
            prediction_response = fetch_prediction(
                config=config,
                token=token,
                vehicle_id=vehicle_id,
                from_date=from_ts,
                to_date=to_ts,
            )

            entry["prediction_status"] = prediction_response.status_code
            if prediction_response.status_code != 200:
                entry["error"] = prediction_response.text
                no_data.append({"vehicle_id": vehicle_id, "error_reason": entry["error"]})
                results.append(entry)
                continue

            prediction_json = prediction_response.json()
            encrypted_data = prediction_json.get("Data")
            if not encrypted_data:
                entry["error"] = "No 'Data' field found in prediction response."
                no_data.append({"vehicle_id": vehicle_id, "error_reason": entry["error"]})
                results.append(entry)
                continue

            decrypted = decrypt_prediction_data(config, encrypted_data)
            entry["decrypted_data"] = decrypted

            file_path = output_dir / f"{vehicle_id}.json"
            with file_path.open("w", encoding="utf-8") as file_handle:
                json.dump(decrypted, file_handle, indent=4)

        except Exception as exc:  # noqa: BLE001 - keep per-vehicle failures isolated
            entry["error"] = str(exc)
            no_data.append({"vehicle_id": vehicle_id, "error_reason": entry["error"]})

        results.append(entry)

    summary_path = output_dir / "vehicle_results.json"
    with summary_path.open("w", encoding="utf-8") as summary_handle:
        json.dump(results, summary_handle, indent=4)

    no_data_path = output_dir / "no_data_vehicles.json"
    with no_data_path.open("w", encoding="utf-8") as no_data_handle:
        json.dump(no_data, no_data_handle, indent=4)

    success_count = sum(1 for item in results if item.get("decrypted_data") is not None)

    report = {
        "from_date": from_ts,
        "to_date": to_ts,
        "output_dir": str(output_dir),
        "summary_file": str(summary_path),
        "no_data_file": str(no_data_path),
        "eligible_vehicle_count": len(vehicle_ids),
        "processed_vehicle_count": len(results),
        "success_count": success_count,
        "failure_count": len(results) - success_count,
        "no_data_count": len(no_data),
    }

    logging.info("Pipeline completed: %s", report)
    return report
