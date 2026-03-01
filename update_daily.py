#!/usr/bin/env python3
"""
update_daily.py — Simpsons Did It Daily Updater

Run this once per day (via cron, GitHub Action, etc.) to:
  1. Fetch today's top headlines
  2. Ask Claude to pick the best matching Simpsons scene
  3. Search Frinkiac for the screenshot
  4. Download the image and embed it as base64 in the HTML
  5. (Optional) Git commit & push to deploy via GitHub Pages

Requirements:
  pip install anthropic requests

Environment variables:
  ANTHROPIC_API_KEY  — Your Claude API key
  NEWS_API_KEY       — (Optional) NewsAPI.org key for headlines
                       If not set, uses RSS feeds instead
"""

import anthropic
import requests
import base64
import json
import re
import os
import sys
from datetime import datetime


# ── Config ──────────────────────────────────────────────────────

HTML_FILE = "simpsons-daily.html"   # Path to your HTML file
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")


# ── Step 1: Get today's headlines ───────────────────────────────

def get_headlines_newsapi():
    """Fetch top headlines from NewsAPI.org (requires free API key)."""
    url = "https://newsapi.org/v2/top-headlines"
    params = {"country": "us", "pageSize": 10, "apiKey": NEWS_API_KEY}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    articles = r.json().get("articles", [])
    return [a["title"] for a in articles if a.get("title")]


def get_headlines_rss():
    """Fallback: scrape headlines from AP News RSS (no key needed)."""
    import xml.etree.ElementTree as ET
    feeds = [
        "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "https://feeds.bbci.co.uk/news/rss.xml",
    ]
    headlines = []
    for feed_url in feeds:
        try:
            r = requests.get(feed_url, timeout=10)
            root = ET.fromstring(r.content)
            for item in root.findall(".//item/title")[:5]:
                if item.text:
                    headlines.append(item.text.strip())
        except Exception as e:
            print(f"  Warning: couldn't fetch {feed_url}: {e}")
    return headlines


def get_headlines():
    """Get headlines from best available source."""
    if NEWS_API_KEY:
        print("📰 Fetching headlines from NewsAPI...")
        try:
            return get_headlines_newsapi()
        except Exception as e:
            print(f"  NewsAPI failed ({e}), falling back to RSS...")
    print("📰 Fetching headlines from RSS feeds...")
    return get_headlines_rss()


# ── Step 2: Ask Claude to match a Simpsons scene ───────────────

CLAUDE_PROMPT = """You are an expert on The Simpsons (seasons 1-15 especially). Given today's news headlines, pick the single most fitting, funny, or eerily prophetic Simpsons scene.

Today's date: {date}

Today's headlines:
{headlines}

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "episodeTitle": "Name of the episode",
  "season": 5,
  "episode": 12,
  "frinkiacQuery": "a short quote or description to search on Frinkiac",
  "caption": "The quote or a short funny caption in quotes",
  "newsHeadline": "The specific headline you're matching (rewrite concisely if needed)",
  "connectionNote": "1-2 sentences explaining why this scene matches today's news. Be witty."
}}

Pick scenes that are:
- Visually recognizable and iconic
- From a specific memorable moment (not just a generic shot)
- Funny in context of the news
- From seasons 1-15 if possible (Frinkiac has better coverage)

Prefer scenes that went viral or are well-known memes when possible."""


def ask_claude(headlines):
    """Send headlines to Claude and get a Simpsons match."""
    print("🤖 Asking Claude for a Simpsons match...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    today = datetime.now().strftime("%B %d, %Y")
    headline_text = "\n".join(f"- {h}" for h in headlines)

    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": CLAUDE_PROMPT.format(date=today, headlines=headline_text)
        }]
    )

    response_text = message.content[0].text.strip()
    # Strip markdown code fences if present
    response_text = re.sub(r'^```json\s*', '', response_text)
    response_text = re.sub(r'\s*```$', '', response_text)

    data = json.loads(response_text)
    print(f"  → Matched: S{data['season']:02d}E{data['episode']:02d} \"{data['episodeTitle']}\"")
    print(f"  → Caption: {data['caption']}")
    return data


# ── Step 3: Search Frinkiac and download the screenshot ────────

def search_frinkiac(query):
    """Search Frinkiac for a scene and return (episode, timestamp)."""
    print(f"🔍 Searching Frinkiac for: \"{query}\"")
    url = f"https://frinkiac.com/api/search?q={requests.utils.quote(query)}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    results = r.json()

    if not results:
        print("  No results found, trying shorter query...")
        # Try first 3 words
        short = " ".join(query.split()[:3])
        url = f"https://frinkiac.com/api/search?q={requests.utils.quote(short)}"
        r = requests.get(url, timeout=10)
        results = r.json()

    if not results:
        return None, None

    # Pick the first result
    best = results[0]
    ep = best["Episode"]
    ts = best["Timestamp"]
    print(f"  → Found: {ep} at timestamp {ts}")
    return ep, ts


def download_frinkiac_image(episode, timestamp):
    """Download a Frinkiac screenshot and return as base64."""
    url = f"https://frinkiac.com/img/{episode}/{timestamp}.jpg"
    print(f"📸 Downloading: {url}")
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    b64 = base64.b64encode(r.content).decode("utf-8")
    print(f"  → Downloaded ({len(r.content)} bytes → {len(b64)} chars base64)")
    return b64, url


# ── Step 4: Update the HTML file ───────────────────────────────

def update_html(match_data, image_base64, frinkiac_url):
    """Replace the DAILY_DATA block in the HTML file."""
    print(f"📝 Updating {HTML_FILE}...")

    with open(HTML_FILE, "r") as f:
        html = f.read()

    today = datetime.now().strftime("%Y-%m-%d")

    # Build the new data block
    # Escape quotes in strings for JS
    def js_escape(s):
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    new_data = f'''  date: "{today}",
  imageBase64: "{image_base64}",
  frinkiacUrl: "{frinkiac_url}",
  caption: "{js_escape(match_data['caption'])}",
  episodeTitle: "{js_escape(match_data['episodeTitle'])}",
  season: {match_data['season']},
  episode: {match_data['episode']},
  newsHeadline: "{js_escape(match_data['newsHeadline'])}",
  connectionNote: "{js_escape(match_data['connectionNote'])}"'''

    # Replace between markers
    pattern = r'(// ===== BEGIN_DAILY_DATA =====\n).*?(\n  // ===== END_DAILY_DATA =====)'
    replacement = f'\\1{new_data}\\2'
    new_html = re.sub(pattern, replacement, html, flags=re.DOTALL)

    if new_html == html:
        print("  ⚠ Warning: markers not found, data may not have been updated!")
        return False

    with open(HTML_FILE, "w") as f:
        f.write(new_html)

    print("  ✅ HTML updated successfully!")
    return True


# ── Step 5 (Optional): Git commit & push ──────────────────────

def git_deploy():
    """Commit and push changes (for GitHub Pages deployment)."""
    today = datetime.now().strftime("%Y-%m-%d")
    os.system(f'git add {HTML_FILE}')
    os.system(f'git commit -m "🍩 Daily update: {today}"')
    os.system('git push')
    print("🚀 Pushed to GitHub!")


# ── Main ────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("🍩 SIMPSONS DID IT — Daily Update")
    print(f"📅 {datetime.now().strftime('%B %d, %Y')}")
    print("=" * 50)

    # Validate config
    if not ANTHROPIC_API_KEY:
        print("❌ Error: ANTHROPIC_API_KEY not set!")
        print("   export ANTHROPIC_API_KEY='your-key-here'")
        sys.exit(1)

    # 1. Headlines
    headlines = get_headlines()
    if not headlines:
        print("❌ No headlines found!")
        sys.exit(1)
    print(f"  Got {len(headlines)} headlines\n")

    # 2. Claude match
    match = ask_claude(headlines)
    print()

    # 3. Frinkiac
    episode, timestamp = search_frinkiac(match["frinkiacQuery"])
    if not episode:
        print("  ⚠ Frinkiac search failed, trying episode+generic search...")
        ep_code = f"S{match['season']:02d}E{match['episode']:02d}"
        episode, timestamp = search_frinkiac(ep_code)

    if episode and timestamp:
        image_b64, frinkiac_url = download_frinkiac_image(episode, timestamp)
    else:
        print("  ⚠ Could not find image on Frinkiac, proceeding without")
        image_b64 = None
        s, e = match["season"], match["episode"]
        frinkiac_url = f"https://frinkiac.com/?p=search&q={requests.utils.quote(match['frinkiacQuery'])}"
    print()

    # 4. Update HTML
    update_html(match, image_b64 or "null", frinkiac_url)
    print()

    # 5. Deploy (uncomment if using git)
    # git_deploy()

    print("✅ Done! Open simpsons-daily.html to see today's match.")
    print()


if __name__ == "__main__":
    main()
