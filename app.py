from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from analytics import build_summary, filter_listings, sort_listings
from config import CACHE_TTL_MINUTES, DEFAULT_AREA
from scraper import build_speedhome_url, scrape_speedhome_data, is_playwright_available
from utils import (
    export_dataframe,
    format_currency,
    format_decimal,
    format_number,
    make_insight_text,
    validate_url,
)

st.set_page_config(page_title="SPEEDHOME Property Price Intelligence", page_icon="🏠", layout="wide")

AREA_OPTIONS = [
    "Kuala Lumpur",
    "Selangor",
    "Penang",
    "Johor Bahru",
    "Ipoh",
    "Melaka",
    "Kuantan",
    "Kota Kinabalu",
    "Kuching",
]


def initialize_session_state() -> None:
    defaults = {
        "dataframe": pd.DataFrame(),
        "last_area": DEFAULT_AREA,
        "bedroom_filter": "All",
        "furniture_filter": "",
        "property_filter": "",
        "area_filter": "",
        "keyword_filter": "",
        "min_price": 0,
        "max_price": 0,
        "sort_by": "Monthly Rent",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


@st.cache_data(show_spinner=False, ttl=CACHE_TTL_MINUTES * 60)
def cached_scrape(area: str | None = None, url: str | None = None) -> tuple[pd.DataFrame, str]:
    return scrape_speedhome_data(area=area, url=url)


def render_search_panel() -> tuple[str, str, bool]:
    with st.container():
        col1, col2 = st.columns([1, 1])
        with col1:
            area_selection = st.selectbox(
                "Search by Area Name",
                options=AREA_OPTIONS,
                index=AREA_OPTIONS.index(st.session_state.last_area) if st.session_state.last_area in AREA_OPTIONS else 0,
                help="Pick an area to generate a SPEEDHOME URL automatically.",
            )
            search_area = area_selection
            st.caption(f"Suggested SPEEDHOME URL: {build_speedhome_url(area=search_area)}")
        with col2:
            pasted_url = st.text_area("Or paste a SPEEDHOME URL", height=80)

        analyze = st.button("Analyze", type="primary")
    return search_area, pasted_url, analyze


def render_sidebar_filters() -> dict[str, object]:
    with st.sidebar:
        st.header("Filters")
        if st.session_state.dataframe.empty:
            bedroom_options = ["All", "Studio", "1 Bedroom", "2 Bedroom", "3 Bedroom", "4 Bedroom"]
        else:
            bedroom_options = ["All"] + sorted(st.session_state.dataframe["Bedroom"].dropna().astype(str).unique().tolist())
        st.session_state.bedroom_filter = st.selectbox("Bedroom Type", bedroom_options, index=0)
        st.session_state.min_price = st.number_input("Minimum Price (RM)", min_value=0, step=100, value=int(st.session_state.min_price))
        st.session_state.max_price = st.number_input("Maximum Price (RM)", min_value=0, step=100, value=int(st.session_state.max_price))
        st.session_state.furniture_filter = st.text_input("Furniture Status", value=st.session_state.furniture_filter)
        st.session_state.property_filter = st.text_input("Property Name", value=st.session_state.property_filter)
        st.session_state.area_filter = st.text_input("Area", value=st.session_state.area_filter)
        st.session_state.keyword_filter = st.text_input("Search Keyword", value=st.session_state.keyword_filter)
        st.session_state.sort_by = st.selectbox("Sort By", ["Monthly Rent", "Sqft", "Bedroom", "Property Name"], index=0)

    return {
        "bedroom": None if st.session_state.bedroom_filter in {"All", ""} else st.session_state.bedroom_filter,
        "furniture": st.session_state.furniture_filter.strip() or None,
        "property_name": st.session_state.property_filter.strip() or None,
        "area": st.session_state.area_filter.strip() or None,
        "keyword": st.session_state.keyword_filter.strip() or None,
        "min_price": None if int(st.session_state.min_price) <= 0 else int(st.session_state.min_price),
        "max_price": None if int(st.session_state.max_price) <= 0 else int(st.session_state.max_price),
    }


def render_summary_cards(filtered_df: pd.DataFrame) -> None:
    st.subheader("Market Summary")
    cards = st.columns(5)
    metrics = [
        ("Total Listings", len(filtered_df)),
        ("Average Rent", format_currency(filtered_df["Monthly Rent"].mean())),
        ("Median Rent", format_currency(filtered_df["Monthly Rent"].median())),
        ("Average Sqft", format_number(filtered_df["Sqft"].mean())),
        ("Fair Price", format_currency((filtered_df["Monthly Rent"].mean() + filtered_df["Monthly Rent"].median()) / 2)),
    ]
    for card, (label, value) in zip(cards, metrics):
        with card:
            st.metric(label=label, value=value)


def render_visualizations(filtered_df: pd.DataFrame) -> None:
    st.markdown("### Visualizations")
    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        st.plotly_chart(px.histogram(filtered_df, x="Monthly Rent", nbins=20, title="Histogram of Monthly Rent"), width="stretch")
    with chart_col2:
        st.plotly_chart(px.box(filtered_df, x="Bedroom", y="Monthly Rent", title="Box Plot by Bedroom Type"), width="stretch")

    chart_col3, chart_col4 = st.columns(2)
    with chart_col3:
        bedroom_counts = filtered_df["Bedroom"].value_counts().reset_index()
        bedroom_counts.columns = ["Bedroom", "Count"]
        st.plotly_chart(px.pie(bedroom_counts, names="Bedroom", values="Count", title="Bedroom Distribution"), width="stretch")
    with chart_col4:
        avg_rent = filtered_df.groupby("Bedroom")["Monthly Rent"].mean().reset_index()
        st.plotly_chart(px.bar(avg_rent, x="Bedroom", y="Monthly Rent", title="Average Rent by Bedroom Type"), width="stretch")

    st.plotly_chart(px.scatter(filtered_df, x="Sqft", y="Monthly Rent", color="Bedroom", title="Rent vs Sqft", hover_data=["Property Name"]), width="stretch")


def render_listings(filtered_df: pd.DataFrame) -> None:
    st.markdown("### Listings")
    display_df = filtered_df.copy()
    display_df["Monthly Rent"] = display_df["Monthly Rent"].apply(format_currency)
    display_df["Annual Rent"] = display_df["Annual Rent"].apply(format_currency)
    display_df["Sqft"] = display_df["Sqft"].apply(format_number)
    display_df["Price per Sqft"] = display_df["Price per Sqft"].apply(format_decimal)
    display_df["Listing URL"] = display_df["Listing URL"].apply(lambda x: x if x else "")

    st.dataframe(
        display_df[[
            "Listing Title",
            "Property Name",
            "Area",
            "Bedroom",
            "Monthly Rent",
            "Annual Rent",
            "Sqft",
            "Price per Sqft",
            "Furnishing Status",
            "Listing URL",
        ]],
        width="stretch",
        hide_index=True,
        column_config={"Listing URL": st.column_config.LinkColumn("Listing URL", display_text="Open")},
    )


initialize_session_state()

st.title("SPEEDHOME Property Price Intelligence")
st.caption("Malaysia Rental Market Analyzer")

st.info("Enter an area or a SPEEDHOME URL to inspect rental listings, generate insights, and download a report.")

# Notify user when Playwright is not available for live scraping
if not is_playwright_available():
    st.warning(
        "Playwright is not installed or not available in this environment. Live scraping may be disabled — the app will use cached or sample data.\n"
        "To enable live scraping, run: `pip install -r requirements.txt` and `python -m playwright install`."
    )

search_area, pasted_url, analyze = render_search_panel()
filters = render_sidebar_filters()

if analyze:
    if not search_area.strip() and not pasted_url.strip():
        st.warning("Please enter an area or a SPEEDHOME URL.")
        st.stop()
    if pasted_url.strip() and not validate_url(pasted_url.strip()):
        st.warning("The pasted URL does not look valid. Please use a full HTTP or HTTPS link.")
        st.stop()

    with st.spinner("Scraping listings and generating insights..."):
        try:
            if pasted_url.strip():
                df, source_note = cached_scrape(url=pasted_url.strip())
            else:
                df, source_note = cached_scrape(area=search_area.strip())
        except Exception as exc:
            st.error(f"Unable to analyze the selected property page: {exc}")
            st.stop()

    if df.empty:
        st.warning("No listings were found for the selected criteria.")
        st.stop()

    st.session_state.dataframe = df
    st.session_state.last_area = search_area.strip() or "Custom URL"

    summary_df = build_summary(df)
    filtered_df = filter_listings(df, filters)
    filtered_df = sort_listings(filtered_df, st.session_state.sort_by)

    st.info(source_note)
    render_summary_cards(filtered_df)
    st.markdown("### Market Insight")
    st.info(make_insight_text(summary_df, filtered_df))

    st.markdown("### Rental Type Availability")
    availability = {
        "Daily": "Not Available" if filtered_df.empty else "Available" if filtered_df["Listing Title"].str.contains("daily", case=False, na=False).any() else "Not Available",
        "Monthly": "Available",
        "Yearly": "Not Available" if filtered_df.empty else "Available" if filtered_df["Listing Title"].str.contains("yearly", case=False, na=False).any() else "Not Available",
    }
    st.dataframe(pd.DataFrame(availability.items(), columns=["Rental Type", "Status"]), width="stretch", hide_index=True)

    render_visualizations(filtered_df)
    render_listings(filtered_df)

    export_col1, export_col2 = st.columns(2)
    download_area_slug = (search_area.strip() or "market").replace(" ", "_")
    stamp = datetime.now().strftime("%Y%m%d")
    with export_col1:
        csv_data = export_dataframe(filtered_df, "data/export.csv", "csv")
        with open(csv_data, "rb") as fh:
            st.download_button(
                "Download CSV",
                fh,
                file_name=f"SPEEDHOME_{download_area_slug}_{stamp}.csv",
                mime="text/csv",
            )
    with export_col2:
        excel_data = export_dataframe(filtered_df, "data/export.xlsx", "excel")
        with open(excel_data, "rb") as fh:
            st.download_button(
                "Download Excel",
                fh,
                file_name=f"SPEEDHOME_{download_area_slug}_{stamp}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

else:
    st.info("Use the search box or paste a SPEEDHOME URL to begin analysis.")
