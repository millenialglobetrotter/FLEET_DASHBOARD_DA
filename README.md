# Fleet Analytics Streamlit MVP

This project now includes a modular pipeline and a Streamlit dashboard.

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Set sensitive values as environment variables (recommended):

- `FLEET_AUTH_CLIENT_ID`
- `FLEET_AUTH_CLIENT_SECRET`
- `FLEET_DECRYPT_CLIENT_ID`
- `FLEET_DECRYPT_CLIENT_SECRET`

If environment values are not set, values from `config.json` are used.

## Run Streamlit App

```bash
streamlit run app.py
```

## What the App Does

- Fetches and decrypts vehicle data through API calls
- Saves run output into `data/runs/YYYY-MM-DD/`
- Displays KPI and leaderboard metrics from run files or uploaded JSON files

## CLI Compatibility

You can continue using the existing Python script entrypoint:

```bash
python get_vehicle_dettails.py
```

It now delegates to the modular pipeline.
