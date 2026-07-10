#!/usr/bin/env python3
"""
THE WIRE — automated AI news briefing builder.

Pipeline:
  1. Pull RSS/Atom feeds listed in feeds.txt
  2. Dedupe + sort, keep the newest items
  3. Verify every link responds (dead links are dropped)
  4. Summarise + tag each story with the Gemini API (falls back to
     raw feed text + keyword tagging if no key / API error)
  5. Render index.html and bump the issue number in state.json

Usage:
  python build.py            # full build (needs internet; uses GEMINI_API_KEY if set)
  python build.py --demo     # offline build with sample data, for previewing the design
"""

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

# ----------------------------------------------------------------------
# Config — edit these
# ----------------------------------------------------------------------
MAX_STORIES = 8          # full posts on the page
MAX_QUICK_HITS = 6       # one-liner links below the feed
LINK_TIMEOUT = 8         # seconds per link check
GEMINI_MODEL = "gemini-2.0-flash"   # update if Google renames models
SITE_TITLE = "The Wire"
SITE_DEK = ("A personal briefing on artificial intelligence — launches, "
            "alerts, research and policy. Curated for signal, published daily.")
LOCATION = "London, UK"
TZ = ZoneInfo("Europe/London")

TAG_KEYWORDS = {  # fallback tagger when Gemini is unavailable
    "alert":    ["outage", "vulnerability", "breach", "deprecat", "lawsuit",
                 "recall", "security", "leak", "shutdown", "incident"],
    "research": ["paper", "study", "benchmark", "arxiv", "research",
                 "researchers", "dataset", "training", "evaluation"],
    "policy":   ["regulation", "regulator", "law", "act", "policy", "eu ",
                 "congress", "senate", "government", "governance", "ruling",
                 "court", "ban", "compliance"],
}

UA = {"User-Agent": "Mozilla/5.0 (TheWire-briefing-bot; +https://github.com)"}


# ----------------------------------------------------------------------
# 1. Fetch feeds
# ----------------------------------------------------------------------
def load_feed_urls(path="feeds.txt"):
    urls = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def fetch_entries(urls):
    import feedparser
    items = []
    for url in urls:
        try:
            parsed = feedparser.parse(url, request_headers=UA)
            source = parsed.feed.get("title", url)[:60]
            for e in parsed.entries[:15]:
                published = e.get("published_parsed") or e.get("updated_parsed")
                ts = time.mktime(published) if published else time.time()
                items.append({
                    "title": (e.get("title") or "").strip(),
                    "link": (e.get("link") or "").strip(),
                    "snippet": re.sub(r"<[^>]+>", " ",
                                      e.get("summary", e.get("description", "")))[:600].strip(),
                    "source": source,
                    "ts": ts,
                })
        except Exception as ex:
            print(f"  [feed error] {url}: {ex}", file=sys.stderr)
    return items


def dedupe_and_sort(items):
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x["ts"], reverse=True):
        key = re.sub(r"\W+", "", it["title"].lower())[:80]
        if it["title"] and it["link"] and key not in seen:
            seen.add(key)
            out.append(it)
    return out


# ----------------------------------------------------------------------
# 2. Link checking
# ----------------------------------------------------------------------
def link_alive(url):
    try:
        r = requests.head(url, timeout=LINK_TIMEOUT, allow_redirects=True, headers=UA)
        if r.status_code < 400:
            return True
        r = requests.get(url, timeout=LINK_TIMEOUT, stream=True, headers=UA)
        return r.status_code < 400
    except requests.RequestException:
        return False


def check_links(items):
    checked = []
    for it in items:
        if link_alive(it["link"]):
            checked.append(it)
        else:
            print(f"  [dead link dropped] {it['link']}", file=sys.stderr)
    # Safety net: if the checker itself is being blocked everywhere,
    # don't publish an empty page.
    return checked if len(checked) >= 4 else items


# ----------------------------------------------------------------------
# 3. Gemini summarise + tag (with graceful fallback)
# ----------------------------------------------------------------------
def gemini_enrich(items, api_key):
    payload_items = [
        {"id": i, "title": it["title"], "source": it["source"],
         "snippet": it["snippet"][:400]}
        for i, it in enumerate(items)
    ]
    prompt = (
        "You are the editor of a daily AI-news briefing. For each item below, "
        "return a JSON array of objects with keys: id, tag, headline, summary.\n"
        "- tag: exactly one of launch, alert, research, policy. Use alert only "
        "for genuinely urgent items (outages, security, breaking changes).\n"
        "- headline: a clean, punchy version of the title, max 14 words.\n"
        "- summary: 1-2 sentences: what happened and why it matters. Use ONLY "
        "facts present in the title/snippet. Do not invent numbers, names or "
        "details. If the snippet is thin, keep the summary short.\n"
        "Return ONLY the JSON array, no markdown.\n\n"
        f"Items:\n{json.dumps(payload_items, ensure_ascii=False)}"
    )
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={api_key}")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json",
                             "temperature": 0.3},
    }
    r = requests.post(url, json=body, timeout=60)
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    enriched = {e["id"]: e for e in json.loads(text)}
    for i, it in enumerate(items):
        e = enriched.get(i, {})
        it["tag"] = e.get("tag") if e.get("tag") in ("launch", "alert",
                                                     "research", "policy") else None
        if e.get("headline"):
            it["title"] = e["headline"]
        it["summary"] = e.get("summary", "")
    return items


def heuristic_tag(item):
    text = (item["title"] + " " + item["snippet"]).lower()
    for tag, words in TAG_KEYWORDS.items():
        if any(w in text for w in words):
            return tag
    return "launch"


def enrich(items):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if api_key:
        try:
            items = gemini_enrich(items, api_key)
            print("  [gemini] summaries generated")
        except Exception as ex:
            print(f"  [gemini failed, using fallback] {ex}", file=sys.stderr)
    for it in items:
        if not it.get("tag"):
            it["tag"] = heuristic_tag(it)
        if not it.get("summary"):
            it["summary"] = (it["snippet"][:220] + "…") if len(it["snippet"]) > 220 \
                            else (it["snippet"] or "Read the full story at the source.")
    return items


# ----------------------------------------------------------------------
# 4. Render
# ----------------------------------------------------------------------
def esc(s):
    return html.escape(s or "", quote=True)


def load_state():
    try:
        with open("state.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"issue": 0}


def render(items):
    state = load_state()
    state["issue"] += 1
    issue_no = f"{state['issue']:03d}"
    now = datetime.now(TZ)
    issue_date = now.strftime("%A %-d %B %Y") if os.name != "nt" \
                 else now.strftime("%A %d %B %Y")
    built_at = now.strftime("%d %b %Y · %H:%M %Z")

    stories = items[:MAX_STORIES]
    hits = items[MAX_STORIES:MAX_STORIES + MAX_QUICK_HITS]
    tag_names = {"launch": "Launch", "alert": "Alert",
                 "research": "Research", "policy": "Policy"}

    ticker = "".join(
        f'<span><b>{tag_names[s["tag"]].upper()} ///</b> {esc(s["title"])}</span>'
        for s in stories[:4]
    )

    story_html = ""
    for i, s in enumerate(stories):
        stamp = datetime.fromtimestamp(s["ts"], TZ).strftime("%d %b %Y · %H:%M").upper()
        story_html += f"""
      <article class="story {'lead' if i == 0 else ''}" data-tag="{s['tag']}">
        <div class="story-meta">
          <span class="tag {s['tag']}">{tag_names[s['tag']]}</span>
          <span class="stamp mono">{stamp} — {esc(s['source'])}</span>
        </div>
        <h2><a href="{esc(s['link'])}" target="_blank" rel="noopener">{esc(s['title'])}</a></h2>
        <p>{esc(s['summary'])}</p>
        <a class="readmore" href="{esc(s['link'])}" target="_blank" rel="noopener">Read the full story →</a>
      </article>"""

    hits_html = "".join(
        f'\n      <li><span class="stamp mono">'
        f'{datetime.fromtimestamp(h["ts"], TZ).strftime("%d %b").upper()}</span>'
        f'<a href="{esc(h["link"])}" target="_blank" rel="noopener">{esc(h["title"])}</a></li>'
        for h in hits
    )

    with open("template.html") as f:
        page = f.read()
    page = (page
            .replace("{{TITLE}}", esc(SITE_TITLE))
            .replace("{{DEK}}", esc(SITE_DEK))
            .replace("{{LOCATION}}", esc(LOCATION))
            .replace("{{ISSUE_NO}}", issue_no)
            .replace("{{ISSUE_DATE}}", issue_date)
            .replace("{{BUILT_AT}}", built_at)
            .replace("{{TICKER}}", ticker)
            .replace("{{STORIES}}", story_html)
            .replace("{{QUICK_HITS}}", hits_html))

    with open("index.html", "w") as f:
        f.write(page)
    with open("state.json", "w") as f:
        json.dump(state, f)
    print(f"  [built] issue {issue_no} — {len(stories)} stories, {len(hits)} quick hits")


# ----------------------------------------------------------------------
# Demo data (offline preview)
# ----------------------------------------------------------------------
DEMO = [
    {"title": "Sample lead story — a major model release", "link": "https://example.com/1",
     "snippet": "A demonstration story so you can preview the layout offline.",
     "source": "Demo Feed", "ts": time.time(), "tag": "launch",
     "summary": "This is what a Gemini-written summary will look like: one or two sentences on what happened and why it matters."},
    {"title": "Sample alert — API deprecation notice", "link": "https://example.com/2",
     "snippet": "", "source": "Demo Feed", "ts": time.time() - 4000, "tag": "alert",
     "summary": "Alerts carry the red tag and should be used sparingly."},
    {"title": "Sample research — new benchmark results", "link": "https://example.com/3",
     "snippet": "", "source": "Demo Feed", "ts": time.time() - 9000, "tag": "research",
     "summary": "Research posts summarise papers and benchmarks in plain English."},
    {"title": "Sample policy — draft AI regulation", "link": "https://example.com/4",
     "snippet": "", "source": "Demo Feed", "ts": time.time() - 20000, "tag": "policy",
     "summary": "Policy posts track the rules of the game."},
] + [
    {"title": f"Sample quick hit #{n}", "link": f"https://example.com/q{n}",
     "snippet": "", "source": "Demo Feed", "ts": time.time() - 30000 - n,
     "tag": "launch", "summary": "One-liner."}
    for n in range(1, 7)
]


def main():
    if "--demo" in sys.argv:
        print("== demo build (offline sample data) ==")
        render(DEMO)
        return
    print("== fetching feeds ==")
    items = dedupe_and_sort(fetch_entries(load_feed_urls()))
    if not items:
        print("No items fetched — keeping the existing index.html untouched.",
              file=sys.stderr)
        sys.exit(0)   # exit cleanly so the workflow doesn't commit an empty page
    print(f"  {len(items)} unique items")
    print("== checking links ==")
    items = check_links(items[:MAX_STORIES + MAX_QUICK_HITS + 6])
    print("== summarising ==")
    items = enrich(items[:MAX_STORIES + MAX_QUICK_HITS])
    print("== rendering ==")
    render(items)


if __name__ == "__main__":
    main()
