#!/usr/bin/env python3
"""
Job Tracker Scraper — Pure Python stdlib, zero dependencies.
Scrapes remote Creative Director / AI creative roles from 8 sources,
scores them, deduplicates, emails a digest, and updates data files.
"""

import json
import os
import re
import smtplib
import ssl
import sys
import hashlib
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import unescape
from urllib.parse import quote_plus

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Title-level keywords: jobs whose TITLE contains these get scored highest
TITLE_KEYWORDS = [
    "creative director", "associate creative director",
    "head of creative", "head of brand",
    "vp creative", "executive creative director",
    "chief creative officer",
    "ai creative director", "ai producer", "ai production director",
    "creative strategist",
    "cmo", "vp marketing", "head of marketing",
]

# Broader keywords matched against title + description
KEYWORDS_HIGH = [
    "creative director", "head of creative", "vp creative",
    "ai creative", "generative ai", "ai design", "ai art director",
    "creative lead", "executive creative director",
    "chief creative officer", "creative strategist",
    "ai producer", "ai production director", "cmo",
    "vp marketing", "head of marketing", "head of brand",
    "associate creative director",
]

KEYWORDS_MED = [
    "art director", "design director", "brand director",
    "creative manager", "content director", "visual director",
    "ai content", "ai brand", "prompt engineer",
    "creative technologist", "creative operations",
]

KEYWORDS_LOW = [
    "creative", "design lead", "brand lead",
    "marketing director", "ai", "generative",
]

REMOTE_SIGNALS = [
    "remote", "anywhere", "distributed", "work from home",
    "wfh", "location independent", "global",
]

# Title-level excludes: if the job TITLE contains any of these, score = 0
TITLE_EXCLUDES = [
    "ux director", "product designer", "graphic designer",
    "brand designer", "content producer", "ui/ux",
    "account executive", "account manager",
    "sales director", "sales manager", "sales representative",
    "sales lead", "sales engineer",
]

EXCLUDE_PATTERNS = [
    r"\bjunior\b", r"\bintern\b", r"\bentry.level\b",
    r"\bassistant\b", r"\bcoordinator\b",
]

SOURCES = [
    {
        "name": "We Work Remotely",
        "type": "rss",
        "url": "https://weworkremotely.com/categories/remote-design-jobs.rss",
    },
    {
        "name": "Remote OK (Design)",
        "type": "json",
        "url": "https://remoteok.com/api?tag=design",
    },
    {
        "name": "Remote OK (Marketing)",
        "type": "json",
        "url": "https://remoteok.com/api?tag=marketing",
    },
    {
        "name": "Himalayas",
        "type": "rss",
        "url": "https://himalayas.app/jobs/rss?category=design",
    },
    {
        "name": "Remotive",
        "type": "json",
        "url": "https://remotive.com/api/remote-jobs?category=design",
    },
    {
        "name": "Jobicy",
        "type": "rss",
        "url": "https://jobicy.com/feed/newjob?tag=design",
    },
    {
        "name": "Arbeitnow",
        "type": "json",
        "url": "https://www.arbeitnow.com/api/job-board-api?tag=design",
    },
    {
        "name": "Authentic Jobs",
        "type": "rss",
        "url": "https://authenticjobs.com/rss/custom.rss",
    },
]

MANUAL_BOARDS = [
    {"name": "LinkedIn", "url": "https://www.linkedin.com/jobs/search/?keywords=creative%20director%20remote&f_WT=2"},
    {"name": "Built In", "url": "https://builtin.com/jobs/remote/design?search=creative+director"},
    {"name": "FlexJobs", "url": "https://www.flexjobs.com/search?search=creative+director&tele_level%5B%5D=All+Telecommuting"},
    {"name": "Wellfound", "url": "https://wellfound.com/role/r/creative-director"},
    {"name": "Dribbble", "url": "https://dribbble.com/jobs?keyword=creative+director&location=Anywhere"},
    {"name": "Working Not Working", "url": "https://workingnotworking.com/jobs?q=creative+director"},
    {"name": "AIGA", "url": "https://designjobs.aiga.org/#/jobs?keywords=creative+director"},
    {"name": "The Muse", "url": "https://www.themuse.com/search?keyword=creative+director&work-flexibility=remote"},
    {"name": "Contra", "url": "https://contra.com/opportunity?query=creative+director"},
    {"name": "Toptal", "url": "https://www.toptal.com/designers"},
    {"name": "Roboflow", "url": "https://roboflow.com/careers"},
    {"name": "Superside", "url": "https://careers.superside.com/"},
    {"name": "Curious Refuge", "url": "https://www.curiousrefuge.com/ai-jobs"},
    {"name": "Creatively", "url": "https://creatively.life/jobs?q=creative+director"},
    {"name": "We Work Remotely (manual)", "url": "https://weworkremotely.com/remote-jobs/search?term=creative+director"},
    {"name": "Remote.co", "url": "https://remote.co/remote-jobs/search/?search_keywords=creative+director"},
    {"name": "JustRemote", "url": "https://justremote.co/remote-design-jobs"},
    {"name": "Pangian", "url": "https://pangian.com/job-travel-remote/?search=creative+director"},
]

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
JOBS_FILE = os.path.join(DATA_DIR, "jobs.json")
META_FILE = os.path.join(DATA_DIR, "meta.json")
SEEN_FILE = os.path.join(DATA_DIR, "seen_ids.json")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_id(title, company, url):
    """Create a deterministic ID for dedup."""
    raw = f"{title.lower().strip()}|{company.lower().strip()}|{url.strip()}"
    return hashlib.md5(raw.encode()).hexdigest()


def fetch(url, retries=2):
    """Fetch URL content with retries and a browser-like User-Agent."""
    headers = {"User-Agent": "Mozilla/5.0 (job-tracker bot; +https://github.com/tshorrock/job-tracker)"}
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries + 1):
        try:
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            if attempt == retries:
                print(f"  WARN: Failed to fetch {url}: {e}")
                return None
    return None


def strip_html(text):
    """Remove HTML tags and decode entities."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def score_job(title, company, description):
    """Score a job 0-10 based on keyword relevance."""
    title_lower = title.lower()
    blob = f"{title} {company} {description}".lower()

    # Title-level excludes: hard block
    for exc in TITLE_EXCLUDES:
        if exc in title_lower:
            return 0

    # General exclude patterns
    for pat in EXCLUDE_PATTERNS:
        if re.search(pat, blob):
            return 0

    score = 0

    # Title-level keyword bonus (strongest signal)
    for kw in TITLE_KEYWORDS:
        if kw in title_lower:
            score += 4
            break  # one title match is enough

    for kw in KEYWORDS_HIGH:
        if kw in blob:
            score += 3

    for kw in KEYWORDS_MED:
        if kw in blob:
            score += 2

    for kw in KEYWORDS_LOW:
        if kw in blob:
            score += 1

    # Bonus for remote signals
    for sig in REMOTE_SIGNALS:
        if sig in blob:
            score += 1
            break

    return min(score, 10)


# ---------------------------------------------------------------------------
# Source parsers
# ---------------------------------------------------------------------------

def parse_rss(xml_text, source_name):
    """Parse RSS/Atom feed into job dicts."""
    jobs = []
    if not xml_text:
        return jobs
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  WARN: XML parse error for {source_name}: {e}")
        return jobs

    # Handle both RSS and Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)

    for item in items:
        title = ""
        link = ""
        company = ""
        description = ""
        pub_date = ""

        # RSS
        t = item.find("title")
        if t is not None and t.text:
            title = t.text.strip()
        l = item.find("link")
        if l is not None and l.text:
            link = l.text.strip()
        elif l is not None:
            link = l.get("href", "")
        d = item.find("description")
        if d is not None and d.text:
            description = strip_html(d.text)
        pd = item.find("pubDate")
        if pd is not None and pd.text:
            pub_date = pd.text.strip()

        # Atom fallback
        if not title:
            t = item.find("atom:title", ns)
            if t is not None and t.text:
                title = t.text.strip()
        if not link:
            l = item.find("atom:link", ns)
            if l is not None:
                link = l.get("href", "")
        if not description:
            c = item.find("atom:content", ns) or item.find("atom:summary", ns)
            if c is not None and c.text:
                description = strip_html(c.text)

        # Try to extract company from title pattern "Title at Company"
        if not company and " at " in title:
            parts = title.rsplit(" at ", 1)
            if len(parts) == 2:
                title, company = parts[0].strip(), parts[1].strip()

        if not title or not link:
            continue

        jobs.append({
            "title": title,
            "company": company or source_name,
            "url": link,
            "description": description[:500],
            "source": source_name,
            "date": pub_date or datetime.now(timezone.utc).isoformat(),
        })

    return jobs


def parse_json_remoteok(data, source_name):
    """Parse Remote OK JSON API response."""
    jobs = []
    if not data:
        return jobs
    try:
        items = json.loads(data)
    except json.JSONDecodeError:
        return jobs

    # Remote OK returns a legal notice as first item
    for item in items:
        if not isinstance(item, dict):
            continue
        if "position" not in item and "title" not in item:
            continue

        title = item.get("position") or item.get("title", "")
        company = item.get("company", source_name)
        url = item.get("url", item.get("apply_url", ""))
        if not url and item.get("id"):
            url = f"https://remoteok.com/remote-jobs/{item['id']}"
        desc = strip_html(item.get("description", ""))
        date = item.get("date", datetime.now(timezone.utc).isoformat())

        if not title or not url:
            continue

        jobs.append({
            "title": title,
            "company": company,
            "url": url,
            "description": desc[:500],
            "source": source_name,
            "date": date,
        })

    return jobs


def parse_json_remotive(data, source_name):
    """Parse Remotive JSON API response."""
    jobs = []
    if not data:
        return jobs
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return jobs

    items = parsed.get("jobs", parsed) if isinstance(parsed, dict) else parsed

    for item in items:
        if not isinstance(item, dict):
            continue

        title = item.get("title", "")
        company = item.get("company_name", source_name)
        url = item.get("url", "")
        desc = strip_html(item.get("description", ""))
        date = item.get("publication_date", datetime.now(timezone.utc).isoformat())

        if not title or not url:
            continue

        jobs.append({
            "title": title,
            "company": company,
            "url": url,
            "description": desc[:500],
            "source": source_name,
            "date": date,
        })

    return jobs


def parse_json_arbeitnow(data, source_name):
    """Parse Arbeitnow JSON API response."""
    jobs = []
    if not data:
        return jobs
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return jobs

    items = parsed.get("data", parsed) if isinstance(parsed, dict) else parsed

    for item in items:
        if not isinstance(item, dict):
            continue

        title = item.get("title", "")
        company = item.get("company_name", source_name)
        url = item.get("url", "")
        if not url and item.get("slug"):
            url = f"https://www.arbeitnow.com/view/{item['slug']}"
        desc = strip_html(item.get("description", ""))
        date = item.get("created_at", datetime.now(timezone.utc).isoformat())
        remote = item.get("remote", False)

        if not title or not url:
            continue

        jobs.append({
            "title": title,
            "company": company,
            "url": url,
            "description": desc[:500],
            "source": source_name,
            "date": date,
        })

    return jobs


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def scrape_all():
    """Scrape all sources, return list of job dicts."""
    all_jobs = []

    for src in SOURCES:
        name = src["name"]
        print(f"Scraping {name}...")
        raw = fetch(src["url"])

        if src["type"] == "rss":
            jobs = parse_rss(raw, name)
        elif "remoteok" in src["url"]:
            jobs = parse_json_remoteok(raw, name)
        elif "remotive" in src["url"]:
            jobs = parse_json_remotive(raw, name)
        elif "arbeitnow" in src["url"]:
            jobs = parse_json_arbeitnow(raw, name)
        else:
            jobs = parse_json_remotive(raw, name)  # Generic fallback

        print(f"  Found {len(jobs)} raw jobs")
        all_jobs.extend(jobs)

    return all_jobs


def deduplicate(jobs, seen_ids):
    """Remove already-seen jobs. Returns (new_jobs, updated_seen_ids)."""
    new_jobs = []
    seen_set = set(seen_ids)

    for job in jobs:
        jid = make_id(job["title"], job["company"], job["url"])
        if jid not in seen_set:
            job["id"] = jid
            new_jobs.append(job)
            seen_set.add(jid)

    return new_jobs, list(seen_set)


def score_and_sort(jobs):
    """Score and sort jobs by relevance (highest first)."""
    for job in jobs:
        job["score"] = score_job(job["title"], job["company"], job["description"])

    # Filter out zero-score jobs (completely irrelevant)
    jobs = [j for j in jobs if j["score"] > 0]

    return sorted(jobs, key=lambda j: j["score"], reverse=True)


def build_email_html(new_jobs, total_count):
    """Build the daily digest email as HTML."""
    now = datetime.now(timezone.utc).strftime("%B %d, %Y")

    high = [j for j in new_jobs if j["score"] >= 5]
    medium = [j for j in new_jobs if 2 <= j["score"] < 5]
    low = [j for j in new_jobs if j["score"] < 2]

    def job_row(job):
        star = "★ " if job["score"] >= 5 else ""
        score_color = "#22c55e" if job["score"] >= 5 else "#eab308" if job["score"] >= 2 else "#94a3b8"
        return f"""
        <tr>
            <td style="padding:12px;border-bottom:1px solid #e2e8f0;">
                <a href="{job['url']}" style="color:#2563eb;font-weight:600;text-decoration:none;font-size:15px;">
                    {star}{job['title']}
                </a><br>
                <span style="color:#64748b;font-size:13px;">{job['company']} &middot; {job['source']}</span>
                <span style="background:{score_color};color:white;padding:2px 8px;border-radius:10px;font-size:11px;margin-left:8px;">
                    {job['score']}/10
                </span>
            </td>
        </tr>"""

    sections = ""
    if high:
        rows = "".join(job_row(j) for j in high)
        sections += f"""
        <h2 style="color:#16a34a;margin:24px 0 8px;">🎯 High Match ({len(high)})</h2>
        <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>"""

    if medium:
        rows = "".join(job_row(j) for j in medium)
        sections += f"""
        <h2 style="color:#ca8a04;margin:24px 0 8px;">Worth a Look ({len(medium)})</h2>
        <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>"""

    if low:
        rows = "".join(job_row(j) for j in low[:10])
        sections += f"""
        <h2 style="color:#94a3b8;margin:24px 0 8px;">Other ({len(low)})</h2>
        <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>"""

    # Manual boards section
    board_links = " &middot; ".join(
        f'<a href="{b["url"]}" style="color:#2563eb;text-decoration:none;">{b["name"]}</a>'
        for b in MANUAL_BOARDS
    )

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:640px;margin:0 auto;padding:20px;color:#1e293b;">
    <h1 style="font-size:22px;color:#0f172a;margin-bottom:4px;">Job Tracker Daily Digest</h1>
    <p style="color:#64748b;margin-top:0;">{now} &middot; {len(new_jobs)} new jobs found &middot; {total_count} total tracked</p>

    {sections if new_jobs else '<p style="color:#64748b;padding:20px;text-align:center;">No new matching jobs today. Check manual boards below.</p>'}

    <hr style="border:none;border-top:1px solid #e2e8f0;margin:32px 0;">
    <h2 style="font-size:16px;color:#0f172a;">📋 Manual Boards to Check</h2>
    <p style="font-size:13px;line-height:2;">{board_links}</p>

    <hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0;">
    <p style="color:#94a3b8;font-size:11px;text-align:center;">
        Sent by <a href="https://tshorrock.github.io/job-tracker/" style="color:#2563eb;">Job Tracker</a> &middot; Runs Mon-Fri 7AM EST
    </p>
</body>
</html>"""
    return html


def send_email(html_body, new_count):
    """Send digest email via Gmail SMTP."""
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    to_email = os.environ.get("TO_EMAIL", "")

    if not all([smtp_user, smtp_pass, to_email]):
        print("WARN: Email secrets not configured, skipping email send.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Job Tracker: {new_count} new jobs found" if new_count else "Job Tracker: No new jobs today"
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())
        print(f"Email sent to {to_email}")
        return True
    except Exception as e:
        print(f"ERROR sending email: {e}")
        return False


def main():
    # Load existing data
    try:
        with open(JOBS_FILE) as f:
            existing_jobs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing_jobs = []

    try:
        with open(SEEN_FILE) as f:
            seen_ids = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        seen_ids = []

    # Scrape
    raw_jobs = scrape_all()
    print(f"\nTotal raw jobs fetched: {len(raw_jobs)}")

    # Dedup
    new_jobs, updated_seen = deduplicate(raw_jobs, seen_ids)
    print(f"New (unseen) jobs: {len(new_jobs)}")

    # Score & sort
    scored_jobs = score_and_sort(new_jobs)
    print(f"After scoring (>0): {len(scored_jobs)}")

    # Merge with existing (new first, then old)
    all_jobs = scored_jobs + existing_jobs

    # Cap at 500 most recent
    all_jobs = all_jobs[:500]

    # Save data
    with open(JOBS_FILE, "w") as f:
        json.dump(all_jobs, f, indent=2)

    with open(SEEN_FILE, "w") as f:
        json.dump(updated_seen, f)

    meta = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "new_count": len(scored_jobs),
        "total_count": len(all_jobs),
        "sources": [s["name"] for s in SOURCES],
        "manual_boards": MANUAL_BOARDS,
    }
    with open(META_FILE, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\nData saved. Total jobs in DB: {len(all_jobs)}")

    # Send email
    email_html = build_email_html(scored_jobs, len(all_jobs))
    send_email(email_html, len(scored_jobs))

    print("Done!")


if __name__ == "__main__":
    main()
