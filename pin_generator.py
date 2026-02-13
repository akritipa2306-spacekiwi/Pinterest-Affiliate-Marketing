#!/usr/bin/env python3
"""
Pinterest Affiliate Marketing — Pin Content Generator
======================================================
Reads classified search queries from the discovery agent, picks the
top unused queries, and generates ready-to-post Pinterest pin content
using the Claude API.  Tracks which queries have been used so you
never create duplicate pins.

Usage:
  export ANTHROPIC_API_KEY="sk-ant-..."

  python3 pin_generator.py              # generate 3 pins (default)
  python3 pin_generator.py --count 5    # generate 5 pins
  python3 pin_generator.py --status     # show tracker stats
  python3 pin_generator.py --mark-posted "apartment office amazon finds"
  python3 pin_generator.py --mark-posted all   # mark all 'created' as posted

Inputs:
  search_discovery_results.json  (from discovery_agent.py)

Outputs:
  generated_pins/YYYY-MM-DD.json  (today's generated pin content)
  pin_tracker.json                (persistent tracking file)
"""

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

import anthropic

from affiliate_linker import (
    create_session,
    search_amazon,
    build_product_link,
    MIN_DELAY_SEC,
    MAX_DELAY_SEC,
    MAX_PRODUCTS_PER_QUERY,
)

# ── Load .env file if present (no extra dependency needed) ────────────────
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _val = _line.split("=", 1)
                os.environ.setdefault(_key.strip(), _val.strip())

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DISCOVERY_FILE = os.path.join(BASE_DIR, "search_discovery_results.json")
TRACKER_FILE = os.path.join(BASE_DIR, "pin_tracker.json")
PINS_DIR = os.path.join(BASE_DIR, "generated_pins")
DEFAULT_PIN_COUNT = 3

# ════════════════════════════════════════════════════════════════════════════
# TRACKER — load / save / query
# ════════════════════════════════════════════════════════════════════════════

def load_tracker() -> dict:
    """Load the tracker file, or create an empty one."""
    if os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "pins_created": [],
        "stats": {
            "total_pins_created": 0,
            "queries_used": [],
        },
    }


def save_tracker(tracker: dict):
    """Write the tracker back to disk."""
    with open(TRACKER_FILE, "w", encoding="utf-8") as f:
        json.dump(tracker, f, indent=2, ensure_ascii=False)


def is_query_used(tracker: dict, query: str) -> bool:
    """Check if a query has already been used (case-insensitive)."""
    used = {q.lower() for q in tracker["stats"]["queries_used"]}
    return query.lower() in used


def record_pin(tracker: dict, query: str, pin_content: dict, source: str):
    """Add a newly generated pin to the tracker."""
    tracker["pins_created"].append({
        "query": query,
        "date_created": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "pin_title": pin_content.get("pin_title", ""),
        "product_category": pin_content.get("product_category", ""),
        "source": source,
        "status": "created",
    })
    tracker["stats"]["total_pins_created"] += 1
    if query.lower() not in {q.lower() for q in tracker["stats"]["queries_used"]}:
        tracker["stats"]["queries_used"].append(query)


# ════════════════════════════════════════════════════════════════════════════
# DISCOVERY RESULTS — load & filter
# ════════════════════════════════════════════════════════════════════════════

def load_discovery_results() -> dict:
    """Load the most recent discovery results."""
    if not os.path.exists(DISCOVERY_FILE):
        print(f"ERROR: Discovery results not found at {DISCOVERY_FILE}")
        print("  Run discovery_agent.py first.")
        sys.exit(1)
    with open(DISCOVERY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_unused_queries(discovery: dict, tracker: dict, count: int) -> list:
    """
    Return the top `count` unused queries from the ranked results.
    Queries are already sorted by priority_score descending in the
    discovery output, so we just skip used ones.
    """
    ranked = discovery.get("all_results_ranked", [])
    unused = []
    for item in ranked:
        q = item.get("query", "")
        if not is_query_used(tracker, q):
            unused.append(item)
        if len(unused) >= count:
            break
    return unused


# ════════════════════════════════════════════════════════════════════════════
# CLAUDE API — Pin Content Generation
# ════════════════════════════════════════════════════════════════════════════

PIN_SYSTEM_PROMPT = """\
You are a Pinterest affiliate marketing expert specializing in home office \
products for small apartments and condos.

For each search query, generate Pinterest pin content optimized for:
- High click-through rate (curiosity + value)
- SEO visibility on Pinterest search
- Affiliate conversions (drive clicks to product links)

For each query, produce:

1. **pin_title** (max 100 characters)
   - Lead with a number or power word ("10 Best", "Game-Changing", "Genius")
   - Include the main keyword naturally
   - Create curiosity or promise a solution

2. **pin_description** (max 500 characters)
   - Open with an empathetic hook about small-space struggles
   - Mention 1-2 specific product benefits
   - End with a clear call-to-action: "Tap the link to shop" or "Find these at the link in bio"
   - Include 5-8 relevant hashtags at the end

3. **board_name** — which Pinterest board this pin belongs on
   (e.g., "Small Space Home Office Ideas", "Apartment Desk Setup")

4. **image_concept** — one sentence describing what the pin image should show
   (flat lay, styled desk shot, before/after, product collage, etc.)

Respond with ONLY a JSON array. Each element must have these exact keys:
  "query", "pin_title", "pin_description", "board_name", "image_concept"

Do NOT wrap the JSON in markdown code fences. Output raw JSON only.
"""


def generate_pin_content_claude(queries_with_data: list) -> list:
    """
    Send queries to Claude and get back pin content for each.
    Returns a list of pin content dicts.
    """
    client = anthropic.Anthropic()

    # Build context-rich prompt with classification data
    lines = []
    for i, item in enumerate(queries_with_data, 1):
        lines.append(
            f"{i}. Query: \"{item['query']}\" "
            f"| Intent: {item.get('intent', 'unknown')} "
            f"| Product: {item.get('product_category', 'general')} "
            f"| Affiliate Potential: {item.get('affiliate_potential', 3)}/5"
        )

    user_message = (
        "Generate Pinterest affiliate pin content for these high-intent "
        "small-apartment home-office search queries:\n\n"
        + "\n".join(lines)
    )

    print("  Generating pin content with Claude API...")

    try:
        message = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4096,
            system=PIN_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        raw = message.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        return json.loads(raw)

    except json.JSONDecodeError:
        print("  [warn] Could not parse Claude response. Using template fallback.")
    except anthropic.BadRequestError as exc:
        print(f"  [warn] Claude API billing error: {exc}")
        print("  => Using template fallback.")
    except anthropic.APIError as exc:
        print(f"  [warn] Claude API error: {exc}")
        print("  => Using template fallback.")
    except Exception as exc:
        print(f"  [warn] Unexpected error: {exc}")
        print("  => Using template fallback.")

    # ── Template fallback ─────────────────────────────────────────────
    return generate_pin_content_template(queries_with_data)


def generate_pin_content_template(queries_with_data: list) -> list:
    """
    Template-based fallback when Claude API is unavailable.
    Produces reasonable pin content using fill-in-the-blank patterns.
    """
    print("  Generating pin content with template engine...")

    templates = [
        {
            "title": "7 {category} Ideas That Actually Fit in a Small Apartment",
            "desc": (
                "Struggling to fit a home office in your tiny apartment? "
                "These space-saving {category} picks are perfect for small spaces. "
                "Tap the link to shop our favorites! "
                "#{query_tag} #SmallSpaceOffice #ApartmentOffice "
                "#HomeOffice #WorkFromHome #SmallApartment"
            ),
            "board": "Small Space Home Office Ideas",
        },
        {
            "title": "Best {category} for Small Spaces (Apartment-Friendly!)",
            "desc": (
                "Working from home in a small apartment? These compact "
                "{category} options maximize your space without sacrificing style. "
                "Find them at the link in bio! "
                "#{query_tag} #CompactOffice #SmallSpaceDesign "
                "#ApartmentLiving #RemoteWork #HomeOfficeTips"
            ),
            "board": "Apartment Desk Setup",
        },
        {
            "title": "Small Apartment? You NEED This {category} Setup",
            "desc": (
                "Stop letting limited space hold back your productivity. "
                "This {category} setup is designed for apartments and condos. "
                "Shop the look — link in bio! "
                "#{query_tag} #TinyOffice #SmallSpaceSolutions "
                "#HomeDeskSetup #ApartmentHack #OfficeInspo"
            ),
            "board": "Compact Home Office Furniture",
        },
    ]

    results = []
    for i, item in enumerate(queries_with_data):
        t = templates[i % len(templates)]
        cat = item.get("product_category", "home office")
        query_tag = item["query"].replace(" ", "")

        results.append({
            "query": item["query"],
            "pin_title": t["title"].format(category=cat.title())[:100],
            "pin_description": t["desc"].format(
                category=cat, query_tag=query_tag
            )[:500],
            "board_name": t["board"],
            "image_concept": (
                f"Styled flat-lay or lifestyle photo showing a {cat} "
                f"in a small, bright apartment setting"
            ),
        })

    return results


# ════════════════════════════════════════════════════════════════════════════
# OUTPUT — save pins & print summary
# ════════════════════════════════════════════════════════════════════════════

def save_generated_pins(pins: list) -> str:
    """Save pins to generated_pins/YYYY-MM-DD.json. Returns the file path."""
    os.makedirs(PINS_DIR, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"{today}.json"
    filepath = os.path.join(PINS_DIR, filename)

    # If file already exists (ran multiple times today), merge
    existing = []
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            existing = data.get("pins", [])

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": today,
        "total_pins": len(existing) + len(pins),
        "pins": existing + pins,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    return filepath


def print_pin_content(pins: list):
    """Pretty-print generated pin content to terminal."""
    for i, pin in enumerate(pins, 1):
        print(f"\n  {'─' * 58}")
        print(f"  PIN #{i}")
        print(f"  {'─' * 58}")
        print(f"  Query:       {pin['query']}")
        print(f"  Title:       {pin['pin_title']}")
        print(f"  Board:       {pin['board_name']}")
        print(f"  Image:       {pin['image_concept']}")
        print(f"\n  Description:")
        # Word-wrap description at ~60 chars
        desc = pin["pin_description"]
        while desc:
            print(f"    {desc[:60]}")
            desc = desc[60:]
        print(f"\n  Amazon Products:")
        amazon = pin.get("amazon_products", [])
        if not amazon:
            print("    (no products found)")
        for p in amazon:
            truncated = p["title"][:65]
            ellipsis = "..." if len(p["title"]) > 65 else ""
            print(f"    - {truncated}{ellipsis}")
            print(f"      Price: {p.get('price', 'N/A')}  |  "
                  f"Reviews: {p.get('review_count', 0):,}")
            print(f"      Link:  {p.get('product_link', '')}")


# ════════════════════════════════════════════════════════════════════════════
# CLI COMMANDS
# ════════════════════════════════════════════════════════════════════════════

def cmd_status():
    """Show tracker statistics."""
    tracker = load_tracker()
    pins = tracker["pins_created"]
    stats = tracker["stats"]

    created = sum(1 for p in pins if p["status"] == "created")
    posted = sum(1 for p in pins if p["status"] == "posted")
    skipped = sum(1 for p in pins if p["status"] == "skipped")

    # Count unused from discovery results
    unused_count = "N/A"
    if os.path.exists(DISCOVERY_FILE):
        discovery = load_discovery_results()
        ranked = discovery.get("all_results_ranked", [])
        unused_count = sum(
            1 for item in ranked
            if not is_query_used(tracker, item.get("query", ""))
        )

    print("=" * 56)
    print("  PIN TRACKER STATUS")
    print("=" * 56)
    print(f"\n  Total Pins Generated:  {stats['total_pins_created']}")
    print(f"    Created (not posted): {created}")
    print(f"    Posted to Pinterest:  {posted}")
    print(f"    Skipped:              {skipped}")
    print(f"\n  Queries Used:          {len(stats['queries_used'])}")
    print(f"  Queries Remaining:     {unused_count}")

    if pins:
        print(f"\n  Recent Pins:")
        for p in pins[-5:]:
            status_icon = {"created": "[ ]", "posted": "[x]", "skipped": "[-]"}
            icon = status_icon.get(p["status"], "[?]")
            print(f"    {icon} {p['date_created']}  {p['query']}")
            print(f"        → {p.get('pin_title', 'N/A')}")

    print()


def cmd_mark_posted(query: str):
    """Mark a query as posted to Pinterest."""
    tracker = load_tracker()

    if query.lower() == "all":
        count = 0
        for pin in tracker["pins_created"]:
            if pin["status"] == "created":
                pin["status"] = "posted"
                count += 1
        save_tracker(tracker)
        print(f"  Marked {count} pin(s) as posted.")
        return

    found = False
    for pin in tracker["pins_created"]:
        if pin["query"].lower() == query.lower():
            pin["status"] = "posted"
            found = True

    if found:
        save_tracker(tracker)
        print(f"  Marked as posted: \"{query}\"")
    else:
        print(f"  Query not found in tracker: \"{query}\"")
        print("  Available queries:")
        for p in tracker["pins_created"]:
            print(f"    - {p['query']} ({p['status']})")


def cmd_generate(count: int):
    """Main generation command."""
    # Validate API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("WARNING: ANTHROPIC_API_KEY not set. Will use template fallback.")

    print("=" * 56)
    print("  PIN CONTENT GENERATOR")
    print("=" * 56)

    # Load inputs
    print("\n[1/5] Loading discovery results & tracker...")
    discovery = load_discovery_results()
    tracker = load_tracker()

    gen_date = discovery["metadata"]["generated_at"][:10]
    total_queries = discovery["metadata"]["total_classified"]
    used_count = len(tracker["stats"]["queries_used"])
    print(f"  Discovery run: {gen_date} ({total_queries} queries)")
    print(f"  Already used:  {used_count} queries")

    # Get unused queries
    print(f"\n[2/5] Selecting top {count} unused queries...")
    candidates = get_unused_queries(discovery, tracker, count)

    if not candidates:
        print("\n  No unused queries remaining!")
        print("  Run discovery_agent.py to collect fresh queries.")
        return

    if len(candidates) < count:
        print(f"  Only {len(candidates)} unused queries remaining "
              f"(requested {count})")

    for i, c in enumerate(candidates, 1):
        print(f"  {i}. [{c.get('priority_score', 0):.1f}] {c['query']} "
              f"({c.get('intent', '?')})")

    # Generate content
    print(f"\n[3/5] Generating pin content...")
    pin_contents = generate_pin_content_claude(candidates)

    # Merge classification data into pin content
    for pin, candidate in zip(pin_contents, candidates):
        pin["source"] = candidate.get("source", "")
        pin["intent"] = candidate.get("intent", "")
        pin["affiliate_potential"] = candidate.get("affiliate_potential", 0)
        pin["priority_score"] = candidate.get("priority_score", 0)
        pin["product_category"] = (
            pin.get("product_category")
            or candidate.get("product_category", "")
        )

    # Search Amazon for real products
    print(f"\n[4/5] Searching Amazon for products...")
    session = create_session()
    for i, pin in enumerate(pin_contents):
        query = pin.get("query", "")
        print(f"  [{i+1}/{len(pin_contents)}] \"{query}\"")
        products = search_amazon(session, query,
                                 max_results=MAX_PRODUCTS_PER_QUERY)
        # Attach product links and build amazon_products list
        amazon_products = []
        for p in products:
            amazon_products.append({
                "asin": p["asin"],
                "title": p["title"],
                "price": p.get("price", "See Amazon"),
                "review_count": p.get("review_count", 0),
                "image_url": p.get("image_url", ""),
                "product_link": build_product_link(p["asin"]),
            })
        pin["amazon_products"] = amazon_products
        print(f"    → {len(amazon_products)} product(s) found")

        # Rate-limit between requests (skip after last)
        if i < len(pin_contents) - 1:
            delay = random.uniform(MIN_DELAY_SEC, MAX_DELAY_SEC)
            print(f"    Waiting {delay:.1f}s before next search...")
            time.sleep(delay)

    # Save & track
    print(f"\n[5/5] Saving results & updating tracker...")
    filepath = save_generated_pins(pin_contents)

    for pin, candidate in zip(pin_contents, candidates):
        record_pin(
            tracker,
            candidate["query"],
            pin,
            candidate.get("source", "unknown"),
        )
    save_tracker(tracker)

    # Print results
    print("\n" + "=" * 56)
    print(f"  GENERATED {len(pin_contents)} PIN(S)")
    print("=" * 56)
    print_pin_content(pin_contents)

    remaining = total_queries - len(tracker["stats"]["queries_used"])
    print(f"\n  {'─' * 58}")
    print(f"  Saved to:         {filepath}")
    print(f"  Tracker updated:  {TRACKER_FILE}")
    print(f"  Queries remaining: {remaining}")
    print(f"\n  Next steps:")
    print(f"    1. Create pin images based on the image concepts above")
    print(f"    2. Post to Pinterest with the title + description")
    print(f"    3. Run: python3 pin_generator.py --mark-posted \"<query>\"")
    print()


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Pinterest Affiliate — Pin Content Generator"
    )
    parser.add_argument(
        "--count", type=int, default=DEFAULT_PIN_COUNT,
        help=f"Number of pins to generate (default: {DEFAULT_PIN_COUNT})"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show tracker statistics"
    )
    parser.add_argument(
        "--mark-posted", metavar="QUERY",
        help='Mark a query as posted (use "all" to mark all created as posted)'
    )

    args = parser.parse_args()

    if args.status:
        cmd_status()
    elif args.mark_posted:
        cmd_mark_posted(args.mark_posted)
    else:
        cmd_generate(args.count)


if __name__ == "__main__":
    main()
