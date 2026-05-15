"""Utility functions for configuration, secrets, and helper operations."""

import json
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest
from typing import Any

import streamlit as st


def secret_or_default(key: str, default_value: Any) -> Any:
    """Get secret value from Streamlit secrets or return default.
    
    Supports nested secrets, e.g., [azure] sas_url = "..."
    """
    if key in st.secrets:
        return st.secrets[key]
    
    if "azure" in st.secrets and key.lower() in st.secrets["azure"]:
        return st.secrets["azure"][key.lower()]
    
    return default_value


def get_secret_value(section: str, key: str, default_value: str = "") -> str:
    """Get secret value from a specific section or return default."""
    if key in st.secrets:
        return str(st.secrets[key])
    
    if section in st.secrets and key.lower() in st.secrets[section]:
        return str(st.secrets[section][key.lower()])
    
    return default_value


def load_local_config() -> dict:
    """Load configuration from local config.json file.
    
    Tries multiple candidate paths and returns empty dict if not found.
    """
    candidate_paths = ["config.json", str(Path(__file__).with_name("config.json"))]
    for path in candidate_paths:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def join_url(base_url: str, endpoint: str) -> str:
    """Join base URL and endpoint with proper formatting."""
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def http_json(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    payload: dict | None = None,
    timeout: int = 15,
) -> dict:
    """Make HTTP request and return JSON response.
    
    Args:
        url: Request URL
        method: HTTP method (GET, POST, etc.)
        headers: Optional request headers
        payload: Optional request body (will be JSON-encoded)
        timeout: Request timeout in seconds
    
    Returns:
        Parsed JSON response or empty dict if response is empty
    """
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    
    req_data = None
    if payload is not None:
        req_data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    
    request_obj = urlrequest.Request(
        url=url, data=req_data, headers=req_headers, method=method
    )
    with urlrequest.urlopen(request_obj, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def first_non_empty(source: dict, keys: list[str], default_value: str = "") -> str:
    """Return first non-empty value from dict for given keys.
    
    Args:
        source: Source dictionary
        keys: List of keys to check in order
        default_value: Value to return if all keys are empty
    
    Returns:
        First non-empty value found or default_value
    """
    for key in keys:
        value = source.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return default_value


def extract_registry_records(registry_response: dict) -> list[dict]:
    """Extract vehicle records from various registry response formats.
    
    Handles multiple response structures:
    - Direct list of records
    - Records nested under 'subscriptions'
    - Records nested under 'customers' -> 'subscriptions'
    - Records nested under 'vehicles'
    """
    data = registry_response.get("data", registry_response)
    
    if isinstance(data, dict):
        if isinstance(data.get("subscriptions"), list):
            return data.get("subscriptions", [])
        if isinstance(data.get("customers"), list):
            records: list[dict] = []
            for customer in data.get("customers", []):
                subs = customer.get("subscriptions", []) if isinstance(customer, dict) else []
                if isinstance(subs, list):
                    records.extend(subs)
            return records
        if isinstance(data.get("vehicles"), list):
            return data.get("vehicles", [])
    
    if isinstance(data, list):
        return data
    
    return []


def normalize_vehicle_id(value: str) -> str:
    """Normalize vehicle ID by converting to uppercase alphanumeric only.
    
    Useful for matching vehicle IDs across different formats.
    """
    return "".join(ch for ch in str(value).upper().strip() if ch.isalnum())


def extract_vehicle_id_from_suffix(suffix: str) -> str:
    """Extract vehicle ID from blob suffix path.
    
    Handles formats like:
    - vehicle_id/data.json
    - vehicle_id.json
    - vehicle_id?query=params
    """
    if not suffix:
        return ""
    
    first_segment = suffix.split("/", 1)[0].split("?", 1)[0].strip()
    if first_segment.lower().endswith(".json"):
        first_segment = first_segment[:-5]
    return first_segment.strip()
