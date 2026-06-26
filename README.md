# SPEEDHOME Property Price Intelligence

## Project Overview
SPEEDHOME Property Price Intelligence is a production-ready Streamlit dashboard for analyzing Malaysian rental market listings from SPEEDHOME. It allows users to search by area or paste a SPEEDHOME URL, scrape listings, generate summary statistics, visualize market trends, and export the results.

## Features
- Search by area name or pasted SPEEDHOME URL
- Responsive Streamlit UI with filters and sorting
- Robust scraper with graceful error handling
- Summary cards, market insights, and Plotly visualizations
- CSV and Excel export support
- Caching to avoid repeated scraping

## Project Structure
- app.py: Streamlit entry point and dashboard UI
- scraper.py: Scraping logic using requests and BeautifulSoup
- analytics.py: Summary metrics and filtering logic
- utils.py: Reusable helpers for formatting, parsing, and exports
- config.py: Configuration constants and request settings
- requirements.txt: Project dependencies
- data/: Output and export files
- assets/: Static assets

## Installation
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Running Locally
```bash
streamlit run app.py
```

## Deployment
The app is ready for Streamlit Cloud. Add the project repository, set the Python version to 3.11, and ensure requirements.txt is present.

## Tech Stack
- Python 3.11
- Streamlit
- Requests
- BeautifulSoup4
- Pandas
- Plotly
- OpenPyXL
- NumPy

## Screenshots
Placeholder for screenshots.

## AI-assisted Development Workflow
The application was developed with modular Python components, reusable utilities, clear separation of concerns, and automated verification steps.
