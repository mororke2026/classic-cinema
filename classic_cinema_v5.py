#!/usr/bin/env python3
"""
Classic Cinema Calendar — v5
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Uses Playwright (real headless browser) to scrape JS-rendered showtimes
from 7 local theaters, classifies classic/revival films using Claude AI,
cross-references your Letterboxd profile (mororke), and outputs:
  1. A beautiful HTML calendar with clickable links + real showtimes
  2. classic_cinema.ics — subscribe in Google Calendar for live updates

Setup:
    pip3 install requests beautifulsoup4 playwright
    playwright install chromium
    export ANTHROPIC_API_KEY='sk-ant-...'

Usage:
    python3 classic_cinema_v5.py
"""

import json
import os
import re
import time
import uuid
from datetime import datetime, date, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ══════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════

LETTERBOXD_USERNAME = "mororke"
OUTPUT_DIR = os.path.expanduser("~/classic-cinema")

THEATERS = {
    # "IFC Center": {
    #         "location": "city", "city": "New York, NY",
    #         "address": "323 6th Ave, New York, NY 10014",
    #         "scrape_strategy": "ifc",
    #         "website": "https://www.ifccenter.com",
    #         "showtimes_url": "https://www.ifccenter.com/films/",
    #         "fandango_id": "AAXKP", "is_regal": False,
    #     },
    # "Angelika Film Center": {
    #         "location": "city", "city": "New York, NY",
    #         "address": "18 W Houston St, New York, NY 10012",
    #         "scrape_strategy": "angelika",
    #         "website": "https://www.angelikafilmcenter.com",
    #         "showtimes_url": "https://www.angelikafilmcenter.com/nyc",
    #         "fandango_id": "AAECI", "is_regal": False,
    #     },
    # "Nitehawk Cinema": {
    #         "location": "city", "city": "Brooklyn, NY",
    #         "address": "136 Metropolitan Ave, Brooklyn, NY 11249",
    #         "scrape_strategy": "nitehawk",
    #         "website": "https://nitehawkcinema.com",
    #         "showtimes_url": "https://nitehawkcinema.com/williamsburg/",
    #         "fandango_id": "AARVP", "is_regal": False,
    #     },
    "Alamo Drafthouse Yonkers": {
        "location": "suburbs", "city": "Yonkers, NY",
        "address": "175 Main St, Yonkers, NY 10701",
        "scrape_strategy": "alamo",
        "website": "https://drafthouse.com/yonkers",
        "showtimes_url": "https://drafthouse.com/yonkers",
        "fandango_id": "AAWWC", "is_regal": False,
    },
    "Regal New Roc": {
        "location": "suburbs", "city": "New Rochelle, NY",
        "address": "33 LeCount Pl, New Rochelle, NY 10801",
        "scrape_strategy": "regal",
        "website": "https://www.regmovies.com",
        "showtimes_url": "https://www.fandango.com/regal-new-roc-4dx-imax-and-rpx-aanlc/theater-page",
        "fandango_id": "AANLC", "is_regal": True,
    },
    "Pelham Picture House": {
        "location": "suburbs", "city": "Pelham, NY",
        "address": "175 Wolf's Lane, Pelham, NY 10803",
        "scrape_strategy": "pelham",
        "website": "https://www.thepicturehouse.org",
        "showtimes_url": "https://www.thepicturehouse.org/now-playing",
        "fandango_id": "AAHRT", "is_regal": False,
    },
    "Jacob Burns Film Center": {
        "location": "suburbs", "city": "Pleasantville, NY",
        "address": "364 Manville Rd, Pleasantville, NY 10570",
        "scrape_strategy": "burns",
        "website": "https://burnsfilmcenter.org",
        "showtimes_url": "https://burnsfilmcenter.org/film/",
        "fandango_id": "AAPXM", "is_regal": False,
    },
    "Mamaroneck Cinemas": {
        "location": "suburbs", "city": "Mamaroneck, NY",
        "address": "243 Mamaroneck Ave, Mamaroneck, NY 10543",
        "scrape_strategy": "fandango",
        "website": "https://www.mamaroneckcinemas.com",
        "showtimes_url": "https://www.fandango.com/mamaroneck-cinemas-aablm/theater-page",
        "fandango_id": "AABLM", "is_regal": False,
    },
}

# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
})

def get_json(url: str) -> Optional[dict | list]:
    try:
        r = SESSION.get(url, timeout=20, headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"    ⚠ JSON failed [{url[:60]}]: {e}")
        return None

def clean(t: str) -> str:
    t = t.strip()
    t = re.sub(r'\s+', ' ', t)
    for old, new in [("&amp;","&"),("&#39;","'"),("&quot;",'"'),
                     ("\u2019","'"),("\u2018","'"),("&nbsp;"," ")]:
        t = t.replace(old, new)
    return t

def make_movie(title, theater_name, dates=None, times=None, url=None, description=None):
    cfg = THEATERS[theater_name]
    return {
        "title":        clean(title),
        "theater_name": theater_name,
        "theater_config": cfg,
        "dates":        dates or [],
        "times":        times or [],
        "source_url":   url or cfg.get("showtimes_url") or cfg["website"],
        "description":  description or "",
    }

def pw_page(playwright, url: str, wait_for: str = None, timeout: int = 20000):
    """Launch a headless Chromium page, navigate, optionally wait for a selector."""
    browser = playwright.chromium.launch(headless=True)
    ctx  = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        # Block images/fonts to speed up loading
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    ctx.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,otf}", lambda r: r.abort())
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        if wait_for:
            page.wait_for_selector(wait_for, timeout=timeout)
        else:
            page.wait_for_load_state("networkidle", timeout=timeout)
    except PWTimeout:
        pass  # Return whatever loaded
    html = page.content()
    browser.close()
    return html

def parse_dates_from_text(text: str) -> list[str]:
    """Extract YYYY-MM-DD dates from free text."""
    found = []
    # ISO dates
    for m in re.finditer(r'\b(\d{4}-\d{2}-\d{2})\b', text):
        found.append(m.group(1))
    # "March 7", "Mar 7", "March 7, 2026"
    months = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
              "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
    yr = datetime.now().year
    for m in re.finditer(
        r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:,?\s*(\d{4}))?\b',
        text, re.I
    ):
        mo  = months[m.group(1).lower()[:3]]
        day = int(m.group(2))
        y   = int(m.group(3)) if m.group(3) else yr
        try:
            found.append(date(y, mo, day).isoformat())
        except ValueError:
            pass
    return list(dict.fromkeys(found))  # dedupe, preserve order

def parse_times_from_text(text: str) -> list[str]:
    """Extract showtime strings like '7:30 PM' from text."""
    found = []
    for m in re.finditer(r'\b(\d{1,2}:\d{2})\s*(am|pm)\b', text, re.I):
        t = f"{m.group(1)} {m.group(2).upper()}"
        if t not in found:
            found.append(t)
    return found

# ══════════════════════════════════════════════════════════════
# THEATER SCRAPERS
# ══════════════════════════════════════════════════════════════

def scrape_alamo(theater_name, pw):
    """Alamo JSON API — presentations + sessions joined by presentationSlug."""
    print(f"    → Alamo API")
    from collections import defaultdict
    movies = []
    data = get_json("https://drafthouse.com/s/mother/v2/schedule/market/yonkers")
    if data:
        try:
            d        = data.get("data", {})
            presos   = d.get("presentations", [])
            sessions = d.get("sessions", [])
            sess_by_slug = defaultdict(list)
            for s in sessions:
                slug = s.get("presentationSlug","")
                if slug:
                    sess_by_slug[slug].append(s)
            grouped = {}
            for p in presos:
                attrs = p.get("presentationAttributeSlugs", [])
                if "first-run" in attrs and "alamo-exclusive" not in attrs:
                    continue
                show  = p.get("show") or {}
                title = clean(show.get("title",""))
                if not title: continue
                slug  = p.get("slug","")
                desc  = show.get("headline","") or ""
                url   = f"https://drafthouse.com/yonkers/show/{slug}" if slug else None
                dates_for_film = []
                times_for_film = []
                for s in sess_by_slug.get(slug, []):
                    raw_dt = s.get("showTimeClt","")
                    if raw_dt and len(raw_dt) >= 16:
                        date_part = raw_dt[:10]
                        time_part = raw_dt[11:16]
                        if date_part and date_part not in dates_for_film:
                            dates_for_film.append(date_part)
                        try:
                            h, m = int(time_part[:2]), int(time_part[3:])
                            ap  = "PM" if h >= 12 else "AM"
                            h12 = h % 12 or 12
                            t12 = f"{h12}:{m:02d} {ap}"
                            if t12 not in times_for_film:
                                times_for_film.append(t12)
                        except Exception:
                            pass
                if title not in grouped:
                    grouped[title] = make_movie(title, theater_name,
                                                dates=dates_for_film, times=times_for_film,
                                                url=url, description=desc)
                else:
                    for dd in dates_for_film:
                        if dd not in grouped[title]["dates"]:
                            grouped[title]["dates"].append(dd)
                    for tt in times_for_film:
                        if tt not in grouped[title]["times"]:
                            grouped[title]["times"].append(tt)
            movies = list(grouped.values())
            print(f"    ✓ API: {len(movies)} films ({sum(1 for m in movies if m['dates'])} with dates)")
        except Exception as e:
            print(f"    ⚠ Parse error: {e}")
    return movies

def scrape_ifc(theater_name, pw):
    """IFC Center — Playwright for JS-rendered schedule."""
    print(f"    → IFC Center (Playwright)")
    movies = []
    try:
        html = pw_page(pw, "https://www.ifccenter.com/films/",
                       wait_for="article, .film-listing, .card")
        soup = BeautifulSoup(html, "html.parser")
        seen = set()

        # Each film is typically an <article> or card with a title link
        for article in soup.find_all(["article","div"], class_=re.compile(r"film|card|movie", re.I)):
            # Title
            title_tag = (article.find(["h2","h3","h4"]) or
                         article.find("a", href=re.compile(r"/films/")))
            if not title_tag: continue
            title = clean(title_tag.get_text())
            if not title or len(title) < 2 or title.lower() in seen: continue
            if any(x in title.lower() for x in ["sign in","menu","search","films","now playing"]): continue
            seen.add(title.lower())

            # URL — prefer film-specific page
            link = article.find("a", href=re.compile(r"/films/[a-z0-9-]+"))
            href = link["href"] if link else ""
            url  = f"https://www.ifccenter.com{href}" if href and not href.startswith("http") else href or THEATERS[theater_name]["showtimes_url"]

            # Dates & times from surrounding text
            text  = article.get_text(" ")
            dates = parse_dates_from_text(text)
            times = parse_times_from_text(text)
            movies.append(make_movie(title, theater_name, dates=dates, times=times, url=url))

        # Fallback: any /films/ links
        if not movies:
            for a in soup.find_all("a", href=re.compile(r"/films/[a-z0-9][a-z0-9-]+")):
                title = clean(a.get_text())
                if title and len(title) > 2 and title.lower() not in seen:
                    seen.add(title.lower())
                    href = a["href"]
                    url  = f"https://www.ifccenter.com{href}" if not href.startswith("http") else href
                    movies.append(make_movie(title, theater_name, url=url))

        print(f"    ✓ {len(movies)} films")
    except Exception as e:
        print(f"    ⚠ IFC error: {e}")
    return movies


def scrape_angelika(theater_name, pw):
    """Angelika — Playwright for JS-rendered schedule."""
    print(f"    → Angelika (Playwright)")
    movies = []
    try:
        html = pw_page(pw, "https://www.angelikafilmcenter.com/nyc",
                       wait_for=".movie-title, .film-title, article h2, article h3")
        soup = BeautifulSoup(html, "html.parser")
        seen = set()

        for article in soup.find_all(["article","div","li"],
                                      class_=re.compile(r"film|movie|show|card", re.I)):
            title_tag = article.find(["h2","h3","h4"])
            if not title_tag: continue
            title = clean(title_tag.get_text())
            if not title or len(title) < 2 or title.lower() in seen: continue
            seen.add(title.lower())

            link  = article.find("a", href=re.compile(r"/film|/movie|/nyc/"))
            href  = link["href"] if link else ""
            url   = (f"https://www.angelikafilmcenter.com{href}"
                     if href and not href.startswith("http") else
                     href or THEATERS[theater_name]["showtimes_url"])

            text  = article.get_text(" ")
            dates = parse_dates_from_text(text)
            times = parse_times_from_text(text)
            movies.append(make_movie(title, theater_name, dates=dates, times=times, url=url))

        print(f"    ✓ {len(movies)} films")
    except Exception as e:
        print(f"    ⚠ Angelika error: {e}")
    return movies


def scrape_nitehawk(theater_name, pw):
    """Nitehawk — Playwright, then check for per-film date pages."""
    print(f"    → Nitehawk (Playwright)")
    movies = []
    try:
        html = pw_page(pw, "https://nitehawkcinema.com/williamsburg/",
                       wait_for=".film-title, .show-title, article h2, .event-title")
        soup = BeautifulSoup(html, "html.parser")
        seen = set()

        for article in soup.find_all(["article","div","li","section"],
                                      class_=re.compile(r"film|show|event|movie|screen", re.I)):
            title_tag = article.find(["h2","h3","h4"])
            if not title_tag: continue
            title = clean(title_tag.get_text())
            if not title or len(title) < 2 or title.lower() in seen: continue
            if any(x in title.lower() for x in ["home","about","gift","contact","menu"]): continue
            seen.add(title.lower())

            link = article.find("a")
            href = link["href"] if link else ""
            url  = (f"https://nitehawkcinema.com{href}"
                    if href and not href.startswith("http") else
                    href or THEATERS[theater_name]["showtimes_url"])

            text  = article.get_text(" ")
            dates = parse_dates_from_text(text)
            times = parse_times_from_text(text)
            movies.append(make_movie(title, theater_name, dates=dates, times=times, url=url))

        # Also check JSON-LD for structured event data
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data  = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") in ("Movie","Event","ScreeningEvent"):
                        title = clean(item.get("name",""))
                        if title and title.lower() not in seen:
                            seen.add(title.lower())
                            raw_date = item.get("startDate","")
                            dates = [raw_date[:10]] if raw_date else []
                            movies.append(make_movie(title, theater_name,
                                dates=dates,
                                url=item.get("url","") or THEATERS[theater_name]["showtimes_url"],
                                description=item.get("description","")))
            except Exception:
                pass

        print(f"    ✓ {len(movies)} films")
    except Exception as e:
        print(f"    ⚠ Nitehawk error: {e}")
    return movies


def scrape_pelham(theater_name, pw):
    """Pelham Picture House — Playwright."""
    print(f"    → Pelham Picture House (Playwright)")
    movies = []
    for url in ["https://www.thepicturehouse.org/now-playing",
                "https://www.thepicturehouse.org"]:
        try:
            html = pw_page(pw, url, wait_for="article, .film, .movie, h2, h3")
            soup = BeautifulSoup(html, "html.parser")
            seen = set()

            for article in soup.find_all(["article","div","li"],
                                          class_=re.compile(r"film|movie|show|card|event", re.I)):
                title_tag = article.find(["h2","h3","h4"])
                if not title_tag: continue
                title = clean(title_tag.get_text())
                if not title or len(title) < 2 or title.lower() in seen: continue
                if any(x in title.lower() for x in ["home","about","contact","donate","gift","membership"]): continue
                seen.add(title.lower())

                link = article.find("a")
                href = link["href"] if link else ""
                film_url = (f"https://www.thepicturehouse.org{href}"
                            if href and not href.startswith("http") else
                            href or url)

                text  = article.get_text(" ")
                dates = parse_dates_from_text(text)
                times = parse_times_from_text(text)
                movies.append(make_movie(title, theater_name, dates=dates, times=times, url=film_url))

            if movies:
                print(f"    ✓ {len(movies)} films")
                break
        except Exception as e:
            print(f"    ⚠ Pelham error ({url}): {e}")

    if not movies:
        print(f"    → Pelham fallback: Fandango")
        movies = scrape_fandango_pw(theater_name, "AAHRT", pw)
    return movies


def scrape_burns(theater_name, pw):
    """Jacob Burns Film Center — Playwright."""
    print(f"    → Jacob Burns (Playwright)")
    movies = []
    try:
        html = pw_page(pw, "https://burnsfilmcenter.org/film/",
                       wait_for=".card-title, article h2, .entry-title")
        soup = BeautifulSoup(html, "html.parser")
        seen = set()

        skip = {"home","about","support","donate","education","membership",
                "gift","calendar","film","contact","press","blog"}

        # Burns film pages: /film/slug/
        for a in soup.find_all("a", href=re.compile(r"/film/[a-z0-9-]+/?$")):
            title = clean(a.get_text())
            if not title or len(title) < 2 or title.lower() in seen: continue
            if title.lower() in skip: continue
            seen.add(title.lower())
            href = a["href"]
            url  = f"https://burnsfilmcenter.org{href}" if not href.startswith("http") else href

            # Fetch the film page for dates/times
            dates, times = [], []
            try:
                film_html = pw_page(pw, url)
                film_soup = BeautifulSoup(film_html, "html.parser")
                text      = film_soup.get_text(" ")
                dates     = parse_dates_from_text(text)
                times     = parse_times_from_text(text)
            except Exception:
                pass

            movies.append(make_movie(title, theater_name, dates=dates, times=times, url=url))

        # Fallback: card titles
        if not movies:
            for sel in ["h3.card-title a","h2.card-title a",".entry-title a","article h2 a"]:
                for tag in soup.select(sel):
                    title = clean(tag.get_text())
                    if title and title.lower() not in seen and len(title) > 2:
                        if title.lower() in skip: continue
                        seen.add(title.lower())
                        href = tag.get("href","")
                        url  = f"https://burnsfilmcenter.org{href}" if href and not href.startswith("http") else href or THEATERS[theater_name]["showtimes_url"]
                        movies.append(make_movie(title, theater_name, url=url))
                if movies: break

        print(f"    ✓ {len(movies)} films")
    except Exception as e:
        print(f"    ⚠ Burns error: {e}")
    return movies


def scrape_regal(theater_name, pw):
    """Regal New Roc — Playwright via Fandango."""
    print(f"    → Regal New Roc (Playwright/Fandango)")
    return scrape_fandango_pw(theater_name, "AANLC", pw)



def scrape_mamaroneck(theater_name, pw):
    """Mamaroneck Cinemas (H&K) — Playwright."""
    print(f"    → Mamaroneck Cinemas (Playwright)")
    movies = []
    url = "https://www.hk-cinemas.com/locations/mamaroneck-cinemas"
    try:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ))
        ctx.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,otf}", lambda r: r.abort())
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        html = page.content()
        browser.close()
        soup = BeautifulSoup(html, "html.parser")
        seen = set()
        skip = {"now playing","coming soon","special events","guest services",
                "gift cards","ticket prices","locations","food & drink"}
        for a in soup.find_all("a", href=re.compile(r"/locations/mamaroneck-cinemas/movie/")):
            title = clean(a.get_text())
            href  = a.get("href","")
            if not title or len(title) < 2 or title.lower() in seen: continue
            if title.lower() in skip: continue
            seen.add(title.lower())
            film_url = f"https://www.hk-cinemas.com{href}" if not href.startswith("http") else href
            parent = a.find_parent(["div","li","article","section"])
            text   = parent.get_text(" ") if parent else ""
            dates  = parse_dates_from_text(text)
            times  = parse_times_from_text(text)
            movies.append(make_movie(title, theater_name, dates=dates, times=times, url=film_url))
        print(f"    ✓ {len(movies)} films")
    except Exception as e:
        print(f"    ⚠ Mamaroneck error: {e}")
    return movies

def scrape_fandango_pw(theater_name, fandango_id, pw):
    """Fandango — clicks through each day to get full week of showtimes."""
    urls = {
        "AANLC": "https://www.fandango.com/regal-new-roc-4dx-imax-and-rpx-aanlc/theater-page",
        "AAHRT": "https://www.fandango.com/the-picture-house-pelham-aahrt/theater-page",
        "AAPXM": "https://www.fandango.com/jacob-burns-film-center-aapxm/theater-page",
        "AARVP": "https://www.fandango.com/nitehawk-cinema-williamsburg-aarvp/theater-page",
        "AABLM": "https://www.fandango.com/mamaroneck-cinemas-aablm/theater-page",
    }
    url = urls.get(fandango_id, "")
    if not url: return []
    skip = {
        "offers","nearby theaters","amenities details","new & coming soon",
        "experience + explore","editorial features","videos","photos",
        "follow us","get fandango apps","movie times calendar",
        "filter movie times by screen format",
        "calendar for movie times. today's date is selected.",
    }
    grouped = {}
    try:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ))
        ctx.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,otf}", lambda r: r.abort())
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(8000)

        # Find all date buttons (up to 7 days)
        # Try multiple selectors for date buttons
        day_buttons = []
        for sel in ["[aria-label*='MARCH']","[aria-label*='APRIL']","[aria-label*='MAY']",
                     "[aria-label*='March']","[aria-label*='April']","[aria-label*='May']",
                     "[class*='date-tab']","[class*='DateTab']","[class*='day-btn']"]:
            btns = page.query_selector_all(sel)
            for b in btns:
                label = b.get_attribute("aria-label","") or b.inner_text()
                if any(d in label.upper() for d in ["MON","TUE","WED","THU","FRI","SAT","SUN"]):
                    day_buttons.append(b)
        # Dedupe
        seen_labels = set()
        unique_buttons = []
        for b in day_buttons:
            label = b.get_attribute("aria-label","") or b.inner_text()
            if label not in seen_labels:
                seen_labels.add(label)
                unique_buttons.append(b)
        day_buttons = unique_buttons
        print(f"    → Found {len(day_buttons)} days to scrape")

        # Always scrape current page first (today)
        days_to_scrape = [None] + day_buttons[1:8]  # today + next 7 days

        for btn in days_to_scrape:
            if btn:
                try:
                    label = btn.get_attribute("aria-label","")
                    btn.click()
                    page.wait_for_timeout(3000)
                    # Extract date from aria-label e.g. "THURSDAY, MARCH 5"
                    import re as re2
                    dm = re2.search(r"(\w+),\s+(\w+)\s+(\d+)", label)
                    current_date = None
                    if dm:
                        months = {"JANUARY":"01","FEBRUARY":"02","MARCH":"03","APRIL":"04",
                                  "MAY":"05","JUNE":"06","JULY":"07","AUGUST":"08",
                                  "SEPTEMBER":"09","OCTOBER":"10","NOVEMBER":"11","DECEMBER":"12"}
                        mo  = months.get(dm.group(2), "03")
                        day = dm.group(3).zfill(2)
                        yr  = "2026"
                        current_date = f"{yr}-{mo}-{day}"
                except Exception:
                    current_date = None
            else:
                current_date = None

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")

            for tag in soup.find_all(["h2","h3"]):
                title = clean(tag.get_text())
                if not title or len(title) < 2: continue
                if title.lower() in skip: continue
                if any(x in title.lower() for x in [
                    "filter","calendar","format","nearby","amenities",
                    "coming soon","follow","apps","editorial","photos","videos"
                ]): continue
                parent = tag.find_parent(["article","div","li","section"])
                text   = parent.get_text(" ") if parent else ""
                times  = parse_times_from_text(text)
                if title not in grouped:
                    grouped[title] = make_movie(title, theater_name, url=url)
                if current_date and current_date not in grouped[title]["dates"]:
                    grouped[title]["dates"].append(current_date)
                for t in times:
                    if t not in grouped[title]["times"]:
                        grouped[title]["times"].append(t)

        browser.close()
        movies = list(grouped.values())
        print(f"    ✓ {len(movies)} films (Fandango)")
    except Exception as e:
        print(f"    ⚠ Fandango error: {e}")
        movies = []
    return movies

def load_letterboxd_csv(username: str) -> dict:
    import csv
    print(f"\n── LETTERBOXD ────────────────────────────────────────")
    lb_dir = os.path.join(OUTPUT_DIR, "letterboxd")
    result = {"watched": {}, "watchlist": {}, "loved": {}, "liked": {}}

    ratings_path = os.path.join(lb_dir, "ratings.csv")
    if os.path.exists(ratings_path):
        with open(ratings_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                title = clean(row.get("Name",""))
                if not title: continue
                try:    rating = float(row.get("Rating", 0))
                except: rating = 0.0
                key   = title.lower()
                entry = {"title": title, "rating": rating, "year": row.get("Year","")}
                result["watched"][key] = entry
                if rating >= 4.5:   result["loved"][key]  = entry
                elif rating >= 3.5: result["liked"][key]  = entry
        print(f"    ✓ Ratings: {len(result['watched'])} films")
    else:
        print(f"    ⚠ ratings.csv not found in {lb_dir}")

    watched_path = os.path.join(lb_dir, "watched.csv")
    if os.path.exists(watched_path):
        before = len(result["watched"])
        with open(watched_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                title = clean(row.get("Name",""))
                if not title: continue
                key = title.lower()
                if key not in result["watched"]:
                    result["watched"][key] = {"title": title, "rating": None, "year": row.get("Year","")}
        print(f"    ✓ Watched: +{len(result['watched'])-before} unrated ({len(result['watched'])} total)")

    watchlist_path = os.path.join(lb_dir, "watchlist.csv")
    if os.path.exists(watchlist_path):
        with open(watchlist_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                title = clean(row.get("Name",""))
                if not title: continue
                result["watchlist"][title.lower()] = {"title": title, "year": row.get("Year","")}
        print(f"    ✓ Watchlist: {len(result['watchlist'])} films")

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
        return movies

    unique = {}
    for m in movies:
        if m["title"] not in unique:
            unique[m["title"]] = m.get("description","")

    print(f"\n── AI CLASSIFICATION ─────────────────────────────────")
    print(f"    Classifying {len(unique)} titles with Claude...")

    film_list = "\n".join(
        f'- "{t}"' + (f'  [Note: {d[:180]}]' if d else "")
        for t, d in unique.items()
    )

    prompt = f"""You are a repertory cinema programmer with encyclopedic knowledge of film history.

I have films currently playing at theaters near New York City. Identify which are CLASSIC or REVIVAL screenings vs. new releases.

MARK AS CLASSIC/REVIVAL if:
- Released more than ~5 years ago AND has genuine cultural standing
- Showing in a special format: 70mm, 35mm, 4K restoration
- Part of a retrospective, anniversary, or director series
- A foreign/art house classic being revived
- A cult or midnight movie staple
- By canonical directors: Hitchcock, Kubrick, Kurosawa, Fellini, Bergman, Godard, Tarkovsky, Lynch, Scorsese, Wilder, Welles, etc.

MARK AS NEW RELEASE if:
- A mainstream new movie from the last 1-2 years in normal theatrical run

When in doubt, lean toward classic if there's reasonable basis.

Films:
{film_list}

Respond ONLY with a JSON array. Each object:
- "title": exact title as given
- "is_classic": true or false
- "year": integer or null
- "reason": brief phrase e.g. "1958 Hitchcock, 4K restoration"

Pure JSON only. No markdown."""

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
        raw  = r.json()["content"][0]["text"]
        raw  = re.sub(r'^```json\s*|^```\s*|\s*```$', '', raw.strip(), flags=re.M)
        info = json.loads(raw)
        cmap = {c["title"]: c for c in info}

        classics = []
        for m in movies:
            c = cmap.get(m["title"])
            if c and c.get("is_classic"):
                m["year"]   = c.get("year")
                m["reason"] = c.get("reason","")
                classics.append(m)

        print(f"    ✓ {len(classics)} classic/revival films out of {len(movies)} total")
        return classics
    except Exception as e:
        print(f"    ✗ Claude error: {e}")
        return movies


# ══════════════════════════════════════════════════════════════
# ICS CALENDAR EXPORT
# ══════════════════════════════════════════════════════════════

def escape_ics(s: str) -> str:
    s = s.replace("\\","\\\\").replace(";","\\;").replace(",","\\,").replace("\n","\\n")
    return s

def generate_ics(movies: list, lb_data: dict) -> str:
    now_utc = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    today   = date.today()

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Classic Cinema Calendar//Larchmont NY//EN",
        "X-WR-CALNAME:Classic Cinema – Westchester Area",
        "X-WR-CALDESC:Classic & revival film screenings near Larchmont NY",
        "X-WR-TIMEZONE:America/New_York",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    for m in movies:
        ctx      = match_letterboxd(m["title"], lb_data)
        theater  = m["theater_name"]
        title    = m["title"]
        year     = m.get("year","")
        reason   = m.get("reason","")
        url      = m.get("source_url","")
        lb_label = ctx.get("label","")
        year_str = f" ({year})" if year else ""
        summary  = escape_ics(f"🎬 {title}{year_str} @ {theater}")

        desc_parts = []
        if reason:   desc_parts.append(reason)
        if lb_label: desc_parts.append(lb_label)

        # Include times in description if available
        times = m.get("times",[])
        if times:
            desc_parts.append("Times: " + " · ".join(times[:8]))

        if url: desc_parts.append(f"Showtimes: {url}")
        description = escape_ics("  |  ".join(desc_parts))

        # Build event dates
        raw_dates   = m.get("dates", [])
        event_dates = []
        for d in raw_dates:
            try:
                event_dates.append(datetime.strptime(d[:10], "%Y-%m-%d").date())
            except Exception:
                pass

        # No dates? Create a 2-week placeholder starting today
        if not event_dates:
            event_dates = [today]

        for event_date in sorted(set(event_dates)):
            uid      = str(uuid.uuid4())
            date_str = event_date.strftime("%Y%m%d")
            next_day = (event_date + timedelta(days=1)).strftime("%Y%m%d")

            lines += [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{now_utc}",
                f"DTSTART;VALUE=DATE:{date_str}",
                f"DTEND;VALUE=DATE:{next_day}",
                f"SUMMARY:{summary}",
                f"DESCRIPTION:{description}",
                f"LOCATION:{escape_ics(m['theater_config']['address'])}",
            ]
            if url:
                lines.append(f"URL:{url}")
            lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


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
                try:    fmts.append(datetime.strptime(d[:10],"%Y-%m-%d").strftime("%a %b %-d"))
                except: fmts.append(d)
            more = f' <span class="more">+{len(dates)-8} more</span>' if len(dates) > 8 else ""
            parts.append(f'<span class="sd">📅 {" · ".join(fmts)}{more}</span>')
        if times:
            uniq = list(dict.fromkeys(times))
            parts.append(f'<span class="st">🕐 {" · ".join(uniq[:6])}</span>')
        return f'<div class="showtimes">{"  ".join(parts)}</div>' if parts else ""

    def movie_card(m):
        ctx      = match_letterboxd(m["title"], lb_data)
        year_s   = f" <span class='year'>({m['year']})</span>" if m.get("year") else ""
        reason_s = f'<span class="reason">{m["reason"]}</span>' if m.get("reason") else ""
        badge    = lb_badge(ctx)
        url      = m.get("source_url","")
        shows    = fmt_showtimes(m.get("dates",[]), m.get("times",[]))

        title_html = (f'<a href="{url}" class="film-link" target="_blank" rel="noopener">'
                      f'<em>{m["title"]}</em></a>{year_s}' if url else
                      f'<em>{m["title"]}</em>{year_s}')

        showtimes_link = (f'<a href="{url}" class="showtimes-link" target="_blank" rel="noopener">'
                          f'See showtimes →</a>' if url and not m.get("dates") else "")

        return f"""
        <div class="movie-card lb-{ctx['status']}">
          <div class="movie-top">
            <span class="movie-title">{title_html}</span>
            {showtimes_link}
          </div>
          <div class="movie-meta">{reason_s}{badge}</div>
          {shows}
        </div>"""

    def theater_block(name, ms):
        # Sort by earliest date ascending (soonest first, farthest last)
        def sort_key(m):
            dates = m.get("dates", [])
            if dates:
                return min(dates)
            return "9999-99-99"
        ms = sorted(ms, key=sort_key)
        cfg = ms[0]["theater_config"]
        rb  = ('<span class="regal-badge">★ YOUR REGAL SUBSCRIPTION</span>'
               if cfg["is_regal"] else "")
        cards      = "".join(movie_card(m) for m in ms)
        cls        = "regal-theater" if cfg["is_regal"] else ""
        theater_url = cfg.get("showtimes_url") or cfg["website"]
        return f"""
      <div class="theater-block {cls}">
        <div class="theater-header">
          <a href="{theater_url}" class="theater-name" target="_blank" rel="noopener">{name}</a>{rb}
          <span class="theater-city">{cfg['city']}</span>
        </div>
        <div class="movie-list">{cards}</div>
      </div>"""

    def section(label, ms):
        if not ms:
            return (f'<div class="section-label"><span>{label}</span></div>'
                    f'<div class="empty-state">No classic screenings found right now.</div>')
        blocks = "".join(theater_block(n,t) for n,t in group(ms).items())
        return f'<div class="section-label"><span>{label}</span></div>{blocks}'

    now      = datetime.now().strftime("%B %-d, %Y at %-I:%M %p")
    total    = len(movies)
    sprockets = '<div class="sprocket"></div>' * 11

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎬</text></svg>">
  <link rel="apple-touch-icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎬</text></svg>">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Classic Cinema Calendar — Westchester Area</title>
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
.cal-btn{{display:inline-block;margin-top:.75rem;font-family:'Josefin Sans',sans-serif;font-size:.65rem;letter-spacing:.14em;text-transform:uppercase;color:var(--gold);border:1px solid var(--gold);padding:.3rem .9rem;text-decoration:none;opacity:.7;transition:opacity .2s;cursor:pointer}}
.cal-btn:hover{{opacity:1}}
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
a.theater-name{{font-family:'Playfair Display',serif;font-size:1rem;font-weight:700;color:var(--brown);text-decoration:none;border-bottom:1px solid transparent;transition:border-color .2s}}
a.theater-name:hover{{border-bottom-color:var(--amber)}}
.regal-theater a.theater-name{{color:var(--regal-navy);font-weight:900}}
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
a.film-link{{color:var(--dark);text-decoration:none;border-bottom:1px solid var(--border);transition:border-color .2s,color .2s}}
a.film-link:hover{{color:var(--red);border-bottom-color:var(--red)}}
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
.showtimes-link{{font-family:'Josefin Sans',sans-serif;font-size:.63rem;letter-spacing:.1em;text-transform:uppercase;color:var(--red);text-decoration:none;border-bottom:1px solid var(--red);white-space:nowrap;transition:opacity .2s;flex-shrink:0}}
.showtimes-link:hover{{opacity:.55}}
.empty-state{{font-family:'Josefin Sans',sans-serif;font-size:.75rem;letter-spacing:.12em;text-transform:uppercase;color:#bbb;text-align:center;padding:1.75rem;border:1px dashed var(--border)}}
.footer{{text-align:center;padding:2rem;border-top:1px solid var(--border);margin-top:3rem;font-family:'Josefin Sans',sans-serif;font-size:.62rem;letter-spacing:.14em;text-transform:uppercase;color:#bbb}}
@media(max-width:580px){{.stats-bar{{flex-direction:column;align-items:center}}.theater-header{{flex-direction:column;align-items:flex-start}}.theater-city{{margin-left:0}}.movie-top{{flex-direction:column}}}}
</style>
</head>
<body>
<header class="masthead">
  <div class="sprocket-strip">{sprockets}</div>
  <h1>Classic Cinema Calendar</h1>
  <div class="tagline">Revival &amp; Repertory Screenings — Westchester Area</div>
  <div class="generated">Generated {now}</div>
  <a class="cal-btn" href="classic_cinema.ics" download>📅 Add to Google Calendar (.ics)</a>
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
            dates=["2026-03-05","2026-03-06"], times=["7:30 PM"],
            url="https://drafthouse.com/yonkers/show/the-trouble-with-harry"),
         "year":1955,"reason":"1955 Hitchcock dark comedy"},
        {**make_movie("Vertigo","IFC Center",
            dates=["2026-03-07","2026-03-08","2026-03-09"], times=["6:00 PM","8:30 PM"],
            url="https://www.ifccenter.com/films/vertigo/"),
         "year":1958,"reason":"1958 Hitchcock, 4K restoration"},
        {**make_movie("Rear Window","IFC Center",
            dates=["2026-03-15","2026-03-16"], times=["4:00 PM","7:00 PM"],
            url="https://www.ifccenter.com/films/rear-window/"),
         "year":1954,"reason":"1954 Hitchcock retrospective"},
        {**make_movie("2001: A Space Odyssey","Nitehawk Cinema",
            dates=["2026-03-08"], times=["9:00 PM"],
            url="https://nitehawkcinema.com/williamsburg/film/2001-a-space-odyssey/"),
         "year":1968,"reason":"1968 Kubrick, 70mm screening"},
        {**make_movie("Chinatown","Angelika Film Center",
            dates=["2026-03-10","2026-03-11"], times=["5:00 PM","8:00 PM"],
            url="https://www.angelikafilmcenter.com/nyc/film/chinatown"),
         "year":1974,"reason":"1974 Polanski noir, 50th anniversary"},
        {**make_movie("Lawrence of Arabia","Regal New Roc",
            dates=["2026-03-12"], times=["6:00 PM"],
            url="https://www.fandango.com/regal-new-roc-4dx-imax-and-rpx-aanlc/theater-page"),
         "year":1962,"reason":"1962 David Lean, 70mm restored print"},
        {**make_movie("Sunset Boulevard","Pelham Picture House",
            dates=["2026-03-14","2026-03-15"], times=["7:30 PM"],
            url="https://www.thepicturehouse.org/now-playing"),
         "year":1950,"reason":"1950 Wilder Hollywood noir"},
        {**make_movie("8½","Jacob Burns Film Center",
            dates=["2026-03-13","2026-03-14"], times=["7:00 PM"],
            url="https://burnsfilmcenter.org/film/eight-and-a-half/"),
         "year":1963,"reason":"1963 Fellini, Italian cinema series"},
        {**make_movie("Rashomon","Jacob Burns Film Center",
            dates=["2026-03-20"], times=["7:30 PM"],
            url="https://burnsfilmcenter.org/film/rashomon/"),
         "year":1950,"reason":"1950 Kurosawa, Janus Films restoration"},
        {**make_movie("There Will Be Blood","IFC Center",
            dates=["2026-03-22","2026-03-23"], times=["4:30 PM","7:30 PM"],
            url="https://www.ifccenter.com/films/there-will-be-blood/"),
         "year":2007,"reason":"2007 PTA masterpiece, anniversary"},
        {**make_movie("Mulholland Drive","Nitehawk Cinema",
            dates=["2026-03-21"], times=["10:00 PM"],
            url="https://nitehawkcinema.com/williamsburg/film/mulholland-drive/"),
         "year":2001,"reason":"2001 Lynch, midnight screening"},
        {**make_movie("The Godfather","Alamo Drafthouse Yonkers",
            dates=["2026-03-25","2026-03-26"], times=["5:00 PM","8:30 PM"],
            url="https://drafthouse.com/yonkers/show/the-godfather"),
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
    print("  CLASSIC CINEMA CALENDAR  v5")
    print("  Larchmont, NY  ·  Playwright + ICS export")
    print("=" * 58)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ── Scrape theaters with Playwright ──
    print("\n── THEATER SCRAPING (Playwright) ─────────────────────")
    all_movies = []

    scrapers = {
        "alamo":    scrape_alamo,
        "ifc":      scrape_ifc,
        "angelika": scrape_angelika,
        "nitehawk": scrape_nitehawk,
        "pelham":   scrape_pelham,
        "burns":    scrape_burns,
        "regal":      scrape_regal,
        "mamaroneck": scrape_mamaroneck,
        "fandango":   lambda n, pw: scrape_fandango_pw(n, THEATERS[n]["fandango_id"], pw),
    }

    with sync_playwright() as pw:
        for name, cfg in THEATERS.items():
            print(f"\n🎬 {name}")
            try:
                fn = scrapers.get(cfg["scrape_strategy"])
                ms = fn(name, pw) if fn else []
                print(f"    ✓ {len(ms)} title(s) found")
                all_movies.extend(ms)
            except Exception as e:
                print(f"    ✗ {e}")
            time.sleep(1.5)

    print(f"\n📋 Total titles scraped: {len(all_movies)}")

    demo_mode = len(all_movies) == 0
    if demo_mode:
        print("⚠ No live data — using demo data")
        all_movies = demo_movies()

    # ── Letterboxd ──
    lb_data = demo_letterboxd() if demo_mode else load_letterboxd_csv(LETTERBOXD_USERNAME)

    # ── Classify with Claude ──
    classics = classify_with_claude(all_movies) if not demo_mode else all_movies

    # Filter out past screening dates
    from datetime import date as date_cls
    today_str = date_cls.today().isoformat()
    for m in classics:
        m["dates"] = [d for d in m["dates"] if d >= today_str]

    # ── Output ──
    print("\n── OUTPUT ────────────────────────────────────────────")

    html = generate_html(classics, lb_data)
    html_path = os.path.join(OUTPUT_DIR, "classic_cinema_calendar.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✓ HTML  → {html_path}")

    ics = generate_ics(classics, lb_data)
    ics_path = os.path.join(OUTPUT_DIR, "classic_cinema.ics")
    with open(ics_path, "w", encoding="utf-8") as f:
        f.write(ics)
    print(f"  ✓ ICS   → {ics_path}")

    json_path = os.path.join(OUTPUT_DIR, "classic_movies.json")
    export = []
    for m in classics:
        ctx = match_letterboxd(m["title"], lb_data)
        export.append({
            "title":      m["title"],
            "year":       m.get("year"),
            "reason":     m.get("reason",""),
            "theater":    m["theater_name"],
            "location":   m["theater_config"]["location"],
            "city":       m["theater_config"]["city"],
            "address":    m["theater_config"]["address"],
            "is_regal":   m["theater_config"]["is_regal"],
            "dates":      m.get("dates",[]),
            "times":      m.get("times",[]),
            "source_url": m.get("source_url",""),
            "lb_status":  ctx.get("status","new"),
            "lb_label":   ctx.get("label",""),
            "lb_rating":  ctx.get("rating"),
        })
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(export, f, indent=2)
    print(f"  ✓ JSON  → {json_path}")

    with_dates = sum(1 for m in classics if m.get("dates"))
    print(f"\n✅ Done — {len(classics)} classic screenings")
    print(f"   {with_dates} have specific dates · {len(classics)-with_dates} link out for times")
    if demo_mode:
        print("   (Demo mode — Playwright found 0 results)")
    else:
        print(f"\n   Open HTML:   open {html_path}")
        print(f"   Add to Cal:  open {ics_path}")

if __name__ == "__main__":
    main()
