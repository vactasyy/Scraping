from analytics import build_summary, filter_listings, sort_listings
from scraper import build_sample_dataframe, build_speedhome_url, is_cloudflare_challenge_page, load_cache, save_cache
from utils import parse_bedroom, parse_price, parse_sqft, validate_url


def test_build_speedhome_url():
    assert build_speedhome_url(area="Kuala Lumpur") == "https://www.speedhome.com/rent/kuala-lumpur"


def test_validate_url():
    assert validate_url("https://example.com") is True
    assert validate_url("not-a-url") is False


def test_parse_helpers():
    assert parse_bedroom("2 bed") == "2 Bedroom"
    assert parse_bedroom("studio") == "Studio"
    assert parse_price("RM 2,500") == 2500.0
    assert parse_price("RM7,500") == 7500.0
    assert parse_sqft("950 sqft") == 950.0
    assert parse_sqft("1,200 sqft") == 1200.0


def test_extract_listings_from_next_data():
    from scraper import extract_listings_from_next_data

    sample_html = """
    <html>
      <head>
        <script id=\"__NEXT_DATA__\" type=\"application/json\">{
          \"props\": {
            \"pageProps\": {
              \"ssrProperties\": {
                \"content\": [
                  {
                    \"name\": \"Luxury Condo\",
                    \"price\": \"RM 3,200\",
                    \"sqft\": \"950 sqft\",
                    \"bedroom\": 2,
                    \"bathroom\": 2,
                    \"slug\": \"luxury-condo\"
                  }
                ]
              }
            }
          }
        }</script>
      </head>
      <body></body>
    </html>
    """
    rows = extract_listings_from_next_data(sample_html)
    assert len(rows) == 1
    assert rows[0]["Listing Title"] == "Luxury Condo"
    assert rows[0]["Monthly Rent"] == 3200.0
    assert rows[0]["Sqft"] == 950.0


def test_speedhome_slug_and_payload_generation():
    from scraper import build_search_payload, extract_speedhome_area_slug_from_url

    assert extract_speedhome_area_slug_from_url("https://www.speedhome.com/rent/kuala-lumpur") == "kuala-lumpur"
    assert extract_speedhome_area_slug_from_url("https://www.speedhome.com/rent?loc=kuala-lumpur") == "kuala-lumpur"
    assert extract_speedhome_area_slug_from_url("https://www.speedhome.com/rent?q=kuala-lumpur") == "kuala-lumpur"

    payload = build_search_payload("kuala-lumpur", page_index=0, items_per_page=50)
    assert payload["searchParams"]["loc"] == "kuala-lumpur"
    assert payload["page"] == 0
    assert payload["itemsPerPage"] == 50
    assert payload["searchParams"]["pg"] == 1


def test_validate_api_response_text():
    from scraper import validate_api_response_text

    api_response = {
        "status": 200,
        "text": '{"content": [{"id": 1, "name": "Test Property"}], "totalElements": 444, "totalPages": 9, "size": 100, "last": false, "empty": false}'
    }
    data = validate_api_response_text(api_response, page_index=0)

    assert data["totalElements"] == 444
    assert data["totalPages"] == 9
    assert isinstance(data["content"], list)


def test_filter_and_sort():
    df = build_sample_dataframe("KL")
    filtered = filter_listings(df, {"bedroom": "2 Bedroom", "min_price": 2500})
    assert len(filtered) == 1
    sorted_df = sort_listings(df, "Monthly Rent")
    assert sorted_df.iloc[0]["Monthly Rent"] >= sorted_df.iloc[-1]["Monthly Rent"]


def test_summary_generation():
    df = build_sample_dataframe("KL")
    summary = build_summary(df)
    assert not summary.empty
    assert "Bedroom Type" in summary.columns


def test_cloudflare_challenge_detection():
    challenge_html = "<html><body><title>Just a moment...</title><div>cf-chl</div></body></html>"
    normal_html = "<html><body><h1>Property listings</h1></body></html>"
    assert is_cloudflare_challenge_page(challenge_html) is True
    assert is_cloudflare_challenge_page(normal_html) is False


def test_cache_round_trip(tmp_path):
    cache_file = tmp_path / "cached_listings.csv"
    df = build_sample_dataframe("KL")
    save_cache(df, cache_file)
    loaded = load_cache(cache_file)
    assert loaded is not None
    assert loaded.shape[0] == df.shape[0]


def test_can_fetch_url_allow_block():
    assert __import__('scraper').can_fetch_url("https://www.speedhome.com/rent/selangor") is True
    assert __import__('scraper').can_fetch_url("https://www.speedhome.com/dashboard/") is False
