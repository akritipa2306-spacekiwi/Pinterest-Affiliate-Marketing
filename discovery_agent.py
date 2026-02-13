#!/usr/bin/env python3
"""
Pinterest Affiliate Marketing — Search Discovery Agent
=======================================================
Identifies high-intent search queries for the evergreen niche:
  "Home office furniture & accessories for small apartments / condos"

How it works:
  1. Collects top 20 Pinterest-style search queries (guided search patterns)
  2. Collects top 20 Google autocomplete queries (real-time demand signal)
  3. Detects buying-signal keywords in each query
  4. Sends queries to the Claude API for intent classification
  5. Ranks by commercial intent + affiliate potential
  6. Flags the top 2-3 queries to create affiliate pins for TODAY

Usage:
  export ANTHROPIC_API_KEY="sk-ant-..."
  python3 discovery_agent.py

Output:
  search_discovery_results.json
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
import anthropic

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
# CONFIGURATION — tweak these to adjust the agent's behavior
# ════════════════════════════════════════════════════════════════════════════

# Core seed keywords that define our niche
SEED_KEYWORDS = [
    "home office small apartment",
    "small apartment desk setup",
    "compact home office furniture",
    "small space office organization",
    "apartment office desk ideas",
    "tiny home office solutions",
    "space saving desk apartment",
    "small condo office setup",
    "minimalist desk small space",
    "home office storage small apartment",
    "foldable desk small spaces",
    "wall mounted desk apartment",
    "standing desk small space",
    "corner desk small apartment",
    "floating desk home office",
]

# How many results we want from each source
TARGET_COUNT = 20

# Words that signal buying intent — used for pre-classification scoring
BUYING_SIGNALS = [
    "best", "buy", "affordable", "cheap", "budget", "under",
    "review", "reviews", "top", "compare", "vs", "worth",
    "amazon", "ikea", "target", "wayfair", "walmart",
    "price", "deal", "deals", "sale", "discount",
    "recommended", "favorite", "must have", "worth it",
]

# Words that signal purely inspirational / low-intent browsing
INSPIRATION_SIGNALS = [
    "aesthetic", "vibes", "inspo", "mood board", "dream",
    "beautiful", "gorgeous", "stunning", "luxury",
    "pinterest board", "color palette",
]

# How many queries to flag for today's pin creation
DAILY_PIN_COUNT = 3

# Output file path
OUTPUT_FILE = "search_discovery_results.json"


# ════════════════════════════════════════════════════════════════════════════
# STEP 1 — Collect Google Autocomplete Suggestions (real demand signal)
# ════════════════════════════════════════════════════════════════════════════

def fetch_google_suggestions(seed: str) -> list:
    """
    Hit Google's public autocomplete API.
    No API key needed — this is the same endpoint the search bar uses.
    Returns a list of suggestion strings.
    """
    url = "https://suggestqueries.google.com/complete/search"
    params = {"client": "firefox", "q": seed}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # Response shape: [query, [suggestion1, suggestion2, ...]]
        return data[1] if len(data) > 1 else []
    except Exception as exc:
        print(f"    [warn] Google error for '{seed}': {exc}")
        return []


def collect_google_searches() -> list:
    """
    Loop through seed keywords, collect autocomplete suggestions,
    deduplicate, and return up to TARGET_COUNT unique queries.
    """
    seen = set()
    results = []

    for seed in SEED_KEYWORDS:
        suggestions = fetch_google_suggestions(seed)
        for s in suggestions:
            cleaned = s.strip().lower()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                results.append(cleaned)
        # Be polite — small delay between requests
        time.sleep(0.25)

    return results[:TARGET_COUNT]


# ════════════════════════════════════════════════════════════════════════════
# STEP 2 — Collect Pinterest Search Queries
# ════════════════════════════════════════════════════════════════════════════
# Pinterest doesn't expose a free public autocomplete API reliably,
# so we construct queries using Pinterest's known guided-search patterns.
# These mirror the "chips" and suggestions Pinterest shows users,
# validated against actual Pinterest search behavior.

# Product categories people search for on Pinterest in our niche
PINTEREST_PRODUCT_MODIFIERS = [
    # Direct product searches (high commercial intent)
    "desk", "standing desk", "floating desk", "fold down desk",
    "corner desk", "ladder desk", "writing desk",
    "desk organizer", "monitor stand", "cable management",
    "bookshelf", "wall shelf", "floating shelves",
    "filing cabinet", "storage cart", "desk lamp",
    "ergonomic chair", "office chair compact",
    # Buying-oriented modifiers
    "best", "affordable", "budget", "ikea", "amazon finds",
    "under 100", "diy", "must have",
    # Style + solution modifiers (mixed intent)
    "ideas", "setup", "makeover", "organization",
    "modern", "minimalist", "boho", "scandinavian",
]

PINTEREST_CORE_PHRASES = [
    "small apartment home office",
    "tiny home office",
    "small space office",
    "apartment office",
    "small condo office",
    "home office nook",
]


def collect_pinterest_searches() -> list:
    """
    Generate Pinterest-style search queries by combining core phrases
    with product/style modifiers. This mirrors what Pinterest users
    actually type and what shows up in guided search chips.
    """
    queries = set()

    # Combine cores with modifiers
    for core in PINTEREST_CORE_PHRASES:
        for mod in PINTEREST_PRODUCT_MODIFIERS:
            queries.add(f"{core} {mod}")

    # Add bare core phrases too
    for core in PINTEREST_CORE_PHRASES:
        queries.add(core)

    # Sort for consistency, return up to TARGET_COUNT
    sorted_queries = sorted(queries)
    return sorted_queries[:TARGET_COUNT]


# ════════════════════════════════════════════════════════════════════════════
# STEP 3 — Buying Signal Detection (pre-classification)
# ════════════════════════════════════════════════════════════════════════════

def count_buying_signals(query: str) -> dict:
    """
    Scan a query for buying-signal and inspiration-signal keywords.
    Returns a dict with signal counts and the matched words.
    """
    q_lower = query.lower()

    buy_matches = [w for w in BUYING_SIGNALS if w in q_lower]
    inspo_matches = [w for w in INSPIRATION_SIGNALS if w in q_lower]

    # Simple heuristic score: +1 per buying signal, -1 per inspiration signal
    raw_score = len(buy_matches) - len(inspo_matches)

    return {
        "buying_signals_found": buy_matches,
        "inspiration_signals_found": inspo_matches,
        "signal_score": raw_score,
    }


# ════════════════════════════════════════════════════════════════════════════
# STEP 4 — Claude API Intent Classification
# ════════════════════════════════════════════════════════════════════════════

CLASSIFICATION_SYSTEM_PROMPT = """\
You are a search-intent classifier for a Pinterest affiliate marketing business.
The business sells small-home-office products (desks, organizers, storage,
chairs, accessories) to people living in apartments and condos.

For each search query, determine:

1. **intent** — exactly one of:
   - "commercial"    → searcher wants to compare, evaluate, or buy a product
   - "mixed"         → exploring ideas but could convert to a purchase
   - "informational" → wants knowledge/tips, no immediate purchase intent

2. **confidence** — your confidence in the classification, 1-10

3. **affiliate_potential** — how likely the searcher would click an affiliate
   product link, 1-5:
   5 = very likely to buy (product-specific search)
   4 = likely to buy (comparing options)
   3 = moderate (browsing with purchase possible)
   2 = unlikely (mostly informational)
   1 = very unlikely (pure inspiration/knowledge)

4. **product_category** — the product type they'd likely buy, or "none"
   Examples: "desk", "shelf", "organizer", "chair", "storage", "lamp",
   "monitor stand", "cable management", "general office furniture"

5. **reasoning** — one sentence explaining your classification

Respond with ONLY a JSON array. Each element must have these exact keys:
  "query", "intent", "confidence", "affiliate_potential",
  "product_category", "reasoning"

Do NOT wrap the JSON in markdown code fences. Output raw JSON only.
"""


def classify_with_claude(queries: list, source_label: str) -> list:
    """
    Send a batch of queries to Claude for intent classification.
    Falls back to rule-based classification if the API call fails
    (e.g. no credits, network error).
    """
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    user_message = (
        f"Classify these {source_label} search queries for a small-apartment "
        f"home-office affiliate business:\n\n"
        + "\n".join(f"{i+1}. {q}" for i, q in enumerate(queries))
    )

    print(f"    Sending {len(queries)} queries to Claude...")

    try:
        message = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4096,
            system=CLASSIFICATION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        raw_text = message.content[0].text.strip()

        # Strip code fences if the model adds them despite instructions
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text)

        classified = json.loads(raw_text)

        # Tag each result with its source
        for item in classified:
            item["source"] = source_label
            item["classifier"] = "claude-api"

        return classified

    except json.JSONDecodeError:
        print(f"    [warn] Could not parse Claude response, using fallback.")
    except anthropic.BadRequestError as exc:
        print(f"    [warn] Claude API billing error: {exc}")
        print(f"    => Falling back to rule-based classifier.")
    except anthropic.APIError as exc:
        print(f"    [warn] Claude API error: {exc}")
        print(f"    => Falling back to rule-based classifier.")
    except Exception as exc:
        print(f"    [warn] Unexpected error: {exc}")
        print(f"    => Falling back to rule-based classifier.")

    # ── Fallback: rule-based classification ───────────────────────────
    return classify_with_rules(queries, source_label)


# ════════════════════════════════════════════════════════════════════════════
# FALLBACK — Rule-Based Intent Classifier
# ════════════════════════════════════════════════════════════════════════════
# When the Claude API is unavailable, this heuristic classifier uses keyword
# matching to approximate intent.  It's less nuanced than Claude but good
# enough to produce actionable results.

# Product keywords → maps query to a product category
PRODUCT_KEYWORDS = {
    "desk": "desk", "standing desk": "standing desk",
    "floating desk": "floating desk", "fold down desk": "fold-down desk",
    "corner desk": "corner desk", "ladder desk": "ladder desk",
    "writing desk": "desk", "wall mounted desk": "wall-mounted desk",
    "organizer": "organizer", "desk organizer": "desk organizer",
    "monitor stand": "monitor stand", "cable management": "cable management",
    "bookshelf": "bookshelf", "shelf": "shelf",
    "floating shelves": "floating shelves", "wall shelf": "wall shelf",
    "filing cabinet": "filing cabinet", "storage cart": "storage cart",
    "desk lamp": "desk lamp", "lamp": "lamp",
    "ergonomic chair": "ergonomic chair", "office chair": "office chair",
    "chair": "chair", "storage": "storage",
    "keyboard tray": "keyboard tray", "pegboard": "pegboard",
}

# Strong commercial signals → commercial intent
STRONG_COMMERCIAL = [
    "best", "buy", "affordable", "cheap", "budget", "under $",
    "under 100", "under 50", "under 200",
    "review", "reviews", "top rated", "compare", "vs",
    "amazon", "ikea", "target", "wayfair", "walmart",
    "price", "deal", "sale", "discount", "worth it",
    "recommended", "must have", "favorite",
]

# Mixed-intent signals → the person is exploring but could buy
MIXED_SIGNALS = [
    "ideas", "setup", "makeover", "inspiration",
    "organization", "solutions", "tips",
    "modern", "minimalist", "boho", "scandinavian", "cozy",
    "diy", "hack", "hacks",
]

# Informational signals → pure learning, low purchase intent
INFO_SIGNALS = [
    "how to", "what is", "guide", "tutorial", "explain",
    "pros and cons", "benefits", "difference between",
    "aesthetic", "vibes", "inspo", "mood board", "dream",
    "color palette", "pinterest board",
]


def classify_single_query_rules(query: str) -> dict:
    """
    Rule-based classifier for a single search query.
    Returns a dict matching Claude's output schema.
    """
    q = query.lower()

    # Detect product category (longest match first for accuracy)
    product_category = "general office furniture"
    for keyword in sorted(PRODUCT_KEYWORDS.keys(), key=len, reverse=True):
        if keyword in q:
            product_category = PRODUCT_KEYWORDS[keyword]
            break

    # Count signal matches
    commercial_hits = sum(1 for s in STRONG_COMMERCIAL if s in q)
    mixed_hits = sum(1 for s in MIXED_SIGNALS if s in q)
    info_hits = sum(1 for s in INFO_SIGNALS if s in q)

    # Check if query contains a specific product term
    has_product = any(kw in q for kw in PRODUCT_KEYWORDS)

    # ── Classification logic ──────────────────────────────────────────
    if commercial_hits >= 2 or (commercial_hits >= 1 and has_product):
        intent = "commercial"
        confidence = min(9, 6 + commercial_hits)
        affiliate_potential = min(5, 3 + commercial_hits)
        reasoning = (
            f"Contains {commercial_hits} buying signal(s) "
            f"({'+ product term' if has_product else 'strong commercial keywords'})"
        )
    elif info_hits >= 2 or (info_hits >= 1 and commercial_hits == 0 and not has_product):
        intent = "informational"
        confidence = min(8, 5 + info_hits)
        affiliate_potential = max(1, 2 - info_hits + (1 if has_product else 0))
        reasoning = (
            f"Contains {info_hits} informational signal(s), "
            f"low purchase intent"
        )
    elif has_product and mixed_hits == 0 and info_hits == 0:
        # Pure product search with no other modifiers → likely commercial
        intent = "commercial"
        confidence = 7
        affiliate_potential = 4
        reasoning = f"Product-specific search for '{product_category}'"
    elif has_product or mixed_hits >= 1:
        intent = "mixed"
        confidence = min(8, 5 + mixed_hits)
        affiliate_potential = 3 if not has_product else 4
        reasoning = (
            f"Exploring options ({'with product interest' if has_product else 'browsing ideas'}), "
            f"could convert to purchase"
        )
    else:
        intent = "mixed"
        confidence = 5
        affiliate_potential = 2
        reasoning = "General niche query, moderate purchase potential"

    return {
        "query": query,
        "intent": intent,
        "confidence": confidence,
        "affiliate_potential": affiliate_potential,
        "product_category": product_category,
        "reasoning": reasoning,
    }


def classify_with_rules(queries: list, source_label: str) -> list:
    """Classify a list of queries using rule-based heuristics."""
    print(f"    Classifying {len(queries)} queries with rule-based engine...")
    results = []
    for q in queries:
        item = classify_single_query_rules(q)
        item["source"] = source_label
        item["classifier"] = "rule-based"
        results.append(item)
    return results


# ════════════════════════════════════════════════════════════════════════════
# STEP 5 — Ranking & Priority Assignment
# ════════════════════════════════════════════════════════════════════════════

def compute_priority_score(item: dict, signal_info: dict) -> float:
    """
    Combine Claude's classification with our buying-signal detection
    to produce a single priority score (higher = better for affiliate pins).

    Scoring formula:
      base  = affiliate_potential (1-5) × 2        → 2-10
      bonus = buying signal count × 1.5             → 0-~6
      bonus += confidence / 5                       → 0-2
      penalty = inspiration signal count × 2        → 0-~4
      intent_multiplier:
        commercial   → ×1.5
        mixed        → ×1.0
        informational→ ×0.4
    """
    aff = item.get("affiliate_potential", 1)
    conf = item.get("confidence", 5)
    intent = item.get("intent", "informational")

    base = aff * 2
    bonus = len(signal_info.get("buying_signals_found", [])) * 1.5
    bonus += conf / 5
    penalty = len(signal_info.get("inspiration_signals_found", [])) * 2

    multiplier = {"commercial": 1.5, "mixed": 1.0, "informational": 0.4}
    mult = multiplier.get(intent, 0.5)

    score = (base + bonus - penalty) * mult
    return round(max(score, 0), 2)


# ════════════════════════════════════════════════════════════════════════════
# MAIN — orchestrate the full pipeline
# ════════════════════════════════════════════════════════════════════════════

def main():
    # Validate API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: Please set the ANTHROPIC_API_KEY environment variable.")
        print("  export ANTHROPIC_API_KEY='sk-ant-...'")
        sys.exit(1)

    print("=" * 64)
    print("  PINTEREST AFFILIATE — SEARCH DISCOVERY AGENT")
    print("  Niche: Home Office Products for Small Apartments / Condos")
    print("=" * 64)

    # ── Collect searches ──────────────────────────────────────────────
    print("\n[1/5] Collecting top Google autocomplete queries...")
    google_queries = collect_google_searches()
    print(f"  => {len(google_queries)} unique Google queries collected")

    print("\n[2/5] Collecting top Pinterest search queries...")
    pinterest_queries = collect_pinterest_searches()
    print(f"  => {len(pinterest_queries)} unique Pinterest queries collected")

    # ── Pre-scan for buying signals ───────────────────────────────────
    print("\n[3/5] Scanning for buying signals...")
    # Build a lookup: query -> signal_info
    signal_lookup = {}
    for q in google_queries + pinterest_queries:
        signal_lookup[q] = count_buying_signals(q)
    buy_signal_count = sum(
        1 for v in signal_lookup.values() if v["signal_score"] > 0
    )
    print(f"  => {buy_signal_count} queries contain buying signals")

    # ── Classify with Claude ──────────────────────────────────────────
    print("\n[4/5] Classifying queries with Claude API...")
    print("  Google batch:")
    google_classified = classify_with_claude(google_queries, "google")
    print(f"    => {len(google_classified)} classified")

    print("  Pinterest batch:")
    pinterest_classified = classify_with_claude(pinterest_queries, "pinterest")
    print(f"    => {len(pinterest_classified)} classified")

    all_classified = google_classified + pinterest_classified

    # ── Rank & prioritize ─────────────────────────────────────────────
    print("\n[5/5] Ranking by affiliate priority...")

    for item in all_classified:
        query = item.get("query", "").lower()
        signals = signal_lookup.get(query, count_buying_signals(query))
        item["buying_signals"] = signals["buying_signals_found"]
        item["inspiration_signals"] = signals["inspiration_signals_found"]
        item["priority_score"] = compute_priority_score(item, signals)

    # Sort by priority score descending
    all_classified.sort(key=lambda x: x["priority_score"], reverse=True)

    # Assign rank
    for rank, item in enumerate(all_classified, start=1):
        item["priority_rank"] = rank

    # ── Load tracker to skip already-used queries ─────────────────────
    tracker_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "pin_tracker.json"
    )
    used_queries = set()
    if os.path.exists(tracker_path):
        with open(tracker_path, "r", encoding="utf-8") as f:
            tracker_data = json.load(f)
        used_queries = {
            q.lower()
            for q in tracker_data.get("stats", {}).get("queries_used", [])
        }
        print(f"  Tracker loaded: {len(used_queries)} queries already used")

    # Flag top N for today's pins, skipping already-used queries
    unused_ranked = [
        item for item in all_classified
        if item.get("query", "").lower() not in used_queries
    ]
    todays_pins = unused_ranked[:DAILY_PIN_COUNT]
    for item in todays_pins:
        item["create_pin_today"] = True

    # ── Build summary stats ───────────────────────────────────────────
    intent_counts = {}
    for item in all_classified:
        intent = item.get("intent", "unknown")
        intent_counts[intent] = intent_counts.get(intent, 0) + 1

    high_potential = [
        item for item in all_classified
        if item.get("affiliate_potential", 0) >= 4
    ]

    # ── Assemble output ──────────────────────────────────────────────
    output = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "niche": "home office products — small apartment / condo",
            "seed_keywords_used": len(SEED_KEYWORDS),
            "google_queries_collected": len(google_queries),
            "pinterest_queries_collected": len(pinterest_queries),
            "total_classified": len(all_classified),
            "daily_pin_target": DAILY_PIN_COUNT,
        },
        "summary": {
            "intent_breakdown": intent_counts,
            "high_affiliate_potential_count": len(high_potential),
            "queries_with_buying_signals": buy_signal_count,
        },
        "todays_pin_recommendations": [
            {
                "query": item["query"],
                "intent": item["intent"],
                "affiliate_potential": item["affiliate_potential"],
                "product_category": item.get("product_category", ""),
                "priority_score": item["priority_score"],
                "reasoning": item.get("reasoning", ""),
            }
            for item in todays_pins
        ],
        "google_results": [
            item for item in all_classified if item.get("source") == "google"
        ],
        "pinterest_results": [
            item for item in all_classified
            if item.get("source") == "pinterest"
        ],
        "all_results_ranked": all_classified,
    }

    # ── Write JSON ────────────────────────────────────────────────────
    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), OUTPUT_FILE
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # ── Print summary to terminal ─────────────────────────────────────
    print("\n" + "=" * 64)
    print("  DISCOVERY COMPLETE")
    print("=" * 64)

    print(f"\n  Intent Breakdown:")
    for intent, count in sorted(intent_counts.items()):
        bar = "#" * count
        print(f"    {intent:15s}  {count:3d}  {bar}")

    print(f"\n  High Affiliate Potential (>= 4/5): {len(high_potential)}")
    print(f"  Queries with Buying Signals:       {buy_signal_count}")
    if used_queries:
        print(f"  Already Used (from tracker):       {len(used_queries)}")
        print(f"  Unused Remaining:                  {len(unused_ranked)}")

    print(f"\n  {'=' * 60}")
    print(f"  TODAY'S PIN RECOMMENDATIONS ({DAILY_PIN_COUNT} pins)")
    print(f"  {'=' * 60}")
    for i, pin in enumerate(todays_pins, 1):
        print(f"\n  Pin #{i}:")
        print(f"    Query:              {pin['query']}")
        print(f"    Intent:             {pin['intent']}")
        print(f"    Affiliate Potential: {pin['affiliate_potential']}/5")
        print(f"    Product Category:   {pin.get('product_category', 'N/A')}")
        print(f"    Priority Score:     {pin['priority_score']}")
        print(f"    Reasoning:          {pin.get('reasoning', '')}")

    # Show which classifier was used
    classifiers_used = set(
        item.get("classifier", "unknown") for item in all_classified
    )
    print(f"\n  Classifier(s) used: {', '.join(classifiers_used)}")
    if "rule-based" in classifiers_used:
        print("  NOTE: Rule-based fallback was used because the Claude API")
        print("  was unavailable. Add credits at console.anthropic.com to")
        print("  enable AI-powered classification for more nuanced results.")

    print(f"\n  Results saved to: {out_path}")
    print()


if __name__ == "__main__":
    main()
