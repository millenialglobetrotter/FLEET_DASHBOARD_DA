# Fleet Level Dashboard - Streamlit Deployment

A Streamlit web application for analyzing Azure Blob Storage partition distribution in IST timezone.

## Features

- **Hour-wise Analysis**: View total sub-partitions aggregated across all days for each hour (IST)
- **Day-wise Analysis**: View total sub-partitions for each day (IST)
- **Interactive Drill-down**: Select a specific day to see the hourly breakdown with detailed statistics
- **IST Timezone Conversion**: All times automatically converted from UTC to IST (UTC+5:30)
- **Real-time Statistics**: Key metrics displayed for each view

## Prerequisites

Make sure you have Python 3.8+ installed and the following dependencies:

```bash
pip install streamlit pandas matplotlib azure-storage-blob
```

Or install from requirements.txt (if you create one):

```bash
pip install -r requirements.txt
```

## Installation

1. Navigate to the project directory:
```bash
cd c:\Users\LAG1BAN\Fleet_Level_dashboard
```

2. Install required packages:
```bash
pip install streamlit pandas matplotlib azure-storage-blob
```

## Running the Dashboard

Execute the following command in your terminal:

```bash
streamlit run streamlit_dashboard.py
```

This will:
- Start a local Streamlit server (usually on http://localhost:8501)
- Automatically open the dashboard in your default web browser

## Configuration

The dashboard includes a **Configuration** section in the left sidebar where you can:
- **SAS URL**: Paste your Azure Blob Storage container SAS URL
- **Container Name**: Specify the container name
- **Year**: Select the year for analysis (default: 2026)
- **Month**: Select the month for analysis (default: 5 - May)

**Default values are pre-filled from your notebook configuration.**

## Dashboard Layout

### 📊 Hour-wise Analysis Tab
- Bar chart showing total sub-partitions for each hour (0-23 IST)
- Statistics: total, average, peak hour, and maximum sub-partitions

### 📅 Day-wise Analysis Tab
- Bar chart showing total sub-partitions for each day
- Statistics: total, average, peak day, and maximum sub-partitions

### 🔍 Drill-down by Day Tab
- Interactive dropdown to select a specific day
- Hourly breakdown for the selected day
- Detailed statistics for that day
- Table view of hourly data

## Features Explained

- **Caching**: Blob data is cached to avoid repeated Azure API calls when changing visualizations
- **Progress Indicator**: Shows progress while fetching data from Azure Blob Storage
- **Responsive Design**: Works on desktop, tablet, and mobile browsers
- **IST Timezone**: All times are in Indian Standard Time (UTC+5:30)

## Troubleshooting

### "ModuleNotFoundError"
Install the missing package:
```bash
pip install [package_name]
```

### "Invalid SAS URL"
Ensure your SAS URL is in the correct format:
```
https://<account_name>.blob.core.windows.net/<container_name>?<sas_token>
```

### Slow Performance
- The first load will fetch all data from Azure Blob Storage
- Subsequent changes to visualizations use cached data
- Clear cache with `Ctrl+C` and restart if you need fresh data

## Deployment Options

To deploy this dashboard online:

1. **Streamlit Cloud** (Free):
   - Push your code to GitHub
   - Deploy via https://share.streamlit.io

2. **Docker**:
   - Create a Dockerfile with Streamlit and dependencies
   - Deploy to any container platform

3. **Traditional Server**:
   - Install on your server
   - Use reverse proxy (nginx/Apache) with systemd service

## File Structure

```
Fleet_Level_dashboard/
├── streamlit_dashboard.py    # Main Streamlit app
├── blob_count.ipynb          # Original Jupyter notebook
├── requirements.txt          # Python dependencies
└── README.md                 # This file
```

## Notes

- Data is filtered to show only up to the current hour for the current month
- All times are converted from UTC to IST automatically
- The dashboard queries Azure Blob Storage directly
- Ensure your SAS token has adequate permissions and hasn't expired
