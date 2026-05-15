#!/usr/bin/env python3
"""
Travis Shorrock — AI-Powered Job Scraper
Sources: LinkedIn Guest API + Adzuna API + JSearch (throttled)
Three category lanes: CORE · ADJACENT
"""

import json, os, re, hashlib, smtplib, urllib.request, urllib.parse, time, random
import base64, email as email_lib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from bs4 import BeautifulSoup

DATA_FILE = Path("data/jobs.json")
SEEN_FILE = Path("data/seen_ids.json")
META_FILE = Path("data/meta.json")

JSEARCH_HOST = "jsearch.p.rapidapi.com"
JSEARCH_URL  = "https://jsearch.p.rapidapi.com/search"

# ─── SEARCH QUERIES ───────────────────────────────────────────────────────────

# JSearch — throttled to every 3rd day (free tier = 200 req/month)
JSEARCH_QUERIES = [
    "creative director remote",
    "chief marketing officer remote",
    "head of creative remote",
    "VP creative remote",
    "chief brand officer remote",
    "head of content remote",
]

# LinkedIn guest API — free, no auth, runs every day
LINKEDIN_QUERIES = [
    # Core
    "creative director",
    "executive creative director",
    "head of creative",
    "chief brand officer",
    "VP creative",
    "head of content",
    "creative technologist",
    "AI creative director",
    # Adjacent
    "head of experience",
    "director of immersive experiences",
    "narrative director",
    "head of programming",
    "chief experience officer",
]

# Adzuna — free API, generous limits, runs every day
ADZUNA_QUERIES = [
    # Core
    "creative director",
    "head of creative",
    "chief brand officer",
    "VP creative",
    "chief marketing officer",
    "head of content",
    # Adjacent
    "head of experience",
    "director immersive experience",
    "narrative director",
    "creative director entertainment",
]

# ─── HARD EXCLUDES ────────────────────────────────────────────────────────────

HARD_EXCLUDES = [
    # Pure engineering
    "software engineer", "backend engineer", "frontend engineer",
    "fullstack engineer", "devops engineer", "data engineer",
    "machine learning engineer", "security engineer", "platform engineer",
    "infrastructure engineer", "systems engineer", "developer", "programmer",
    # Pure UX/product design (not creative direction)
    "ux designer", "ui designer", "ui/ux designer",
    "user experience designer", "interaction designer",
    # Pure sales
    "account executive", "sales representative",
    # Pure finance/medical/legal
    "finance director", "medical director", "clinical director",
    "legal counsel", "data scientist", "data analyst",
    # Junior only
    "junior", "intern", "entry level", "customer support",
    "technical support", "help desk", "customer service",
]

# ─── DOMAIN BLOCKLIST ─────────────────────────────────────────────────────────

BLOCKED_DOMAINS = {
    'liveblog365.com', 'unaux.com', 'infinityfree.me', 'wuaze.com',
    'fast-page.org', 'zya.me', 'starterparadise.com', 'lovestoblog.com',
    'iceiy.com', 'hiresociall.com', 'jaabz.com', 'learn4good.com',
    'jooble.org', 'whatjobs.com', 'bebee.com', 'theelitejob.com',
    'lensa.com', 'jobleads.com', 'talent.com', 'gusher.co',
    'career.zycto.com', 'wfh.hiresociall.com',
}

def domain_ok(url):
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower().replace('www.', '')
        return not any(blocked in netloc for blocked in BLOCKED_DOMAINS)
    except:
        return True

# ─── CLAUDE SCORING PROMPT ────────────────────────────────────────────────────

TRAVIS_PROFILE = """
You are scoring job postings for Travis Shorrock. He has three job lanes he tracks.
Assign the correct CATEGORY first, then score within that category.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CATEGORY DEFINITIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CORE — Senior creative leadership, any flavor.
If it's a senior title and creative/brand/marketing is in the DNA, it's Core.
Titles: CD, ECD, GCD, ACD, VP Creative, Head of Creative, Head of Brand, Head of Content,
Chief Brand Officer, CMO, Creative Partner, AI Creative Director, Creative Technologist Lead,
VP Marketing with creative scope, Chief Creative Officer.
Focus: Advertising, brand, AI-powered creative, integrated campaigns, content at scale.

ADJACENT — Has a creative or experiential angle but lives outside traditional advertising.
Titles: Head of Experience, Director of Immersive Experiences, Narrative Director,
Head of Programming, Chief Experience Officer, Head of Culture, Creative Lead at
entertainment venues, theme parks, gaming studios, hospitality brands, festivals.
These roles still require creative thinking but aren't traditional CD/brand roles.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCORING RULES BY CATEGORY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FOR CORE roles — score based on fit with Travis's background:
MUST-HAVES for high score:
- 100% remote. Hybrid or on-site = score 2 max.
- Senior level only. Junior/coordinator/mid-level = score 0.
- Compensation in USD or CAD preferred. Other currencies = score lower.
- Location/Timezone: Travis is moving to Costa Rica (Aug 2026). He cannot accept
  jobs that require US residency, US citizenship, or US work authorization.
  HARD RULE: If the posting explicitly says "US residents only", "must be a US citizen",
  "must reside in the US", "authorized to work in the US", "no visa sponsorship",
  "W-2 only", or any similar US-only restriction, AND does NOT also mention Canada,
  North America, the Americas, LATAM, or "anywhere worldwide", score it 0.
  If the job mentions Canada, North America, Americas, LATAM, Mexico, Costa Rica,
  or "anywhere worldwide" alongside US, it's fine. Score normally.
  If location is not mentioned at all, assume open and do NOT penalize.
  Strongly prefer postings that explicitly include Canada / Latin America / worldwide.
  Penalize 2 points if posting requires European or Asian working hours specifically.

Travis's background:
- National CD at T&Pm 10yrs: Toyota Canada, TELUS — large-scale integrated campaigns
- CD at tms: Nissan North America, Diageo (Guinness, Smirnoff, Strongbow)
- Creative Group Head at Havas: Volvo Canada
- Deep hands-on AI: Midjourney, Runway, Higgsfield, ComfyUI, Claude Code
- TV, OOH, digital, CRM, packaging — full integrated creative
- Built and led large creative departments from scratch

Score 9-10: Perfect fit — senior, remote, USD, strong creative scope
Score 7-8:  Strong fit — minor gaps (DTC focus, PST timezone, slightly off-brief)
Score 5-6:  Interesting — worth a look but clear gaps
Score 3-4:  Stretch — notable mismatches but not disqualified
Score 1-2:  Weak — remote or seniority issues
Score 0:    Disqualified — not remote, not senior, wrong currency/region

FOR ADJACENT roles — score based on how interesting and accessible the role is:
Score 7-10: Genuinely fascinating, senior scope, Travis could walk in
Score 4-6:  Interesting but niche expertise gap
Score 1-3:  Too specialized or too junior

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Category must be either CORE or ADJACENT. No other values are valid.
Respond ONLY with JSON: {"score": 7, "category": "CORE", "reason": "one punchy sentence"}
"""

# ─── FILTERS ──────────────────────────────────────────────────────────────────

HYBRID_SIGNALS = [
    "hybrid", "on-site", "onsite", "in-office", "in office",
    "in-person", "in person", "office required", "must be local",
    "must be in", "days in office", "days per week in",
    "occasional travel required", "relocation required",
]

def title_ok(title):
    t = title.lower()
    return not any(ex in t for ex in HARD_EXCLUDES)

def is_remote_clean(job):
    text = ((job.get("title") or "") + " " + (job.get("description") or "")).lower()
    return not any(signal in text for signal in HYBRID_SIGNALS)

REMOTE_SIGNALS = [
    "remote", "work from home", "wfh", "fully distributed",
    "anywhere", "telecommute", "telework", "distributed team",
    "work from anywhere", "location independent",
]

def has_remote_signal(job):
    """Require at least one positive remote signal in title or description."""
    text = ((job.get("title") or "") + " " + (job.get("description") or "")).lower()
    return any(signal in text for signal in REMOTE_SIGNALS)

# ─── US-ONLY FILTER ───────────────────────────────────────────────────────────
# Travis is moving to Costa Rica (Aug 2026). Drop jobs that explicitly require
# US residency / citizenship / work auth and don't mention Canada or the Americas.

US_ONLY_PATTERNS = [
    r"\bu\.?s\.?\s*(citizens?|residents?|nationals?)\s+only\b",
    r"\b(citizens?|residents?)\s+of\s+the\s+u\.?s\.?\b",
    r"\bmust\s+be\s+(a\s+)?u\.?s\.?\s*(citizen|resident|national)\b",
    r"\bmust\s+be\s+(located|based|residing|living)\s+in\s+the\s+(u\.?s\.?|united states|usa)\b",
    r"\bmust\s+(reside|live|be located|be based)\s+in\s+the\s+(u\.?s\.?|united states|usa)\b",
    r"\bauthorized\s+to\s+work\s+in\s+the\s+(u\.?s\.?|united states|usa)\b",
    r"\bwork\s+authorization\s+in\s+the\s+(u\.?s\.?|united states|usa)\b",
    r"\b(u\.?s\.?|united states|usa)\s+(residents?|citizens?|based)\s+only\b",
    r"\bonly\s+open\s+to\s+(u\.?s\.?|united states|usa)\b",
    r"\bopen\s+(only\s+)?to\s+(u\.?s\.?|united states|usa)\s+(residents|citizens|applicants)\b",
    r"\bcontinental\s+united states\s+only\b",
    r"\b(u\.?s\.?|united states)\s+only\b",
    r"\bcannot\s+sponsor\b.*\bvisa\b",
    r"\bno\s+visa\s+sponsorship\b",
    r"\bw-?2\s+only\b",
]

AMERICAS_OPEN_PATTERNS = [
    r"\bcanad(a|ian)\b",
    r"\bnorth\s+america\b",
    r"\bamericas\b",
    r"\b(latam|latin america)\b",
    r"\bworldwide\b",
    r"\banywhere\s+(in\s+)?(the\s+)?(world|globe)\b",
    r"\bglobal(ly)?\s+(remote|distributed)\b",
    r"\bremote\s+(international|worldwide|global)\b",
    r"\bopen\s+to\s+(applicants\s+)?(from\s+)?(any|all)\s+(country|countries|locations?)\b",
    r"\bcosta\s+rica\b",
    r"\bmexico\b",
]

_US_ONLY_RE = re.compile("|".join(US_ONLY_PATTERNS), re.IGNORECASE)
_AMERICAS_OK_RE = re.compile("|".join(AMERICAS_OPEN_PATTERNS), re.IGNORECASE)

def is_us_only(job):
    """
    True only if the description EXPLICITLY restricts to US residents/citizens
    AND does NOT mention Canada, North America, or other Americas-friendly signals.
    Returns False for jobs with no description (cannot confirm restriction).
    Returns False if posting mentions Canada/NA even if it also mentions US.
    """
    desc = (job.get("description") or "").strip()
    if not desc:
        return False
    if _AMERICAS_OK_RE.search(desc):
        return False
    return bool(_US_ONLY_RE.search(desc))

# ─── SENIOR TITLE RELEVANCE ───────────────────────────────────────────────────
# Used for sources that don't have query-based pre-filtering (RemoteOK, Remotive, WWR).
# Pulls senior creative/brand/marketing leadership titles and adjacent leadership roles.

SENIOR_TITLE_RE = re.compile(
    r"\b("
    # Explicit senior CD/ECD/GCD/ACD
    r"creative director|executive creative director|group creative director|"
    r"associate creative director|acd|ecd|gcd|"
    # Head of <X>
    r"head of (creative|brand|content|marketing|design|experience|programming|"
    r"culture|communications|growth|copy|story)|"
    # VP <X> / SVP <X>
    r"(?:s?vp|vice president)\s+(of\s+)?(creative|marketing|brand|design|content|"
    r"experience|communications)|"
    # Chief X Officer
    r"chief (brand|marketing|creative|experience|content|communications) officer|"
    r"cmo|cco|cbo|ccmo|cxo|"
    # Modifier + Director (catches Brand Director, Art Director, Creative Director, etc.)
    r"(creative|brand|art|content|marketing|design|narrative|experience|immersive|"
    r"communications|copy|story)\s+director|"
    # Director of <X> / Director, <X>
    r"director\s*(of|,)\s+(creative|brand|content|marketing|design|experience|"
    r"communications|copy|story)|"
    # Lead patterns
    r"(creative|brand|content|marketing|design)\s+lead|"
    # AI / tech creative roles
    r"creative technologist|ai creative director|creative partner"
    r")\b",
    re.IGNORECASE,
)

def is_relevant_title(job):
    """Match senior creative/brand/marketing leadership patterns in the title."""
    return bool(SENIOR_TITLE_RE.search(job.get("title") or ""))

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def make_id(title, company):
    t = re.sub(r'[^a-z0-9]', '', (title or '').lower())[:30]
    c = re.sub(r'[^a-z0-9]', '', (company or '').lower())[:20]
    return hashlib.md5(f"{t}{c}".encode()).hexdigest()[:12]

def load_json(path, default):
    try: return json.loads(Path(path).read_text())
    except: return default

def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))

# ─── LINKEDIN GUEST SCRAPER ───────────────────────────────────────────────────

LINKEDIN_UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

_LINKEDIN_JOB_ID_PATTERNS = [
    # ?currentJobId=4414195118
    re.compile(r"currentJobId=(\d+)"),
    # /jobs/view/4414195118 or /jobs/view/creative-director-at-liquid-agency-4414195118
    re.compile(r"/(?:comm/)?jobs/view/[^/?\s#]*?(\d{6,})(?:[/?#]|$)"),
    # /jobs/4414195118 (rarer)
    re.compile(r"/jobs/(\d{6,})(?:[/?#]|$)"),
    # Last resort: any trailing 7+ digit number before query string
    re.compile(r"(\d{7,})(?:[/?#]|$)"),
]

def _extract_linkedin_job_id(url):
    """Pull the numeric job ID out of any LinkedIn job URL variant."""
    if not url:
        return None
    for pat in _LINKEDIN_JOB_ID_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    return None

def fetch_linkedin_jd(url):
    """
    Fetch a LinkedIn job's description via the public guest job-posting endpoint.
    Returns description text (str) or '' on failure. No auth required.
    LinkedIn rate-limits aggressively; caller should sleep between calls.
    """
    job_id = _extract_linkedin_job_id(url)
    if not job_id:
        return ""

    endpoint = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    headers = {
        "User-Agent": random.choice(LINKEDIN_UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://www.linkedin.com/jobs/",
    }
    try:
        req = urllib.request.Request(endpoint, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status != 200:
                return ""
            html = r.read().decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        # The JD body is in .show-more-less-html__markup (sometimes nested)
        node = soup.find("div", class_="show-more-less-html__markup") \
            or soup.find("section", class_="show-more-less-html") \
            or soup.find("div", class_="description__text")
        if not node:
            # Fallback: full visible text of the page (will be noisier)
            return soup.get_text(" ", strip=True)[:4000]
        return node.get_text(" ", strip=True)[:4000]
    except Exception:
        return ""

def fetch_linkedin(query, location=None):
    """Scrape LinkedIn public guest API — no login required."""
    jobs = []
    headers = {
        "User-Agent": random.choice(LINKEDIN_UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://www.linkedin.com/jobs/",
    }

    params_dict = {
        "keywords": query,
        "f_WT": "2",
        "f_E": "4,5,6",
        "f_TPR": "r86400",
        "start": "0",
    }
    if location:
        params_dict["location"] = location
    params = urllib.parse.urlencode(params_dict)

    url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{params}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status != 200:
                print(f"    ⚠ LinkedIn [{query}] → HTTP {r.status}")
                return []
            html = r.read().decode("utf-8", errors="replace")

        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("div", class_="base-card")

        for card in cards:
            title_el   = card.find("h3", class_="base-search-card__title")
            company_el = card.find("h4", class_="base-search-card__subtitle")
            link_el    = card.find("a", class_="base-card__full-link")

            title   = title_el.text.strip() if title_el else ""
            company = company_el.text.strip() if company_el else ""
            url_raw = link_el["href"].split("?")[0] if link_el else ""

            if title and url_raw:
                jobs.append({
                    "title":       title,
                    "company":     company,
                    "url":         url_raw,
                    "description": "",
                    "salary":      "",
                    "source":      "LinkedIn",
                    "posted":      "",
                })

        time.sleep(2)
        print(f"    LinkedIn [{query}] → {len(jobs)} results")

    except Exception as e:
        print(f"    ⚠ LinkedIn [{query}] → {e}")

    # Enrich each guest-API result with its JD body so is_us_only can filter
    # and Claude scores against full context (not title-only fallback path).
    enriched = 0
    for j in jobs:
        jd = fetch_linkedin_jd(j["url"])
        if jd:
            j["description"] = jd
            enriched += 1
        time.sleep(1.2)
    if jobs:
        print(f"    LinkedIn [{query}] → enriched {enriched}/{len(jobs)} with JD bodies")

    return jobs

# ─── ADZUNA FETCHER ───────────────────────────────────────────────────────────

def fetch_adzuna(query, app_id, app_key, country="us"):
    """Fetch jobs from Adzuna API — free tier, generous limits."""
    jobs = []

    params = urllib.parse.urlencode({
        "app_id":           app_id,
        "app_key":          app_key,
        "what":             query,
        "what_and":         "remote",
        "results_per_page": 50,
        "sort_by":          "date",
        "max_days_old":     3,
        "full_time":        1,
    })

    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1?{params}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "job-tracker/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())

        for j in data.get("results", []):
            title     = (j.get("title") or "").strip()
            company   = (j.get("company", {}) or {}).get("display_name", "").strip()
            url_apply = j.get("redirect_url") or j.get("adref") or ""
            desc      = (j.get("description") or "")[:1200]
            salary    = ""
            sal_min   = j.get("salary_min")
            sal_max   = j.get("salary_max")
            if sal_min and sal_max:
                salary = f"${int(sal_min):,}–${int(sal_max):,} USD/year"
            elif sal_min:
                salary = f"${int(sal_min):,}+ USD/year"

            if title and url_apply and domain_ok(url_apply):
                jobs.append({
                    "title":       title,
                    "company":     company,
                    "url":         url_apply,
                    "description": desc,
                    "salary":      salary,
                    "source":      "Adzuna",
                    "posted":      j.get("created", ""),
                })

        time.sleep(0.5)
        print(f"    Adzuna [{query}] → {len(jobs)} results")

    except Exception as e:
        print(f"    ⚠ Adzuna [{query}] → {e}")

    return jobs

# ─── REMOTEOK FETCHER ─────────────────────────────────────────────────────────

REMOTEOK_TAGS = [
    "marketing", "content", "creative", "brand", "design",
    "executive", "director", "lead", "manager",
]

def fetch_remoteok():
    """Fetch all jobs from RemoteOK public API and filter by relevant tags/titles."""
    jobs = []
    try:
        req = urllib.request.Request(
            "https://remoteok.com/api",
            headers={"User-Agent": "job-tracker/1.0"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())

        # First item is metadata, skip it
        listings = data[1:] if len(data) > 1 else []

        for j in listings:
            title   = (j.get("position") or "").strip()
            company = (j.get("company") or "").strip()
            url     = j.get("apply_url") or j.get("url") or ""
            tags    = [t.lower() for t in (j.get("tags") or [])]
            desc    = re.sub(r'<[^>]+>', ' ', j.get("description") or "")[:1200]
            salary  = ""
            if j.get("salary_min") and j.get("salary_max"):
                salary = f"${int(j['salary_min']):,}–${int(j['salary_max']):,}"
            elif j.get("salary_min"):
                salary = f"${int(j['salary_min']):,}+"

            # Keep only if tags or title suggest relevance
            relevant = any(tag in tags for tag in REMOTEOK_TAGS) or \
                       any(t in title.lower() for t in ["director", "head of", "chief", "vp ", "lead", "creative", "brand", "content", "marketing"])

            if relevant and title and url and domain_ok(url):
                jobs.append({
                    "title":       title,
                    "company":     company,
                    "url":         url,
                    "description": desc,
                    "salary":      salary,
                    "source":      "RemoteOK",
                    "posted":      "",
                })

        print(f"    RemoteOK → {len(jobs)} relevant results from {len(listings)} total")
        time.sleep(1)

    except Exception as e:
        print(f"    ⚠ RemoteOK → {e}")

    return jobs

# ─── REMOTIVE FETCHER ─────────────────────────────────────────────────────────

REMOTIVE_CATEGORIES = [
    "marketing",
    "design",
    "artificial-intelligence",
    "communications",
]

def fetch_remotive():
    """Fetch jobs from Remotive free API across relevant categories."""
    jobs = []
    for category in REMOTIVE_CATEGORIES:
        try:
            url = f"https://remotive.com/api/remote-jobs?category={category}&limit=50"
            req = urllib.request.Request(url, headers={"User-Agent": "job-tracker/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())

            listings = data.get("jobs", [])
            for j in listings:
                title    = (j.get("title") or "").strip()
                company  = (j.get("company_name") or "").strip()
                url_apply = j.get("url") or ""
                desc     = re.sub(r'<[^>]+>', ' ', j.get("description") or "")[:1200]
                salary   = j.get("salary") or ""
                location = j.get("candidate_required_location") or "Worldwide"

                # Skip explicitly European/Asian timezone-required roles
                skip_regions = ["europe", "european", "asia", "apac", "emea", "africa", "middle east"]
                if any(r in location.lower() for r in skip_regions) and \
                   not any(r in location.lower() for r in ["americas", "worldwide", "global", "us", "canada"]):
                    continue

                if title and url_apply and domain_ok(url_apply):
                    jobs.append({
                        "title":       title,
                        "company":     company,
                        "url":         url_apply,
                        "description": desc,
                        "salary":      salary,
                        "source":      "Remotive",
                        "posted":      j.get("publication_date", ""),
                    })

            print(f"    Remotive [{category}] → {len(listings)} results")
            time.sleep(0.5)

        except Exception as e:
            print(f"    ⚠ Remotive [{category}] → {e}")

    return jobs

# ─── WE WORK REMOTELY FETCHER ─────────────────────────────────────────────────

WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-sales-and-marketing-jobs.rss",
    "https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss",
    "https://weworkremotely.com/categories/remote-design-jobs.rss",
]

def fetch_weworkremotely():
    """Fetch jobs from We Work Remotely RSS feeds."""
    jobs = []
    for feed_url in WWR_FEEDS:
        try:
            req = urllib.request.Request(feed_url, headers={"User-Agent": "job-tracker/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                xml_data = r.read()

            root = ET.fromstring(xml_data)
            channel = root.find("channel")
            items = channel.findall("item") if channel else []

            count = 0
            for item in items:
                title_el   = item.find("title")
                link_el    = item.find("link")
                desc_el    = item.find("description")

                raw_title = title_el.text if title_el is not None else ""
                # WWR titles are formatted as "Company: Job Title"
                if ": " in raw_title:
                    company, title = raw_title.split(": ", 1)
                else:
                    company, title = "", raw_title

                url_apply = link_el.text if link_el is not None else ""
                desc = re.sub(r'<[^>]+>', ' ', desc_el.text or "")[:1200] if desc_el is not None else ""

                if title and url_apply:
                    jobs.append({
                        "title":       title.strip(),
                        "company":     company.strip(),
                        "url":         url_apply,
                        "description": desc,
                        "salary":      "",
                        "source":      "WeWorkRemotely",
                        "posted":      "",
                    })
                    count += 1

            print(f"    WeWorkRemotely [{feed_url.split('/')[-1].replace('.rss','')}] → {count} results")
            time.sleep(0.5)

        except Exception as e:
            print(f"    ⚠ WeWorkRemotely → {e}")

    return jobs

# ─── LINKEDIN EMAIL FETCHER (Gmail API) ───────────────────────────────────────

def fetch_linkedin_email():
    """
    Parse LinkedIn Job Alert emails from Gmail.
    LinkedIn sends daily digests from jobalerts-noreply@linkedin.com.
    Uses multiple parsing strategies since LinkedIn changes email format frequently.
    """
    jobs = []

    refresh_token  = os.environ.get("GMAIL_REFRESH_TOKEN", "")
    client_id      = os.environ.get("GMAIL_CLIENT_ID", "")
    client_secret  = os.environ.get("GMAIL_CLIENT_SECRET", "")

    if not all([refresh_token, client_id, client_secret]):
        print("    Gmail: skipped (credentials not set)")
        return jobs

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        )
        creds.refresh(Request())

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)

        # Search for LinkedIn job alert emails from the last 2 days
        results = service.users().messages().list(
            userId="me",
            q="from:jobalerts-noreply@linkedin.com newer_than:2d",
            maxResults=20,
        ).execute()

        messages = results.get("messages", [])
        print(f"    Gmail: found {len(messages)} LinkedIn alert email(s)")

        for msg_meta in messages:
            msg = service.users().messages().get(
                userId="me",
                id=msg_meta["id"],
                format="full",
            ).execute()

            def collect_bodies(payload, acc):
                """Walk MIME tree and collect all (mime_type, decoded_body) parts."""
                mime = payload.get("mimeType", "")
                data = payload.get("body", {}).get("data", "")
                if data and mime in ("text/html", "text/plain"):
                    try:
                        acc.append((mime, base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")))
                    except Exception:
                        pass
                for part in payload.get("parts", []):
                    collect_bodies(part, acc)

            # Prefer HTML over plain text. LinkedIn multipart emails put plain text first
            # which is what triggered the garbage-leak bug (Strategy 2 grabbing scaffolding lines).
            all_bodies = []
            collect_bodies(msg.get("payload", {}), all_bodies)
            html_body  = next((b for m, b in all_bodies if m == "text/html"), "")
            plain_body = next((b for m, b in all_bodies if m == "text/plain"), "")
            if html_body:
                body, mime = html_body, "text/html"
            else:
                body, mime = plain_body, "text/plain"
            if not body:
                continue

            email_jobs = []

            # ── Strategy 1: Parse HTML with BeautifulSoup ─────────────────────
            if "html" in mime or "<html" in body.lower():
                soup = BeautifulSoup(body, "html.parser")

                # Strategy 1a: Find all links containing linkedin.com/jobs.
                # Skip anchor text that's obvious email scaffolding (Apply, location,
                # "X people clicked apply", etc.). Then dedup by job URL keeping the
                # longest remaining title (usually the actual job title).
                strategy_1a_garbage = [
                    "actively hiring", "apply with resume", "apply now",
                    "easy apply", "people clicked apply", "promoted by hirer",
                    "responses managed", "view all jobs", "see all",
                    "unsubscribe", "manage", "settings", "connections",
                    "view profile", "view company", "click here",
                    "show more", "show less", "sign in", "log in",
                    "follow", "save this job", "dismiss", "not interested",
                ]
                candidates_by_url = {}
                for a in soup.find_all("a", href=True):
                    href = a.get("href", "")
                    if "linkedin.com" not in href:
                        continue
                    if not any(x in href for x in ["/jobs/view/", "/comm/jobs/view/", "currentJobId="]):
                        continue

                    title = a.get_text(separator=" ", strip=True)
                    if not title or len(title) < 4 or len(title) > 200:
                        continue
                    title_lower = title.lower()
                    if any(g in title_lower for g in strategy_1a_garbage):
                        continue
                    # Skip pure-location anchor text (city / state names alone)
                    if re.fullmatch(r"[a-z .,\-]{2,40}", title_lower) and \
                       any(loc in title_lower for loc in ["canada", "united states", " usa", "ontario", "quebec", "california", "texas", "new york", "metropolitan area", "remote"]):
                        continue

                    # Clean URL — strip tracking params but keep job ID
                    clean_url = href.split("?")[0] if "?" in href else href
                    if "currentJobId=" in href:
                        params = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                        job_id = params.get("currentJobId", [""])[0]
                        if job_id:
                            clean_url = f"https://www.linkedin.com/jobs/view/{job_id}/"

                    # Try to get company from nearby elements
                    company = ""
                    parent = a.find_parent(["td", "div", "table", "tr"])
                    if parent:
                        all_text = [t.get_text(strip=True) for t in parent.find_all(["span", "p", "div", "td"])
                                   if t.get_text(strip=True) and t.get_text(strip=True) != title]
                        cand = [t for t in all_text if 1 < len(t) < 100 and t != title]
                        if cand:
                            company = cand[0]

                    # Keep the longest title we've seen per URL (likely the actual job title)
                    existing = candidates_by_url.get(clean_url)
                    if not existing or len(title) > len(existing["title"]):
                        candidates_by_url[clean_url] = {
                            "title":       title,
                            "company":     company,
                            "url":         clean_url,
                            "description": "",
                            "salary":      "",
                            "source":      "LinkedIn",
                            "posted":      "",
                        }

                email_jobs.extend(candidates_by_url.values())

                # Strategy 1b: Look for job title patterns in text even without /jobs/view/ URL
                # Strict filtering to avoid email scaffolding (buttons, footers, nav links)
                if not email_jobs:
                    # Garbage phrases that appear in LinkedIn email chrome, not job titles
                    garbage_phrases = [
                        "apply", "unsubscribe", "manage", "settings", "connections",
                        "canada", "ontario", "toronto", "united states", "view all",
                        "see more", "learn more", "click here", "sign in", "log in",
                        "linkedin", "privacy", "terms", "help", "support", "profile",
                        "resume", "salary", "dismiss", "not interested", "save",
                        "easy apply", "promoted", "following", "connect",
                    ]
                    # Strong job title signals — must contain at least one
                    strong_signals = [
                        "creative director", "head of creative", "chief creative",
                        "executive creative", "group creative", "vp creative",
                        "chief brand", "head of brand", "brand director",
                        "chief marketing", "head of marketing", "vp marketing",
                        "head of content", "director of content", "content director",
                        "head of experience", "narrative director", "chief experience",
                        "creative technologist", "ai creative", "creative lead",
                    ]
                    for a in soup.find_all("a", href=True):
                        href = a.get("href", "")
                        if "linkedin.com" not in href and "li.com" not in href:
                            continue
                        title = a.get_text(separator=" ", strip=True)
                        title_lower = title.lower()
                        # Must be reasonable title length
                        if not (8 < len(title) < 100):
                            continue
                        # Must NOT be garbage UI chrome
                        if any(g in title_lower for g in garbage_phrases):
                            continue
                        # Must contain a strong job title signal
                        if not any(s in title_lower for s in strong_signals):
                            continue
                        email_jobs.append({
                            "title":       title,
                            "company":     "",
                            "url":         href,
                            "description": "",
                            "salary":      "",
                            "source":      "LinkedIn",
                            "posted":      "",
                        })

            # ── Strategy 2: Plain text parsing ────────────────────────────────
            if not email_jobs and body:
                # LinkedIn plain text emails list jobs as:
                # "Job Title at Company\nhttps://www.linkedin.com/jobs/view/XXXXX"
                strategy_2_garbage = [
                    "actively hiring", "apply with resume", "apply now",
                    "easy apply", "people clicked apply", "promoted by",
                    "view all jobs", "see all", "unsubscribe", "manage your",
                    "view profile", "view company", "click here",
                    "this company is", "you can also", "based on your",
                ]
                lines = body.split("\n")
                for i, line in enumerate(lines):
                    line = line.strip()
                    if "linkedin.com/jobs/view/" in line or "linkedin.com/comm/jobs/view/" in line:
                        url = re.search(r'https?://[^\s<>"]+linkedin\.com[^\s<>"]*jobs[^\s<>"]*', line)
                        if url:
                            clean_url = url.group(0).split("?")[0]
                            title = lines[i-1].strip() if i > 0 else ""
                            company = lines[i-2].strip() if i > 1 else ""
                            if not title or len(title) <= 4:
                                continue
                            tlow = title.lower()
                            if any(g in tlow for g in strategy_2_garbage):
                                continue
                            email_jobs.append({
                                "title":       title,
                                "company":     company,
                                "url":         clean_url,
                                "description": "",
                                "salary":      "",
                                "source":      "LinkedIn",
                                "posted":      "",
                            })

            jobs.extend(email_jobs)
            print(f"    Gmail email parsed: {len(email_jobs)} jobs")

        # Deduplicate by URL
        seen_local = set()
        unique = []
        for j in jobs:
            if j["url"] and j["url"] not in seen_local:
                seen_local.add(j["url"])
                unique.append(j)

        # Enrich each job with its description via the LinkedIn public job-posting endpoint.
        # Without this the is_us_only filter has nothing to filter on and Claude falls back
        # to title-only scoring, which can't catch "must be based in the US" language.
        print(f"    Gmail LinkedIn: enriching {len(unique)} jobs with descriptions...")
        enriched_count = 0
        for j in unique:
            jd = fetch_linkedin_jd(j["url"])
            if jd:
                j["description"] = jd
                enriched_count += 1
            time.sleep(1.2)  # be polite to LinkedIn
        print(f"    Gmail LinkedIn: {enriched_count}/{len(unique)} enriched with JD bodies")
        print(f"    Gmail LinkedIn total: {len(unique)} unique jobs from {len(messages)} emails")
        return unique

    except Exception as e:
        print(f"    ⚠ Gmail LinkedIn: {e}")
        import traceback
        traceback.print_exc()
        return []

# ─── JSEARCH FETCHER ──────────────────────────────────────────────────────────

def fetch_jsearch(query, rapidapi_key, num_pages=1):
    """Fetch jobs from JSearch API — throttled to every 3rd day."""
    all_jobs = []
    for page in range(1, num_pages + 1):
        params = urllib.parse.urlencode({
            "query":          query,
            "page":           page,
            "num_pages":      1,
            "date_posted":    "3days",
            "remote_jobs_only": "true",
        })
        url = f"{JSEARCH_URL}?{params}"
        req = urllib.request.Request(url, headers={
            "X-RapidAPI-Key":  rapidapi_key,
            "X-RapidAPI-Host": JSEARCH_HOST,
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())
            jobs = data.get("data", [])
            for j in jobs:
                title      = (j.get("job_title") or "").strip()
                company    = (j.get("employer_name") or "").strip()
                url_apply  = j.get("job_apply_link") or j.get("job_url") or ""
                desc       = (j.get("job_description") or "")[:1200]
                salary     = ""
                if j.get("job_min_salary") and j.get("job_max_salary"):
                    salary = f"${int(j['job_min_salary']):,}–${int(j['job_max_salary']):,} {j.get('job_salary_currency','USD')}/{j.get('job_salary_period','year')}"
                elif j.get("job_min_salary"):
                    salary = f"${int(j['job_min_salary']):,}+ {j.get('job_salary_currency','USD')}"

                if title and url_apply and domain_ok(url_apply):
                    all_jobs.append({
                        "title":       title,
                        "company":     company,
                        "url":         url_apply,
                        "description": desc,
                        "salary":      salary,
                        "source":      "JSearch",
                        "posted":      j.get("job_posted_at_datetime_utc", ""),
                    })
            time.sleep(0.5)
        except Exception as e:
            print(f"    ⚠ JSearch [{query}] p{page} → {e}")
    return all_jobs

# ─── MAIN FETCH ───────────────────────────────────────────────────────────────

def fetch_all(rapidapi_key):
    all_jobs = []
    seen_urls = set()

    adzuna_id  = os.environ.get("ADZUNA_APP_ID", "")
    adzuna_key = os.environ.get("ADZUNA_APP_KEY", "")

    # ── LinkedIn Email Alerts (Gmail API — primary LinkedIn source) ────────────
    # Pre-filtered by Travis's LinkedIn Job Alert settings, so no remote/title-relevance
    # post-check needed beyond title_ok.
    print("\n  LinkedIn Job Alert emails (Gmail):")
    email_jobs = fetch_linkedin_email()
    fresh = [j for j in email_jobs if title_ok(j["title"]) and not is_us_only(j) and j["url"] not in seen_urls]
    for j in fresh: seen_urls.add(j["url"])
    all_jobs.extend(fresh)

    # ── LinkedIn guest API (fallback — often blocked by datacenter IPs) ────────
    print("\n  LinkedIn guest API (fallback):")
    for query in LINKEDIN_QUERIES:
        jobs = fetch_linkedin(query)
        # No has_remote_signal: LinkedIn pre-filters via f_WT=2 (Remote workplace flag)
        # and returns empty descriptions, so the keyword check would strip 90% of valid jobs.
        fresh = [j for j in jobs if title_ok(j["title"]) and is_remote_clean(j) and not is_us_only(j) and j["url"] not in seen_urls]
        for j in fresh: seen_urls.add(j["url"])
        all_jobs.extend(fresh)

    # ── Adzuna (free, every day) ──────────────────────────────────────────────
    if adzuna_id and adzuna_key:
        print("\n  Adzuna API:")
        adz_dropped_us = 0
        for query in ADZUNA_QUERIES:
            jobs = fetch_adzuna(query, adzuna_id, adzuna_key, country="us")
            adz_dropped_us += sum(1 for j in jobs if is_us_only(j))
            fresh = [j for j in jobs if title_ok(j["title"]) and is_remote_clean(j) and has_remote_signal(j) and not is_us_only(j) and j["url"] not in seen_urls]
            for j in fresh: seen_urls.add(j["url"])
            all_jobs.extend(fresh)
        for query in ADZUNA_QUERIES[:2]:
            jobs = fetch_adzuna(query, adzuna_id, adzuna_key, country="ca")
            adz_dropped_us += sum(1 for j in jobs if is_us_only(j))
            fresh = [j for j in jobs if title_ok(j["title"]) and is_remote_clean(j) and has_remote_signal(j) and not is_us_only(j) and j["url"] not in seen_urls]
            for j in fresh: seen_urls.add(j["url"])
            all_jobs.extend(fresh)
        if adz_dropped_us:
            print(f"    Adzuna: dropped {adz_dropped_us} US-only role(s)")
    else:
        print("\n  Adzuna: skipped (ADZUNA_APP_ID / ADZUNA_APP_KEY not set)")

    # ── RemoteOK (free, every day) ────────────────────────────────────────────
    print("\n  RemoteOK:")
    jobs = fetch_remoteok()
    ro_dropped_us = sum(1 for j in jobs if is_us_only(j))
    fresh = [j for j in jobs if title_ok(j["title"]) and is_remote_clean(j) and not is_us_only(j) and j["url"] not in seen_urls]
    for j in fresh: seen_urls.add(j["url"])
    if ro_dropped_us:
        print(f"    RemoteOK: dropped {ro_dropped_us} US-only role(s)")
    all_jobs.extend(fresh)

    # ── Remotive (free, every day) ────────────────────────────────────────────
    print("\n  Remotive:")
    jobs = fetch_remotive()
    before = len(jobs)
    rv_dropped_us = sum(1 for j in jobs if is_us_only(j))
    fresh = [j for j in jobs if title_ok(j["title"]) and is_remote_clean(j) and is_relevant_title(j) and not is_us_only(j) and j["url"] not in seen_urls]
    for j in fresh: seen_urls.add(j["url"])
    print(f"    Remotive → {before} fetched · {len(fresh)} kept · {rv_dropped_us} US-only dropped")
    all_jobs.extend(fresh)

    # ── We Work Remotely (free, every day) ────────────────────────────────────
    print("\n  We Work Remotely:")
    jobs = fetch_weworkremotely()
    before = len(jobs)
    wwr_dropped_us = sum(1 for j in jobs if is_us_only(j))
    fresh = [j for j in jobs if title_ok(j["title"]) and is_remote_clean(j) and is_relevant_title(j) and not is_us_only(j) and j["url"] not in seen_urls]
    for j in fresh: seen_urls.add(j["url"])
    print(f"    WeWorkRemotely → {before} fetched · {len(fresh)} kept · {wwr_dropped_us} US-only dropped")
    all_jobs.extend(fresh)

    # ── JSearch (throttled: every 3rd day only) ───────────────────────────────
    run_jsearch = (datetime.now(timezone.utc).day % 3 == 0)
    if run_jsearch and rapidapi_key:
        print("\n  JSearch (throttled run):")
        for query in JSEARCH_QUERIES:
            print(f"  → \"{query}\"")
            jobs = fetch_jsearch(query, rapidapi_key, num_pages=1)
            # No has_remote_signal: JSearch already enforces remote_jobs_only=true server-side.
            fresh = [j for j in jobs if title_ok(j["title"]) and is_remote_clean(j) and not is_us_only(j) and j["url"] not in seen_urls]
            for j in fresh: seen_urls.add(j["url"])
            print(f"     {len(jobs)} fetched · {len(fresh)} kept")
            all_jobs.extend(fresh)
    elif not run_jsearch:
        print("\n  JSearch: skipped today (throttled — runs every 3rd day)")
    else:
        print("\n  JSearch: skipped (RAPIDAPI_KEY not set)")

    print(f"\n  Total pipeline: {len(all_jobs)} unique jobs before dedup/scoring")
    return all_jobs

# ─── CLAUDE SCORING ───────────────────────────────────────────────────────────

def score_batch(jobs, label=""):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  ⚠ No ANTHROPIC_API_KEY")
        for job in jobs:
            job["score"] = 3
            job["category"] = "ADJACENT"
            job["score_method"] = "fallback"
        return jobs

    scored = []
    for job in jobs:
        has_desc = bool((job.get('description') or '').strip())

        if has_desc:
            scoring_instruction = f"""{TRAVIS_PROFILE}

JOB:
Title: {job.get('title', '')}
Company: {job.get('company', '')}
Salary: {job.get('salary', '')}
Description: {(job.get('description') or '')[:1500]}"""
        else:
            # Title-only scoring — no description available (e.g. LinkedIn email jobs)
            # Score purely on title fit. Ignore remote/comp/timezone must-haves since
            # we can't verify them from title alone. These will be reviewed by Travis.
            scoring_instruction = f"""You are scoring a job title for Travis Shorrock, a senior Creative Director (25+ years, National CD at T&Pm/Havas/tms — Toyota, TELUS, Nissan, Diageo). He tracks CORE and ADJACENT roles.

CORE: CD, ECD, GCD, ACD, VP Creative, Head of Creative, Head of Brand, Head of Content, Chief Brand Officer, CMO, Creative Partner, AI Creative Director, Creative Technologist Lead.
ADJACENT: Head of Experience, Director of Immersive Experiences, Narrative Director, Head of Programming, Chief Experience Officer, Creative Lead at entertainment/gaming/hospitality.

IMPORTANT: You have TITLE ONLY. No description. Score based purely on whether the title matches Travis's seniority and lane.
- Score 7-10: Title is a direct senior match (Creative Director, Head of Creative, ECD, Chief Brand Officer, etc.)
- Score 5-6: Good match but slightly off (Director of Brand, Head of Marketing, Creative Lead)
- Score 3-4: Adjacent or interesting stretch
- Score 1-2: Title suggests junior or wrong field
- Score 0: Title is clearly wrong (designer, engineer, coordinator, or obviously non-creative)
DO NOT penalize for lack of remote/comp/timezone info — you don't have that data.
DO NOT penalize for lack of US-residency info — that gets checked downstream.
Category must be CORE or ADJACENT only. Never return any other value.

JOB:
Title: {job.get('title', '')}
Company: {job.get('company', '')}

Respond ONLY with JSON: {{"score": 7, "category": "CORE", "reason": "one punchy sentence"}}"""

        prompt = scoring_instruction

        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                data=json.dumps({
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 200,
                    "messages": [{"role": "user", "content": prompt}]
                }).encode()
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())
            text = data["content"][0]["text"].strip()
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if not match:
                raise ValueError(f"No JSON found in response: {text[:100]}")
            result = json.loads(match.group())
            job["score"]        = int(result.get("score", 3))
            job["category"]     = result.get("category", "ADJACENT").upper()
            job["score_reason"] = result.get("reason", "")
            job["score_method"] = "claude"
            print(f"     [{job['score']}/10] [{job['category']}] {job['title'][:55]}")
            if job.get("score_reason") and job['score'] >= 4:
                print(f"        → {job['score_reason'][:80]}")
            time.sleep(0.3)
        except Exception as e:
            print(f"     ⚠ scoring failed: {e}")
            job["score"]        = 3
            job["category"]     = "ADJACENT"
            job["score_method"] = "fallback"
        scored.append(job)
    return scored

# ─── PERSIST ──────────────────────────────────────────────────────────────────

def process_jobs(raw_jobs):
    raw_seen = load_json(SEEN_FILE, {})
    if isinstance(raw_seen, list):
        raw_seen = {id: datetime.now(timezone.utc).isoformat() for id in raw_seen}
    cutoff = datetime.now(timezone.utc) - timedelta(days=21)
    seen = {k: v for k, v in raw_seen.items()
            if datetime.fromisoformat(v) > cutoff}
    print(f"  seen_ids: {len(raw_seen)} loaded, {len(raw_seen)-len(seen)} expired, {len(seen)} active")
    existing = load_json(DATA_FILE, [])

    new_jobs = []
    for job in raw_jobs:
        jid = make_id(job["title"], job["company"])
        if jid in seen: continue
        job["id"]    = jid
        job["added"] = datetime.now(timezone.utc).isoformat()
        seen[jid]    = datetime.now(timezone.utc).isoformat()
        new_jobs.append(job)

    print(f"\n  → {len(new_jobs)} new jobs to score...")
    if new_jobs:
        new_jobs = score_batch(new_jobs)

    kept = [j for j in new_jobs if (j.get("score") or 0) >= 3]
    dropped = [j for j in new_jobs if (j.get("score") or 0) < 3]
    print(f"\n  → {len(kept)} total kept (score ≥ 3)")
    if dropped:
        print(f"  → {len(dropped)} dropped (score < 3):")
        for j in dropped:
            print(f"     [{j.get('score',0)}/10] {j.get('title','')[:55]} @ {j.get('company','')[:30]}")

    all_jobs = (kept + existing)[:300]
    save_json(DATA_FILE, all_jobs)
    save_json(SEEN_FILE, seen)
    save_json(META_FILE, {
        "updated":     datetime.now(timezone.utc).isoformat(),
        "new_count":   len(kept),
        "total_count": len(all_jobs),
    })
    return kept, all_jobs

# ─── EMAIL ────────────────────────────────────────────────────────────────────

def build_html(new_jobs, all_jobs):
    today = datetime.now().strftime("%A, %B %d, %Y")
    core     = [j for j in new_jobs if j.get("category") == "CORE"]
    adjacent = [j for j in new_jobs if j.get("category") == "ADJACENT"]

    def row(j, color):
        sc     = j.get("score", 0)
        dots   = "●" * min(sc, 5) + "○" * max(0, 5 - min(sc, 5))
        reason = j.get("score_reason", "")
        salary = f'<span style="color:#556677;font-size:11px;"> · {j["salary"]}</span>' if j.get("salary") else ""
        return f"""<tr>
          <td style="padding:12px 10px;border-bottom:1px solid #1c2a3a;">
            <a href="{j['url']}" style="color:{color};font-weight:700;font-size:14px;text-decoration:none;">{j['title']}</a><br>
            <span style="color:#778899;font-size:12px;">{j['company']}{salary}</span>
            {f'<br><span style="color:#445566;font-size:11px;font-style:italic;">{reason}</span>' if reason else ''}
          </td>
          <td style="padding:12px 10px;border-bottom:1px solid #1c2a3a;color:{color};white-space:nowrap;">{dots}</td>
        </tr>"""

    def section(title, jobs, color):
        if not jobs: return ""
        rows = "".join(row(j, color) for j in sorted(jobs, key=lambda j: j.get("score", 0), reverse=True)[:10])
        return f"""
        <div style="margin-bottom:28px;">
          <div style="font-size:10px;letter-spacing:3px;color:{color};font-family:monospace;text-transform:uppercase;margin-bottom:12px;">{title}</div>
          <table style="width:100%;border-collapse:collapse;">{rows}</table>
        </div>"""

    return f"""<!DOCTYPE html><html><body style="margin:0;background:#080c14;font-family:Helvetica,Arial,sans-serif;color:#c8d8e8;">
<div style="max-width:660px;margin:0 auto;padding:28px 20px;">
  <div style="border-bottom:2px solid #00E5CC;padding-bottom:16px;margin-bottom:24px;">
    <div style="font-size:10px;letter-spacing:3px;color:#00E5CC;font-family:monospace;margin-bottom:8px;">DAILY JOB BRIEF · AI-SCORED · REMOTE ONLY</div>
    <div style="font-size:26px;font-weight:700;color:#fff;">Travis Shorrock</div>
    <div style="font-size:12px;color:#556677;margin-top:4px;">{today}</div>
  </div>
  <div style="display:flex;gap:10px;margin-bottom:24px;flex-wrap:wrap;">
    <span style="background:rgba(0,229,204,.12);border:1px solid rgba(0,229,204,.3);color:#00E5CC;padding:4px 12px;border-radius:20px;font-size:12px;">{len(core)} Core</span>
    <span style="background:rgba(185,131,255,.12);border:1px solid rgba(185,131,255,.3);color:#B983FF;padding:4px 12px;border-radius:20px;font-size:12px;">{len(adjacent)} Adjacent</span>
  </div>
  {section("Core Roles", core, "#00E5CC")}
  {section("Adjacent Roles", adjacent, "#B983FF")}
</div></body></html>"""

def send_email(new_jobs, all_jobs):
    # NOTE: If Gmail SMTP fails with "Username and Password not accepted",
    # the Gmail app password needs to be regenerated at myaccount.google.com/apppasswords
    # This is separate from the Gmail API OAuth used for reading job alert emails.
    user = os.environ.get("SMTP_USER", ""); pwd = os.environ.get("SMTP_PASS", "")
    to   = os.environ.get("TO_EMAIL", user)
    if not user or not pwd:
        print("  ⚠ Email skipped — no credentials"); return
    core = len([j for j in new_jobs if j.get("category") == "CORE"])
    adj  = len([j for j in new_jobs if j.get("category") == "ADJACENT"])
    today = datetime.now().strftime("%b %d")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎯 Jobs {today} — {core} core · {adj} adjacent"
    msg["From"] = user; msg["To"] = to
    msg.attach(MIMEText(build_html(new_jobs, all_jobs), "html"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls(); s.login(user, pwd); s.sendmail(user, to, msg.as_string())
        print(f"  ✓ Email → {to}")
    except Exception as e:
        print(f"  ⚠ Email failed: {e}")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("Travis Shorrock Job Scraper — Multi-Source Edition")
    print(datetime.now().strftime("%Y-%m-%d %H:%M UTC"))
    print("=" * 55)

    rapidapi_key = os.environ.get("RAPIDAPI_KEY", "")

    print("\n[1/3] Fetching from LinkedIn + Adzuna + JSearch...")
    raw_jobs = fetch_all(rapidapi_key)

    print("\n[2/3] Scoring with Claude Haiku...")
    new_jobs, all_jobs = process_jobs(raw_jobs)

    print("\n[3/3] Sending email...")
    if new_jobs:
        send_email(new_jobs, all_jobs)
    else:
        print("  → No new jobs today, skipping email")

    print("\n✅ Done.")
