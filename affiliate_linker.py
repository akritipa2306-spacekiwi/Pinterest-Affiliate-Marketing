#!/usr/bin/env python3
"""
Pinterest Affiliate Marketing — Amazon Affiliate Link Collector
================================================================
Reads today's pin recommendations from the discovery agent output,
searches Amazon for matching products, extracts product details,
and generates ready-to-use affiliate links.

NOTE: This is a bootstrap tool for the MVP phase before you qualify
for the Amazon Product Advertising API (PA-API).  Once you have 3
qualifying sales, switch to the PA-API for reliable, ToS-compliant
product data.

Usage:
  python3 affiliate_linker.py                          # today's pins
  python3 affiliate_linker.py --query "small desk lamp" # manual query
  python3 affiliate_linker.py --associate-id MY-TAG-20  # override tag
  python3 affiliate_linker.py --count 2                 # fewer pins

Inputs:
  search_discovery_results.json  (from discovery_agent.py)

Outputs:
  affiliate_links/YYYY-MM-DD.json
"""

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("ERROR: Missing dependencies. Install with:")
    print("  pip3 install requests beautifulsoup4")
    sys.exit(1)

# ── Load .env file if present ─────────────────────────────────────────
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _val = _line.split("=", 1)
                os.environ.setdefault(_key.strip(), _val.strip())

# ════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DISCOVERY_FILE = os.path.join(BASE_DIR, "search_discovery_results.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "affiliate_links")

DEFAULT_ASSOCIATE_ID = "endofjune-20"
MAX_PRODUCTS_PER_QUERY = 5
MIN_DELAY_SEC = 4
MAX_DELAY_SEC = 8

AMAZON_SEARCH_URL = "https://www.amazon.com/s"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) "
    "Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# ════════════════════════════════════════════════════════════════════════
# HTTP SESSION
# ════════════════════════════════════════════════════════════════════════

def create_session() -> requests.Session:
    """Build a requests session with realistic browser headers."""
    session = requests.Session()
    ua = random.choice(USER_AGENTS)
    session.headers.update({
        "User-Agent": ua,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    return session


# ════════════════════════════════════════════════════════════════════════
# AMAZON SEARCH & PARSE
# ════════════════════════════════════════════════════════════════════════

def search_amazon(session: requests.Session, query: str,
                  max_results: int = 5) -> list:
    """
    Search Amazon for `query` and return up to `max_results` products.
    Returns a list of product dicts.
    """
    params = {"k": query, "ref": "nb_sb_noss"}

    try:
        resp = session.get(AMAZON_SEARCH_URL, params=params, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"    [error] Request failed: {exc}")
        return []

    # Detect bot-blocking
    lower_text = resp.text[:5000].lower()
    if "captcha" in lower_text or "robot" in lower_text or resp.status_code == 503:
        print("    [warn] Amazon returned CAPTCHA / bot check — skipping.")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    return _extract_products(soup, max_results)


def _extract_products(soup: BeautifulSoup, max_results: int) -> list:
    """Parse Amazon search-results HTML into product dicts."""
    products = []

    result_cards = soup.select('[data-component-type="s-search-result"]')

    for card in result_cards:
        if len(products) >= max_results:
            break

        asin = card.get("data-asin", "").strip()
        if not asin:
            continue

        # ── Title ──────────────────────────────────────────────────
        title_el = card.select_one("h2 a span") or card.select_one("h2 span")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue  # skip cards with no usable title

        # ── Price ──────────────────────────────────────────────────
        price = _extract_price(card)

        # ── Rating ─────────────────────────────────────────────────
        rating = None
        for sel in [
            "i.a-icon-star-small span.a-icon-alt",
            "i.a-icon-star span.a-icon-alt",
            "span.a-icon-alt",
        ]:
            rating_el = card.select_one(sel)
            if rating_el:
                m = re.search(r"([\d.]+)\s+out\s+of\s+([\d.]+)",
                              rating_el.get_text(strip=True))
                if m:
                    rating = float(m.group(1))
                    break

        # ── Review count ───────────────────────────────────────────
        review_count = _extract_review_count(card)

        # ── Image URL ──────────────────────────────────────────────
        img_el = card.select_one("img.s-image")
        image_url = img_el.get("src", "") if img_el else ""

        # ── Product URL (organic, no affiliate tag yet) ────────────
        link_el = card.select_one("h2 a")
        product_url = ""
        if link_el and link_el.get("href"):
            href = link_el["href"]
            if href.startswith("/"):
                href = "https://www.amazon.com" + href
            product_url = href

        products.append({
            "asin": asin,
            "title": title,
            "price": price,
            "rating": rating,
            "review_count": review_count,
            "image_url": image_url,
            "product_url": product_url,
        })

    return products


def _extract_price(card) -> str:
    """Try multiple selectors to pull a price string from a result card."""
    # Method 1: screen-reader price span
    offscreen = card.select_one(".a-price .a-offscreen")
    if offscreen:
        return offscreen.get_text(strip=True)

    # Method 2: whole + fraction
    whole_el = card.select_one(".a-price-whole")
    frac_el = card.select_one(".a-price-fraction")
    if whole_el:
        whole = whole_el.get_text(strip=True).rstrip(".")
        frac = frac_el.get_text(strip=True) if frac_el else "00"
        return f"${whole}.{frac}"

    return "See Amazon"


def _extract_review_count(card) -> int:
    """Pull review count from a result card, trying several patterns."""
    # Pattern 1: aria-label on the reviews link
    reviews_link = card.select_one('a[href*="customerReviews"]')
    if reviews_link:
        aria = reviews_link.get("aria-label", "")
        m = re.search(r"([\d,]+)", aria)
        if m:
            return int(m.group(1).replace(",", ""))
        span = reviews_link.select_one("span")
        if span:
            m = re.search(r"([\d,]+)", span.get_text(strip=True))
            if m:
                return int(m.group(1).replace(",", ""))

    # Pattern 2: sibling span after the star rating
    for span in card.select("span.a-size-base.s-underline-text"):
        text = span.get_text(strip=True).replace(",", "")
        if text.isdigit():
            return int(text)

    return 0


def build_product_link(asin: str) -> str:
    """Generate a plain Amazon product link."""
    return f"https://www.amazon.com/dp/{asin}/"


# ════════════════════════════════════════════════════════════════════════
# PIN RECOMMENDATIONS — load & build search queries
# ════════════════════════════════════════════════════════════════════════

def load_pin_recommendations() -> list:
    """Load today's pin recommendations from the discovery file."""
    if not os.path.exists(DISCOVERY_FILE):
        print(f"ERROR: {DISCOVERY_FILE} not found.")
        print("  Run discovery_agent.py first.")
        sys.exit(1)

    with open(DISCOVERY_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    recs = data.get("todays_pin_recommendations", [])
    if not recs:
        print("No pin recommendations found in discovery results.")
        sys.exit(1)

    return recs


def build_search_query(rec: dict) -> str:
    """
    Convert a pin recommendation into an effective Amazon search query.
    Keeps product-focused terms; strips words Amazon ignores.
    """
    query = rec.get("query", "")
    category = rec.get("product_category", "")

    # If the query is very generic, append the product category
    if category and category != "none" and category not in query.lower():
        query = f"{query} {category}"

    return query


# ════════════════════════════════════════════════════════════════════════
# OUTPUT — save & display
# ════════════════════════════════════════════════════════════════════════

def save_results(results: list, associate_id: str) -> str:
    """Write results to affiliate_links/YYYY-MM-DD.json. Returns path."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filepath = os.path.join(OUTPUT_DIR, f"{today}.json")

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": today,
        "associate_id": associate_id,
        "total_pins": len(results),
        "total_products": sum(r["products_found"] for r in results),
        "pins": results,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    return filepath


def print_results(results: list, associate_id: str):
    """Pretty-print affiliate link results to the terminal."""
    for i, pin in enumerate(results, 1):
        print(f"\n  {'═' * 58}")
        print(f"  PIN #{i}: {pin['query']}")
        print(f"  Amazon search: \"{pin['search_query']}\"")
        print(f"  {'═' * 58}")

        products = pin.get("products", [])
        if not products:
            print("  (no products found)")
            continue

        for j, p in enumerate(products, 1):
            link = build_product_link(p["asin"])
            truncated = p["title"][:72]
            ellipsis = "..." if len(p["title"]) > 72 else ""
            stars = f"{p['rating']}/5" if p["rating"] else "N/A"
            reviews = f"{p['review_count']:,}" if p["review_count"] else "0"

            print(f"\n  [{j}] {truncated}{ellipsis}")
            print(f"      Price:   {p['price']}")
            print(f"      Rating:  {stars}  ({reviews} reviews)")
            print(f"      ASIN:    {p['asin']}")
            print(f"      Image:   {p['image_url'][:80]}...")
            print(f"      Link:    {link}")


# ════════════════════════════════════════════════════════════════════════
# MAIN COMMAND
# ════════════════════════════════════════════════════════════════════════

def cmd_generate(associate_id: str, count: int, manual_query: str = None):
    """Search Amazon for each pin recommendation and collect products."""
    print("=" * 58)
    print("  AMAZON AFFILIATE LINK COLLECTOR")
    print("=" * 58)
    print(f"  Associate ID: {associate_id}")

    session = create_session()
    results = []

    # ── Build query list ───────────────────────────────────────────
    if manual_query:
        queries = [{
            "query": manual_query,
            "search_query": manual_query,
            "product_category": "general",
        }]
        print(f"  Mode: manual query")
    else:
        print(f"\n[1/3] Loading pin recommendations...")
        recs = load_pin_recommendations()
        queries = []
        for rec in recs[:count]:
            sq = build_search_query(rec)
            queries.append({
                "query": rec["query"],
                "search_query": sq,
                "product_category": rec.get("product_category", ""),
                "intent": rec.get("intent", ""),
                "affiliate_potential": rec.get("affiliate_potential", 0),
                "priority_score": rec.get("priority_score", 0),
            })
        print(f"  Loaded {len(queries)} pin recommendation(s)")

    # ── Search Amazon for each query ───────────────────────────────
    step = "2/3" if not manual_query else "1/2"
    print(f"\n[{step}] Searching Amazon ({len(queries)} quer{'y' if len(queries) == 1 else 'ies'})...")

    for i, q in enumerate(queries):
        search_q = q["search_query"]
        print(f"\n  [{i+1}/{len(queries)}] \"{search_q}\"")

        products = search_amazon(session, search_q,
                                 max_results=MAX_PRODUCTS_PER_QUERY)

        # Attach product links
        for p in products:
            p["product_link"] = build_product_link(p["asin"])

        q["products"] = products
        q["products_found"] = len(products)
        results.append(q)

        print(f"    → {len(products)} product(s) found")

        # Rate-limit between requests (skip after last)
        if i < len(queries) - 1:
            delay = random.uniform(MIN_DELAY_SEC, MAX_DELAY_SEC)
            print(f"    Waiting {delay:.1f}s before next search...")
            time.sleep(delay)

    # ── Save ───────────────────────────────────────────────────────
    step = "3/3" if not manual_query else "2/2"
    print(f"\n[{step}] Saving results...")
    filepath = save_results(results, associate_id)

    # ── Display ────────────────────────────────────────────────────
    total_products = sum(r["products_found"] for r in results)
    print("\n" + "=" * 58)
    print(f"  RESULTS — {len(results)} pin(s), {total_products} products")
    print("=" * 58)
    print_results(results, associate_id)

    print(f"\n  {'─' * 58}")
    print(f"  Saved to: {filepath}")
    print(f"  Total affiliate links: {total_products}")
    print(f"\n  Next steps:")
    print(f"    1. Pick the best 1-2 products per pin")
    print(f"    2. Copy the affiliate link into your pin")
    print(f"    3. Use the image URL as reference for your pin image")
    print()


# ════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Amazon Affiliate Link Collector for Pinterest Pins"
    )
    parser.add_argument(
        "--associate-id",
        default=os.environ.get("AMAZON_ASSOCIATE_ID", DEFAULT_ASSOCIATE_ID),
        help=f"Amazon Associates tag (default: {DEFAULT_ASSOCIATE_ID})",
    )
    parser.add_argument(
        "--count", type=int, default=3,
        help="Number of pin recommendations to process (default: 3)",
    )
    parser.add_argument(
        "--query", type=str, default=None,
        help="Manual search query (skips pin recommendations)",
    )

    args = parser.parse_args()
    cmd_generate(args.associate_id, args.count, args.query)


if __name__ == "__main__":
    main()
