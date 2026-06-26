from __future__ import annotations

import importlib.util
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import json
import pandas as pd
import requests
from bs4 import BeautifulSoup

from config import TIMEOUT_SECONDS
from utils import parse_bedroom, parse_price, parse_sqft, validate_url

logger = logging.getLogger(__name__)

DEBUG_HTML_PATH = Path(__file__).resolve().parent / "data" / "debug.html"
CACHE_HTML_PATH = Path(__file__).resolve().parent / "data" / "cached_page.html"
CACHE_CSV_PATH = Path(__file__).resolve().parent / "data" / "cached_listings.csv"
API_SEARCH_ENDPOINT = "https://speedhome.com/api/properties/search"


def is_playwright_available() -> bool:
    """Return True if the 'playwright' package is importable (does not guarantee browsers installed)."""
    try:
        return importlib.util.find_spec("playwright") is not None
    except Exception:
        return False


def build_speedhome_url(area: str | None = None, url: str | None = None) -> str:
    if url and url.strip():
        return url.strip()
    if area and area.strip():
        slug = re.sub(r"[^a-z0-9]+", "-", area.strip().lower()).strip("-")
        return f"https://www.speedhome.com/rent/{slug}"
    raise ValueError("Either an area or URL is required.")


def build_sample_dataframe(area_name: str | None = None) -> pd.DataFrame:
    area = area_name or "Demo Market"
    sample_rows = [
        {
            "Listing Title": "Modern Condo Near KLCC",
            "Property Name": "Skyline Residence",
            "Area": area,
            "Bedroom": "2 Bedroom",
            "Bathroom": "2 Bathroom",
            "Monthly Rent": 2800,
            "Annual Rent": 33600,
            "Sqft": 950,
            "Furnishing Status": "Fully Furnished",
            "Listing URL": "https://www.speedhome.com",
            "Price per Sqft": 2.95,
        },
        {
            "Listing Title": "Cozy Studio in City Center",
            "Property Name": "Urban Nest",
            "Area": area,
            "Bedroom": "Studio",
            "Bathroom": "1 Bathroom",
            "Monthly Rent": 1800,
            "Annual Rent": 21600,
            "Sqft": 650,
            "Furnishing Status": "Partially Furnished",
            "Listing URL": "https://www.speedhome.com",
            "Price per Sqft": 2.77,
        },
        {
            "Listing Title": "Spacious Family Apartment",
            "Property Name": "Garden Heights",
            "Area": area,
            "Bedroom": "3 Bedroom",
            "Bathroom": "2 Bathroom",
            "Monthly Rent": 3600,
            "Annual Rent": 43200,
            "Sqft": 1200,
            "Furnishing Status": "Unfurnished",
            "Listing URL": "https://www.speedhome.com",
            "Price per Sqft": 3.00,
        },
    ]
    return pd.DataFrame(sample_rows)


def _parse_robots_disallow_rules(robots_txt: str) -> list[str]:
    rules: list[str] = []
    current_uas: list[str] = []
    last_field: str | None = None

    for raw_line in robots_txt.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        if ":" not in line:
            continue

        field, value = [part.strip() for part in line.split(":", 1)]
        field_lower = field.lower()
        value = value or ""

        if field_lower == "user-agent":
            if last_field and last_field != "user-agent":
                current_uas = []
            current_uas.append(value.lower())
            last_field = "user-agent"
            continue

        if field_lower == "disallow":
            if any(ua == "*" for ua in current_uas):
                rules.append(value)
            last_field = "disallow"
            continue

        if field_lower == "allow":
            last_field = "allow"
            continue

    return rules


def _matches_disallow_rule(path: str, rule: str) -> bool:
    rule = rule.strip()
    if not rule:
        return False
    if rule == "/":
        return True
    if "*" in rule:
        regex = "^" + re.escape(rule).replace(r"\*", ".*")
        return re.match(regex, path) is not None
    return path.startswith(rule)


def _fetch_robots_txt(robots_url: str) -> requests.Response:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36",
    }
    return requests.get(robots_url, headers=headers, timeout=TIMEOUT_SECONDS, allow_redirects=True)


def _load_robots_txt_for_url(parsed_url) -> str | None:
    robots_url = f"{parsed_url.scheme}://{parsed_url.netloc}/robots.txt"
    try:
        response = _fetch_robots_txt(robots_url)
        if response.status_code == 200 and response.url.rstrip("/").endswith("/robots.txt"):
            return response.text

        if parsed_url.netloc.startswith("www."):
            fallback_url = f"{parsed_url.scheme}://{parsed_url.netloc[4:]}/robots.txt"
            response = _fetch_robots_txt(fallback_url)
            if response.status_code == 200 and response.url.rstrip("/").endswith("/robots.txt"):
                return response.text

        logger.warning(
            "Could not load robots.txt from %s; status=%s final_url=%s",
            robots_url,
            response.status_code,
            response.url,
        )
        return None
    except requests.RequestException as exc:
        logger.warning("Could not fetch robots.txt: %s", exc)
        return None


def can_fetch_url(url: str) -> bool:
    parsed = urlparse(url)
    robots_txt = _load_robots_txt_for_url(parsed)
    if robots_txt is None:
        print("Robots check: ALLOWED")
        return True

    rules = _parse_robots_disallow_rules(robots_txt)
    target_path = parsed.path or "/"
    if parsed.query:
        target_path += f"?{parsed.query}"
    blocked = any(_matches_disallow_rule(target_path, rule) for rule in rules)
    if blocked:
        print("Robots check: BLOCKED")
        return False

    print("Robots check: ALLOWED")
    return True


def is_cloudflare_challenge_page(html: str) -> bool:
    lowered = html.lower()
    return "just a moment" in lowered or "cf-chl" in lowered or ("challenge" in lowered and "cloudflare" in lowered)


def launch_browser():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - environment may not have playwright installed
        raise RuntimeError("playwright is required for live scraping") from exc

    pw = sync_playwright().start()
    # Launch Chromium with common flags to reduce automation detection fingerprint
    browser = pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    # return tuple (browser, playwright) so caller can close playwright if needed
    return browser, pw


def fetch_rendered_html(url: str) -> tuple[str, str]:
    if not can_fetch_url(url):
        raise RuntimeError("robots.txt blocks this page.")
    browser, pw = launch_browser()
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1440, "height": 1400},
        ignore_https_errors=True,
        locale="en-MY",
        extra_http_headers={"accept-language": "en-MY,en;q=0.9"},
    )
    # Add small anti-detection init scripts
    try:
        context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4]});
        """)
    except Exception:
        # some playwright versions may not support add_init_script on context creation
        pass

    page = context.new_page()
    start_time = time.perf_counter()
    data_dir = DEBUG_HTML_PATH.parent
    try:
        # Ensure data dir exists before any writes
        data_dir.mkdir(parents=True, exist_ok=True)
        nav_error = None
        # Use commit to get the initial response early and let JavaScript continue loading
        try:
            page.goto(url, wait_until="commit", timeout=30000)
        except Exception as nav_exc:
            nav_error = nav_exc
            logger.warning("page.goto raised (commit): %s", nav_exc)

        # Wait for a real Speedhome property card to appear in the DOM
        card_selector = "div[class*='PropertyCard']"
        try:
            page.wait_for_selector(card_selector, timeout=15000)
            # Trigger any lazy loading that requires a small scroll into view
            try:
                page.evaluate("""
                    async () => {
                        const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                        const step = Math.max(window.innerHeight / 2, 800);
                        while (window.scrollY + window.innerHeight < document.body.scrollHeight) {
                            window.scrollBy(0, step);
                            await delay(300);
                        }
                        await delay(500);
                    }
                """)
                page.wait_for_timeout(1200)
            except Exception as scroll_exc:
                logger.debug("Auto-scroll did not complete: %s", scroll_exc)
            # Wait again briefly for more content to settle after scroll
            try:
                page.wait_for_selector(card_selector, timeout=10000)
            except Exception:
                logger.info("Property cards were found but did not stabilize after scroll")
        except Exception:
            logger.info("PropertyCard selector did not appear before timeout; capturing current content")

        # Attempt to read page content even if navigation or selector wait failed
        html = ""
        try:
            html = page.content()
        except Exception as content_exc:
            logger.exception("page.content() failed: %s", content_exc)

        # Immediately write debug HTML and screenshot regardless of later parsing
        try:
            if html:
                DEBUG_HTML_PATH.write_text(html, encoding="utf-8")
                print("HTML saved:", DEBUG_HTML_PATH.resolve())
            try:
                screenshot_path = data_dir / "challenge.png"
                page.screenshot(path=str(screenshot_path))
                print("Screenshot saved:", screenshot_path.resolve())
            except Exception as ss_exc:
                logger.exception("Failed to save screenshot: %s", ss_exc)
        except Exception:
            logger.exception("Failed writing debug artifacts")

        elapsed = time.perf_counter() - start_time
        logger.info("Current URL: %s", url)
        logger.info("Elapsed scraping time: %.2fs", elapsed)
        # print page title and URL for quick inspection
        try:
            print("PAGE TITLE:", page.title())
            print("PAGE URL:", page.url)
        except Exception:
            logger.exception("Could not print page title or url")

        if not html:
            # Return empty html to let caller fallback to cache/sample
            if nav_error:
                return "", f"Playwright navigation failed: {nav_error}"
            return "", f"No HTML content could be retrieved from {url}."

        return html, f"Rendered HTML loaded in {elapsed:.2f}s"
    except Exception as exc:
        logger.exception("Playwright failed to render %s", url)
        return "", f"Playwright render failed: {exc}"
    finally:
        try:
            context.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


def extract_speedhome_area_slug_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    path = parsed.path.lower().strip("/")
    if path.startswith("rent/"):
        return path.split("/", 1)[1] if "/" in path else None
    if path == "rent":
        qs = parse_qs(parsed.query)
        for key in ("loc", "q", "search_term_string", "area"):
            if key in qs and qs[key]:
                return qs[key][0]
        return None
    if path.startswith("rent"):
        parts = path.split("/")
        if len(parts) > 1:
            return parts[1]
    qs = parse_qs(parsed.query)
    for key in ("loc", "q", "search_term_string", "area"):
        if key in qs and qs[key]:
            return qs[key][0]
    return None


def build_search_payload(area_slug: str, page_index: int = 0, items_per_page: int = 100) -> dict[str, Any]:
    return {
        "searchParams": {
            "loc": area_slug,
            "pg": page_index + 1,
            "genderPreference": "ALL",
        },
        "pathname": "/",
        "page": page_index,
        "itemsPerPage": items_per_page,
    }


def fetch_speedhome_api_page(page, area_slug: str, page_index: int, items_per_page: int) -> dict[str, Any]:
    payload = build_search_payload(area_slug, page_index, items_per_page)
    result = page.evaluate("""
        async (payload) => {
            const response = await fetch('/api/properties/search', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                },
                credentials: 'same-origin',
                body: JSON.stringify(payload),
            });
            const text = await response.text();
            return {status: response.status, text};
        }
    """, payload)
    return result


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    try:
        return int(str(value))
    except (ValueError, TypeError):
        return None


def validate_api_response_text(api_response: dict[str, Any], page_index: int) -> dict[str, Any]:
    if not isinstance(api_response, dict):
        raise RuntimeError("API response was not a valid dictionary.")
    status = api_response.get("status")
    if status != 200:
        raise RuntimeError(f"API request failed with status {status if status is not None else 'unknown'}.")
    text = api_response.get("text")
    if not isinstance(text, str):
        raise RuntimeError("API response text is missing or invalid.")
    try:
        data = json.loads(text)
    except ValueError:
        raise RuntimeError("API returned invalid JSON payload")
    if not isinstance(data, dict):
        raise RuntimeError("API did not return a JSON object.")
    total_pages = _parse_int(data.get("totalPages"))
    total_elements = _parse_int(data.get("totalElements"))
    if total_pages is not None and total_pages < 1:
        raise RuntimeError("API returned invalid totalPages value.")
    if total_elements is not None and total_elements < 0:
        raise RuntimeError("API returned invalid totalElements value.")
    if total_elements is not None and total_elements < page_index * data.get("size", 0):
        raise RuntimeError("API totalElements is smaller than the current offset.")
    return data


def extract_listings_from_api_content(content: list[dict[str, Any]], base_url: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        title = item.get("name") or item.get("title") or ""
        if not title:
            continue
        property_name = item.get("name") or item.get("propertyName") or title
        area = item.get("city") or item.get("state") or ""
        if not area:
            address = item.get("address") or ""
            parts = [p.strip() for p in str(address).split(",") if p.strip()]
            area = parts[-1] if parts else ""
        bedroom_raw = item.get("bedroom")
        bedroom = (
            f"{int(bedroom_raw)} Bedroom"
            if isinstance(bedroom_raw, (int, float)) and bedroom_raw > 0
            else ("Studio" if bedroom_raw == 0 else "Unknown")
        )
        bath_raw = item.get("bathroom") or item.get("bathrooms") or item.get("bathroomCount")
        bathroom = (
            f"{int(bath_raw)} Bathroom"
            if isinstance(bath_raw, (int, float)) and bath_raw >= 0
            else str(bath_raw) if bath_raw is not None
            else "Unknown"
        )
        price_value = item.get("price")
        if isinstance(price_value, str):
            price_value = parse_price(price_value)
        elif isinstance(price_value, (int, float)):
            price_value = float(price_value)
        else:
            price_value = float("nan")
        sqft_value = item.get("sqft")
        if isinstance(sqft_value, str):
            sqft_value = parse_sqft(sqft_value)
        elif isinstance(sqft_value, (int, float)):
            sqft_value = float(sqft_value)
        else:
            sqft_value = float("nan")
        furnishing_map = {
            "FULL": "Fully Furnished",
            "PARTIAL": "Partially Furnished",
            "NONE": "Unfurnished",
        }
        furnishing = furnishing_map.get(item.get("furnishType") or item.get("furnish")) or (
            "Fully Furnished" if item.get("furnishes") else "Unknown"
        )
        slug = item.get("slug") or item.get("ref")
        listing_url = ""
        if isinstance(slug, str) and slug:
            if slug.startswith("http"):
                listing_url = slug
            else:
                listing_url = f"https://www.speedhome.com/property/{slug}"

        rows.append(
            {
                "Listing Title": title,
                "Property Name": property_name,
                "Area": area or "",
                "Bedroom": bedroom,
                "Bathroom": bathroom,
                "Monthly Rent": price_value,
                "Annual Rent": price_value * 12 if price_value and not pd.isna(price_value) else float("nan"),
                "Sqft": sqft_value,
                "Furnishing Status": furnishing,
                "Listing URL": listing_url,
                "Price per Sqft": (price_value / sqft_value)
                if sqft_value and sqft_value > 0 and price_value and not pd.isna(price_value)
                else float("nan"),
            }
        )
    return rows


def fetch_speedhome_data_via_api(target_url: str) -> tuple[list[dict[str, Any]], str]:
    if not can_fetch_url(target_url):
        raise RuntimeError("robots.txt blocks this page.")
    browser, pw = launch_browser()
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1440, "height": 1400},
        ignore_https_errors=True,
        locale="en-MY",
        extra_http_headers={"accept-language": "en-MY,en;q=0.9"},
    )
    try:
        try:
            context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4]});
            """)
        except Exception:
            pass
        page = context.new_page()
        # Ensure landing page cookies and client context are established before API calls.
        page.goto("https://speedhome.com", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)
        page.goto(target_url, wait_until="load", timeout=30000)
        page.wait_for_timeout(3000)
        slug = extract_speedhome_area_slug_from_url(page.url) or extract_speedhome_area_slug_from_url(target_url)
        if not slug:
            raise RuntimeError("Unable to determine Speedhome area slug for API scraping.")
        items_per_page = 100
        page_index = 0
        rows: list[dict[str, Any]] = []
        total_pages = None
        total_elements = None
        while True:
            api_response = fetch_speedhome_api_page(page, slug, page_index, items_per_page)
            data = validate_api_response_text(api_response, page_index)
            content = data.get("content") if isinstance(data, dict) else None
            if not isinstance(content, list) or not content:
                break
            rows.extend(extract_listings_from_api_content(content, target_url))
            if total_pages is None:
                total_pages = _parse_int(data.get("totalPages"))
            if total_elements is None:
                total_elements = _parse_int(data.get("totalElements"))
            if data.get("last") is True or data.get("empty") is True:
                break
            if len(content) < items_per_page:
                break
            if total_pages is not None and page_index >= total_pages - 1:
                break
            page_index += 1
            if page_index >= 100:
                break
        page_count = page_index + 1
        metadata = []
        if total_pages is not None:
            metadata.append(f"totalPages={total_pages}")
        if total_elements is not None:
            metadata.append(f"totalElements={total_elements}")
        metadata_text = f" ({', '.join(metadata)})" if metadata else ""
        return rows, f"Live API data fetched from {target_url}{metadata_text} | pages={page_count}"
    finally:
        try:
            context.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


def parse_listing_cards(html: str) -> list[BeautifulSoup]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[BeautifulSoup] = []
    seen: set[int] = set()

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        if "/rent/" not in href and "/property" not in href and "/listing" not in href:
            continue
        current = link.parent
        for _ in range(4):
            if current is None:
                break
            text = current.get_text(" ", strip=True)
            if text and len(text) > 20:
                item_id = id(current)
                if item_id not in seen:
                    seen.add(item_id)
                    candidates.append(current)
                break
            current = current.parent

    if not candidates:
        for node in soup.find_all(["article", "li", "div", "section", "main"]):
            if node.get_text(" ", strip=True):
                candidates.append(node)

    return candidates


def parse_listing_details(card: BeautifulSoup, fallback_area: str) -> dict[str, Any] | None:
    try:
        title = card.find(["h1", "h2", "h3", "h4", "p", "span"])
        title_text = title.get_text(" ", strip=True) if title else ""
        if not title_text:
            return None

        text_block = card.get_text(" ", strip=True)
        link_tag = card.find("a", href=True)
        link_url = link_tag["href"] if link_tag else ""
        full_url = link_url if link_url.startswith("http") else f"https://www.speedhome.com{link_url}" if link_url else ""

        area_text = fallback_area
        rent_match = re.search(r"RM\s*([0-9,\.]+)", text_block)
        sqft_match = re.search(r"([0-9,\.]+)\s*sqft", text_block, flags=re.IGNORECASE)
        bedroom_match = re.search(r"(\d+)\s*bed", text_block, flags=re.IGNORECASE)
        bathroom_match = re.search(r"(\d+)\s*bath", text_block, flags=re.IGNORECASE)

        furnishing_text = "Unknown"
        lowered = text_block.lower()
        if "fully furnished" in lowered:
            furnishing_text = "Fully Furnished"
        elif "partially furnished" in lowered:
            furnishing_text = "Partially Furnished"
        elif "unfurnished" in lowered:
            furnishing_text = "Unfurnished"

        monthly_rent = parse_price(rent_match.group(1) if rent_match else text_block)
        annual_rent = monthly_rent * 12 if not pd.isna(monthly_rent) else float("nan")
        sqft = parse_sqft(sqft_match.group(1) if sqft_match else text_block)
        price_per_sqft = monthly_rent / sqft if sqft and not pd.isna(monthly_rent) else float("nan")

        bedroom_value = f"{bedroom_match.group(1)} Bedroom" if bedroom_match else "Unknown"
        bathroom_value = f"{bathroom_match.group(1)} Bathroom" if bathroom_match else "Unknown"

        return {
            "Listing Title": title_text,
            "Property Name": title_text,
            "Area": area_text,
            "Bedroom": parse_bedroom(bedroom_value),
            "Bathroom": bathroom_value,
            "Monthly Rent": monthly_rent,
            "Annual Rent": annual_rent,
            "Sqft": sqft,
            "Furnishing Status": furnishing_text,
            "Listing URL": full_url,
            "Price per Sqft": price_per_sqft,
        }
    except Exception as exc:
        logger.warning("Parsing error for card: %s", exc)
        return None


def extract_listings_from_next_data(html: str, base_url: str | None = None) -> list[dict[str, Any]]:
    try:
        match = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, flags=re.S)
        if not match:
            return []
        data = json.loads(match.group(1))
        page_props = data.get("props", {}).get("pageProps", {})
        ssr = page_props.get("ssrProperties") or page_props.get("initialState") or page_props
        content = None
        if isinstance(ssr, dict):
            content = ssr.get("content") or ssr.get("listings") or ssr.get("data")
        if not content:
            def find_content(obj):
                if isinstance(obj, dict):
                    for value in obj.values():
                        found = find_content(value)
                        if found:
                            return found
                elif isinstance(obj, list):
                    if obj and isinstance(obj[0], dict) and any(k in obj[0] for k in ("name", "price", "sqft")):
                        return obj
                    for item in obj:
                        found = find_content(item)
                        if found:
                            return found
                return None
            content = find_content(page_props)
        if not content or not isinstance(content, list):
            return []

        rows: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            title = item.get("name") or item.get("title") or ""
            property_name = item.get("name") or item.get("propertyName") or title
            area = item.get("city") or item.get("state") or None
            if not area:
                address = item.get("address") or ""
                parts = [p.strip() for p in str(address).split(",") if p.strip()]
                if parts:
                    area = parts[-1]
            bedroom_raw = item.get("bedroom")
            bedroom = (
                f"{int(bedroom_raw)} Bedroom"
                if isinstance(bedroom_raw, (int, float)) and bedroom_raw > 0
                else ("Studio" if bedroom_raw == 0 else "Unknown")
            )
            bath_raw = item.get("bathroom") or item.get("bathrooms") or item.get("bathroomCount")
            bathroom = (
                f"{int(bath_raw)} Bathroom"
                if isinstance(bath_raw, (int, float)) and bath_raw >= 0
                else str(bath_raw)
            )
            price_value = item.get("price")
            if isinstance(price_value, str):
                price_value = parse_price(price_value)
            elif isinstance(price_value, (int, float)):
                price_value = float(price_value)
            else:
                price_value = float("nan")
            sqft_value = item.get("sqft")
            if isinstance(sqft_value, str):
                sqft_value = parse_sqft(sqft_value)
            elif isinstance(sqft_value, (int, float)):
                sqft_value = float(sqft_value)
            else:
                sqft_value = float("nan")
            furnishing_map = {
                "FULL": "Fully Furnished",
                "PARTIAL": "Partially Furnished",
                "NONE": "Unfurnished",
            }
            furnishing = furnishing_map.get(item.get("furnishType") or item.get("furnish")) or (
                "Fully Furnished" if item.get("furnishes") else "Unknown"
            )
            slug = item.get("slug") or item.get("ref")
            listing_url = ""
            if slug:
                if isinstance(slug, str) and slug.startswith("http"):
                    listing_url = slug
                else:
                    listing_url = f"https://www.speedhome.com/property/{slug}"
            rows.append({
                "Listing Title": title,
                "Property Name": property_name,
                "Area": area or "",
                "Bedroom": bedroom,
                "Bathroom": bathroom,
                "Monthly Rent": price_value,
                "Annual Rent": price_value * 12 if price_value and not pd.isna(price_value) else float("nan"),
                "Sqft": sqft_value,
                "Furnishing Status": furnishing,
                "Listing URL": listing_url,
                "Price per Sqft": (price_value / sqft_value) if sqft_value and sqft_value > 0 and price_value and not pd.isna(price_value) else float("nan"),
            })
        return rows
    except Exception:
        return []


def build_dataframe(listings: list[dict[str, Any]]) -> pd.DataFrame:
    if not listings:
        return pd.DataFrame(columns=[
            "Listing Title",
            "Property Name",
            "Area",
            "Bedroom",
            "Bathroom",
            "Monthly Rent",
            "Annual Rent",
            "Sqft",
            "Furnishing Status",
            "Listing URL",
            "Price per Sqft",
        ])
    return pd.DataFrame(listings)


def save_cache(df: pd.DataFrame, cache_path: Path | None = None) -> None:
    target = cache_path or CACHE_CSV_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(target, index=False)


def load_cache(cache_path: Path | None = None) -> pd.DataFrame | None:
    target = cache_path or CACHE_CSV_PATH
    if not target.exists():
        return None
    return pd.read_csv(target)


def scrape_speedhome_data(area: str | None = None, url: str | None = None) -> tuple[pd.DataFrame, str]:
    target_url = build_speedhome_url(area=area, url=url)
    if not validate_url(target_url):
        raise ValueError("Please provide a valid URL.")

    if not can_fetch_url(target_url):
        # Ensure debug artifacts exist even when robots.txt blocks live fetch
        try:
            DEBUG_HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
            note = f"Robots.txt denies fetching {target_url}. No live HTML retrieved."
            DEBUG_HTML_PATH.write_text(f"<html><body><pre>{note}</pre></body></html>", encoding="utf-8")
            # write a tiny 1x1 PNG placeholder
            png_bytes = b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII='
            try:
                import base64
                (DEBUG_HTML_PATH.parent / "challenge.png").write_bytes(base64.b64decode(png_bytes))
            except Exception:
                # fallback: create an empty file
                (DEBUG_HTML_PATH.parent / "challenge.png").write_bytes(b"")
            print("HTML saved:", DEBUG_HTML_PATH.resolve())
            print("Screenshot saved:", (DEBUG_HTML_PATH.parent / "challenge.png").resolve())
        except Exception:
            logger.exception("Failed to write debug artifacts for robots.txt block")
        cached = load_cache()
        if cached is not None:
            return cached, "robots.txt blocks this route, so the latest cached dataset is being used."
        return build_sample_dataframe(area or "Demo Market"), "robots.txt blocks this route, so sample data is shown."

    try:
        html, message = fetch_rendered_html(target_url)
    except RuntimeError as exc:
        # Playwright isn't available in this environment — fall back to cache or sample data
        logger.warning("Playwright unavailable: %s", exc)
        cached = load_cache()
        if cached is not None:
            return cached, f"Playwright not available; using cached data. ({exc})"
        return build_sample_dataframe(area or "Demo Market"), f"Playwright not available; sample data is shown. ({exc})"

    if not html:
        cached = load_cache()
        if cached is not None:
            logger.warning("Playwright failed; using cached data")
            return cached, "Live scraping is temporarily unavailable. Cached data is being used."
        logger.warning("Playwright failed; returning sample data")
        return build_sample_dataframe(area or "Demo Market"), "Live scraping is temporarily unavailable. Sample data is shown."

    # Save rendered HTML snapshot
    try:
        DEBUG_HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEBUG_HTML_PATH.write_text(html, encoding="utf-8")
        CACHE_HTML_PATH.write_text(html, encoding="utf-8")
    except Exception:
        logger.exception("Failed to save HTML debug or cache files")

    # Detect Cloudflare-style challenge pages
    if is_cloudflare_challenge_page(html):
        logger.warning("Cloudflare challenge detected on %s", target_url)
        cached = load_cache()
        if cached is not None:
            return cached, "Cloudflare challenge detected; cached data is being used."
        return build_sample_dataframe(area or "Demo Market"), "Cloudflare challenge detected; sample data is shown."

    # Prefer structured __NEXT_DATA__ JSON content when available
    listing_rows = extract_listings_from_next_data(html, target_url)
    if listing_rows:
        df = build_dataframe(listing_rows)
        save_cache(df)
        return df, f"Live data from {target_url} | {message}"

    # Try the internal API endpoint first for full pagination when page content is available.
    try:
        api_rows, api_message = fetch_speedhome_data_via_api(target_url)
        if api_rows:
            df = build_dataframe(api_rows)
            save_cache(df)
            return df, api_message
    except Exception as api_exc:
        logger.warning("API-first scraping failed: %s", api_exc)

    cards = parse_listing_cards(html)
    logger.info("Number of listings found: %s", len(cards))
    if not cards:
        cached = load_cache()
        if cached is not None:
            return cached, "No listings were found in the rendered HTML. Cached data is being used."
        return build_sample_dataframe(area or "Demo Market"), "No listings were found in the rendered HTML. Sample data is shown."

    listing_rows = []
    parse_errors = 0
    for card in cards:
        parsed = parse_listing_details(card, area or "Unknown Area")
        if parsed:
            listing_rows.append(parsed)
        else:
            parse_errors += 1

    logger.info("Parser errors: %s", parse_errors)
    if not listing_rows:
        cached = load_cache()
        if cached is not None:
            return cached, "No valid listings could be parsed. Cached data is being used."
        return build_sample_dataframe(area or "Demo Market"), "No valid listings could be parsed. Sample data is shown."

    df = build_dataframe(listing_rows)
    save_cache(df)
    return df, f"Live data from {target_url} | {message}"
