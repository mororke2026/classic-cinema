#!/usr/bin/env python3
"""
Classic Cinema Calendar — v3
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scrapes showtimes from 7 local theaters using requests + BeautifulSoup,
classifies classic/revival films using Claude AI, cross-references your
Letterboxd profile (mororke), and outputs a beautiful HTML calendar +
JSON for Google Calendar import.

Usage:
    python3 classic_cinema_v3.py

Requirements:
    pip3 install requests beautifulsoup4
    export ANTHROPIC_API_KEY='sk-ant-...'
"""

import json
import os
import re
import time
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ══════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════

LETTERBOXD_USERNAME = "mororke"
OUTPUT_DIR = os.path.expanduser("~/classic-cinema")

THEATERS = {
    "IFC Center": {
        "location": "city", "city": "New York, NY",
        "address": "323 6th Ave, New York, NY 10014",
        "url": "https://www.ifccenter.com/films/",
        "fandango_id": "AAXKP", "is_regal": False,
        "scrape_strategy": "ifc",
        "website": "https://www.ifccenter.com",
    },
    "Angelika Film Center": {
        "location": "city", "city": "New York, NY",
        "address": "18 W Houston St, New York, NY 10012",
        "url": "https://www.angelikafilmcenter.com/nyc",
        "fandango_id": "AAECI", "is_regal": False,
        "scrape_strategy": "angelika",
        "website": "https://www.angelikafilmcenter.com",
    },
    "Nitehawk Cinema": {
        "location": "city", "city": "Brooklyn, NY",
        "address": "136 Metropolitan Ave, Brooklyn, NY 11249",
        "url": "https://nitehawkcinema.com/williamsburg/",
        "fandango_id": "AARVP", "is_regal": False,
        "scrape_strategy": "nitehawk",
        "website": "https://nitehawkcinema.com",
    },
    "Alamo Drafthouse Yonkers": {
        "location": "suburbs", "city": "Yonkers, NY",
        "address": "175 Main St, Yonkers, NY 10701",
        "url": "https://drafthouse.com/yonkers",
        "fandango_id": "AAWWC", "is_regal": False,
        "scrape_strategy": "alamo",
        "website": "https://drafthouse.com/yonkers",
    },
    "Regal New Roc": {
        "location": "suburbs", "city": "New Rochelle, NY",
        "address": "33 LeCount Pl, New Rochelle, NY 10801",
        "url": "https://www.fandango.com/regal-new-roc-4dx-imax-and-rpx-aanlc/theater-page",
        "fandango_id": "AANLC", "is_regal": True,
        "scrape_strategy": "fandango",
        "website": "https://www.fandango.com",
    },
    "Pelham Picture House": {
        "location": "suburbs", "city": "Pelham, NY",
        "address": "175 Wolf's Lane, Pelham, NY 10803",
        "url": "https://www.fandango.com/the-picture-house-pelham-aahrt/theater-page",
        "fandango_id": "AAHRT", "is_regal": False,
        "scrape_strategy": "pelham",
        "website": "https://www.thepicturehouse.org",
    },
    "Jacob Burns Film Center": {
        "location": "suburbs", "city": "Pleasantville, NY",
        "address": "364 Manville Rd, Pleasantville, NY 10570",
        "url": "https://burnsfilmcenter.org/film/",
        "fandango_id": "AAPXM", "is_regal": False,
        "scrape_strategy": "burns",
        "website": "https://burnsfilmcenter.org",
    },
}

# ══════════════════════════════════════════════════════════════
# HTTP SESSION
# ══════════════════════════════════════════════════════════════

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
})

def get(url: str, **kwargs) -> Optional[requests.Response]:
    try:
        r = SESSION.get(url, timeout=20, **kwargs)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"    ⚠ GET failed [{url[:65]}]: {e}")
        return None

def get_soup(url: str, **kwargs) -> Optional[BeautifulSoup]:
    r = get(url, **kwargs)
    if r:
        return BeautifulSoup(r.text, "html.parser")
    return None

def get_json(url: str, **kwargs) -> Optional[dict | list]:
    r = get(url, headers={"Accept": "application/json"}, **kwargs)
    if r:
        try:
            return r.json()
        except Exception as e:
            print(f"    ⚠ JSON parse failed: {e}")
    return None

def clean_title(t: str) -> str:
    t = t.strip()
    t = re.sub(r'\s+', ' ', t)
    for old, new in [("&amp;","&"),("&#39;","'"),("&quot;",'"'),
                     ("\u2019","'"),("\u2018","'"),("&nbsp;"," ")]:
        t = t.replace(old, new)
    return t

def make_movie(title, theater_name, dates=None, times=None,
               url=None, description=None):
    return {
        "title": clean_title(title),
        "theater_name": theater_name,
        "theater_config": THEATERS[theater_name],
        "dates": dates or [],
        "times": times or [],
        "source_url": url or THEATERS[theater_name]["website"],
        "description": description or "",
    }

# ══════════════════════════════════════════════════════════════
# THEATER SCRAPERS
# ══════════════════════════════════════════════════════════════

def scrape_alamo(theater_name):
    print(f"    → Alamo Drafthouse API")
    movies = []

    data = get_json("https://drafthouse.com/s/mother/v2/schedule/market/yonkers")
    if data:
        try:
            presentations = data.get("data", {}).get("presentations", [])
            grouped = {}
            for p in presentations:
                film = p.get("film") or {}
                title = film.get("name", "").strip()
                if not title:
                    continue
                slug  = film.get("slug", "")
                date  = p.get("showLocalDate", "")
                time_ = p.get("showLocalTime", "")
                desc  = film.get("synopsis", "") or film.get("description", "")
                if title not in grouped:
                    grouped[title] = make_movie(
                        title, theater_name,
                        url=f"https://drafthouse.com/yonkers/show/{slug}" if slug else None,
                        description=desc
                    )
                if date and date not in grouped[title]["dates"]:
                    grouped[title]["dates"].append(date)
                if time_ and time_ not in grouped[title]["times"]:
                    grouped[title]["times"].append(time_)
            movies = list(grouped.values())
        except Exception as e:
            print(f"    ⚠ Alamo parse error: {e}")

    if not movies:
        print(f"    → Alamo HTML fallback")
        soup = get_soup("https://drafthouse.com/yonkers")
        if soup:
            for tag in soup.find_all(["h2","h3","h4"],
                                      class_=re.compile(r"film|movie|title", re.I)):
                t = clean_title(tag.get_text())
                if t and len(t) > 2:
                    movies.append(make_movie(t, theater_name))

    if not movies:
        print(f"    → Alamo Fandango fallback")
        movies = scrape_fandango(theater_name, "AAWWC")

    return movies


def scrape_ifc(theater_name):
    print(f"    → IFC Center")
    movies = []

    # IFC Center via Moviefone (IFC's own site is JS-rendered)
    soup = get_soup("https://www.moviefone.com/showtimes/theater/ifc-center-new-york/3mJY2G8MIJmFVajqgCRfy5/")
    if soup:
        seen = set()
        for sel in ["h3.movie-title", "h2.movie-title", ".movie-title a",
                    "a.movie-title", "[class*='movie-title']",
                    "h3 a", "h2 a", ".title a"]:
            for tag in soup.select(sel):
                title = clean_title(tag.get_text())
                if title and title.lower() not in seen and len(title) > 2:
                    if any(x in title.lower() for x in ["sign in","log in","menu","search"]):
                        continue
                    seen.add(title.lower())
                    parent = tag.find_parent(["article","div","li","section"])
                    times, dates = [], []
                    if parent:
                        for t in parent.find_all(string=re.compile(r"\d+:\d+\s*(am|pm)", re.I)):
                            times.append(t.strip())
                    movies.append(make_movie(title, theater_name, times=times,
                        url="https://www.moviefone.com/showtimes/theater/ifc-center-new-york/3mJY2G8MIJmFVajqgCRfy5/"))
        if movies:
            return movies

    # Fallback: IFC WordPress site
    soup = get_soup("https://www.ifccenter.com/films/")
    if not soup:
        return movies

    # IFC lists films as articles or divs with film titles
    # Try multiple selectors
    film_links = (
        soup.select("h3.card-title a") or
        soup.select("h2.card-title a") or
        soup.select(".card-title a") or
        soup.select("h2.film-title a") or
        soup.select("h3.film-title a") or
        soup.select(".film-listing h2 a") or
        soup.select(".film-listing h3 a") or
        soup.select("article h2 a") or
        soup.select("article h3 a") or
        soup.select(".entry-title a")
    )

    seen = set()
    for link in film_links:
        title = clean_title(link.get_text())
        if title and title.lower() not in seen and len(title) > 2:
            seen.add(title.lower())
            href = link.get("href", "")
            url = href if href.startswith("http") else f"https://www.ifccenter.com{href}"

            # Try to find showtime near this link
            parent = link.find_parent(["article", "div", "li"])
            times = []
            dates = []
            if parent:
                for t in parent.find_all(string=re.compile(r'\d+:\d+\s*(AM|PM)', re.I)):
                    times.append(t.strip())
                for d in parent.find_all(string=re.compile(r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun)', re.I)):
                    dates.append(d.strip())

            movies.append(make_movie(title, theater_name,
                                     dates=dates, times=times, url=url))

    # Fallback: any link to /films/ subpages
    if not movies:
        for a in soup.find_all("a", href=re.compile(r'/films/[a-z0-9-]+')):
            title = clean_title(a.get_text())
            if title and len(title) > 2 and title.lower() not in seen:
                seen.add(title.lower())
                movies.append(make_movie(title, theater_name,
                    url=f"https://www.ifccenter.com{a['href']}"))

    return movies


def scrape_angelika(theater_name):
    print(f"    → Angelika Film Center")
    movies = []

    for url in ["https://www.angelikafilmcenter.com/nyc/films",
                "https://www.angelikafilmcenter.com/nyc"]:
        soup = get_soup(url)
        if not soup:
            continue

        seen = set()
        for sel in ["h2.movie-title","h3.movie-title",".film-title",
                    ".movie-title","article h2","article h3"]:
            for tag in soup.select(sel):
                title = clean_title(tag.get_text())
                if title and title.lower() not in seen and len(title) > 2:
                    seen.add(title.lower())
                    # Look for times in parent container
                    parent = tag.find_parent(["article","div","li","section"])
                    times = []
                    if parent:
                        for t in parent.find_all(
                                string=re.compile(r'\d+:\d+\s*(am|pm)', re.I)):
                            times.append(t.strip())
                    movies.append(make_movie(title, theater_name,
                                             times=times, url=url))
        if movies:
            break

    # Fallback: Fandango
    if not movies:
        movies = scrape_fandango(theater_name, "AABVV")

    return movies


def scrape_nitehawk(theater_name):
    print(f"    → Nitehawk Cinema")
    movies = []
    soup = get_soup("https://nitehawkcinema.com/williamsburg/")
    if not soup:
        return movies

    seen = set()
    selectors = [
        ".film-title", ".movie-title", "h2.title", "h3.title",
        ".show-title", "article h2", "article h3",
        ".screening-title", ".event-title"
    ]
    for sel in selectors:
        for tag in soup.select(sel):
            title = clean_title(tag.get_text())
            if title and title.lower() not in seen and len(title) > 2:
                seen.add(title.lower())
                parent = tag.find_parent(["article","div","li","section"])
                times, dates = [], []
                if parent:
                    for t in parent.find_all(
                            string=re.compile(r'\d+:\d+\s*(am|pm)', re.I)):
                        times.append(t.strip())
                    for d in parent.find_all(
                            string=re.compile(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d+', re.I)):
                        dates.append(d.strip())
                movies.append(make_movie(title, theater_name,
                                         dates=dates, times=times))

    # Also check for embedded JSON
    if not movies:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") in ("Movie","Event","ScreeningEvent"):
                        title = item.get("name","")
                        if title and title.lower() not in seen:
                            seen.add(title.lower())
                            date = item.get("startDate","")[:10]
                            movies.append(make_movie(
                                title, theater_name,
                                dates=[date] if date else [],
                                description=item.get("description","")
                            ))
            except Exception:
                pass

    return movies


def scrape_pelham(theater_name):
    print(f"    → Pelham Picture House")
    # Use Fandango directly - website is JS-rendered
    return scrape_fandango(theater_name, "AAHRT")


def scrape_burns(theater_name):
    print(f"    → Jacob Burns Film Center")
    movies = []
    soup = get_soup("https://burnsfilmcenter.org/film/")
    if soup:
        seen = set()
        # Burns uses h3.card-title for film listings
        for sel in ["h3.card-title a", "h2.card-title a", ".card-title a",
                    ".entry-title a", "h2.entry-title", "article h2 a"]:
            for tag in soup.select(sel):
                title = clean_title(tag.get_text())
                href  = tag.get("href","") if tag.name == "a" else ""
                if title and title.lower() not in seen and len(title) > 2:
                    if any(x in title.lower() for x in
                           ["home","about","support","donate","education","membership","gift","calendar"]):
                        continue
                    seen.add(title.lower())
                    parent = tag.find_parent(["article","div","li","section"])
                    times, dates = [], []
                    if parent:
                        for t in parent.find_all(
                                string=re.compile(r'\d+:\d+\s*(am|pm)', re.I)):
                            times.append(t.strip())
                        for d in parent.find_all(
                                string=re.compile(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d+', re.I)):
                            dates.append(d.strip())
                    movies.append(make_movie(title, theater_name,
                        dates=dates, times=times, url=href or None))
        if movies:
            print(f"    → Found {len(movies)} titles via card-title selector")

    if not movies:
        movies = scrape_fandango(theater_name, "AAPXM")

    return movies


# Correct Fandango URL slugs for each theater
FANDANGO_URLS = {
    "AAWWC": "https://www.fandango.com/alamo-drafthouse-yonkers-aawwc/theater-page",
    "AAECI": "https://www.fandango.com/angelika-film-center-and-cafe-aaeci/theater-page",
    "AANLC": "https://www.fandango.com/regal-new-roc-4dx-imax-and-rpx-aanlc/theater-page",
    "AAHRT": "https://www.fandango.com/the-picture-house-pelham-aahrt/theater-page",
    "AAPXM": "https://www.fandango.com/jacob-burns-film-center-aapxm/theater-page",
    "AARVP": "https://www.fandango.com/nitehawk-cinema-williamsburg-aarvp/theater-page",
}

def scrape_fandango(theater_name, fandango_id):
    print(f"    → Fandango (ID: {fandango_id})")
    movies = []
    url = FANDANGO_URLS.get(fandango_id)
    if not url:
        slug = theater_name.lower().replace(" ", "-")
        url = f"https://www.fandango.com/{slug}-{fandango_id.lower()}/theater-page"
    soup = get_soup(url)
    if not soup:
        return movies

    seen = set()
    for sel in ["[data-movie-title]", ".movie-title", ".film-title",
                "h3.title", "h2.title"]:
        for tag in soup.select(sel):
            title = tag.get("data-movie-title") or clean_title(tag.get_text())
            if title and title.lower() not in seen and len(title) > 2:
                seen.add(title.lower())
                parent = tag.find_parent(["article","div","li","section"])
                times = []
                if parent:
                    for t in parent.find_all(
                            string=re.compile(r'\d+:\d+\s*(am|pm)', re.I)):
                        times.append(t.strip())
                movies.append(make_movie(title, theater_name,
                                         times=times, url=url))
    return movies


# ══════════════════════════════════════════════════════════════
# LETTERBOXD CSV READER
# ══════════════════════════════════════════════════════════════

def load_letterboxd_csv(username: str) -> dict:
    """Read Letterboxd data from exported CSV files in ~/classic-cinema/letterboxd/"""
    import csv
    print(f"\n── LETTERBOXD ────────────────────────────────────────")
    lb_dir = os.path.join(OUTPUT_DIR, "letterboxd")
    result = {"watched": {}, "watchlist": {}, "loved": {}, "liked": {}}

    # ratings.csv — films with star ratings
    ratings_path = os.path.join(lb_dir, "ratings.csv")
    if os.path.exists(ratings_path):
        with open(ratings_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                title = clean_title(row.get("Name",""))
                if not title: continue
                try: rating = float(row.get("Rating", 0))
                except ValueError: rating = 0.0
                key = title.lower()
                entry = {"title": title, "rating": rating, "year": row.get("Year","")}
                result["watched"][key] = entry
                if rating >= 4.5:
                    result["loved"][key] = entry
                elif rating >= 3.5:
                    result["liked"][key] = entry
        print(f"    ✓ Ratings: {len(result['watched'])} films")
    else:
        print(f"    ⚠ ratings.csv not found in {lb_dir}")

    # watched.csv — all logged films (including unrated)
    watched_path = os.path.join(lb_dir, "watched.csv")
    if os.path.exists(watched_path):
        before = len(result["watched"])
        with open(watched_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                title = clean_title(row.get("Name",""))
                if not title: continue
                key = title.lower()
                if key not in result["watched"]:
                    result["watched"][key] = {"title": title, "rating": None, "year": row.get("Year","")}
        print(f"    ✓ Watched: +{len(result['watched'])-before} unrated ({len(result['watched'])} total)")
    else:
        print(f"    ⚠ watched.csv not found in {lb_dir}")

    # watchlist.csv — want to see
    watchlist_path = os.path.join(lb_dir, "watchlist.csv")
    if os.path.exists(watchlist_path):
        with open(watchlist_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                title = clean_title(row.get("Name",""))
                if not title: continue
                result["watchlist"][title.lower()] = {"title": title, "year": row.get("Year","")}
        print(f"    ✓ Watchlist: {len(result['watchlist'])} films")
    else:
        print(f"    ⚠ watchlist.csv not found in {lb_dir}")

    print(f"    ✓ Loved (4.5-5★): {len(result['loved'])} · Liked (3.5-4★): {len(result['liked'])}")
    return result


def match_letterboxd(title: str, lb: dict) -> dict:
    key = re.sub(r'\s*\([^)]+\)\s*$', '', title.lower()).strip()

    for lookup in [key, title.lower().strip()]:
        if lookup in lb["loved"]:
            r = lb["loved"][lookup].get("rating", 5.0)
            return {"status":"loved",  "rating":r, "label":f"⭐ You rated this {_stars(r)}"}
        if lookup in lb["liked"]:
            r = lb["liked"][lookup].get("rating", 4.0)
            return {"status":"liked",  "rating":r, "label":f"👍 You rated this {_stars(r)}"}
        if lookup in lb["watchlist"]:
            return {"status":"watchlist", "label":"📋 On your watchlist"}
        if lookup in lb["watched"]:
            r = lb["watched"][lookup].get("rating")
            return {"status":"seen", "rating":r,
                    "label":"👁 You've seen this" + (f" ({_stars(r)})" if r else "")}

    return {"status":"new", "label":"🆕 New to you"}


def _stars(r):
    if not r: return ""
    return "★" * int(r) + ("½" if (r % 1) >= 0.5 else "")


# ══════════════════════════════════════════════════════════════
# CLAUDE AI CLASSIFIER
# ══════════════════════════════════════════════════════════════

def classify_with_claude(movies: list) -> list:
    api_key = os.environ.get("ANTHROPIC_API_KEY","")
    if not api_key:
        print("\n⚠ ANTHROPIC_API_KEY not set — skipping classification")
        print("  Run: export ANTHROPIC_API_KEY='sk-ant-...'")
        return movies

    unique = {}
    for m in movies:
        t = m["title"]
        if t not in unique:
            unique[t] = m.get("description","")

    print(f"\n── AI CLASSIFICATION ─────────────────────────────────")
    print(f"    Classifying {len(unique)} titles with Claude...")

    film_list = "\n".join(
        f'- "{t}"' + (f'  [Note: {d[:180]}]' if d else "")
        for t, d in unique.items()
    )

    prompt = f"""You are a repertory cinema programmer with encyclopedic knowledge of film history — think the sensibility of Film Forum, IFC Center, or the Criterion Collection.

I have films currently playing at theaters near New York City. Identify which are CLASSIC or REVIVAL screenings vs. new releases in their normal theatrical run.

MARK AS CLASSIC/REVIVAL if:
- Released more than ~5 years ago AND has genuine cultural standing
- A recognized landmark film even from the 90s/2000s (Eyes Wide Shut, Mulholland Drive, There Will Be Blood, etc.)
- Showing in a special format: 70mm, 35mm, 4K restoration — dead giveaways
- Part of a retrospective, anniversary, or director series
- A foreign/art house classic being revived
- A cult or midnight movie staple
- By canonical directors: Hitchcock, Kubrick, Kurosawa, Fellini, Bergman, Godard, Tarkovsky, Lynch, Scorsese, Wilder, Welles, etc.

MARK AS NEW RELEASE if:
- A mainstream new movie from the last 1-2 years in normal theatrical run
- No signals of being a repertory/revival screening

When in doubt, lean toward classic if there's reasonable basis.

Films:
{film_list}

Respond ONLY with a JSON array. Each object:
- "title": exact title as given
- "is_classic": true or false
- "year": integer or null
- "reason": brief phrase e.g. "1958 Hitchcock, 4K restoration" or "2024 new release"

Pure JSON only. No markdown, no preamble."""

    try:
        r = SESSION.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 4000,
                "messages": [{"role":"user","content":prompt}]
            },
            timeout=45
        )
        r.raise_for_status()
        raw = r.json()["content"][0]["text"]
        raw = re.sub(r'^```json\s*|^```\s*|\s*```$', '', raw.strip(), flags=re.M)
        classifications = json.loads(raw)
        class_map = {c["title"]: c for c in classifications}

        classics = []
        for m in movies:
            info = class_map.get(m["title"])
            if info and info.get("is_classic"):
                m["year"]   = info.get("year")
                m["reason"] = info.get("reason","")
                classics.append(m)

        print(f"    ✓ {len(classics)} classic/revival films out of {len(movies)} total")
        return classics

    except Exception as e:
        print(f"    ✗ Claude error: {e}")
        return movies


# ══════════════════════════════════════════════════════════════
# HTML GENERATOR
# ══════════════════════════════════════════════════════════════

def generate_html(movies: list, lb_data: dict) -> str:
    city_ms   = [m for m in movies if m["theater_config"]["location"] == "city"]
    suburb_ms = [m for m in movies if m["theater_config"]["location"] == "suburbs"]

    def group(lst):
        g = {}
        for m in lst:
            g.setdefault(m["theater_name"],[]).append(m)
        return g

    def lb_badge(ctx):
        cls = {"loved":"lb-loved","liked":"lb-liked","watchlist":"lb-watchlist",
               "seen":"lb-seen","new":"lb-new"}.get(ctx.get("status","new"),"lb-new")
        return f'<span class="lb-badge {cls}">{ctx.get("label","")}</span>'

    def fmt_showtimes(dates, times):
        parts = []
        if dates:
            fmts = []
            for d in sorted(set(dates))[:8]:
                try:
                    fmts.append(datetime.strptime(d[:10],"%Y-%m-%d").strftime("%a %b %-d"))
                except Exception:
                    fmts.append(d)
            more = f' <span class="more">+{len(dates)-8} more</span>' if len(dates)>8 else ""
            parts.append(f'<span class="sd">📅 {" · ".join(fmts)}{more}</span>')
        if times:
            clean = []
            for t in times:
                t = t.strip()
                try:
                    m2 = re.match(r'^(\d{1,2}):(\d{2})\s*(am|pm)?$', t, re.I)
                    if m2:
                        h,mn = int(m2.group(1)),int(m2.group(2))
                        ap = m2.group(3) or ("PM" if h>=12 else "AM")
                        h2 = h%12 or 12
                        t = f"{h2}:{mn:02d} {ap.upper()}"
                except Exception:
                    pass
                if t and t not in clean:
                    clean.append(t)
            if clean:
                parts.append(f'<span class="st">🕐 {" · ".join(clean[:8])}</span>')
        return f'<div class="showtimes">{"  ".join(parts)}</div>' if parts else ""

    def movie_card(m):
        ctx      = match_letterboxd(m["title"], lb_data)
        year_s   = f" <span class='year'>({m['year']})</span>" if m.get("year") else ""
        reason_s = f'<span class="reason">{m["reason"]}</span>' if m.get("reason") else ""
        shows    = fmt_showtimes(m.get("dates",[]), m.get("times",[]))
        badge    = lb_badge(ctx)
        link     = (f'<a href="{m["source_url"]}" class="tix" target="_blank">Tickets →</a>'
                    if m.get("source_url") else "")
        return f"""
        <div class="movie-card lb-{ctx['status']}">
          <div class="movie-top">
            <span class="movie-title"><em>{m['title']}</em>{year_s}</span>
            {link}
          </div>
          <div class="movie-meta">{reason_s}{badge}</div>
          {shows}
        </div>"""

    def theater_block(name, ms):
        cfg = ms[0]["theater_config"]
        rb  = ('<span class="regal-badge">★ YOUR REGAL SUBSCRIPTION</span>'
               if cfg["is_regal"] else "")
        cards = "".join(movie_card(m) for m in ms)
        cls = "regal-theater" if cfg["is_regal"] else ""
        return f"""
      <div class="theater-block {cls}">
        <div class="theater-header">
          <span class="theater-name">{name}</span>{rb}
          <span class="theater-city">{cfg['city']}</span>
        </div>
        <div class="movie-list">{cards}</div>
      </div>"""

    def section(label, ms):
        if not ms:
            return f'<div class="section-label"><span>{label}</span></div><div class="empty-state">No classic screenings found right now.</div>'
        blocks = "".join(theater_block(n,t) for n,t in group(ms).items())
        return f'<div class="section-label"><span>{label}</span></div>{blocks}'

    now   = datetime.now().strftime("%B %-d, %Y at %-I:%M %p")
    total = len(movies)
    sprockets = '<div class="sprocket"></div>' * 11

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Classic Cinema Calendar — Larchmont Area</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;0,900;1,400;1,700&family=Libre+Baskerville:ital,wght@0,400;0,700;1,400&family=Josefin+Sans:wght@300;400;600;700&display=swap" rel="stylesheet">
<style>
:root{{
  --cream:#f5f0e8;--parchment:#ede6d3;--dark:#150f06;--brown:#3d2b1a;
  --amber:#b8742a;--gold:#d4a853;--red:#8b1a1a;--muted:#7a6a55;
  --border:#c8b89a;--regal-navy:#0f2340;--regal-gold:#c9a84c;
  --lb-loved:#e8a020;--lb-liked:#5b8dd9;--lb-watchlist:#4caf7d;
  --lb-seen:#888;--lb-new:#9c6fb5;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--cream);background-image:repeating-linear-gradient(0deg,transparent,transparent 30px,rgba(100,70,30,.045) 30px,rgba(100,70,30,.045) 31px);color:var(--dark);font-family:'Libre Baskerville',Georgia,serif;min-height:100vh}}
body::before{{content:'';position:fixed;inset:0;pointer-events:none;z-index:9999;opacity:.03;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");background-size:160px}}
.masthead{{background:var(--dark);border-bottom:3px double var(--gold);padding:1.75rem 1.5rem 1.25rem;text-align:center;position:relative;overflow:hidden}}
.masthead::after{{content:'';position:absolute;inset:0;pointer-events:none;background:repeating-linear-gradient(90deg,transparent,transparent 44px,rgba(212,168,83,.035) 44px,rgba(212,168,83,.035) 45px)}}
.sprocket-strip{{display:flex;justify-content:center;gap:14px;margin-bottom:1rem}}
.sprocket{{width:13px;height:13px;border:2px solid var(--gold);border-radius:3px;opacity:.45}}
.masthead h1{{font-family:'Playfair Display',serif;font-size:clamp(1.9rem,5vw,3.4rem);font-weight:900;font-style:italic;color:var(--gold);letter-spacing:.06em;text-transform:uppercase;text-shadow:1px 2px 6px rgba(0,0,0,.6);line-height:1}}
.masthead .tagline{{font-family:'Josefin Sans',sans-serif;font-size:.72rem;letter-spacing:.28em;color:var(--border);text-transform:uppercase;margin-top:.5rem}}
.masthead .generated{{font-family:'Josefin Sans',sans-serif;font-size:.62rem;letter-spacing:.14em;color:rgba(255,255,255,.25);margin-top:.6rem;text-transform:uppercase}}
.container{{max-width:860px;margin:0 auto;padding:2rem 1.25rem}}
.stats-bar{{display:flex;justify-content:center;flex-wrap:wrap;gap:1.5rem;background:var(--parchment);border:1px solid var(--border);padding:.7rem 1.5rem;margin-bottom:1.5rem;font-family:'Josefin Sans',sans-serif;font-size:.72rem;letter-spacing:.1em;text-transform:uppercase;color:var(--brown)}}
.stats-bar strong{{color:var(--red);font-size:.85rem}}
.legend{{display:flex;flex-wrap:wrap;gap:.5rem .9rem;justify-content:center;margin-bottom:2rem;font-family:'Josefin Sans',sans-serif;font-size:.65rem;letter-spacing:.08em;text-transform:uppercase}}
.legend-item{{display:flex;align-items:center;gap:.35rem}}
.legend-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
.section-label{{display:flex;align-items:center;gap:.75rem;margin:2.5rem 0 1.25rem}}
.section-label::before,.section-label::after{{content:'';flex:1;height:1px;background:var(--amber)}}
.section-label span{{font-family:'Josefin Sans',sans-serif;font-size:.68rem;font-weight:700;letter-spacing:.28em;text-transform:uppercase;color:var(--amber);white-space:nowrap;padding:0 .4rem}}
.theater-block{{background:white;border:1px solid var(--border);border-top:3px solid var(--amber);margin-bottom:1.4rem;box-shadow:1px 3px 10px rgba(0,0,0,.065);animation:fadeUp .35s ease both}}
.theater-block.regal-theater{{border-top:3px solid var(--regal-navy);background:linear-gradient(135deg,#f5f7fc 0%,white 25%)}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:translateY(0)}}}}
.theater-header{{display:flex;align-items:center;flex-wrap:wrap;gap:.6rem;padding:.75rem 1.15rem;background:var(--parchment);border-bottom:1px solid var(--border)}}
.regal-theater .theater-header{{background:linear-gradient(90deg,#e4e9f5,#ecf0fa)}}
.theater-name{{font-family:'Playfair Display',serif;font-size:1rem;font-weight:700;color:var(--brown)}}
.regal-theater .theater-name{{color:var(--regal-navy);font-weight:900}}
.theater-city{{font-family:'Josefin Sans',sans-serif;font-size:.62rem;letter-spacing:.14em;text-transform:uppercase;color:#999;margin-left:auto}}
.regal-badge{{background:var(--regal-navy);color:var(--regal-gold);font-family:'Josefin Sans',sans-serif;font-size:.58rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;padding:.18rem .5rem;border:1px solid var(--regal-gold);border-radius:2px}}
.movie-list{{padding:.15rem 0}}
.movie-card{{padding:.8rem 1.15rem;border-bottom:1px dashed var(--border);transition:background .15s;border-left:3px solid transparent}}
.movie-card:last-child{{border-bottom:none}}
.movie-card:hover{{background:#fdf9f3}}
.movie-card.lb-loved{{border-left-color:var(--lb-loved)}}
.movie-card.lb-liked{{border-left-color:var(--lb-liked)}}
.movie-card.lb-watchlist{{border-left-color:var(--lb-watchlist)}}
.movie-card.lb-seen{{border-left-color:#ddd}}
.movie-card.lb-new{{border-left-color:var(--lb-new)}}
.movie-top{{display:flex;align-items:baseline;justify-content:space-between;gap:.75rem;flex-wrap:wrap}}
.movie-title{{font-family:'Playfair Display',serif;font-size:.98rem;color:var(--dark)}}
.movie-title .year{{font-family:'Josefin Sans',sans-serif;font-size:.7rem;color:var(--muted);font-style:normal;letter-spacing:.04em}}
.movie-meta{{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin-top:.25rem}}
.reason{{font-family:'Josefin Sans',sans-serif;font-size:.63rem;letter-spacing:.1em;text-transform:uppercase;color:var(--amber)}}
.lb-badge{{display:inline-block;font-family:'Josefin Sans',sans-serif;font-size:.6rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;padding:.15rem .45rem;border-radius:2px;border:1px solid currentColor;white-space:nowrap}}
.lb-badge.lb-loved{{color:var(--lb-loved);background:#fff8ec}}
.lb-badge.lb-liked{{color:var(--lb-liked);background:#eef3fc}}
.lb-badge.lb-watchlist{{color:var(--lb-watchlist);background:#edfaf4}}
.lb-badge.lb-seen{{color:var(--lb-seen);background:#f5f5f5}}
.lb-badge.lb-new{{color:var(--lb-new);background:#f8f0fc}}
.showtimes{{font-family:'Josefin Sans',sans-serif;font-size:.68rem;letter-spacing:.04em;margin-top:.35rem;display:flex;flex-wrap:wrap;gap:.25rem .75rem;align-items:center}}
.sd{{color:#666}}
.st{{color:var(--amber);font-weight:700;letter-spacing:.06em}}
.more{{color:#aaa;font-style:italic}}
.tix{{font-family:'Josefin Sans',sans-serif;font-size:.63rem;letter-spacing:.1em;text-transform:uppercase;color:var(--red);text-decoration:none;border-bottom:1px solid var(--red);white-space:nowrap;transition:opacity .2s;flex-shrink:0}}
.tix:hover{{opacity:.55}}
.empty-state{{font-family:'Josefin Sans',sans-serif;font-size:.75rem;letter-spacing:.12em;text-transform:uppercase;color:#bbb;text-align:center;padding:1.75rem;border:1px dashed var(--border)}}
.footer{{text-align:center;padding:2rem;border-top:1px solid var(--border);margin-top:3rem;font-family:'Josefin Sans',sans-serif;font-size:.62rem;letter-spacing:.14em;text-transform:uppercase;color:#bbb}}
@media(max-width:580px){{.stats-bar{{flex-direction:column;align-items:center}}.theater-header{{flex-direction:column;align-items:flex-start}}.theater-city{{margin-left:0}}.movie-top{{flex-direction:column}}}}
</style>
</head>
<body>
<header class="masthead">
  <div class="sprocket-strip">{sprockets}</div>
  <h1>Classic Cinema</h1>
  <div class="tagline">Revival &amp; Repertory Screenings — Larchmont Area</div>
  <div class="generated">Generated {now}</div>
</header>
<main class="container">
  <div class="stats-bar">
    <span><strong>{total}</strong> Classic Screenings</span>
    <span><strong>{len(city_ms)}</strong> In The City</span>
    <span><strong>{len(suburb_ms)}</strong> In The Suburbs</span>
  </div>
  <div class="legend">
    <div class="legend-item"><div class="legend-dot" style="background:var(--lb-loved)"></div><span>You loved it (★★★★½–★★★★★)</span></div>
    <div class="legend-item"><div class="legend-dot" style="background:var(--lb-liked)"></div><span>You liked it (★★★½–★★★★)</span></div>
    <div class="legend-item"><div class="legend-dot" style="background:var(--lb-watchlist)"></div><span>On your watchlist</span></div>
    <div class="legend-item"><div class="legend-dot" style="background:var(--lb-seen)"></div><span>You've seen it</span></div>
    <div class="legend-item"><div class="legend-dot" style="background:var(--lb-new)"></div><span>New to you</span></div>
  </div>
  {section("✦ New York City", city_ms)}
  {section("✦ Westchester &amp; Suburbs", suburb_ms)}
</main>
<footer class="footer">
  Classic Cinema Calendar · Larchmont, NY · @{LETTERBOXD_USERNAME} · AI by Claude · {now}
</footer>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════
# DEMO DATA
# ══════════════════════════════════════════════════════════════

def demo_movies():
    return [
        {**make_movie("The Trouble with Harry","Alamo Drafthouse Yonkers",
            dates=["2026-03-05","2026-03-06"],times=["7:30 PM"]),
         "year":1955,"reason":"1955 Hitchcock dark comedy"},
        {**make_movie("Vertigo","IFC Center",
            dates=["2026-03-07","2026-03-08","2026-03-09"],times=["6:00 PM","8:30 PM"]),
         "year":1958,"reason":"1958 Hitchcock, 4K restoration"},
        {**make_movie("Rear Window","IFC Center",
            dates=["2026-03-15","2026-03-16"],times=["4:00 PM","7:00 PM"]),
         "year":1954,"reason":"1954 Hitchcock retrospective"},
        {**make_movie("2001: A Space Odyssey","Nitehawk Cinema",
            dates=["2026-03-08"],times=["9:00 PM"]),
         "year":1968,"reason":"1968 Kubrick, 70mm screening"},
        {**make_movie("Chinatown","Angelika Film Center",
            dates=["2026-03-10","2026-03-11"],times=["5:00 PM","8:00 PM"]),
         "year":1974,"reason":"1974 Polanski noir, 50th anniversary"},
        {**make_movie("Lawrence of Arabia","Regal New Roc",
            dates=["2026-03-12"],times=["6:00 PM"]),
         "year":1962,"reason":"1962 David Lean, 70mm restored print"},
        {**make_movie("Sunset Boulevard","Pelham Picture House",
            dates=["2026-03-14","2026-03-15"],times=["7:30 PM"]),
         "year":1950,"reason":"1950 Wilder Hollywood noir"},
        {**make_movie("8½","Jacob Burns Film Center",
            dates=["2026-03-13","2026-03-14"],times=["7:00 PM"]),
         "year":1963,"reason":"1963 Fellini, Italian cinema series"},
        {**make_movie("Rashomon","Jacob Burns Film Center",
            dates=["2026-03-20"],times=["7:30 PM"]),
         "year":1950,"reason":"1950 Kurosawa, Janus Films restoration"},
        {**make_movie("There Will Be Blood","IFC Center",
            dates=["2026-03-22","2026-03-23"],times=["4:30 PM","7:30 PM"]),
         "year":2007,"reason":"2007 PTA masterpiece, anniversary"},
        {**make_movie("Mulholland Drive","Nitehawk Cinema",
            dates=["2026-03-21"],times=["10:00 PM"]),
         "year":2001,"reason":"2001 Lynch, midnight screening"},
        {**make_movie("The Godfather","Alamo Drafthouse Yonkers",
            dates=["2026-03-25","2026-03-26"],times=["5:00 PM","8:30 PM"]),
         "year":1972,"reason":"1972 Coppola, 4K remaster"},
    ]

def demo_letterboxd():
    return {
        "watched":{
            "vertigo":{"title":"Vertigo","rating":5.0},
            "chinatown":{"title":"Chinatown","rating":5.0},
            "2001: a space odyssey":{"title":"2001: A Space Odyssey","rating":5.0},
            "the godfather":{"title":"The Godfather","rating":5.0},
            "rashomon":{"title":"Rashomon","rating":4.5},
            "there will be blood":{"title":"There Will Be Blood","rating":4.5},
            "sunset boulevard":{"title":"Sunset Boulevard","rating":4.0},
            "8½":{"title":"8½","rating":4.0},
        },
        "watchlist":{
            "the trouble with harry":{"title":"The Trouble with Harry"},
            "rear window":{"title":"Rear Window"},
            "mulholland drive":{"title":"Mulholland Drive"},
        },
        "loved":{
            "vertigo":{"title":"Vertigo","rating":5.0},
            "chinatown":{"title":"Chinatown","rating":5.0},
            "2001: a space odyssey":{"title":"2001: A Space Odyssey","rating":5.0},
            "the godfather":{"title":"The Godfather","rating":5.0},
            "rashomon":{"title":"Rashomon","rating":4.5},
            "there will be blood":{"title":"There Will Be Blood","rating":4.5},
        },
        "liked":{
            "sunset boulevard":{"title":"Sunset Boulevard","rating":4.0},
            "8½":{"title":"8½","rating":4.0},
        },
    }


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print("=" * 58)
    print("  CLASSIC CINEMA CALENDAR  v3")
    print("  Larchmont, NY  ·  with Letterboxd integration")
    print("=" * 58)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ── Scrape theaters ──
    print("\n── THEATER SCRAPING ──────────────────────────────────")
    scrapers = {
        "alamo":    scrape_alamo,
        "ifc":      scrape_ifc,
        "angelika": scrape_angelika,
        "nitehawk": scrape_nitehawk,
        "pelham":   scrape_pelham,
        "burns":    scrape_burns,
        "fandango": lambda n: scrape_fandango(n, THEATERS[n]["fandango_id"]),
    }
    all_movies = []
    for name, cfg in THEATERS.items():
        print(f"\n🎬 {name}")
        try:
            fn = scrapers.get(cfg["scrape_strategy"])
            ms = fn(name) if fn else []
            print(f"    ✓ {len(ms)} title(s)")
            all_movies.extend(ms)
        except Exception as e:
            print(f"    ✗ {e}")
        time.sleep(1.0)

    print(f"\n📋 Total titles scraped: {len(all_movies)}")

    demo_mode = len(all_movies) == 0
    if demo_mode:
        print("⚠ No live data — using demo data")
        all_movies = demo_movies()

    # ── Letterboxd ──
    lb_data = demo_letterboxd() if demo_mode else load_letterboxd_csv(LETTERBOXD_USERNAME)

    # ── Classify ──
    classics = classify_with_claude(all_movies) if not demo_mode else all_movies

    # ── Output ──
    print("\n── OUTPUT ────────────────────────────────────────────")
    html = generate_html(classics, lb_data)

    html_path = os.path.join(OUTPUT_DIR, "classic_cinema_calendar.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✓ HTML  → {html_path}")

    json_path = os.path.join(OUTPUT_DIR, "classic_movies.json")
    export = []
    for m in classics:
        ctx = match_letterboxd(m["title"], lb_data)
        export.append({
            "title":     m["title"],
            "year":      m.get("year"),
            "reason":    m.get("reason",""),
            "theater":   m["theater_name"],
            "location":  m["theater_config"]["location"],
            "city":      m["theater_config"]["city"],
            "address":   m["theater_config"]["address"],
            "is_regal":  m["theater_config"]["is_regal"],
            "dates":     m.get("dates",[]),
            "times":     m.get("times",[]),
            "source_url":m.get("source_url",""),
            "lb_status": ctx.get("status","new"),
            "lb_label":  ctx.get("label",""),
            "lb_rating": ctx.get("rating"),
        })
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(export, f, indent=2)
    print(f"  ✓ JSON  → {json_path}")

    print(f"\n✅ Done — {len(classics)} classic screenings found.")
    if demo_mode:
        print("  (Demo mode — run again after confirming internet access)")
    else:
        print(f"  Open: open {html_path}")

if __name__ == "__main__":
    main()
