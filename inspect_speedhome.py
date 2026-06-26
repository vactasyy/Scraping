from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from pathlib import Path

url = 'https://www.speedhome.com/rent/selangor'
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
    # Use DOMContentLoaded then a fixed timeout to avoid waiting indefinitely
    page.goto(url, wait_until='domcontentloaded', timeout=90000)
    page.wait_for_timeout(8000)
    html = page.content()
    Path('data').mkdir(parents=True, exist_ok=True)
    Path('data/debug_inspect.html').write_text(html, encoding='utf-8')
    try:
        page.screenshot(path=str(Path('data') / 'challenge.png'))
    except Exception:
        pass
    print('TITLE:', page.title())
    print('URL:', page.url)
    soup = BeautifulSoup(html, 'html.parser')
    print('HTML length:', len(html))
    tags = []
    for tag in soup.find_all(True):
        if tag.name and (tag.get('class') or tag.get('id')):
            tags.append((tag.name, tag.get('class'), tag.get('id')))
    print('UNIQUE_TAGS_SAMPLE:')
    seen = set()
    for item in tags:
        if item not in seen:
            print(item)
            seen.add(item)
            if len(seen) >= 80:
                break
    print('--- TEXT NODES ---')
    for el in soup.find_all(['h1', 'h2', 'h3', 'h4', 'p', 'a', 'span', 'div', 'li', 'article']):
        text = ' '.join(el.get_text(' ', strip=True).split())
        if len(text) > 20 and 'chat' not in text.lower() and 'list' not in text.lower():
            print(text[:220])
            break
    browser.close()
