import json
import calendar
import os
from datetime import datetime
from collections import defaultdict
import pandas as pd
from azure.storage.blob import ContainerClient


def load_sas_url() -> str:
    env_sas_url = os.getenv("SAS_URL") or os.getenv("AZURE_CONTAINER_SAS_URL")
    if env_sas_url:
        return env_sas_url.strip()

    try:
        with open("config.json", "r", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except (OSError, json.JSONDecodeError):
        config = {}

    config_sas_url = config.get("sas_url") or config.get("SAS_URL")
    if config_sas_url:
        return str(config_sas_url).strip()

    secrets_path = os.path.join("streamlit_deploy", ".streamlit", "secrets.toml")
    if os.path.exists(secrets_path):
        try:
            with open(secrets_path, "r", encoding="utf-8") as secrets_file:
                for line in secrets_file:
                    stripped = line.strip()
                    if stripped.startswith("SAS_URL") and "=" in stripped:
                        return stripped.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass

    return ""


def export_processed_ids_to_excel(sas_url: str, year: int, month: int, output_file: str = "processed_ids.xlsx"):
    """
    Fetch unique sub-partition IDs from result-data path and export to Excel.
    Each row contains: unique_id, day, hours (comma-separated list of hours where it appeared)
    """
    
    if not sas_url:
        print("Error: SAS URL is required.")
        return
    
    container_client = ContainerClient.from_container_url(sas_url)
    now = datetime.now()
    
    if (year, month) > (now.year, now.month):
        print("Error: Selected month is in the future.")
        return
    
    _, num_days = calendar.monthrange(year, month)
    last_day = now.day if (year == now.year and month == now.month) else num_days
    
    # Dictionary to store: {day: {unique_id: set(hours)}}
    data_structure = defaultdict(lambda: defaultdict(set))
    
    print(f"Fetching data from result-data/{year}/{month:02d}/...")
    total_hours = sum(
        (now.hour + 1) if (year == now.year and month == now.month and day == now.day) else 24
        for day in range(1, last_day + 1)
    )
    processed = 0
    
    for day in range(1, last_day + 1):
        end_hour = now.hour if (year == now.year and month == now.month and day == now.day) else 23
        
        for hour in range(end_hour + 1):
            hour_path = f"result-data/{year}/{month:02d}/{day:02d}/{hour:02d}/"
            
            # List all blobs in this hour folder
            for blob in container_client.list_blobs(name_starts_with=hour_path):
                suffix = blob.name[len(hour_path):]
                if "/" in suffix:
                    unique_id = suffix.split("/", 1)[0]
                    data_structure[day][unique_id].add(hour)
            
            processed += 1
            if processed % 10 == 0 or processed == total_hours:
                print(f"Progress: {processed}/{total_hours} hours processed")
    
    # Convert to DataFrame format
    rows = []
    for day in sorted(data_structure.keys()):
        for unique_id in sorted(data_structure[day].keys()):
            hours_list = sorted(list(data_structure[day][unique_id]))
            hours_str = ",".join(str(h) for h in hours_list)
            rows.append({
                "Day": day,
                "Unique_ID": unique_id,
                "Hours": hours_str,
                "Hour_Count": len(hours_list)
            })
    
    df = pd.DataFrame(rows)
    
    if df.empty:
        print("No data found for the specified month/year.")
        return
    
    # Export to Excel
    df.to_excel(output_file, sheet_name="Processed IDs", index=False)
    print(f"\nExcel file created: {output_file}")
    print(f"Total unique IDs: {len(df)}")
    print(f"Total rows: {len(df)}")
    print(f"\nFirst few rows:")
    print(df.head(10))


if __name__ == "__main__":
    sas_url = load_sas_url()
    if not sas_url:
        sas_url = input("Enter SAS URL: ").strip()
    
    year = int(input("Enter Year (e.g., 2026): "))
    month = int(input("Enter Month (1-12): "))
    output_file = input("Enter output Excel filename (default: processed_ids.xlsx): ") or "processed_ids.xlsx"
    
    export_processed_ids_to_excel(sas_url, year, month, output_file)
