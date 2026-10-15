#!/usr/bin/env python3
"""
Travis Shorrock — Job Scraper v2

Sources (all optional except the free ones):
  1. Apify "Advanced LinkedIn Job Search API" (APIFY_TOKEN)  — clean, pre-filtered LinkedIn feed
  2. LinkedIn guest API (free)                               — backup LinkedIn feed
  3. LinkedIn job-alert emails via Gmail (GMAIL_* secrets)
  4. Company careers pages — Greenhouse / Lever / Ashby (free, data/watchlist.json)
  5. Adzuna, JSearch, RemoteOK, Remotive, We Work Remotely (existing)

Pipeline: collect → cheap title/dup filters → fetch full job descriptions for new
jobs only → Claude extracts the facts (remote, where, pay, seniority, fit) with
structured outputs → the score is calculated in code from those facts → save.
"""

import json, os, re, hashlib, smtplib, urllib.request, urllib.parse, urllib.error
import time, random, base64, html as htmllib
import xml.etree.ElementTree as ET
import concurrent.futures as cf
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from bs4 import BeautifulSoup

DATA_FILE      = Path("data/jobs.json")
SEEN_FILE      = Path("data/seen_ids.json")
META_FILE      = Path("data/meta.json")
WATCHLIST_FILE = Path("data/watchlist.json")
FEEDBACK_FILE  = Path("data/feedback.json")

MAX_JOBS        = 300    # rolling window shown on the dashboard
SCORE_FLOOR     = 4      # jobs below this are dropped
SEEN_DAYS       = 60     # don't re-show the same job for this long (stops reposts)
DESC_CHARS      = 12000  # keep full job descriptions (LinkedIn JDs run 4-8k chars)
MAX_LI_FETCHES  = 150    # cap on LinkedIn description fetches per run
SCORING_MODEL   = os.environ.get("SCORING_MODEL") or "claude-sonnet-5"
FALLBACK_MODEL  = "claude-haiku-4-5-20251001"

JSEARCH_HOST = "jsearch.p.rapidapi.com"
JSEARCH_URL  = "https://jsearch.p.rapidapi.com/search"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

# ─── SEARCH QUERIES ───────────────────────────────────────────────────────────

LINKEDIN_QUERIES = [
    "creative director", "executive creative director", "group creative director",
    "head of creative", "chief creative officer", "chief brand officer", "VP creative",
    "head of brand", "head of content", "creative technologist", "AI creative director",
    "head of experience", "narrative director", "chief experience officer",
]
LINKEDIN_LOCATIONS = ["United States", "Canada"]
LINKEDIN_PAGES     = 2   # 10 results per page

APIFY_ACTOR  = "fantastic-jobs~advanced-linkedin-job-search-api"
APIFY_TITLES = [
    "Creative Director", "Executive Creative Director", "Group Creative Director",
    "Associate Creative Director", "Head of Creative", "Chief Creative Officer",
    "Chief Brand Officer", "Chief Marketing Officer", "VP Creative", "Vice President Creative",
    "VP Brand", "Head of Brand", "Head of Content", "Brand Director", "Creative Technologist",
    "Head of Experience", "Narrative Director", "Chief Experience Officer",
]

JSEARCH_QUERIES = [
    "creative director remote", "chief marketing officer remote", "head of creative remote",
    "VP creative remote", "chief brand officer remote", "head of content remote",
]
ADZUNA_QUERIES = [
    "creative director", "head of creative", "chief brand officer", "VP creative",
    "chief marketing officer", "head of content",
]

# ─── CHEAP FILTERS (run before anything costs money) ─────────────────────────

HARD_EXCLUDES = [
    "software engineer", "backend engineer", "frontend engineer", "fullstack engineer",
    "devops engineer", "data engineer", "machine learning engineer", "security engineer",
    "platform engineer", "infrastructure engineer", "systems engineer", "developer",
    "programmer", "ux designer", "ui designer", "ui/ux designer", "user experience designer",
    "interaction designer", "product designer", "account executive", "sales representative",
    "finance director", "medical director", "clinical director", "legal counsel",
    "data scientist", "data analyst", "junior", "intern", "entry level", "coordinator",
    "customer support", "technical support", "help desk", "customer service",
]

SENIOR_TITLE_RE = re.compile(
    r"\b("
    r"creative director|creative lead|creative technologist|creative partner|acd|ecd|gcd|cco|cbo|cmo|"
    r"chief (creative|brand|marketing|experience|content|storytelling) officer|"
    r"(head|vp|svp|evp|vice president)\b.{0,30}\b(creative|brand|content|marketing|design|experience|"
    r"storytelling|narrative|immersive|programming|culture|editorial)|"
    r"(brand|content|editorial|narrative|immersive|experiential|design|ai|creative) director|"
    r"director,?\s+(of\s+)?(creative|brand|content|editorial|design|narrative|storytelling|experiential|immersive)|"
    r"(brand|creative)( design)? lead"
    r")\b",
    re.IGNORECASE,
)
TITLE_NOISE_RE = re.compile(
    r"engineer|engineering|analyst|analytics|product manag|product marketing|product design|"
    r"sales|support|regulatory|finance|counsel|recruit|operations manager|scientist|developer",
    re.IGNORECASE,
)

STRONG_HYBRID_RE = re.compile(
    r"\bhybrid\s+(role|position|schedule|work|model|environment|arrangement)\b|"
    r"\b\d\s*(\+\s*)?days?\s+(a|per)\s+week\s+(in|at)\s+(the|our)?\s*(office|studio|hq)\b|"
    r"\b(fully|100%)\s+(on-?site|in[- ]office)\b|\bthis (role|position) is (on-?site|hybrid|in[- ]office)\b|"
    r"\bmust (live|reside|be located) within \d+\s*(miles|km)\b",
    re.IGNORECASE,
)
REMOTE_WORD_RE = re.compile(r"\bremote\b|work from home|\bwfh\b|fully distributed|work from anywhere|telecommut|remote-first", re.IGNORECASE)
HYBRID_LOCATION_RE = re.compile(r"\b(hybrid|on-?site|in[- ]office)\b", re.IGNORECASE)

US_ONLY_RE = re.compile("|".join([
    r"\bu\.?s\.?\s*(citizens?|residents?|nationals?)\s+only\b",
    r"\b(citizens?|residents?)\s+of\s+the\s+(u\.?s\.?|united states|usa)\s+only\b",
    r"\bmust\s+be\s+(a\s+)?u\.?s\.?\s*(citizen|resident|national)\b",
    r"\bmust\s+(be\s+)?(located|based|residing|living|reside|live)\s+in\s+the\s+(u\.?s\.?|united states|usa)\b",
    r"\b(u\.?s\.?|united states|usa)\s+(residents?|citizens?|based)\s+only\b",
    r"\bonly\s+open\s+to\s+(u\.?s\.?|united states|usa)\b",
    r"\bcontinental\s+united states\s+only\b",
]), re.IGNORECASE)
AMERICAS_OK_RE = re.compile(
    r"\bcanad(a|ian)\b|\bnorth\s+america\b|\bamericas\b|\blatam\b|\blatin america\b|"
    r"\bworldwide\b|\banywhere\b|\bcosta\s+rica\b|\bmexico\b|\bglobal(ly)?\s+(remote|distributed)\b",
    re.IGNORECASE,
)

BLOCKED_DOMAINS = {
    'liveblog365.com', 'unaux.com', 'infinityfree.me', 'wuaze.com', 'fast-page.org', 'zya.me',
    'starterparadise.com', 'lovestoblog.com', 'iceiy.com', 'hiresociall.com', 'jaabz.com',
    'learn4good.com', 'jooble.org', 'whatjobs.com', 'bebee.com', 'theelitejob.com', 'lensa.com',
    'jobleads.com', 'talent.com', 'gusher.co', 'career.zycto.com', 'wfh.hiresociall.com',
    'remotejobs.dedyn.io', 'mysmartpros.com', 'vacancyglobalpro.up.railway.app', 'jobgether.com',
}
# Reposting agencies: the same role is already in the feed from the real employer, or it's bait.
BLOCKED_COMPANIES = {'jobgether', 'remote quest jobs', 'vacancy global pro', 'remotepromsp', 'skill',
                     'remoterush', 'lensa'}

# Where Travis lives stays out of this public repo's text (encoded so the file doesn't list it).
PRIVATE_RE = re.compile(r"\b(" + base64.b64decode("Y29zdGEgcmljYXxub3NhcmF8Z3VhbmFjYXN0ZQ==").decode() + r")\b( resident)?", re.I)

def scrub(job):
    if job.get("score_reason"): job["score_reason"] = PRIVATE_RE.sub("his location", job["score_reason"])
    f = job.get("facts") or {}
    if f.get("red_flags"): f["red_flags"] = [PRIVATE_RE.sub("his location", x) for x in f["red_flags"]]
    return job

def company_ok(company):
    return (company or '').strip().lower() not in BLOCKED_COMPANIES


def domain_ok(url):
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower().replace('www.', '')
        return not any(b in netloc for b in BLOCKED_DOMAINS)
    except Exception:
        return True

def title_ok(title):
    t = (title or "").lower()
    return bool(t) and not any(ex in t for ex in HARD_EXCLUDES)

def is_relevant_title(title):
    return bool(SENIOR_TITLE_RE.search(title or "")) and not TITLE_NOISE_RE.search(title or "")

def is_clearly_hybrid(job):
    if HYBRID_LOCATION_RE.search(job.get("location") or "") or HYBRID_LOCATION_RE.search(job.get("title") or ""):
        return True
    return bool(STRONG_HYBRID_RE.search(job.get("description") or ""))

def is_us_only(job):
    desc = job.get("description") or ""
    return bool(desc) and not AMERICAS_OK_RE.search(desc) and bool(US_ONLY_RE.search(desc))

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def make_id(title, company):
    t = re.sub(r'[^a-z0-9]', '', (title or '').lower())[:30]
    c = re.sub(r'[^a-z0-9]', '', (company or '').lower())[:20]
    return hashlib.md5(f"{t}{c}".encode()).hexdigest()[:12]

def load_json(path, default):
    try: return json.loads(Path(path).read_text())
    except Exception: return default

def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))

def http_get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "job-tracker/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")

def http_json(url, headers=None, timeout=20, data=None):
    h = {"User-Agent": "job-tracker/2.0", **(headers or {})}
    body = json.dumps(data).encode() if data is not None else None
    if body: h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=h, data=body)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def html_to_text(s):
    if not s: return ""
    s = htmllib.unescape(s)
    return BeautifulSoup(s, "html.parser").get_text(" ", strip=True)

def new_job(**kw):
    base = {"title": "", "company": "", "url": "", "description": "", "salary": "",
            "location": "", "source": "", "posted": "", "li_id": ""}
    base.update({k: (v if v is not None else "") for k, v in kw.items()})
    base["title"] = base["title"].strip()
    base["company"] = base["company"].strip()
    base["description"] = (base["description"] or "")[:DESC_CHARS]
    return base

def fmt_money(lo, hi, cur="USD", unit=""):
    def k(v):
        v = float(v)
        return f"${v/1000:.0f}K" if v >= 1000 else f"${v:.0f}"
    unit = (unit or "").upper()
    if unit == "YEAR" and max(float(lo or 0), float(hi or 0)) < 1000: unit = "HOUR"
    unit = {"YEAR": "/yr", "HOUR": "/hr", "MONTH": "/mo"}.get(unit, "")
    cur = "" if (cur or "USD").upper() == "USD" else f" {cur}"
    if lo and hi: return f"{k(lo)}–{k(hi)}{unit}{cur}"
    if lo: return f"{k(lo)}+{unit}{cur}"
    if hi: return f"up to {k(hi)}{unit}{cur}"
    return ""

# ─── LINKEDIN (guest API) ────────────────────────────────────────────────────

LI_ID_RE = [re.compile(r"currentJobId=(\d+)"),
            re.compile(r"/(?:comm/)?jobs/view/[^/?\s#]*?(\d{6,})(?:[/?#]|$)"),
            re.compile(r"(\d{8,})(?:[/?#]|$)")]

def linkedin_id(url):
    for p in LI_ID_RE:
        m = p.search(url or "")
        if m: return m.group(1)
    return ""

def li_url(job_id):
    return f"https://www.linkedin.com/jobs/view/{job_id}/"

def li_headers():
    return {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.5", "Referer": "https://www.linkedin.com/jobs/"}

def fetch_linkedin_details(job_id):
    """Full job page via the public guest endpoint: description, pay box, company, location, seniority."""
    try:
        page = http_get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}", li_headers(), 15)
    except Exception:
        return {}
    s = BeautifulSoup(page, "html.parser")
    out = {}
    node = s.find("div", class_="show-more-less-html__markup") or s.find("div", class_="description__text")
    if node: out["description"] = node.get_text(" ", strip=True)[:DESC_CHARS]
    sal = s.find(class_="compensation__salary")
    if sal: out["salary"] = sal.get_text(" ", strip=True)
    org = s.find("a", class_="topcard__org-name-link") or s.find(class_="topcard__org-name-link")
    if org: out["company"] = org.get_text(" ", strip=True)
    loc = s.select_one(".topcard__flavor--bullet")
    if loc: out["location"] = loc.get_text(" ", strip=True)
    for li in s.select("li.description__job-criteria-item"):
        h, v = li.find("h3"), li.find("span")
        if h and v and "seniority" in h.get_text().lower():
            out["seniority"] = v.get_text(strip=True)
    return out

def fetch_linkedin_search(query, location, page):
    params = {"keywords": query, "f_WT": "2", "f_E": "4,5,6", "f_TPR": "r86400",
              "start": str(page * 10), "location": location}
    url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?" + urllib.parse.urlencode(params)
    jobs = []
    try:
        s = BeautifulSoup(http_get(url, li_headers(), 15), "html.parser")
        for card in s.find_all("div", class_="base-card"):
            t = card.find("h3", class_="base-search-card__title")
            c = card.find("h4", class_="base-search-card__subtitle")
            a = card.find("a", class_="base-card__full-link")
            loc = card.find("span", class_="job-search-card__location")
            tm = card.find("time")
            sal = card.find("span", class_="job-search-card__salary-info")
            urn = card.get("data-entity-urn", "")
            jid = urn.rsplit(":", 1)[-1] if urn else linkedin_id(a["href"] if a else "")
            if not (t and jid): continue
            jobs.append(new_job(title=t.get_text(strip=True), company=c.get_text(strip=True) if c else "",
                                url=li_url(jid), li_id=jid, source="LinkedIn",
                                # searched with LinkedIn's Remote filter (f_WT=2), so LinkedIn labels it Remote
                                location=(loc.get_text(strip=True) + " · " if loc else "") + "Remote (LinkedIn label)",
                                posted=tm.get("datetime", "") if tm else "",
                                salary=sal.get_text(" ", strip=True) if sal else ""))
    except urllib.error.HTTPError as e:
        print(f"    ⚠ LinkedIn [{query} · {location} · p{page+1}] HTTP {e.code}")
    except Exception as e:
        print(f"    ⚠ LinkedIn [{query} · {location} · p{page+1}] {e}")
    time.sleep(1.5 + random.random())
    return jobs

def fetch_linkedin_guest():
    out = []
    for q in LINKEDIN_QUERIES:
        for loc in LINKEDIN_LOCATIONS:
            for p in range(LINKEDIN_PAGES):
                batch = fetch_linkedin_search(q, loc, p)
                out += batch
                if len(batch) < 10: break
    print(f"    LinkedIn guest: {len(out)} cards")
    return out

# ─── LINKEDIN (Apify — paid, cleanest) ───────────────────────────────────────

def fetch_apify_linkedin():
    token = os.environ.get("APIFY_TOKEN", "")
    if not token:
        print("    Apify: skipped (APIFY_TOKEN not set)")
        return []
    limit = int(os.environ.get("APIFY_LIMIT") or "35")
    payload = {
        "timeRange": "24h", "limit": max(10, limit), "titleSearch": APIFY_TITLES,
        "titleExclusionSearch": ["Designer", "Engineer", "Intern", "Coordinator", "Recruiter", "Sales"],
        "aiWorkArrangementFilter": ["Remote Solely", "Remote OK"],
        "seniorityFilter": ["Director", "Executive", "Mid-Senior level", "Not Applicable"],
        "removeAgency": True, "descriptionType": "text", "populateAiRemoteLocation": True,
    }
    url = (f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items?"
           + urllib.parse.urlencode({"token": token, "timeout": 280}))
    try:
        items = http_json(url, timeout=300, data=payload)
    except urllib.error.HTTPError as e:
        print(f"    ⚠ Apify HTTP {e.code}: {e.read()[:300]!r}")
        return []
    except Exception as e:
        print(f"    ⚠ Apify: {e}")
        return []
    out = []
    for j in items if isinstance(items, list) else []:
        jid = str(j.get("linkedin_id") or linkedin_id(j.get("url", "")) or "")
        locs = j.get("ai_remote_location") or j.get("locations_derived") or []
        loc = ", ".join(l if isinstance(l, str) else ", ".join(filter(None, [l.get("city"), l.get("admin"), l.get("country")]))
                        for l in locs[:3]) if isinstance(locs, list) else str(locs)
        arr = j.get("ai_work_arrangement") or ""
        salary = fmt_money(j.get("ai_salary_min_value") or j.get("ai_salary_value"), j.get("ai_salary_max_value"),
                           j.get("ai_salary_currency") or "USD", j.get("ai_salary_unit_text") or "")
        out.append(new_job(title=j.get("title", ""), company=j.get("organization", ""),
                           url=li_url(jid) if jid else j.get("url", ""), li_id=jid,
                           description=j.get("description_text", ""), salary=salary,
                           location=(f"{arr} · " if arr else "") + (loc or ""),
                           posted=j.get("date_posted", ""), source="LinkedIn+"))
    print(f"    Apify LinkedIn: {len(out)} jobs")
    return out

# ─── LINKEDIN (job-alert emails in Gmail) ────────────────────────────────────

def fetch_linkedin_email():
    rt, cid, cs = (os.environ.get(k, "") for k in ("GMAIL_REFRESH_TOKEN", "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET"))
    if not all([rt, cid, cs]):
        print("    Gmail: skipped (credentials not set)")
        return []
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        creds = Credentials(token=None, refresh_token=rt, client_id=cid, client_secret=cs,
                            token_uri="https://oauth2.googleapis.com/token",
                            scopes=["https://www.googleapis.com/auth/gmail.readonly"])
        creds.refresh(Request())
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        msgs = svc.users().messages().list(userId="me", q="from:jobalerts-noreply@linkedin.com newer_than:2d",
                                           maxResults=20).execute().get("messages", [])
        print(f"    Gmail: {len(msgs)} LinkedIn alert email(s)")
        found = {}
        junk = ["actively hiring", "actively recruiting", "apply", "people clicked", "promoted",
                "responses managed", "view all", "see all", "unsubscribe", "manage", "settings",
                "view profile", "view company", "click here", "show more", "sign in", "save", "dismiss"]
        for m in msgs:
            msg = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
            bodies = []
            def walk(p):
                d = p.get("body", {}).get("data", "")
                if d and p.get("mimeType") == "text/html":
                    bodies.append(base64.urlsafe_b64decode(d).decode("utf-8", errors="replace"))
                for part in p.get("parts", []): walk(part)
            walk(msg.get("payload", {}))
            for body in bodies:
                for a in BeautifulSoup(body, "html.parser").find_all("a", href=True):
                    jid = linkedin_id(a["href"]) if "linkedin.com" in a["href"] and "jobs" in a["href"] else ""
                    title = a.get_text(" ", strip=True)
                    if not jid or not (4 <= len(title) <= 150) or any(g in title.lower() for g in junk):
                        continue
                    if jid not in found or len(title) < len(found[jid]["title"]):
                        found[jid] = new_job(title=title, url=li_url(jid), li_id=jid, source="LinkedIn")
        print(f"    Gmail: {len(found)} jobs parsed")
        return list(found.values())
    except Exception as e:
        print(f"    ⚠ Gmail: {e}")
        return []

# ─── COMPANY CAREERS PAGES (Greenhouse / Lever / Ashby) ──────────────────────

REMOTE_LOC_RE = re.compile(r"remote|anywhere|distributed|north america|americas|canada|latam|united states|usa|\bus\b", re.I)

def age_days(posted):
    """Days since posting, or None if unknown."""
    if not posted: return None
    try:
        if isinstance(posted, (int, float)) or str(posted).isdigit():
            dt = datetime.fromtimestamp(float(posted) / 1000, timezone.utc)
        else:
            dt = datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except Exception:
        return None

def _ats_recent(posted, days=30):
    try:
        if isinstance(posted, (int, float)):
            dt = datetime.fromtimestamp(posted / 1000, timezone.utc)
        else:
            dt = datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt > datetime.now(timezone.utc) - timedelta(days=days)
    except Exception:
        return True

def fetch_greenhouse(slug, name):
    out = []
    d = http_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout=20)
    for j in d.get("jobs", []):
        title, loc = j.get("title", ""), (j.get("location") or {}).get("name", "")
        if not (title_ok(title) and is_relevant_title(title) and REMOTE_LOC_RE.search(loc or "remote")):
            continue
        if not _ats_recent(j.get("updated_at") or j.get("first_published")):
            continue
        desc, salary = "", ""
        try:
            full = http_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{j['id']}?pay_transparency=true", timeout=20)
            desc = html_to_text(full.get("content", ""))
            pr = (full.get("pay_input_ranges") or [])
            if pr:
                p = pr[0]
                salary = fmt_money((p.get("min_cents") or 0) / 100, (p.get("max_cents") or 0) / 100, p.get("currency_type", "USD"), "YEAR")
        except Exception:
            pass
        out.append(new_job(title=title, company=name, url=j.get("absolute_url", ""), description=desc,
                           salary=salary, location=loc, posted=j.get("first_published") or j.get("updated_at", ""),
                           source="Careers"))
    return out

def fetch_lever(slug, name):
    out = []
    for j in http_json(f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=20):
        title = j.get("text", "")
        cats = j.get("categories") or {}
        loc = " / ".join(filter(None, [cats.get("location"), j.get("workplaceType")]))
        if not (title_ok(title) and is_relevant_title(title)): continue
        if (j.get("workplaceType") or "") in ("onsite", "hybrid"): continue
        if not _ats_recent(j.get("createdAt")): continue
        desc = " ".join([j.get("descriptionPlain") or ""] +
                        [f"{l.get('text','')}: {html_to_text(l.get('content',''))}" for l in j.get("lists") or []] +
                        [j.get("additionalPlain") or ""])
        sr = j.get("salaryRange") or {}
        salary = fmt_money(sr.get("min"), sr.get("max"), sr.get("currency", "USD"),
                           "YEAR" if "year" in (sr.get("interval") or "") else "")
        out.append(new_job(title=title, company=name, url=j.get("hostedUrl", ""), description=desc,
                           salary=salary, location=loc, posted=j.get("createdAt", ""), source="Careers"))
    return out

def fetch_ashby(slug, name):
    out = []
    d = http_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true", timeout=25)
    for j in d.get("jobs", []):
        title = j.get("title", "")
        wt = (j.get("workplaceType") or "")
        if not (title_ok(title) and is_relevant_title(title)): continue
        if wt in ("OnSite", "Hybrid") and not j.get("isRemote"): continue
        if not _ats_recent(j.get("publishedAt")): continue
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        loc = " / ".join(filter(None, [j.get("location"), "Remote" if j.get("isRemote") else wt]))
        out.append(new_job(title=title, company=name, url=j.get("jobUrl", ""),
                           description=j.get("descriptionPlain", ""), salary=comp, location=loc,
                           posted=j.get("publishedAt", ""), source="Careers"))
    return out

def fetch_watchlist():
    wl = load_json(WATCHLIST_FILE, {}).get("companies", [])
    fns = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}
    out, errors = [], 0
    def run(c):
        try: return fns[c["ats"]](c["slug"], c.get("name") or c["slug"])
        except Exception: return None
    with cf.ThreadPoolExecutor(8) as ex:
        for res in ex.map(run, [c for c in wl if c.get("ats") in fns]):
            if res is None: errors += 1
            else: out += res
    print(f"    Careers pages: {len(out)} senior remote roles from {len(wl)} companies ({errors} unreachable)")
    return out

# ─── OTHER BOARDS (unchanged sources, tidied) ────────────────────────────────

def fetch_adzuna():
    aid, akey = os.environ.get("ADZUNA_APP_ID", ""), os.environ.get("ADZUNA_APP_KEY", "")
    if not (aid and akey):
        print("    Adzuna: skipped"); return []
    out = []
    for country, queries in (("us", ADZUNA_QUERIES), ("ca", ADZUNA_QUERIES[:2])):
        for q in queries:
            params = urllib.parse.urlencode({"app_id": aid, "app_key": akey, "what": q, "what_and": "remote",
                                             "results_per_page": 50, "sort_by": "date", "max_days_old": 3})
            try:
                d = http_json(f"https://api.adzuna.com/v1/api/jobs/{country}/search/1?{params}")
                for j in d.get("results", []):
                    u = j.get("redirect_url", "")
                    if domain_ok(u):
                        out.append(new_job(title=j.get("title", ""), company=(j.get("company") or {}).get("display_name", ""),
                                           url=u, description=j.get("description", ""),
                                           salary=fmt_money(j.get("salary_min"), j.get("salary_max"), "USD", "YEAR"),
                                           location=(j.get("location") or {}).get("display_name", ""),
                                           posted=j.get("created", ""), source="Adzuna"))
            except Exception as e:
                print(f"    ⚠ Adzuna [{q}] {e}")
            time.sleep(0.4)
    print(f"    Adzuna: {len(out)}")
    return out

def fetch_jsearch():
    key = os.environ.get("RAPIDAPI_KEY", "")
    if not key or datetime.now(timezone.utc).day % 3 != 0:
        print("    JSearch: skipped today"); return []
    out = []
    for q in JSEARCH_QUERIES:
        params = urllib.parse.urlencode({"query": q, "page": 1, "num_pages": 1, "date_posted": "3days", "remote_jobs_only": "true"})
        try:
            d = http_json(f"{JSEARCH_URL}?{params}", {"X-RapidAPI-Key": key, "X-RapidAPI-Host": JSEARCH_HOST})
            for j in d.get("data", []):
                u = j.get("job_apply_link") or ""
                if domain_ok(u):
                    out.append(new_job(title=j.get("job_title", ""), company=j.get("employer_name", ""), url=u,
                                       description=j.get("job_description", ""),
                                       salary=fmt_money(j.get("job_min_salary"), j.get("job_max_salary"),
                                                        j.get("job_salary_currency") or "USD", j.get("job_salary_period") or ""),
                                       location=", ".join(filter(None, [j.get("job_city"), j.get("job_country")])),
                                       posted=j.get("job_posted_at_datetime_utc", ""), source="JSearch"))
        except Exception as e:
            print(f"    ⚠ JSearch [{q}] {e}")
    print(f"    JSearch: {len(out)}")
    return out

def fetch_remoteok():
    out = []
    try:
        for j in http_json("https://remoteok.com/api")[1:]:
            out.append(new_job(title=j.get("position", ""), company=j.get("company", ""),
                               url=j.get("apply_url") or j.get("url", ""), description=html_to_text(j.get("description", "")),
                               salary=fmt_money(j.get("salary_min"), j.get("salary_max"), "USD", "YEAR"),
                               location=j.get("location", ""), posted=j.get("date", ""), source="RemoteOK"))
    except Exception as e:
        print(f"    ⚠ RemoteOK {e}")
    return out

def fetch_remotive():
    out = []
    for cat in ("marketing", "design", "artificial-intelligence", "communications"):
        try:
            for j in http_json(f"https://remotive.com/api/remote-jobs?category={cat}&limit=50").get("jobs", []):
                out.append(new_job(title=j.get("title", ""), company=j.get("company_name", ""), url=j.get("url", ""),
                                   description=html_to_text(j.get("description", "")), salary=j.get("salary", ""),
                                   location=j.get("candidate_required_location", ""),
                                   posted=j.get("publication_date", ""), source="Remotive"))
        except Exception as e:
            print(f"    ⚠ Remotive [{cat}] {e}")
    return out

def fetch_wwr():
    out = []
    for feed in ("remote-sales-and-marketing-jobs", "remote-management-and-finance-jobs", "remote-design-jobs"):
        try:
            root = ET.fromstring(http_get(f"https://weworkremotely.com/categories/{feed}.rss").encode())
            for it in root.iter("item"):
                raw = it.findtext("title") or ""
                company, title = raw.split(": ", 1) if ": " in raw else ("", raw)
                out.append(new_job(title=title, company=company, url=it.findtext("link") or "",
                                   description=html_to_text(it.findtext("description") or ""),
                                   location=it.findtext("region") or "", source="WeWorkRemotely"))
        except Exception as e:
            print(f"    ⚠ WWR [{feed}] {e}")
    return out

# ─── COLLECT + FILTER ────────────────────────────────────────────────────────

def load_seen():
    raw = load_json(SEEN_FILE, {})
    if isinstance(raw, list):
        raw = {k: datetime.now(timezone.utc).isoformat() for k in raw}
    cutoff = datetime.now(timezone.utc) - timedelta(days=SEEN_DAYS)
    out = {}
    for k, v in raw.items():
        try:
            if datetime.fromisoformat(v) > cutoff and not v.startswith("2026-09-28T18:"):
                out[k] = v  # (the 28 Sep 18:xx run used a too-strict US rule; those get re-scored once)
        except Exception:
            pass
    return out

def fetch_working_nomads():
    """workingnomads.com free public feed — simple JSON, no key."""
    out = []
    try:
        for j in http_json("https://www.workingnomads.com/api/exposed_jobs/"):
            out.append(new_job(title=j.get("title", ""), company=j.get("company_name", ""),
                               url=j.get("url", ""), description=html_to_text(j.get("description", "")),
                               location=j.get("location", ""), posted=j.get("pub_date", ""),
                               source="Working Nomads"))
    except Exception as e:
        print(f"    ⚠ Working Nomads {e}")
    print(f"    Working Nomads: {len(out)}")
    return out

def fetch_himalayas():
    """himalayas.app free public API — includes salary, seniority and country restrictions."""
    out = []
    try:
        d = http_json("https://himalayas.app/jobs/api?limit=100")
        for j in d.get("jobs", []):
            locs = ", ".join(j.get("locationRestrictions") or []) or "Worldwide"
            posted = j.get("pubDate", "")
            if isinstance(posted, (int, float)):
                posted = datetime.fromtimestamp(posted, timezone.utc).isoformat()
            out.append(new_job(title=j.get("title", ""), company=j.get("companyName", ""),
                               url=j.get("applicationLink") or j.get("guid", ""),
                               description=html_to_text(j.get("description", "")),
                               salary=fmt_money(j.get("minSalary"), j.get("maxSalary"),
                                                j.get("currency") or "USD", j.get("salaryPeriod") or "YEAR"),
                               location=locs, posted=str(posted), source="Himalayas"))
    except Exception as e:
        print(f"    ⚠ Himalayas {e}")
    print(f"    Himalayas: {len(out)}")
    return out

def collect(seen):
    stats = {}
    # strict=False: the source already searched by title (LinkedIn), so only drop obvious noise.
    # strict=True: wide-net sources, so the title must look like senior creative leadership.
    sources = [
        ("Apify LinkedIn", fetch_apify_linkedin, False), ("LinkedIn alerts", fetch_linkedin_email, False),
        ("LinkedIn guest", fetch_linkedin_guest, False), ("Careers pages", fetch_watchlist, True),
        ("Adzuna", fetch_adzuna, True), ("JSearch", fetch_jsearch, True),
        # RemoteOK re-added 15 Oct 2026 (Travis asked for it); Remotive and WWR stay dropped (1 new role in 15 runs).
        ("RemoteOK", fetch_remoteok, True),
        ("Working Nomads", fetch_working_nomads, True), ("Himalayas", fetch_himalayas, True),
    ]
    picked, keys = [], set()
    for label, fn, strict in sources:
        print(f"\n  {label}:")
        got = fn()
        kept = 0
        for j in got:
            if not (j["title"] and j["url"]) or not title_ok(j["title"]):
                continue
            if not domain_ok(j["url"]) or not company_ok(j["company"]):
                continue
            if strict and not is_relevant_title(j["title"]):
                continue
            if not strict and TITLE_NOISE_RE.search(j["title"]):
                continue
            k_li = f"li:{j['li_id']}" if j["li_id"] else None
            k_tc = make_id(j["title"], j["company"]) if j["company"] else None
            if any(k and (k in seen or k in keys) for k in (k_li, k_tc, j["url"])):
                continue
            for k in (k_li, k_tc, j["url"]):
                if k: keys.add(k)
            picked.append(j); kept += 1
        stats[label] = {"fetched": len(got), "new": kept}
        print(f"    → {kept} new after title + duplicate filters")
    return picked, stats

def enrich_linkedin(jobs):
    need = [j for j in jobs if j["li_id"] and (len(j["description"]) < 500 or not j["company"])][:MAX_LI_FETCHES]
    print(f"\n  Fetching full LinkedIn descriptions for {len(need)} new jobs…")
    ok = 0
    for j in need:
        d = fetch_linkedin_details(j["li_id"])
        if d.get("description"):
            ok += 1
            j["description"] = d["description"]
        if d.get("company") and not j["company"]: j["company"] = d["company"]
        if d.get("location") and d["location"] not in j["location"]:
            j["location"] = (d["location"] + " · " + j["location"]).strip(" ·")
        if d.get("salary"): j["salary"] = d["salary"]
        if d.get("seniority"): j["li_seniority"] = d["seniority"]
        time.sleep(1.0 + random.random() * 0.6)
    print(f"    {ok}/{len(need)} descriptions fetched")

# ─── FULL POSTINGS FOR SHORT SNIPPETS ────────────────────────────────────────
# Adzuna gives 500-character teasers and JSearch often gives summaries. The pay, location and
# "who can we hire" rules sit at the bottom of the real posting, so open it and read the whole thing.

SHORT_DESC     = 2500   # anything shorter than this is treated as a snippet
MAX_PAGE_FETCH = 80

def _jsonld_posting(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try: data = json.loads(tag.string or "")
        except Exception: continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if isinstance(it, dict) and "JobPosting" in str(it.get("@type", "")):
                return it
    return None

def _posting_text(page):
    soup = BeautifulSoup(page, "html.parser")
    jp = _jsonld_posting(soup)
    parts = []
    if jp:
        parts.append(html_to_text(jp.get("description", "")))
        loc = jp.get("jobLocationType") or ""
        req = jp.get("applicantLocationRequirements")
        if loc or req: parts.append(f"Location type: {loc}. Applicant location requirements: {json.dumps(req)}")
        sal = jp.get("baseSalary")
        if sal: parts.append(f"Base salary: {json.dumps(sal)}")
    text = " ".join(p for p in parts if p)
    if len(text) < 800:   # no structured data: fall back to the page's readable text
        for t in soup(["script", "style", "nav", "header", "footer", "noscript", "svg", "form"]): t.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        text = main.get_text(" ", strip=True)
    return text

def fetch_full_posting(url):
    m = re.search(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]{36})", url or "")
    try:
        if m:
            d = http_json(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
            lists = " ".join(f"{x.get('text','')}: {html_to_text(x.get('content',''))}" for x in d.get("lists", []))
            return " ".join([d.get("descriptionPlain", ""), lists, d.get("additionalPlain", ""),
                             f"Workplace: {d.get('workplaceType','')}", json.dumps(d.get("salaryRange") or {})])
        return _posting_text(http_get(url, {"User-Agent": UA, "Accept": "text/html"}, 20))
    except Exception:
        return ""

def enrich_short(jobs):
    need = [j for j in jobs if len(j.get("description") or "") < SHORT_DESC and j.get("url")
            and "linkedin.com" not in j["url"]][:MAX_PAGE_FETCH]
    print(f"\n  Opening {len(need)} postings that only came with a snippet…")
    ok = 0
    for j in need:
        text = fetch_full_posting(j["url"])
        if len(text) > len(j.get("description") or "") + 300:
            j["description"] = text[:DESC_CHARS]; ok += 1
        time.sleep(0.5)
    for j in jobs:
        j["snippet_only"] = len(j.get("description") or "") < SHORT_DESC
    print(f"    {ok}/{len(need)} full postings read")

# ─── CLAUDE: EXTRACT FACTS, THEN SCORE IN CODE ───────────────────────────────

PROFILE = """You are screening job postings for Travis Shorrock. Read the WHOLE posting (location, pay and
remote rules are usually near the bottom) and report the facts. Be literal: only say something is stated
if the posting says it.

WHO TRAVIS IS
- Senior Creative Director, 30+ years in agencies. Canadian, based outside the US on Central time (UTC-6).
   Wants 100% remote work. Can work Americas business hours (Eastern/Central easily, Pacific fine).
- National CD at T&Pm (10 yrs): Toyota Canada, TELUS — large integrated campaigns, 1,000+ assets/month.
- CD at tms (6.5 yrs): Nissan North America, Diageo (Guinness, Smirnoff, Strongbow) — TV, OOH, packaging, CRM.
- Creative Group Head at Havas: Volvo Canada. One TV spot ranked 4th globally for effectiveness by Kantar.
- Built and led large creative departments from scratch. Awards: LIA, NY Festivals, ADCC, Communication Arts, Graphis.
- Hands-on daily with AI creative tools (Midjourney, Runway, Higgsfield, ComfyUI, Claude Code). Strong fit
  for AI-enabled creative, content at scale, brand studios, performance creative, AI companies' brand teams.
- Target pay: $150K+ USD. Not interested in UX/UI/product design, pure sales, or junior roles.
- Strongly prefers working for US companies and being paid in US dollars. Being hired through a Canadian
  entity on Canadian pay (CAD) is a step down for him, not a win.

LANES
- CORE: senior creative leadership — CD, ECD, GCD, ACD, VP/Head of Creative, Head of Brand/Content,
  Chief Brand/Creative/Marketing Officer with real creative scope, AI Creative Director, Creative Technologist lead.
- ADJACENT: creative leadership outside advertising — experience, immersive, narrative, programming, culture,
  entertainment, gaming, hospitality, festivals.
- NOT_A_FIT: anything else (designer IC roles, product design, engineering, sales, ops, junior).

REMOTE RULES: he only wants 100% remote. "Remote (LinkedIn label)" in the location means the employer
tagged it Remote on LinkedIn; treat that as FULLY_REMOTE unless the description mentions office days,
hybrid, or on-site work, which always win.

GEO RULES (be careful — this decides whether he can actually be hired)
- OPEN_ANYWHERE: worldwide / anywhere / any country.
- AMERICAS_OR_LATAM: Americas, LATAM, North & South America, or lists Central American / Latin American countries.
- CANADA_OK: Canada is explicitly allowed (alone or with the US).
- US_REMOTE: remote in the US ("Remote - US", "Remote, USA", a list of US cities, states or US pay zones)
  but no explicit rule that candidates must live in the US.
- US_ONLY: the posting EXPLICITLY requires US residence or citizenship ("must reside in the US",
  "US citizens only", "must be located in the United States"). "Authorized to work in the US",
  "no visa sponsorship" or "W-2" alone do NOT count; use US_REMOTE for those.
- OTHER_REGION_ONLY: limited to Europe, UK, Asia, Australia etc.
- NOT_STATED: nothing said about where candidates can live.

HIRE PATH (decides whether a Canadian living outside the US can actually take the job. He does NOT hold
US work authorization, so a full-time US-payroll job is only open to him if the company can hire him
another way.)
- OPEN_GLOBAL: posting says it hires in any country, uses an employer of record, or "work from anywhere".
- CANADA_POSSIBLE: US-anchored, but the posting signals the company also employs people in Canada or in
  "the countries where we operate", lists Canadian locations or pay, or offers a choice of countries.
  (Usually means a Canadian payroll in CAD, which he likes less.)
- CONTRACT: freelance, contract, consultant or agency-of-record work, where he could invoice from abroad.
- US_EMPLOYEE: full-time employee role tied to the US (US city or "Remote, USA" location, US pay zones,
  401(k), HSA, W-2) with nothing suggesting another country is possible.
- UNCLEAR: can't tell.

FIT (0-10) = how well Travis's background matches the actual job, ignoring logistics:
9-10 his exact sweet spot; 7-8 strong; 5-6 decent with gaps; 3-4 stretch; 0-2 wrong job."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lane", "seniority", "remote", "geo", "hire_path", "employer_base", "pay_currency", "timezone", "comp_min_usd", "comp_max_usd",
                 "comp_text", "location_text", "fit", "headline", "red_flags"],
    "properties": {
        "lane":          {"type": "string", "enum": ["CORE", "ADJACENT", "NOT_A_FIT"]},
        "seniority":     {"type": "string", "enum": ["EXECUTIVE", "SENIOR_LEADER", "MID", "JUNIOR"],
                          "description": "EXECUTIVE = C-level, VP, Head of; SENIOR_LEADER = CD, ECD, GCD, ACD, Director, senior lead; MID = manager or senior individual contributor; JUNIOR = below that"},
        "remote":        {"type": "string", "enum": ["FULLY_REMOTE", "REMOTE_SOME_TRAVEL", "HYBRID", "ONSITE", "UNCLEAR"],
                          "description": "FULLY_REMOTE only if the posting says remote with no required office days. Any required office days = HYBRID. A city with no mention of remote = ONSITE."},
        "geo":           {"type": "string", "enum": ["OPEN_ANYWHERE", "AMERICAS_OR_LATAM", "CANADA_OK", "US_REMOTE", "US_ONLY", "OTHER_REGION_ONLY", "NOT_STATED"]},
        "hire_path":     {"type": "string", "enum": ["OPEN_GLOBAL", "CANADA_POSSIBLE", "CONTRACT", "US_EMPLOYEE", "UNCLEAR"]},
        "employer_base": {"type": "string", "enum": ["US", "CANADA", "OTHER", "UNCLEAR"], "description": "Where the hiring company is headquartered"},
        "pay_currency":  {"type": "string", "enum": ["USD", "CAD", "OTHER", "NOT_STATED"], "description": "Currency of the stated pay"},
        "timezone":      {"type": "string", "enum": ["AMERICAS_HOURS", "PACIFIC_HOURS", "EUROPE_ASIA_HOURS", "NOT_STATED"]},
        "comp_min_usd":  {"type": "integer", "description": "Annual base pay floor in USD, 0 if not stated"},
        "comp_max_usd":  {"type": "integer", "description": "Annual base pay ceiling in USD, 0 if not stated"},
        "comp_text":     {"type": "string", "description": "Pay as written, short, empty if none"},
        "location_text": {"type": "string", "description": "Where candidates can be, in under 8 words"},
        "fit":           {"type": "integer", "description": "0-10 background fit"},
        "headline":      {"type": "string", "description": "One plain sentence: the single biggest reason this is or isn't worth his time"},
        "red_flags":     {"type": "array", "items": {"type": "string"}, "description": "Up to 3 short dealbreaker-style concerns"},
    },
}

def feedback_block():
    fb = load_json(FEEDBACK_FILE, {})
    def lines(d):
        items = sorted(d.values(), key=lambda x: x.get("at", ""), reverse=True)[:12]
        return "\n".join(f"- {x.get('title','')} @ {x.get('company','')}" for x in items)
    liked, disliked = lines(fb.get("saved", {})), lines(fb.get("dismissed", {}))
    if not (liked or disliked): return ""
    return ("\n\nTRAVIS'S RECENT REACTIONS (use these to calibrate FIT — similar roles should score similarly)\n"
            + (f"Saved (wants more like these):\n{liked}\n" if liked else "")
            + (f"Dismissed (wants fewer like these):\n{disliked}\n" if disliked else ""))

def call_claude(system_text, user_text, model, structured=True):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    body = {"model": model, "max_tokens": 800,
            "system": [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user_text}]}
    if structured:
        body["output_config"] = {"format": {"type": "json_schema", "schema": SCHEMA}}
    else:
        body["messages"][0]["content"] += "\n\nRespond ONLY with a JSON object with these keys: " + ", ".join(SCHEMA["required"])
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                 headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read())
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(m.group() if m else text)

def compute_score(f):
    s = max(0, min(10, int(f.get("fit", 0))))
    if f["lane"] == "NOT_A_FIT":            s = min(s, 2)
    if f["seniority"] == "MID":             s = min(s, 4)
    if f["seniority"] == "JUNIOR":          s = min(s, 1)
    if f["remote"] in ("HYBRID", "ONSITE"): s = min(s, 2)
    if f["remote"] == "REMOTE_SOME_TRAVEL": s -= 1
    if f["geo"] in ("US_ONLY", "OTHER_REGION_ONLY"): s = min(s, 3)
    if f["geo"] == "US_REMOTE":             s -= 1
    if f["geo"] in ("OPEN_ANYWHERE", "AMERICAS_OR_LATAM") and s >= 5: s += 1
    if f["timezone"] == "EUROPE_ASIA_HOURS": s -= 2
    hi, lo = f.get("comp_max_usd") or 0, f.get("comp_min_usd") or 0
    if hi and hi < 120000: s -= 2
    elif lo >= 150000 and s >= 5: s += 1
    # He can't take a US-payroll job without US work authorization. Keep the role visible
    # (great roles are worth asking about) but it can't outrank roles he can actually be hired for.
    hp, cur, base = f.get("hire_path"), f.get("pay_currency"), f.get("employer_base")
    # What he wants: a US company paying USD, hiring from anywhere or on contract.
    if hp in ("OPEN_GLOBAL", "CONTRACT") and (cur == "USD" or base == "US") and s >= 6: s += 1
    if hp == "US_EMPLOYEE" and f["geo"] in ("US_REMOTE", "NOT_STATED"): s = min(s, 5)
    # US role, but the way in is probably a Canadian payroll in CAD. Possible, not what he's after.
    if hp == "CANADA_POSSIBLE" and f["geo"] == "US_REMOTE":          s = min(s, 6)
    return max(0, min(10, s))

def score_one(job, system_text, state):
    user = (f"JOB POSTING\nTitle: {job['title']}\nCompany: {job['company']}\nLocation (as listed): {job['location']}\n"
            f"Pay (as listed): {job['salary']}\nLinkedIn seniority: {job.get('li_seniority','')}\n"
            f"Source: {job['source']}\n"
            f"{'NOTE: only a short snippet of this posting was available. Use NOT_STATED / UNCLEAR for anything it does not say.' if job.get('snippet_only') else 'Full posting below. Read all of it, including the fine print at the end.'}\n\nDescription:\n{job['description'] or '(no description available — judge from the title only and use NOT_STATED / UNCLEAR)'}")
    for attempt in range(3):
        try:
            f = call_claude(system_text, user, state["model"], state["structured"])
            break
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:300]
            if e.code == 400 and "output_config" in msg and state["structured"]:
                print("    (structured outputs not accepted — falling back to plain JSON)")
                state["structured"] = False; continue
            if e.code == 404 and state["model"] != FALLBACK_MODEL:
                print(f"    (model {state['model']} unavailable — falling back to {FALLBACK_MODEL})")
                state["model"] = FALLBACK_MODEL; continue
            if e.code in (429, 529, 500, 503):
                time.sleep(5 * (attempt + 1)); continue
            print(f"    ⚠ scoring HTTP {e.code}: {msg}"); f = None; break
        except Exception as e:
            print(f"    ⚠ scoring failed: {e}"); f = None; time.sleep(2)
    else:
        f = None
    if not f:
        job.update(score=3, category="ADJACENT", score_method="fallback", score_reason="Couldn't be scored automatically — check by hand.")
        return job
    f.setdefault("red_flags", [])
    job["score"]        = compute_score(f)
    age = age_days(job.get("posted"))
    if age is not None:
        job["age_days"] = age
        if age > 14: job["score"] = max(0, job["score"] - 1)   # stale: shortlists fill in the first days
    # Travis only wants 100% remote: if Claude can't confirm it and the posting never says "remote", drop it.
    if f["remote"] == "UNCLEAR" and not REMOTE_WORD_RE.search(" ".join([job["title"], job["location"], job["description"]])):
        job["score"] = min(job["score"], 2)
    # Remote not confirmed in the posting: keep it, but it can't outrank roles that are confirmed remote.
    if f["remote"] == "UNCLEAR":
        job["score"] = min(job["score"], 6)
    job["category"]     = "ADJACENT" if f["lane"] == "ADJACENT" else "CORE"
    job["score_reason"] = f.get("headline", "")
    job["score_method"] = state["model"]
    job["facts"]        = {k: f.get(k) for k in ("lane", "seniority", "remote", "geo", "hire_path", "employer_base", "pay_currency", "timezone", "fit",
                                                  "comp_min_usd", "comp_max_usd", "red_flags")}
    if f.get("comp_text") and not job["salary"]: job["salary"] = f["comp_text"]
    if f.get("location_text"): job["where"] = f["location_text"]
    return job

def score_all(jobs):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  ⚠ No ANTHROPIC_API_KEY — using fallback scores")
        for j in jobs: j.update(score=3, category="ADJACENT", score_method="fallback")
        return jobs
    system_text = PROFILE + feedback_block()
    state = {"model": SCORING_MODEL, "structured": True}
    if jobs:  # first call alone so the prompt cache is warm for the rest
        score_one(jobs[0], system_text, state)
    with cf.ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda j: score_one(j, system_text, state), jobs[1:]))
    for j in sorted(jobs, key=lambda j: -j.get("score", 0))[:40]:
        print(f"     [{j['score']}/10] {j['title'][:50]} @ {j['company'][:25]} — {j.get('score_reason','')[:70]}")
    return jobs

# ─── PERSIST ─────────────────────────────────────────────────────────────────

def process(stats):
    seen = load_seen()
    print(f"  seen: {len(seen)} jobs remembered (last {SEEN_DAYS} days)")
    raw, stats = collect(seen)
    print(f"\n  → {len(raw)} new candidates across all sources")
    enrich_linkedin(raw)
    enrich_short(raw)

    cand, dropped = [], {"hybrid": 0, "us_only": 0}
    for j in raw:
        if is_clearly_hybrid(j): dropped["hybrid"] += 1; continue
        if is_us_only(j):        dropped["us_only"] += 1; continue
        cand.append(j)
    print(f"  → dropped {dropped['hybrid']} hybrid/on-site and {dropped['us_only']} explicitly US-only before scoring")

    now = datetime.now(timezone.utc).isoformat()
    print(f"\n  Scoring {len(cand)} jobs with {SCORING_MODEL}…")
    scored = score_all(cand)
    # Remember everything we judged (kept or not) so it isn't re-scored. Jobs that failed
    # to score are NOT remembered, so they get another chance tomorrow.
    for j in raw:
        if j.get("score_method") == "fallback": continue
        for k in (f"li:{j['li_id']}" if j["li_id"] else None, make_id(j["title"], j["company"]) if j["company"] else None):
            if k: seen[k] = now
    kept = []
    existing = load_json(DATA_FILE, [])
    before = len(existing)
    existing = [j for j in existing
                if (j.get("facts") or {}).get("remote") not in ("HYBRID", "ONSITE") and not is_clearly_hybrid({
                    "title": j.get("title", ""), "location": j.get("location", ""), "description": j.get("description", "")})]
    if before != len(existing): print(f"  → removed {before - len(existing)} hybrid/on-site jobs already on the dashboard")
    existing = [j for j in existing if company_ok(j.get("company")) and domain_ok(j.get("url", ""))]
    for j in existing:
        if (j.get("facts") or {}).get("remote") == "UNCLEAR" and j.get("score", 0) > 6: j["score"] = 6
    before = len(existing) + len(scored)
    existing = [j for j in existing if assign_board(j)]
    scored   = [j for j in scored   if assign_board(j)]
    if before != len(existing) + len(scored):
        dropped["us_payroll"] = before - len(existing) - len(scored)
        print(f"  → dropped {dropped['us_payroll']} US-payroll-only roles (no US work authorization)")
    for j in existing + scored: j["board"] = assign_board(j)
    for j in existing + scored: scrub(j)
    existing_ids = {j.get("id") for j in existing}
    for j in scored:
        if j.get("score", 0) < SCORE_FLOOR: continue
        j["id"] = make_id(j["title"], j["company"] or j["li_id"])
        if j["id"] in existing_ids: continue
        j["added"] = now
        kept.append(j)
    print(f"\n  → kept {len(kept)} (score ≥ {SCORE_FLOOR}), dropped {len(scored) - len(kept)}")

    rejected = sorted([j for j in scored if j.get("score", 0) < SCORE_FLOOR], key=lambda j: -j.get("score", 0))[:150]
    save_json(Path("data/rejected.json"), [{k: j.get(k) for k in ("title", "company", "url", "source", "score", "score_method",
                                            "score_reason", "facts", "where", "salary")} for j in rejected])
    all_jobs = (kept + existing)[:MAX_JOBS]
    save_json(DATA_FILE, all_jobs)
    save_json(SEEN_FILE, seen)
    save_json(META_FILE, {"updated": now, "new_count": len(kept), "total_count": len(all_jobs),
                          "model": SCORING_MODEL, "sources": stats, "prefilter_dropped": dropped})
    save_picks(kept, all_jobs, now)
    return kept, all_jobs

def assign_board(j):
    """Which board a job belongs on. None = don't show it at all.
    - US board: US/global jobs that pay USD and can plausibly hire him (anywhere, contract, Canada-eligible).
    - Canada board: anything Canadian — Canadian employer, role in Canada, or CAD pay. Kept visible, ranked
      only against each other, never mixed into the US ranking.
    - US-payroll-only roles need US work authorization he doesn't have, so they're not shown."""
    f = j.get("facts") or {}
    if f.get("hire_path") == "US_EMPLOYEE":                                 return None
    if f.get("employer_base") == "CANADA" or f.get("pay_currency") == "CAD": return "canada"
    return "us"

PICKS_FILE = Path("data/picks.json")

def save_picks(kept, all_jobs, now):
    """Small file the 5am Daily Brief reads: today's best new roles, plus strong ones from the last week."""
    def slim(j):
        f = j.get("facts") or {}
        return {"title": j["title"], "company": j["company"], "url": j["url"], "score": j["score"],
                "salary": j.get("salary", ""), "where": j.get("where") or j.get("location", ""),
                "remote": f.get("remote"), "geo": f.get("geo"), "hire_path": f.get("hire_path"),
                "pay_currency": f.get("pay_currency"), "board": j.get("board", "us"), "why": j.get("score_reason", ""),
                "red_flags": f.get("red_flags", []), "added": j.get("added", "")[:10]}
    today = sorted([j for j in kept if j.get("score", 0) >= 7], key=lambda j: -j["score"])[:8]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    week = sorted([j for j in all_jobs if j.get("score", 0) >= 7 and j.get("added", "") >= cutoff and j not in today],
                  key=lambda j: -j["score"])[:8]
    save_json(PICKS_FILE, {"updated": now, "new_today": len(kept), "today": [slim(j) for j in today],
                           "this_week": [slim(j) for j in week]})

# ─── EMAIL ───────────────────────────────────────────────────────────────────

def build_html(new_jobs):
    today = datetime.now().strftime("%A, %B %d, %Y")
    def row(j, color):
        sub = " · ".join(filter(None, [j.get("company"), j.get("where") or j.get("location"), j.get("salary")]))
        flags = list((j.get("facts") or {}).get("red_flags") or [])
        hp = (j.get("facts") or {}).get("hire_path")
        if hp == "US_EMPLOYEE":     flags.insert(0, "Needs US work authorization")
        if hp == "CANADA_POSSIBLE": flags.insert(0, "US-based; Canada hire possible but likely CAD pay")
        flag_html = f'<br><span style="color:#b36b6b;font-size:11px;">⚑ {"; ".join(flags[:2])}</span>' if flags else ""
        return f"""<tr><td style="padding:12px 10px;border-bottom:1px solid #1c2a3a;">
          <a href="{j['url']}" style="color:{color};font-weight:700;font-size:14px;text-decoration:none;">{htmllib.escape(j['title'])}</a><br>
          <span style="color:#8899aa;font-size:12px;">{htmllib.escape(sub)}</span><br>
          <span style="color:#667788;font-size:12px;font-style:italic;">{htmllib.escape(j.get('score_reason',''))}</span>{flag_html}
        </td><td style="padding:12px 10px;border-bottom:1px solid #1c2a3a;color:{color};font-size:18px;font-weight:700;text-align:right;">{j.get('score',0)}</td></tr>"""
    def section(title, jobs, color):
        if not jobs: return ""
        rows = "".join(row(j, color) for j in sorted(jobs, key=lambda j: -j.get("score", 0))[:12])
        return f'<div style="margin-bottom:28px;"><div style="font-size:10px;letter-spacing:3px;color:{color};font-family:monospace;text-transform:uppercase;margin-bottom:12px;">{title}</div><table style="width:100%;border-collapse:collapse;">{rows}</table></div>'
    ca   = [j for j in new_jobs if j.get("board") == "canada"]
    core = [j for j in new_jobs if j.get("category") == "CORE" and j.get("board") != "canada"]
    adj  = [j for j in new_jobs if j.get("category") == "ADJACENT" and j.get("board") != "canada"]
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body style="margin:0;background:#080c14;font-family:Helvetica,Arial,sans-serif;color:#c8d8e8;">
<div style="max-width:660px;margin:0 auto;padding:28px 20px;">
  <div style="border-bottom:2px solid #00E5CC;padding-bottom:16px;margin-bottom:24px;">
    <div style="font-size:10px;letter-spacing:3px;color:#00E5CC;font-family:monospace;margin-bottom:8px;">DAILY JOB BRIEF · REMOTE ONLY</div>
    <div style="font-size:26px;font-weight:700;color:#fff;">Travis Shorrock</div>
    <div style="font-size:12px;color:#556677;margin-top:4px;">{today} · <a href="https://tshorrock.github.io/job-tracker/" style="color:#00E5CC;">open dashboard</a></div>
  </div>
  {section("Core Roles · US / USD", core, "#00E5CC")}
  {section("Adjacent Roles", adj, "#B983FF")}
  {section("Canada Roles", ca, "#F5A623")}
</div></body></html>"""

def send_email(new_jobs):
    user, pwd = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASS", "")
    to = os.environ.get("TO_EMAIL", user)
    if not (user and pwd):
        print("  ⚠ Email skipped — no credentials"); return "skipped: SMTP_USER / SMTP_PASS not set"
    top = [j for j in new_jobs if j.get("score", 0) >= 7]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎯 Jobs {datetime.now():%b %d} — {len(top)} strong · {len(new_jobs)} new"
    msg["From"], msg["To"] = user, to
    msg.attach(MIMEText(build_html(new_jobs), "html", "utf-8"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls(); s.login(user, pwd); s.sendmail(user, to, msg.as_string())
        print(f"  ✓ Email → {to}")
        return "sent"
    except Exception as e:
        print(f"  ⚠ Email failed: {e}")
        return f"failed: {type(e).__name__}: {str(e)[:160]}"

# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(f"Travis Shorrock Job Scraper v2 — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    print("=" * 60)
    # GitHub delays scheduled runs by hours, so the workflow fires several times overnight.
    # Only the first one that starts on a given night does the work.
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        last = load_json(META_FILE, {}).get("updated", "")
        try:
            if last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=12):
                print(f"Already ran at {last} — skipping this scheduled start."); raise SystemExit(0)
        except ValueError:
            pass
    new_jobs, _ = process({})
    print("\n[email]")
    status = send_email(new_jobs) if new_jobs else "skipped: no new jobs"
    print(f"  → {status}")
    meta = load_json(META_FILE, {}); meta["email"] = status; save_json(META_FILE, meta)
    print("\n✅ Done.")
