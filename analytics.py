from __future__ import annotations

import pandas as pd
import numpy as np

from utils import parse_bedroom


def build_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[
            "Bedroom Type",
            "Number of Units",
            "Average Rent",
            "Median Rent",
            "Mode Rent",
            "Minimum Rent",
            "Maximum Rent",
            "Fair Price",
            "Average Sqft",
            "Average Price per Sqft",
        ])

    temp = df.copy()
    temp["Bedroom Type"] = temp["Bedroom"].apply(parse_bedroom)
    summary_rows: list[dict[str, object]] = []
    for bedroom_type in ["Studio", "1 Bedroom", "2 Bedroom", "3 Bedroom", "4 Bedroom"]:
        subset = temp[temp["Bedroom Type"] == bedroom_type]
        if subset.empty:
            continue
        summary_rows.append(
            {
                "Bedroom Type": bedroom_type,
                "Number of Units": int(len(subset)),
                "Average Rent": round(float(subset["Monthly Rent"].mean()), 2),
                "Median Rent": round(float(subset["Monthly Rent"].median()), 2),
                "Mode Rent": (
                    round(float(subset["Monthly Rent"].mode().iloc[0]), 2)
                    if not subset["Monthly Rent"].mode().empty
                    else None
                ),
                "Minimum Rent": round(float(subset["Monthly Rent"].min()), 2),
                "Maximum Rent": round(float(subset["Monthly Rent"].max()), 2),
                "Fair Price": (
                    round(
                        float(
                            (subset["Monthly Rent"].mean() + subset["Monthly Rent"].median()) / 2
                        ),
                        2,
                    )
                ),
                "Average Sqft": round(float(subset["Sqft"].mean()), 2),
                "Average Price per Sqft": round(float(subset["Price per Sqft"].mean()), 2),
            }
        )
    return pd.DataFrame(summary_rows)


def filter_listings(df: pd.DataFrame, filters: dict[str, object]) -> pd.DataFrame:
    filtered = df.copy()
    if filtered.empty:
        return filtered

    filtered["Furnishing Status"] = filtered["Furnishing Status"].fillna("")
    filtered["Property Name"] = filtered["Property Name"].fillna("")
    filtered["Area"] = filtered["Area"].fillna("")
    filtered["Listing Title"] = filtered["Listing Title"].fillna("")

    if filters.get("bedroom"):
        filtered = filtered[filtered["Bedroom"] == filters["bedroom"]]
    if filters.get("furniture"):
        filtered = filtered[filtered["Furnishing Status"].str.contains(filters["furniture"], case=False, na=False)]
    if filters.get("property_name"):
        filtered = filtered[filtered["Property Name"].str.contains(filters["property_name"], case=False, na=False)]
    if filters.get("area"):
        filtered = filtered[filtered["Area"].str.contains(filters["area"], case=False, na=False)]
    if filters.get("keyword"):
        keyword = str(filters["keyword"])
        mask = np.logical_or.reduce([
            filtered["Listing Title"].str.contains(keyword, case=False, na=False),
            filtered["Property Name"].str.contains(keyword, case=False, na=False),
            filtered["Area"].str.contains(keyword, case=False, na=False),
        ])
        filtered = filtered[mask]
    min_price = filters.get("min_price")
    max_price = filters.get("max_price")
    if min_price is not None:
        filtered = filtered[filtered["Monthly Rent"] >= float(min_price)]
    if max_price is not None:
        filtered = filtered[filtered["Monthly Rent"] <= float(max_price)]
    return filtered


def sort_listings(df: pd.DataFrame, sort_by: str) -> pd.DataFrame:
    if df.empty:
        return df
    sort_map = {
        "Monthly Rent": "Monthly Rent",
        "Sqft": "Sqft",
        "Bedroom": "Bedroom",
        "Property Name": "Property Name",
    }
    if sort_by in sort_map:
        return df.sort_values(by=sort_map[sort_by], ascending=False if sort_by != "Property Name" else True)
    return df
