# Fleet Level Dashboard (Streamlit Deploy Package)

This folder is deployment-ready for GitHub + Streamlit Community Cloud.

## Files

- app.py: Streamlit app entrypoint
- requirements.txt: Python dependencies
- .streamlit/config.toml: Streamlit runtime settings
- .streamlit/secrets.toml.example: secrets template (do not commit real secrets)
- .gitignore: excludes local/env/secret files

## Local Run

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the app:

```bash
streamlit run app.py
```

## Deploy to Streamlit Community Cloud

1. Push this folder to a GitHub repo.
2. In Streamlit Cloud, create a new app and select:
   - Repository: your repo
   - Branch: your branch
   - Main file path: streamlit_deploy/app.py
3. In Streamlit Cloud app settings, add secrets using values from .streamlit/secrets.toml.example.
4. Deploy.

## Notes

- Keep SAS tokens only in Streamlit secrets, not in source files.
- Use the in-app Refresh Data button to clear cache and fetch fresh Azure data.
