from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import pandas as pd


def format_currency(value: Any) -> str:
    if pd.isna(value):
        return "Not Available"
    return f"RM{float(value):,.0f}"


def format_number(value: Any) -> str:
    if pd.isna(value):
        return "Not Available"
    return f"{float(value):,.0f}"


def format_decimal(value: Any) -> str:
    if pd.isna(value):
        return "Not Available"
    return f"{float(value):,.2f}"


def validate_url(url: str) -> bool:
    pattern = re.compile(r"^https?://.+", re.IGNORECASE)
    return bool(pattern.match(url.strip()))


def parse_bedroom(value: Any) -> str:
    if pd.isna(value):
        return "Unknown"
    text = str(value).strip().lower()
    if "studio" in text or "studi" in text:
        return "Studio"
    if re.search(r"\b(\d+)\s*bed", text):
        match = re.search(r"\b(\d+)\s*bed", text)
        if match:
            return f"{int(match.group(1))} Bedroom"
    return text.title()


def parse_price(value: Any) -> float:
    if pd.isna(value):
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    text = re.sub(r"(?i)rm", "", text)
    text = text.replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return float("nan")
    try:
        return float(match.group(0))
    except ValueError:
        return float("nan")


def parse_sqft(value: Any) -> float:
    if pd.isna(value):
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    text = re.sub(r"(?i)sqft", "", text)
    text = text.replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return float("nan")
    try:
        return float(match.group(0))
    except ValueError:
        return float("nan")


def export_dataframe(df: pd.DataFrame, file_path: str, fmt: str) -> str:
    if fmt == "csv":
        df.to_csv(file_path, index=False)
    elif fmt == "excel":
        df.to_excel(file_path, index=False, engine="openpyxl")
    return file_path


def build_download_filename(area_name: str, fmt: str) -> str:
    area_slug = re.sub(r"[^a-zA-Z0-9]+", "_", area_name).strip("_") or "market"
    stamp = datetime.now().strftime("%Y%m%d")
    return f"SPEEDHOME_{area_slug}_{stamp}.{fmt}"


def make_insight_text(summary_df: pd.DataFrame, listings_df: pd.DataFrame) -> str:
    if listings_df.empty:
        return "No listings were available for analysis."

    avg_rent = float(listings_df["Monthly Rent"].mean())
    avg_sqft = float(listings_df["Sqft"].mean())
    price_per_sqft = float(listings_df["Price per Sqft"].mean())
    largest = float(listings_df["Sqft"].max())
    bedroom_mode = listings_df["Bedroom"].mode()
    dominant_bedroom = str(bedroom_mode.iloc[0]) if not bedroom_mode.empty else "Unknown"
    below_average_count = int((listings_df["Monthly Rent"] < avg_rent).sum())

    return (
        f"Average rent is RM{avg_rent:,.0f}.\n"
        f"Most listings are {dominant_bedroom} units.\n"
        f"Average size is {avg_sqft:,.0f} sqft.\n"
        f"{below_average_count} units sit below the market average.\n"
        f"Largest property found is {largest:,.0f} sqft.\n"
        f"Average price per sqft is RM{price_per_sqft:.2f}."
    )
