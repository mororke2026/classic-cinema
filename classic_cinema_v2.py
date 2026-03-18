#!/usr/bin/env python3
"""
Classic Cinema Calendar — v2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scrapes showtimes from 7 local theaters, classifies classic/revival
films using Claude AI, cross-references your Letterboxd profile
(mororke), and outputs a beautiful HTML calendar + JSON for
Google Calendar import.

Usage:
    python3 classic_cinema_v2.py

Requirements:
    - Python 3.10+
    - ANTHROPIC_API_KEY environment variable set
    - Internet connection
"""

import json
import os
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from typing import Optional
import urllib.error
import urllib.parse
import urllib.request

# ══════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════

LETTERBOXD_USERNAME = "mororke"

THEATERS = {
    # ── CITY (NYC) ──────────────────────────────────────────
    "IFC Center": {
        "location": "city",
        "city": "New York, NY",
        "address": "323 6th Ave, New York, NY 10014",
        "url": "https://www.ifccenter.com/films/",
        "fandango_id": "AAXKP",
        "is_regal": False,
        "scrape_strategy": "ifc",
        "website": "https://www.ifccenter.com",
    },
    "Angelika Film Center": {
        "location": "city",
        "city": "New York, NY",
        "address": "18 W Houston St, New York, NY 10012",
        "url": "https://www.angelikafilmcenter.com/nyc",
        "fandango_id": "AABVV",
        "is_regal": False,
        "scrape_strategy": "angelika",
        "website": "https://www.angelikafilmcenter.com",
    },
    "Nitehawk Cinema": {
        "location": "city",
        "city": "Brooklyn, NY",
        "address": "136 Metropolitan Ave, Brooklyn, NY 11249",
        "url": "https://nitehawkcinema.com/williamsburg/",
        "fandango_id": "AARVP",
        "is_regal": False,
        "scrape_strategy": "nitehawk",
        "website": "https://nitehawkcinema.com",
    },

    # ── SUBURBS (Westchester) ────────────────────────────────
    "Alamo Drafthouse Yonkers": {
        "location": "suburbs",
        "city": "Yonkers, NY",
        "address": "175 Main St, Yonkers, NY 10701",
        "url": "https://drafthouse.com/yonkers",
        "fandango_id": None,
        "is_regal": False,
        "scrape_strategy": "alamo",
        "website": "https://drafthouse.com/yonkers",
    },
    "Regal New Roc": {
        "location": "suburbs",
        "city": "New Rochelle, NY",
        "address": "33 LeCount Pl, New Rochelle, NY 10801",
        "url": "https://www.regmovies.com/theaters/regal-new-roc/0000000103",
        "fandango_id": "AAJVP",
        "is_regal": True,
        "scrape_strategy": "fandango",
        "website": "https://www.regmovies.com",
    },
    "Pelham Picture House": {
        "location": "suburbs",
        "city": "Pelham, NY",
        "address": "175 Wolf's Lane, Pelham, NY 10803",
        "url": "https://www.thepicturehouse.org/",
        "fandango_id": "AAHRT",
        "is_regal": False,
        "scrape_strategy": "pelham",
        "website": "https://www.thepicturehouse.org",
    },
    "Jacob Burns Film Center": {
        "location": "suburbs",
        "city": "Pleasantville, NY",
        "address": "364 Manville Rd, Pleasantville, NY 10570",
        "url": "https://burnsfilmcenter.org/film/",
        "fandango_id": "AAPXM",
        "is_regal": False,
        "scrape_strategy": "burns",
        "website": "https://burnsfilmcenter.org",
    },
}

# ══════════════════════════════════════════════════════════════
# HTTP UTILITIES
# ══════════════════════════════════════════════════════════════

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

def fetch_url(url: str, extra_headers: dict = None, timeout: int = 20) -> Optional[str]:
    try:
        req = urllib.request.Request(
            url, headers={**HEADERS, **(extra_headers or {})}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # Handle gzip
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            enc = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(enc, errors="replace")
    except Exception as e:
        print(f"    ⚠ fetch failed [{url[:60]}...]: {e}")
        return None

def fetch_json(url: str, extra_headers: dict = None) -> Optional[dict | list]:
    text = fetch_url(url, extra_headers)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception as e:
        print(f"    ⚠ JSON parse failed: {e}")
        return None

class TextExtractor(HTMLParser):
    """Strip all HTML tags and return clean text."""
    def __init__(self):
        super().__init__()
        self.chunks = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            stripped = data.strip()
            if stripped:
                self.chunks.append(stripped)

    def get_text(self):
        return "\n".join(self.chunks)

def strip_html(html: str) -> str:
    p = TextExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    return p.get_text()

def clean_title(title: str) -> str:
    """Normalize a movie title — trim whitespace, decode entities."""
    title = title.strip()
    title = re.sub(r'\s+', ' ', title)
    title = title.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    title = title.replace("\u2019", "'").replace("\u2018", "'")
    return title

# ══════════════════════════════════════════════════════════════
# LETTERBOXD SCRAPER
# ══════════════════════════════════════════════════════════════

def scrape_letterboxd(username: str) -> dict:
    """
    Scrape a Letterboxd public profile and return:
    {
        "watched":   { title_lower: {"title": str, "rating": float|None} },
        "watchlist": { title_lower: {"title": str} },
        "loved":     { title_lower: {"title": str, "rating": float} },  # 4.5-5 stars
        "liked":     { title_lower: {"title": str, "rating": float} },  # 3.5-4 stars
    }
    """
    print(f"\n🎞  Fetching Letterboxd profile: {username}")
    base = f"https://letterboxd.com/{username}"
    result = {"watched": {}, "watchlist": {}, "loved": {}, "liked": {}}

    def scrape_paginated(url_template: str, category: str, rating: float = None):
        """Scrape multiple pages of a Letterboxd list section."""
        page = 1
        while True:
            url = url_template.format(page=page)
            html = fetch_url(url)
            if not html:
                break

            # Letterboxd film entries: <li class="poster-container"> ... data-film-name="..."
            titles_found = re.findall(
                r'data-film-name="([^"]+)"', html
            )
            # Also try alt text pattern
            alt_titles = re.findall(
                r'<img[^>]+alt="([^"]+)"[^>]+class="[^"]*image[^"]*"', html
            )
            # And link title pattern
            link_titles = re.findall(
                r'<a[^>]+title="([^"]+)"[^>]+class="[^"]*frame[^"]*"', html
            )

            all_found = titles_found or alt_titles or link_titles
            if not all_found:
                break

            for raw_title in all_found:
                title = clean_title(raw_title)
                if not title or len(title) < 2:
                    continue
                key = title.lower().strip()
                entry = {"title": title}
                if rating is not None:
                    entry["rating"] = rating

                result[category][key] = entry

                # Also track in watched
                if category != "watchlist":
                    if key not in result["watched"]:
                        result["watched"][key] = {
                            "title": title,
                            "rating": rating
                        }

            # Check for next page
            if 'class="next"' not in html and "next" not in html.lower()[-500:]:
                break
            page += 1
            if page > 20:  # safety cap
                break
            time.sleep(0.5)

    # Scrape watched films (all)
    scrape_paginated(
        f"{base}/films/page/{{page}}/",
        "watched"
    )

    # Scrape watchlist
    scrape_paginated(
        f"{base}/watchlist/page/{{page}}/",
        "watchlist"
    )

    # Scrape by rating tiers
    for stars, rating_val in [("5", 5.0), ("4.5", 4.5), ("4", 4.0), ("3.5", 3.5)]:
        category = "loved" if rating_val >= 4.5 else "liked"
        scrape_paginated(
            f"{base}/films/rated/{stars}/page/{{page}}/",
            category,
            rating_val
        )

    print(f"    ✓ Watched: {len(result['watched'])} films")
    print(f"    ✓ Watchlist: {len(result['watchlist'])} films")
    print(f"    ✓ Loved (4.5-5★): {len(result['loved'])} films")
    print(f"    ✓ Liked (3.5-4★): {len(result['liked'])} films")

    return result

def match_letterboxd(title: str, lb_data: dict) -> dict:
    """
    Match a movie title against Letterboxd data.
    Returns a dict with personal context for this film.
    """
    # Try exact match first, then fuzzy
    key = title.lower().strip()

    # Strip year suffixes like "(1958)" for matching
    key_clean = re.sub(r'\s*\(\d{4}\)\s*$', '', key).strip()
    # Strip format notes like "(70mm)" 
    key_clean = re.sub(r'\s*\([^)]+\)\s*$', '', key_clean).strip()

    def check(lookup_key):
        context = {}
        if lookup_key in lb_data["loved"]:
            entry = lb_data["loved"][lookup_key]
            context["status"] = "loved"
            context["rating"] = entry.get("rating", 5.0)
            context["label"] = f"⭐ You rated this {_stars(entry.get('rating',5.0))}"
        elif lookup_key in lb_data["liked"]:
            entry = lb_data["liked"][lookup_key]
            context["status"] = "liked"
            context["rating"] = entry.get("rating", 4.0)
            context["label"] = f"👍 You rated this {_stars(entry.get('rating',4.0))}"
        elif lookup_key in lb_data["watchlist"]:
            context["status"] = "watchlist"
            context["label"] = "📋 On your watchlist"
        elif lookup_key in lb_data["watched"]:
            entry = lb_data["watched"][lookup_key]
            r = entry.get("rating")
            context["status"] = "seen"
            context["label"] = f"👁 You've seen this" + (f" ({_stars(r)})" if r else "")
            if r:
                context["rating"] = r
        else:
            context["status"] = "new"
            context["label"] = "🆕 New to you"
        return context

    ctx = check(key)
    if ctx["status"] == "new" and key_clean != key:
        ctx = check(key_clean)

    return ctx

def _stars(rating: float) -> str:
    """Convert numeric rating to star string."""
    if not rating:
        return ""
    full = int(rating)
    half = 1 if (rating - full) >= 0.5 else 0
    return "★" * full + ("½" if half else "")

# ══════════════════════════════════════════════════════════════
# THEATER SCRAPERS
# ══════════════════════════════════════════════════════════════

def make_movie(title: str, theater_name: str, dates=None, times=None, url=None, description=None) -> dict:
    """Create a standardized movie dict."""
    return {
        "title": clean_title(title),
        "theater_name": theater_name,
        "theater_config": THEATERS[theater_name],
        "dates": dates or [],
        "times": times or [],
        "source_url": url or THEATERS[theater_name]["website"],
        "description": description or "",
    }

def scrape_alamo(theater_name: str) -> list[dict]:
    print(f"    → Alamo Drafthouse JSON API")
    movies = []

    # Try their schedule API
    data = fetch_json(
        "https://drafthouse.com/s/mother/v2/schedule/market/yonkers",
        extra_headers={"Accept": "application/json", "Referer": "https://drafthouse.com/"}
    )

    if data:
        try:
            presentations = (
                data.get("data", {}).get("presentations", [])
                or data.get("presentations", [])
            )
            grouped = {}
            for p in presentations:
                film = p.get("film", {}) or {}
                title = film.get("name", "").strip()
                if not title:
                    continue
                slug = film.get("slug", "")
                show_date = p.get("showLocalDate", "") or p.get("date", "")
                show_time = p.get("showLocalTime", "") or p.get("time", "")
                description = film.get("synopsis", "") or film.get("description", "")

                if title not in grouped:
                    grouped[title] = make_movie(
                        title, theater_name,
                        url=f"https://drafthouse.com/yonkers/show/{slug}" if slug else None,
                        description=description
                    )
                if show_date and show_date not in grouped[title]["dates"]:
                    grouped[title]["dates"].append(show_date)
                if show_time and show_time not in grouped[title]["times"]:
                    grouped[title]["times"].append(show_time)

            movies = list(grouped.values())
        except Exception as e:
            print(f"    ⚠ Alamo API parse error: {e}")

    # Fallback: HTML scrape
    if not movies:
        print(f"    → Alamo HTML fallback")
        html = fetch_url("https://drafthouse.com/yonkers?showCalendar=true")
        if html:
            # Try to find embedded JSON state
            for pattern in [
                r'window\.__PRELOADED_STATE__\s*=\s*(\{.+?\});\s*</script>',
                r'"presentations"\s*:\s*(\[.+?\])\s*[,}]',
            ]:
                m = re.search(pattern, html, re.DOTALL)
                if m:
                    try:
                        chunk = json.loads(m.group(1))
                        if isinstance(chunk, list):
                            for p in chunk:
                                title = (p.get("film") or {}).get("name", "")
                                if title:
                                    movies.append(make_movie(title, theater_name))
                        break
                    except Exception:
                        pass

            if not movies:
                # Last resort: find film titles in page text
                titles = re.findall(r'"filmTitle"\s*:\s*"([^"]{3,80})"', html)
                titles += re.findall(r'data-film-name="([^"]{3,80})"', html)
                seen = set()
                for t in titles:
                    if t not in seen:
                        seen.add(t)
                        movies.append(make_movie(t, theater_name))

    return movies

def scrape_ifc(theater_name: str) -> list[dict]:
    print(f"    → IFC Center")
    html = fetch_url("https://www.ifccenter.com/films/")
    movies = []
    if not html:
        return movies

    # IFC embeds JSON-LD structured data
    json_ld = re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
    for blob in json_ld:
        try:
            data = json.loads(blob)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") in ("Movie", "ScreeningEvent", "Event"):
                    title = item.get("name", "")
                    if title:
                        date_str = item.get("startDate", "")
                        movies.append(make_movie(
                            title, theater_name,
                            dates=[date_str[:10]] if date_str else [],
                            description=item.get("description", "")
                        ))
        except Exception:
            pass

    if not movies:
        # HTML pattern fallback
        patterns = [
            r'<h2[^>]*>\s*<a[^>]*href="/films/[^"]*"[^>]*>([^<]{3,80})</a>',
            r'<h3[^>]*class="[^"]*title[^"]*"[^>]*>([^<]{3,80})</h3>',
            r'<a[^>]*href="/films/[a-z0-9-]+"[^>]*>([^<]{3,80})</a>',
        ]
        seen = set()
        for pat in patterns:
            for m in re.finditer(pat, html, re.IGNORECASE):
                title = clean_title(m.group(1))
                if title and title.lower() not in seen and len(title) > 2:
                    seen.add(title.lower())
                    movies.append(make_movie(title, theater_name))

    return movies

def scrape_angelika(theater_name: str) -> list[dict]:
    print(f"    → Angelika Film Center")
    movies = []

    # Try Angelika's own site first
    html = fetch_url("https://www.angelikafilmcenter.com/nyc/films")
    if not html:
        html = fetch_url("https://www.angelikafilmcenter.com/nyc")

    if html:
        patterns = [
            r'<h\d[^>]*class="[^"]*movie[^"]*title[^"]*"[^>]*>([^<]{3,80})</h\d>',
            r'<div[^>]*class="[^"]*film-title[^"]*"[^>]*>([^<]{3,80})</div>',
            r'<a[^>]*href="[^"]*angelika[^"]*film[^"]*"[^>]*>([^<]{3,80})</a>',
            r'"name"\s*:\s*"([^"]{3,80})"',
        ]
        seen = set()
        for pat in patterns:
            for m in re.finditer(pat, html, re.IGNORECASE):
                title = clean_title(m.group(1))
                if (title and title.lower() not in seen
                        and len(title) > 2
                        and not title.startswith("http")):
                    seen.add(title.lower())
                    movies.append(make_movie(title, theater_name))

    # Fallback: Fandango
    if not movies:
        movies = scrape_fandango_page(theater_name, "AABVV")

    return movies

def scrape_nitehawk(theater_name: str) -> list[dict]:
    print(f"    → Nitehawk Cinema")
    html = fetch_url("https://nitehawkcinema.com/williamsburg/")
    movies = []
    if not html:
        return movies

    # Try JSON-LD first
    json_ld = re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
    for blob in json_ld:
        try:
            data = json.loads(blob)
            items = data if isinstance(data, list) else [data]
            for item in items:
                title = item.get("name", "")
                if title and item.get("@type") in ("Movie", "Event", "ScreeningEvent"):
                    movies.append(make_movie(
                        title, theater_name,
                        description=item.get("description", "")
                    ))
        except Exception:
            pass

    if not movies:
        patterns = [
            r'<h\d[^>]*class="[^"]*title[^"]*"[^>]*>([^<]{3,80})</h\d>',
            r'<div[^>]*class="[^"]*movie-title[^"]*"[^>]*>([^<]{3,80})</div>',
            r'"filmTitle"\s*:\s*"([^"]{3,80})"',
        ]
        seen = set()
        for pat in patterns:
            for m in re.finditer(pat, html, re.IGNORECASE):
                title = clean_title(m.group(1))
                if title and title.lower() not in seen and len(title) > 2:
                    seen.add(title.lower())
                    movies.append(make_movie(title, theater_name))

    return movies

def scrape_pelham(theater_name: str) -> list[dict]:
    print(f"    → Pelham Picture House")
    movies = []

    html = fetch_url("https://www.thepicturehouse.org/")
    if html:
        patterns = [
            r'<h\d[^>]*class="[^"]*movie[^"]*"[^>]*>([^<]{3,80})</h\d>',
            r'<a[^>]*href="[^"]*movie[^"]*"[^>]*>([^<]{3,80})</a>',
            r'"name"\s*:\s*"([^"]{3,80})"',
        ]
        seen = set()
        for pat in patterns:
            for m in re.finditer(pat, html, re.IGNORECASE):
                title = clean_title(m.group(1))
                if title and title.lower() not in seen and len(title) > 2:
                    seen.add(title.lower())
                    movies.append(make_movie(title, theater_name))

    if not movies:
        movies = scrape_fandango_page(theater_name, "AAHRT")

    return movies

def scrape_burns(theater_name: str) -> list[dict]:
    print(f"    → Jacob Burns Film Center")
    movies = []

    for url in ["https://burnsfilmcenter.org/film/", "https://burnsfilmcenter.org/now-playing/"]:
        html = fetch_url(url)
        if not html:
            continue

        # Burns uses WordPress — look for post titles
        patterns = [
            r'<h2[^>]*class="[^"]*entry-title[^"]*"[^>]*>\s*<a[^>]*>([^<]{3,80})</a>',
            r'<h1[^>]*class="[^"]*entry-title[^"]*"[^>]*>([^<]{3,80})</h1>',
            r'<h\d[^>]*class="[^"]*film[^"]*"[^>]*>([^<]{3,80})</h\d>',
            r'<a[^>]*href="https://burnsfilmcenter\.org/film/[^"]*"[^>]*>([^<]{3,80})</a>',
        ]
        seen = set()
        for pat in patterns:
            for m in re.finditer(pat, html, re.IGNORECASE):
                title = clean_title(m.group(1))
                if title and title.lower() not in seen and len(title) > 2:
                    seen.add(title.lower())
                    movies.append(make_movie(title, theater_name, url=url))
        if movies:
            break

    if not movies:
        movies = scrape_fandango_page(theater_name, "AAPXM")

    return movies

def scrape_fandango_page(theater_name: str, fandango_id: str) -> list[dict]:
    print(f"    → Fandango fallback (ID: {fandango_id})")
    movies = []
    url = f"https://www.fandango.com/{theater_name.lower().replace(' ', '-')}-{fandango_id.lower()}/theater-page"
    html = fetch_url(url)
    if not html:
        return movies

    patterns = [
        r'data-movie-title="([^"]{3,80})"',
        r'"movieTitle"\s*:\s*"([^"]{3,80})"',
        r'<h3[^>]*class="[^"]*title[^"]*"[^>]*>\s*<a[^>]*>([^<]{3,80})</a>',
    ]
    seen = set()
    for pat in patterns:
        for m in re.finditer(pat, html, re.IGNORECASE):
            title = clean_title(m.group(1))
            if title and title.lower() not in seen and len(title) > 2:
                seen.add(title.lower())
                movies.append(make_movie(title, theater_name))

    return movies

# ══════════════════════════════════════════════════════════════
# MAIN SCRAPE ORCHESTRATOR
# ══════════════════════════════════════════════════════════════

def scrape_all_theaters() -> list[dict]:
    all_movies = []
    scrapers = {
        "alamo":    scrape_alamo,
        "ifc":      scrape_ifc,
        "angelika": scrape_angelika,
        "nitehawk": scrape_nitehawk,
        "pelham":   scrape_pelham,
        "burns":    scrape_burns,
        "fandango": lambda name: scrape_fandango_page(
            name, THEATERS[name]["fandango_id"]
        ),
    }

    for theater_name, config in THEATERS.items():
        print(f"\n🎬 {theater_name}")
        strategy = config["scrape_strategy"]
        try:
            fn = scrapers.get(strategy)
            movies = fn(theater_name) if fn else []
            print(f"    ✓ {len(movies)} title(s) found")
            all_movies.extend(movies)
            time.sleep(1.2)
        except Exception as e:
            print(f"    ✗ Error: {e}")

    return all_movies

# ══════════════════════════════════════════════════════════════
# CLAUDE AI CLASSIFIER
# ══════════════════════════════════════════════════════════════

def classify_with_claude(movies: list[dict]) -> list[dict]:
    """
    Use Claude to identify classic/revival screenings using a
    nuanced cinephile-informed prompt. Returns only classics.
    """
    if not movies:
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("\n⚠ ANTHROPIC_API_KEY not set — skipping AI classification")
        print("  Set it with: export ANTHROPIC_API_KEY='your-key-here'")
        return movies  # return all unfiltered

    # Build a rich input for Claude — title + description if available
    unique = {}
    for m in movies:
        t = m["title"]
        if t not in unique:
            unique[t] = m.get("description", "")

    print(f"\n🤖 Classifying {len(unique)} titles with Claude AI...")

    film_list = "\n".join(
        f'- "{title}"' + (f' [Description: {desc[:200]}]' if desc else "")
        for title, desc in unique.items()
    )

    prompt = f"""You are a repertory cinema programmer with encyclopedic knowledge of film history — think the sensibility of Film Forum, IFC Center, or Criterion Collection.

I have a list of films currently showing at theaters near New York City. Your job is to identify which are CLASSIC or REVIVAL screenings versus new releases in their normal theatrical run.

MARK AS CLASSIC/REVIVAL if the film:
- Was originally released more than ~5 years ago (pre-2020), especially if it has genuine cultural standing
- Is a recognized landmark — even if from the 1990s or 2000s (e.g. Eyes Wide Shut, Mulholland Drive, There Will Be Blood absolutely count)
- Is showing in a special format: 70mm, 35mm, 4K restoration, DCP restoration — these are dead giveaways
- Is part of a retrospective, anniversary, or director series
- Is a foreign language or art house classic being revived
- Is a cult or midnight movie staple (Rocky Horror, etc.)
- Was made by canonical directors: Hitchcock, Kubrick, Kurosawa, Fellini, Bergman, Godard, Tarkovsky, Lynch, Scorsese, Wilder, Ford, Welles, Cassavetes, etc.

MARK AS NEW RELEASE if the film:
- Is a mainstream new movie from the last 1-2 years in its normal run
- Is clearly a current blockbuster, new indie, or new documentary
- Has no signals of being a repertory/revival screening

For ambiguous cases, lean toward marking as classic if there's any reasonable case for it.

Films to classify:
{film_list}

Respond ONLY with a valid JSON array. Each object must have:
- "title": exact title string as provided
- "is_classic": true or false
- "year": integer release year, or null if unknown
- "reason": brief phrase (e.g. "1958 Hitchcock masterpiece", "2024 new release", "1974 Polanski noir, anniversary revival")

No preamble. No markdown. Pure JSON array only."""

    try:
        payload = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": prompt}]
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        raw = data.get("content", [{}])[0].get("text", "[]")
        raw = re.sub(r"^```json\s*|^```\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        classifications = json.loads(raw)

        class_map = {c["title"]: c for c in classifications}

        classics = []
        for movie in movies:
            info = class_map.get(movie["title"])
            if info and info.get("is_classic"):
                movie["year"] = info.get("year")
                movie["reason"] = info.get("reason", "")
                classics.append(movie)

        print(f"    ✓ {len(classics)} classic/revival films identified out of {len(movies)} total")
        return classics

    except Exception as e:
        print(f"    ✗ Claude API error: {e}")
        print("    → Returning all movies unfiltered")
        return movies

# ══════════════════════════════════════════════════════════════
# HTML GENERATOR
# ══════════════════════════════════════════════════════════════

def generate_html(movies: list[dict], lb_data: dict) -> str:
    """Generate a rich, vintage-cinema styled HTML calendar."""

    city_movies    = [m for m in movies if m["theater_config"]["location"] == "city"]
    suburb_movies  = [m for m in movies if m["theater_config"]["location"] == "suburbs"]

    def group_by_theater(lst):
        grouped = {}
        for m in lst:
            grouped.setdefault(m["theater_name"], []).append(m)
        return grouped

    def lb_badge(ctx: dict) -> str:
        status = ctx.get("status", "new")
        label  = ctx.get("label", "")
        css_class = {
            "loved":     "lb-loved",
            "liked":     "lb-liked",
            "watchlist": "lb-watchlist",
            "seen":      "lb-seen",
            "new":       "lb-new",
        }.get(status, "lb-new")
        return f'<span class="lb-badge {css_class}">{label}</span>'

    def format_showtimes(dates: list, times: list) -> str:
        lines = []
        if dates:
            formatted_dates = []
            for d in sorted(set(dates))[:8]:
                try:
                    dt = datetime.strptime(d[:10], "%Y-%m-%d")
                    formatted_dates.append(dt.strftime("%a %b %-d"))
                except Exception:
                    formatted_dates.append(d)
            more = f" +{len(dates)-8} more" if len(dates) > 8 else ""
            lines.append(f'<span class="showtime-dates">📅 {" · ".join(formatted_dates)}{more}</span>')
        if times:
            clean_times = []
            for t in times:
                t = t.strip()
                try:
                    if re.match(r"^\d{2}:\d{2}$", t):
                        h, mn = map(int, t.split(":"))
                        period = "PM" if h >= 12 else "AM"
                        h = h % 12 or 12
                        t = f"{h}:{mn:02d} {period}"
                except Exception:
                    pass
                if t and t not in clean_times:
                    clean_times.append(t)
            if clean_times:
                lines.append(f'<span class="showtime-times">🕐 {" · ".join(clean_times[:8])}</span>')
        if not lines:
            return ""
        return f'<div class="showtimes">{"  ".join(lines)}</div>'

    def render_movie_card(m: dict) -> str:
        ctx = match_letterboxd(m["title"], lb_data)
        year_str = f" <span class='year'>({m['year']})</span>" if m.get("year") else ""
        reason   = f'<span class="reason">{m["reason"]}</span>' if m.get("reason") else ""
        dates_html = format_showtimes(m.get("dates", []), m.get("times", []))
        badge    = lb_badge(ctx)
        link     = (f'<a href="{m["source_url"]}" class="tickets-link" '
                    f'target="_blank">Tickets →</a>') if m.get("source_url") else ""

        return f"""
        <div class="movie-card lb-{ctx['status']}">
          <div class="movie-top">
            <span class="movie-title"><em>{m['title']}</em>{year_str}</span>
            {link}
          </div>
          <div class="movie-meta">
            {reason}
            {badge}
          </div>
          {dates_html}
        </div>"""

    def render_theater(name: str, t_movies: list) -> str:
        cfg = t_movies[0]["theater_config"]
        is_regal = cfg["is_regal"]
        regal_badge = ('<span class="regal-badge">★ YOUR REGAL SUBSCRIPTION</span>'
                       if is_regal else "")
        cards = "".join(render_movie_card(m) for m in t_movies)
        return f"""
      <div class="theater-block {'regal-theater' if is_regal else ''}">
        <div class="theater-header">
          <span class="theater-name">{name}</span>
          {regal_badge}
          <span class="theater-city">{cfg['city']}</span>
        </div>
        <div class="movie-list">{cards}</div>
      </div>"""

    def render_section(label: str, movie_list: list) -> str:
        if not movie_list:
            return f"""
      <div class="section-label"><span>{label}</span></div>
      <div class="empty-state">No classic screenings found right now — check back soon.</div>"""
        grouped = group_by_theater(movie_list)
        blocks = "".join(render_theater(n, ms) for n, ms in grouped.items())
        return f"""
      <div class="section-label"><span>{label}</span></div>
      {blocks}"""

    generated = datetime.now().strftime("%B %-d, %Y at %-I:%M %p")
    total = len(movies)
    city_ct = len(city_movies)
    sub_ct = len(suburb_movies)
    sprockets = "".join(['<div class="sprocket"></div>'] * 11)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Classic Cinema Calendar — Larchmont Area</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;0,900;1,400;1,700&family=Libre+Baskerville:ital,wght@0,400;0,700;1,400&family=Josefin+Sans:wght@300;400;600;700&display=swap" rel="stylesheet">
<style>
:root {{
  --cream:       #f5f0e8;
  --parchment:   #ede6d3;
  --dark:        #150f06;
  --brown:       #3d2b1a;
  --amber:       #b8742a;
  --gold:        #d4a853;
  --red:         #8b1a1a;
  --muted:       #7a6a55;
  --border:      #c8b89a;
  --regal-navy:  #0f2340;
  --regal-gold:  #c9a84c;

  /* Letterboxd status colors */
  --lb-loved:     #e8a020;
  --lb-liked:     #5b8dd9;
  --lb-watchlist: #4caf7d;
  --lb-seen:      #888;
  --lb-new:       #9c6fb5;
}}

*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
  background: var(--cream);
  background-image:
    repeating-linear-gradient(
      0deg, transparent, transparent 30px,
      rgba(100,70,30,.045) 30px, rgba(100,70,30,.045) 31px
    );
  color: var(--dark);
  font-family: 'Libre Baskerville', Georgia, serif;
  min-height: 100vh;
}}

/* ── Grain overlay ── */
body::before {{
  content: '';
  position: fixed; inset: 0; pointer-events: none; z-index: 9999;
  opacity: .03;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  background-size: 160px;
}}

/* ── Masthead ── */
.masthead {{
  background: var(--dark);
  border-bottom: 3px double var(--gold);
  padding: 1.75rem 1.5rem 1.25rem;
  text-align: center;
  position: relative;
  overflow: hidden;
}}
.masthead::after {{
  content: '';
  position: absolute; inset: 0; pointer-events: none;
  background: repeating-linear-gradient(
    90deg, transparent, transparent 44px,
    rgba(212,168,83,.035) 44px, rgba(212,168,83,.035) 45px
  );
}}
.sprocket-strip {{
  display: flex; justify-content: center; gap: 14px; margin-bottom: 1rem;
}}
.sprocket {{
  width: 13px; height: 13px;
  border: 2px solid var(--gold); border-radius: 3px; opacity: .45;
}}
.masthead h1 {{
  font-family: 'Playfair Display', serif;
  font-size: clamp(1.9rem, 5vw, 3.4rem);
  font-weight: 900; font-style: italic;
  color: var(--gold);
  letter-spacing: .06em; text-transform: uppercase;
  text-shadow: 1px 2px 6px rgba(0,0,0,.6);
  line-height: 1;
}}
.masthead .tagline {{
  font-family: 'Josefin Sans', sans-serif;
  font-size: .72rem; letter-spacing: .28em;
  color: var(--border); text-transform: uppercase;
  margin-top: .5rem;
}}
.masthead .generated {{
  font-family: 'Josefin Sans', sans-serif;
  font-size: .62rem; letter-spacing: .14em;
  color: rgba(255,255,255,.25); margin-top: .6rem;
  text-transform: uppercase;
}}

/* ── Stats bar ── */
.container {{ max-width: 860px; margin: 0 auto; padding: 2rem 1.25rem; }}
.stats-bar {{
  display: flex; justify-content: center; flex-wrap: wrap; gap: 1.5rem;
  background: var(--parchment); border: 1px solid var(--border);
  padding: .7rem 1.5rem; margin-bottom: 2.5rem;
  font-family: 'Josefin Sans', sans-serif;
  font-size: .72rem; letter-spacing: .1em; text-transform: uppercase;
  color: var(--brown);
}}
.stats-bar strong {{ color: var(--red); font-size: .85rem; }}

/* ── Legend ── */
.legend {{
  display: flex; flex-wrap: wrap; gap: .5rem .9rem;
  justify-content: center;
  margin-bottom: 2rem;
  font-family: 'Josefin Sans', sans-serif;
  font-size: .65rem; letter-spacing: .08em; text-transform: uppercase;
}}
.legend-item {{ display: flex; align-items: center; gap: .35rem; }}
.legend-dot {{
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
}}

/* ── Section label ── */
.section-label {{
  display: flex; align-items: center; gap: .75rem;
  margin: 2.5rem 0 1.25rem;
}}
.section-label::before, .section-label::after {{
  content: ''; flex: 1; height: 1px; background: var(--amber);
}}
.section-label span {{
  font-family: 'Josefin Sans', sans-serif;
  font-size: .68rem; font-weight: 700;
  letter-spacing: .28em; text-transform: uppercase;
  color: var(--amber); white-space: nowrap; padding: 0 .4rem;
}}

/* ── Theater block ── */
.theater-block {{
  background: white;
  border: 1px solid var(--border);
  border-top: 3px solid var(--amber);
  margin-bottom: 1.4rem;
  box-shadow: 1px 3px 10px rgba(0,0,0,.065);
  animation: fadeUp .35s ease both;
}}
.theater-block.regal-theater {{
  border-top: 3px solid var(--regal-navy);
  background: linear-gradient(135deg, #f5f7fc 0%, white 25%);
}}
@keyframes fadeUp {{
  from {{ opacity: 0; transform: translateY(8px); }}
  to   {{ opacity: 1; transform: translateY(0); }}
}}

/* ── Theater header ── */
.theater-header {{
  display: flex; align-items: center; flex-wrap: wrap; gap: .6rem;
  padding: .75rem 1.15rem;
  background: var(--parchment);
  border-bottom: 1px solid var(--border);
}}
.regal-theater .theater-header {{
  background: linear-gradient(90deg, #e4e9f5, #ecf0fa);
}}
.theater-name {{
  font-family: 'Playfair Display', serif;
  font-size: 1rem; font-weight: 700; color: var(--brown);
}}
.regal-theater .theater-name {{
  color: var(--regal-navy); font-weight: 900;
}}
.theater-city {{
  font-family: 'Josefin Sans', sans-serif;
  font-size: .62rem; letter-spacing: .14em;
  text-transform: uppercase; color: #999; margin-left: auto;
}}
.regal-badge {{
  background: var(--regal-navy); color: var(--regal-gold);
  font-family: 'Josefin Sans', sans-serif;
  font-size: .58rem; font-weight: 700; letter-spacing: .12em;
  text-transform: uppercase; padding: .18rem .5rem;
  border: 1px solid var(--regal-gold); border-radius: 2px;
}}

/* ── Movie card ── */
.movie-list {{ padding: .15rem 0; }}
.movie-card {{
  padding: .8rem 1.15rem;
  border-bottom: 1px dashed var(--border);
  transition: background .15s;
  border-left: 3px solid transparent;
}}
.movie-card:last-child {{ border-bottom: none; }}
.movie-card:hover {{ background: #fdf9f3; }}

/* Subtle left accent by Letterboxd status */
.movie-card.lb-loved     {{ border-left-color: var(--lb-loved); }}
.movie-card.lb-liked     {{ border-left-color: var(--lb-liked); }}
.movie-card.lb-watchlist {{ border-left-color: var(--lb-watchlist); }}
.movie-card.lb-seen      {{ border-left-color: #ddd; }}
.movie-card.lb-new       {{ border-left-color: var(--lb-new); }}

.movie-top {{
  display: flex; align-items: baseline;
  justify-content: space-between; gap: .75rem; flex-wrap: wrap;
}}
.movie-title {{
  font-family: 'Playfair Display', serif;
  font-size: .98rem; color: var(--dark);
}}
.movie-title .year {{
  font-family: 'Josefin Sans', sans-serif;
  font-size: .7rem; color: var(--muted);
  font-style: normal; letter-spacing: .04em;
}}
.movie-meta {{
  display: flex; align-items: center; gap: .6rem; flex-wrap: wrap;
  margin-top: .25rem;
}}
.reason {{
  font-family: 'Josefin Sans', sans-serif;
  font-size: .63rem; letter-spacing: .1em;
  text-transform: uppercase; color: var(--amber);
}}

/* ── Letterboxd badges ── */
.lb-badge {{
  display: inline-block;
  font-family: 'Josefin Sans', sans-serif;
  font-size: .6rem; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; padding: .15rem .45rem;
  border-radius: 2px; border: 1px solid currentColor;
  white-space: nowrap;
}}
.lb-badge.lb-loved     {{ color: var(--lb-loved);     background: #fff8ec; }}
.lb-badge.lb-liked     {{ color: var(--lb-liked);     background: #eef3fc; }}
.lb-badge.lb-watchlist {{ color: var(--lb-watchlist); background: #edfaf4; }}
.lb-badge.lb-seen      {{ color: var(--lb-seen);      background: #f5f5f5; }}
.lb-badge.lb-new       {{ color: var(--lb-new);       background: #f8f0fc; }}

.showtimes {{
  font-family: 'Josefin Sans', sans-serif;
  font-size: .68rem; letter-spacing: .04em;
  margin-top: .35rem;
  display: flex; flex-wrap: wrap; gap: .25rem .75rem; align-items: center;
}}
.showtime-dates {{ color: #666; }}
.showtime-times {{ color: var(--amber); font-weight: 700; letter-spacing: .06em; }}
.more {{ color: #aaa; font-style: italic; }}
.tickets-link {{
  font-family: 'Josefin Sans', sans-serif;
  font-size: .63rem; letter-spacing: .1em; text-transform: uppercase;
  color: var(--red); text-decoration: none;
  border-bottom: 1px solid var(--red);
  white-space: nowrap; transition: opacity .2s;
  flex-shrink: 0;
}}
.tickets-link:hover {{ opacity: .55; }}

/* ── Misc ── */
.empty-state {{
  font-family: 'Josefin Sans', sans-serif;
  font-size: .75rem; letter-spacing: .12em; text-transform: uppercase;
  color: #bbb; text-align: center; padding: 1.75rem;
  border: 1px dashed var(--border);
}}
.footer {{
  text-align: center; padding: 2rem;
  border-top: 1px solid var(--border); margin-top: 3rem;
  font-family: 'Josefin Sans', sans-serif;
  font-size: .62rem; letter-spacing: .14em;
  text-transform: uppercase; color: #bbb;
}}

@media (max-width: 580px) {{
  .stats-bar {{ flex-direction: column; align-items: center; }}
  .theater-header {{ flex-direction: column; align-items: flex-start; }}
  .theater-city {{ margin-left: 0; }}
  .movie-top {{ flex-direction: column; }}
}}
</style>
</head>
<body>

<header class="masthead">
  <div class="sprocket-strip">{sprockets}</div>
  <h1>Classic Cinema</h1>
  <div class="tagline">Revival &amp; Repertory Screenings — Larchmont Area</div>
  <div class="generated">Generated {generated}</div>
</header>

<main class="container">

  <div class="stats-bar">
    <span><strong>{total}</strong> Classic Screenings</span>
    <span><strong>{city_ct}</strong> In The City</span>
    <span><strong>{sub_ct}</strong> In The Suburbs</span>
  </div>

  <div class="legend">
    <div class="legend-item">
      <div class="legend-dot" style="background:var(--lb-loved)"></div>
      <span>You loved it (★★★★½–★★★★★)</span>
    </div>
    <div class="legend-item">
      <div class="legend-dot" style="background:var(--lb-liked)"></div>
      <span>You liked it (★★★½–★★★★)</span>
    </div>
    <div class="legend-item">
      <div class="legend-dot" style="background:var(--lb-watchlist)"></div>
      <span>On your watchlist</span>
    </div>
    <div class="legend-item">
      <div class="legend-dot" style="background:var(--lb-seen)"></div>
      <span>You've seen it</span>
    </div>
    <div class="legend-item">
      <div class="legend-dot" style="background:var(--lb-new)"></div>
      <span>New to you</span>
    </div>
  </div>

  {render_section("✦ New York City", city_movies)}
  {render_section("✦ Westchester &amp; Suburbs", suburb_movies)}

</main>

<footer class="footer">
  Classic Cinema Calendar · Larchmont, NY
  · Letterboxd: {LETTERBOXD_USERNAME}
  · AI-classified by Claude · {generated}
</footer>

</body>
</html>"""

# ══════════════════════════════════════════════════════════════
# DEMO DATA (used when network is unavailable)
# ══════════════════════════════════════════════════════════════

def get_demo_data() -> list[dict]:
    return [
        {**make_movie("The Trouble with Harry", "Alamo Drafthouse Yonkers",
            dates=["2026-03-05","2026-03-06"], times=["7:30 PM"],
            description="Alfred Hitchcock's wry dark comedy from 1955"),
         "year": 1955, "reason": "1955 Hitchcock dark comedy"},
        {**make_movie("Vertigo", "IFC Center",
            dates=["2026-03-07","2026-03-08","2026-03-09"], times=["8:00 PM"],
            description="4K restoration of Hitchcock's 1958 masterpiece"),
         "year": 1958, "reason": "1958 Hitchcock, 4K restoration"},
        {**make_movie("Rear Window", "IFC Center",
            dates=["2026-03-15","2026-03-16"], times=["6:30 PM"],
            description="Hitchcock retrospective — 1954"),
         "year": 1954, "reason": "1954 Hitchcock, retrospective series"},
        {**make_movie("2001: A Space Odyssey", "Nitehawk Cinema",
            dates=["2026-03-08"], times=["9:00 PM"],
            description="70mm screening of Kubrick's landmark 1968 sci-fi epic"),
         "year": 1968, "reason": "1968 Kubrick, rare 70mm print"},
        {**make_movie("Chinatown", "Angelika Film Center",
            dates=["2026-03-10","2026-03-11"], times=["7:00 PM"],
            description="50th anniversary revival of Polanski's 1974 neo-noir"),
         "year": 1974, "reason": "1974 Polanski noir, 50th anniversary"},
        {**make_movie("Lawrence of Arabia", "Regal New Roc",
            dates=["2026-03-12"], times=["6:00 PM"],
            description="Restored 70mm print of David Lean's 1962 epic"),
         "year": 1962, "reason": "1962 David Lean epic, 70mm restored print"},
        {**make_movie("Sunset Boulevard", "Pelham Picture House",
            dates=["2026-03-14","2026-03-15"], times=["7:30 PM"],
            description="Billy Wilder's 1950 Hollywood noir classic"),
         "year": 1950, "reason": "1950 Wilder, Hollywood noir classic"},
        {**make_movie("8½", "Jacob Burns Film Center",
            dates=["2026-03-13","2026-03-14"], times=["7:00 PM"],
            description="Fellini's 1963 masterwork — Italian cinema series"),
         "year": 1963, "reason": "1963 Fellini, Italian cinema retrospective"},
        {**make_movie("Rashomon", "Jacob Burns Film Center",
            dates=["2026-03-20"], times=["7:30 PM"],
            description="Kurosawa's 1950 classic, Janus Films restoration"),
         "year": 1950, "reason": "1950 Kurosawa, Janus Films restoration"},
        {**make_movie("There Will Be Blood", "IFC Center",
            dates=["2026-03-22","2026-03-23"], times=["7:00 PM"],
            description="PTA's 2007 masterpiece — anniversary screening"),
         "year": 2007, "reason": "2007 PTA masterpiece, anniversary screening"},
        {**make_movie("Mulholland Drive", "Nitehawk Cinema",
            dates=["2026-03-21"], times=["10:00 PM"],
            description="Lynch's 2001 neo-noir nightmare — midnight screening"),
         "year": 2001, "reason": "2001 Lynch, midnight screening"},
        {**make_movie("The Godfather", "Alamo Drafthouse Yonkers",
            dates=["2026-03-25","2026-03-26"], times=["7:30 PM"],
            description="Coppola's 1972 classic — 4K remaster"),
         "year": 1972, "reason": "1972 Coppola, 4K remastered"},
    ]

def get_demo_letterboxd() -> dict:
    """Sample Letterboxd data for mororke for demo purposes."""
    return {
        "watched": {
            "vertigo": {"title": "Vertigo", "rating": 5.0},
            "chinatown": {"title": "Chinatown", "rating": 5.0},
            "2001: a space odyssey": {"title": "2001: A Space Odyssey", "rating": 5.0},
            "the godfather": {"title": "The Godfather", "rating": 5.0},
            "rashomon": {"title": "Rashomon", "rating": 4.5},
            "there will be blood": {"title": "There Will Be Blood", "rating": 4.5},
            "sunset boulevard": {"title": "Sunset Boulevard", "rating": 4.0},
            "8½": {"title": "8½", "rating": 4.0},
        },
        "watchlist": {
            "the trouble with harry": {"title": "The Trouble with Harry"},
            "rear window": {"title": "Rear Window"},
            "mulholland drive": {"title": "Mulholland Drive"},
        },
        "loved": {
            "vertigo": {"title": "Vertigo", "rating": 5.0},
            "chinatown": {"title": "Chinatown", "rating": 5.0},
            "2001: a space odyssey": {"title": "2001: A Space Odyssey", "rating": 5.0},
            "the godfather": {"title": "The Godfather", "rating": 5.0},
            "rashomon": {"title": "Rashomon", "rating": 4.5},
            "there will be blood": {"title": "There Will Be Blood", "rating": 4.5},
        },
        "liked": {
            "sunset boulevard": {"title": "Sunset Boulevard", "rating": 4.0},
            "8½": {"title": "8½", "rating": 4.0},
        },
    }

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print("=" * 58)
    print("  CLASSIC CINEMA CALENDAR  v2")
    print("  Larchmont, NY  ·  with Letterboxd integration")
    print("=" * 58)

    # ── Step 1: Scrape theaters ──────────────────────────────
    print("\n── THEATER SCRAPING ──────────────────────────────────")
    all_movies = scrape_all_theaters()
    print(f"\n📋 Total raw titles scraped: {len(all_movies)}")

    demo_mode = False
    if not all_movies:
        print("\n⚠ No live data (network restricted in this environment).")
        print("  Using demo data to show you exactly how the output looks.")
        all_movies = get_demo_data()
        demo_mode = True

    # ── Step 2: Scrape Letterboxd ────────────────────────────
    print("\n── LETTERBOXD ────────────────────────────────────────")
    if demo_mode:
        print(f"  Using demo Letterboxd data for @{LETTERBOXD_USERNAME}")
        lb_data = get_demo_letterboxd()
    else:
        lb_data = scrape_letterboxd(LETTERBOXD_USERNAME)

    # ── Step 3: Classify with Claude ────────────────────────
    print("\n── AI CLASSIFICATION ─────────────────────────────────")
    if demo_mode:
        classic_movies = all_movies  # already classic in demo
        print("  Demo mode: using pre-classified classics")
    else:
        classic_movies = classify_with_claude(all_movies)

    # ── Step 4: Generate HTML ────────────────────────────────
    print("\n── OUTPUT ────────────────────────────────────────────")
    html = generate_html(classic_movies, lb_data)

    html_path = "/Users/michaelororke/classic-cinema/classic_cinema_calendar.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✓ HTML → {html_path}")

    # Save JSON for Google Calendar step
    json_path = "/Users/michaelororke/classic-cinema/classic_movies.json"
    export = []
    for m in classic_movies:
        ctx = match_letterboxd(m["title"], lb_data)
        export.append({
            "title":       m["title"],
            "year":        m.get("year"),
            "reason":      m.get("reason", ""),
            "theater":     m["theater_name"],
            "location":    m["theater_config"]["location"],
            "city":        m["theater_config"]["city"],
            "address":     m["theater_config"]["address"],
            "is_regal":    m["theater_config"]["is_regal"],
            "dates":       m.get("dates", []),
            "times":       m.get("times", []),
            "source_url":  m.get("source_url", ""),
            "lb_status":   ctx.get("status", "new"),
            "lb_label":    ctx.get("label", ""),
            "lb_rating":   ctx.get("rating"),
        })
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(export, f, indent=2)
    print(f"  ✓ JSON → {json_path}")

    print(f"\n✅ Done — {len(classic_movies)} classic screenings found.")
    if demo_mode:
        print("  (Run on your Mac with internet to get live theater data)")

if __name__ == "__main__":
    main()
