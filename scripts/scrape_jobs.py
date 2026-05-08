#!/usr/bin/env python3
"""
Travis Shorrock — AI-Powered Job Scraper
Sources: LinkedIn Guest API + Adzuna API + JSearch (throttled)
Three category lanes: CORE · ADJACENT
"""

import json, os, re, hashlib, smtplib, urllib.request, urllib.parse, time, random
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
- Timezone: Prefer US-compatible. If not mentioned, do NOT penalize — assume US-compatible.
  Only penalize if posting explicitly requires European or Asian hours.

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

def fetch_linkedin(query):
    """Scrape LinkedIn public guest API — no login required."""
    jobs = []
    headers = {
        "User-Agent": random.choice(LINKEDIN_UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://www.linkedin.com/jobs/",
    }

    params = urllib.parse.urlencode({
        "keywords": query,
        "location": "United States",
        "f_WT": "2",
        "f_E": "4,5,6",
        "f_TPR": "r86400",
        "start": "0",
    })

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

    # ── LinkedIn (free, every day) ────────────────────────────────────────────
    print("\n  LinkedIn guest API:")
    for query in LINKEDIN_QUERIES:
        jobs = fetch_linkedin(query)
        fresh = [j for j in jobs if title_ok(j["title"]) and is_remote_clean(j) and has_remote_signal(j) and j["url"] not in seen_urls]
        for j in fresh: seen_urls.add(j["url"])
        all_jobs.extend(fresh)

    # ── Adzuna (free, every day) ──────────────────────────────────────────────
    if adzuna_id and adzuna_key:
        print("\n  Adzuna API:")
        for query in ADZUNA_QUERIES:
            jobs = fetch_adzuna(query, adzuna_id, adzuna_key, country="us")
            fresh = [j for j in jobs if title_ok(j["title"]) and is_remote_clean(j) and has_remote_signal(j) and j["url"] not in seen_urls]
            for j in fresh: seen_urls.add(j["url"])
            all_jobs.extend(fresh)
        for query in ADZUNA_QUERIES[:2]:
            jobs = fetch_adzuna(query, adzuna_id, adzuna_key, country="ca")
            fresh = [j for j in jobs if title_ok(j["title"]) and is_remote_clean(j) and has_remote_signal(j) and j["url"] not in seen_urls]
            for j in fresh: seen_urls.add(j["url"])
            all_jobs.extend(fresh)
    else:
        print("\n  Adzuna: skipped (ADZUNA_APP_ID / ADZUNA_APP_KEY not set)")

    # ── JSearch (throttled: every 3rd day only) ───────────────────────────────
    run_jsearch = (datetime.now(timezone.utc).day % 3 == 0)
    if run_jsearch and rapidapi_key:
        print("\n  JSearch (throttled run):")
        for query in JSEARCH_QUERIES:
            print(f"  → \"{query}\"")
            jobs = fetch_jsearch(query, rapidapi_key, num_pages=1)
            fresh = [j for j in jobs if title_ok(j["title"]) and is_remote_clean(j) and has_remote_signal(j) and j["url"] not in seen_urls]
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
        prompt = f"""{TRAVIS_PROFILE}

JOB:
Title: {job.get('title', '')}
Company: {job.get('company', '')}
Salary: {job.get('salary', '')}
Description: {(job.get('description') or '')[:1500]}"""

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
                    "max_tokens": 120,
                    "messages": [{"role": "user", "content": prompt}]
                }).encode()
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())
            text = data["content"][0]["text"].strip()
            result = json.loads(re.search(r'\{.*\}', text, re.DOTALL).group())
            job["score"]        = int(result.get("score", 3))
            job["category"]     = result.get("category", "ADJACENT").upper()
            job["score_reason"] = result.get("reason", "")
            job["score_method"] = "claude"
            if job["score"] >= 5:
                print(f"     [{job['score']}/10] [{job['category']}] {job['title'][:55]}")
                if job.get("score_reason"):
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
    print(f"\n  → {len(kept)} total kept (score ≥ 3)")

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
