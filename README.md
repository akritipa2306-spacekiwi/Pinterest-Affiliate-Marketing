# Pinterest Affiliate Marketing Agent


An automated pipeline for finding high-intent Pinterest search queries, generating ready-to-post pin content, and collecting Amazon affiliate product links — all powered by the Claude API.

**Niche:** Home office furniture & accessories for small apartments and condos.

---

## How It Works

The system is a three-stage pipeline:

```
discovery_agent.py
        │
        └──► search_discovery_results.json
                        │
              ┌─────────┴──────────┐
              ▼                    ▼
     pin_generator.py      affiliate_linker.py
              │
              ▼
   generated_pins/YYYY-MM-DD.json
   pin_tracker.json
```

**Stage 1 — Discover:** Find which search queries people are actually using right now and score them by affiliate potential.

**Stage 2a — Generate:** Turn the top queries into complete pin content (title, description, board, image concept) and attach matching Amazon products.

**Stage 2b — Link (optional):** Standalone mode to collect affiliate links for any query without generating full pin content.

**Automation:** `weekly_pins.sh` runs Stage 2a on a cron schedule and pushes `latest.json` to git.

---

## Project Structure

```
Pinterest-Affiliate-Marketing/
├── discovery_agent.py          # Stage 1: query discovery & intent classification
├── pin_generator.py            # Stage 2a: pin content generation + product search
├── affiliate_linker.py         # Stage 2b: standalone Amazon product link collector
├── weekly_pins.sh              # Cron-friendly automation script
│
├── search_discovery_results.json  # Output of discovery_agent; input to others
├── pin_tracker.json               # Tracks generated/posted pins (gitignored)
│
├── generated_pins/
│   ├── latest.json                # Most recent run — the only file pushed to git
│   └── YYYY-MM-DD.json            # Dated output files (local only, gitignored)
│
├── affiliate_links/
│   └── YYYY-MM-DD.json            # Output of affiliate_linker (local only)
│
├── logs/
│   └── weekly_pins.log            # Automation run logs (gitignored)
│
├── .env                           # API keys (gitignored — never commit)
└── .gitignore
```

---

## Prerequisites

- Python 3.8+
- An [Anthropic API key](https://console.anthropic.com)
- An [Amazon Associates](https://affiliate-program.amazon.com) account and associate tag

Install Python dependencies:

```bash
pip3 install requests beautifulsoup4 anthropic
```

---

## Setup

**1. Clone the repository**

```bash
git clone <your-repo-url>
cd Pinterest-Affiliate-Marketing
```

**2. Create your `.env` file**

```bash
cp .env.example .env   # if an example exists, otherwise create it manually
```

Edit `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
AMAZON_ASSOCIATE_ID=your-tag-20
```

The scripts load `.env` automatically — no `python-dotenv` package needed.

---

## Usage

### Stage 1 — Search Discovery Agent

Finds the highest-intent search queries for today's pins.

```bash
python3 discovery_agent.py
```

**What it does:**
1. Collects up to 20 real Google autocomplete suggestions for 15 seed keywords
2. Generates 20 Pinterest-style search queries from known search patterns
3. Scans every query for buying-signal keywords vs. inspiration-only keywords
4. Sends both batches to Claude for intent classification:
   - `commercial` — ready to compare or buy
   - `mixed` — exploring, but could convert
   - `informational` — low purchase intent
5. Ranks all queries by a weighted priority score
6. Skips queries already used (reads `pin_tracker.json`)
7. Flags the top 3 unused queries as **today's pin recommendations**

**Output:** `search_discovery_results.json`

**Fallback:** If the Claude API is unavailable, a built-in rule-based classifier runs instead and produces actionable results.

---

### Stage 2a — Pin Content Generator

Generates complete, ready-to-post Pinterest pin content.

```bash
# Generate 3 pins (default)
python3 pin_generator.py

# Generate a different number of pins
python3 pin_generator.py --count 5

# Show tracker stats (pins created, posted, remaining queries)
python3 pin_generator.py --status

# Mark a specific query as posted to Pinterest
python3 pin_generator.py --mark-posted "small apartment desk setup"

# Mark all 'created' pins as posted at once
python3 pin_generator.py --mark-posted all
```

**What it does (per pin):**
1. Picks the top unused queries from `search_discovery_results.json`
2. Sends them to Claude, which returns for each query:
   - **Pin title** (≤100 chars, opens with a number or power word)
   - **Pin description** (≤500 chars, empathetic hook + CTA + hashtags)
   - **Board name** (which Pinterest board it belongs on)
   - **Image concept** (one-sentence creative brief for the pin image)
3. Searches Amazon for real matching products (title, price, rating, review count, ASIN)
4. Saves everything to `generated_pins/YYYY-MM-DD.json`
5. Updates `pin_tracker.json` so those queries are never reused

**Outputs:**
- `generated_pins/YYYY-MM-DD.json` — full pin content with Amazon products
- `pin_tracker.json` — updated tracking file

**Fallback:** Template-based pin content is used if the Claude API is unavailable.

---

### Stage 2b — Affiliate Link Collector (standalone)

Searches Amazon and returns affiliate-ready product data without generating full pin content.

```bash
# Process today's top 3 pin recommendations
python3 affiliate_linker.py

# Process a specific number of recommendations
python3 affiliate_linker.py --count 2

# Search for any product manually
python3 affiliate_linker.py --query "small desk lamp"

# Use a different associate tag
python3 affiliate_linker.py --associate-id my-other-tag-20
```

**Output:** `affiliate_links/YYYY-MM-DD.json` — product list with ASINs, prices, ratings, review counts, and direct product links.

> **MVP note:** This script scrapes Amazon search results, which is a bootstrap approach before you qualify for the [Amazon Product Advertising API](https://webservices.amazon.com/paapi5/documentation/) (requires 3 qualifying sales). Switch to PA-API once eligible for reliable, ToS-compliant data.

---

### Automation — Weekly Cron Script

`weekly_pins.sh` runs the full generation pipeline unattended and pushes results to git.

```bash
# Make it executable (first time only)
chmod +x weekly_pins.sh

# Run manually
./weekly_pins.sh
```

**What it does:**
1. Sources `.env` to load API keys
2. Runs `pin_generator.py --count 3`
3. Copies the dated output to `generated_pins/latest.json`
4. Commits and pushes `latest.json` to `main`
5. Appends a timestamped log to `logs/weekly_pins.log`

**Set up as a cron job** (example: every Friday at 8 AM):

```bash
crontab -e
```

```cron
0 8 * * 5 /Users/akritiparida/Pinterest-Affiliate-Marketing/weekly_pins.sh
```

> Make sure `discovery_agent.py` has been run recently before the cron job executes, or add it as a first step in `weekly_pins.sh`.

---

## Output Files

### `search_discovery_results.json`

```json
{
  "metadata": { "generated_at": "...", "total_classified": 40 },
  "summary": {
    "intent_breakdown": { "commercial": 12, "mixed": 20, "informational": 8 },
    "high_affiliate_potential_count": 15,
    "queries_with_buying_signals": 18
  },
  "todays_pin_recommendations": [
    {
      "query": "best desk for small apartment",
      "intent": "commercial",
      "affiliate_potential": 5,
      "product_category": "desk",
      "priority_score": 18.3,
      "reasoning": "High-intent product search with strong commercial signals"
    }
  ],
  "all_results_ranked": [ ... ]
}
```

### `generated_pins/YYYY-MM-DD.json`

```json
{
  "date": "2026-02-13",
  "total_pins": 3,
  "pins": [
    {
      "query": "best desk for small apartment",
      "pin_title": "7 Best Desks for Small Apartments (Space-Saving Picks)",
      "pin_description": "Struggling to fit a home office into your tiny apartment? ...",
      "board_name": "Small Space Home Office Ideas",
      "image_concept": "Styled flat-lay of a compact white desk with monitor...",
      "amazon_products": [
        {
          "asin": "B08XYZ1234",
          "title": "Compact Writing Desk with Storage Shelf",
          "price": "$89.99",
          "review_count": 4823,
          "product_link": "https://www.amazon.com/dp/B08XYZ1234/"
        }
      ]
    }
  ]
}
```

### `affiliate_links/YYYY-MM-DD.json`

```json
{
  "associate_id": "endofjune-20",
  "total_pins": 3,
  "total_products": 12,
  "pins": [
    {
      "query": "best desk for small apartment",
      "products_found": 4,
      "products": [ ... ]
    }
  ]
}
```

---

## Recommended Workflow

```
Week 1 — Setup
  1. Run discovery_agent.py once
  2. Review search_discovery_results.json (check the top-ranked queries)
  3. Run pin_generator.py --count 3
  4. Inspect generated_pins/YYYY-MM-DD.json
  5. Create pin images based on the image_concept field
  6. Post pins to Pinterest manually
  7. Run: python3 pin_generator.py --mark-posted "query here"

Ongoing — Automated
  • Cron runs weekly_pins.sh each week
  • Re-run discovery_agent.py monthly (or when queries run low)
  • Check pin_generator.py --status to monitor progress
```

---

## Priority Scoring

The discovery agent ranks queries using a weighted formula:

| Factor | Weight |
|---|---|
| Affiliate potential (1–5) | ×2 base score |
| Buying signal keyword count | +1.5 per signal |
| Claude's classification confidence | +confidence/5 |
| Inspiration-only signal count | −2 per signal |
| Intent multiplier | commercial ×1.5, mixed ×1.0, informational ×0.4 |

Higher scores = better candidates for affiliate pins. The top 3 unused scores become today's recommendations.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes (for AI features) | Your Anthropic API key |
| `AMAZON_ASSOCIATE_ID` | Yes | Your Amazon Associates tracking tag (e.g., `yourtag-20`) |

Both are loaded automatically from `.env` in the project root. You can also export them as shell environment variables.

---

## Gitignore Policy

Only `generated_pins/latest.json` is committed to the repository. Everything else stays local:

| Gitignored | Reason |
|---|---|
| `.env` | Contains API keys |
| `logs/` | Noisy run logs |
| `pin_tracker.json` | Local state — committing would cause conflicts |
| `generated_pins/YYYY-MM-DD.json` | Dated files stay local; `latest.json` is the canonical output |
| `affiliate_links/` | Local research files |

---

## Troubleshooting

**`ANTHROPIC_API_KEY not set`**
Make sure your `.env` file exists in the project root and contains the key. The scripts load it automatically.

**`ERROR: search_discovery_results.json not found`**
Run `discovery_agent.py` before running `pin_generator.py` or `affiliate_linker.py`.

**`[warn] Amazon returned CAPTCHA / bot check`**
Amazon's bot detection triggered. Wait a few minutes and try again, or reduce the number of queries processed in one session.

**`No unused queries remaining`**
All discovered queries have been used. Re-run `discovery_agent.py` to collect fresh queries.

**Claude API used rule-based fallback**
The Claude API was unavailable (billing, network, etc.). The rule-based classifier produces good results but is less nuanced. Add credits at [console.anthropic.com](https://console.anthropic.com) to restore AI classification.
