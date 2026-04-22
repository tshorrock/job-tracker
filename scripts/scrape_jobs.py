#!/usr/bin/env python3
"""
Travis Shorrock — AI-Powered Job Scraper
Data source: JSearch API (LinkedIn + Indeed + Glassdoor + ZipRecruiter)
Three category lanes: CORE · ADJACENT · WILDCARD
"""

import json, os, re, hashlib, smtplib, urllib.request, time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

DATA_FILE = Path("data/jobs.json")
SEEN_FILE = Path("data/seen_ids.json")
META_FILE = Path("data/meta.json")

JSEARCH_HOST = "jsearch.p.rapidapi.com"
JSEARCH_URL  = "https://jsearch.p.rapidapi.com/search"

# ─── SEARCH QUERIES ───────────────────────────────────────────────────────────
# Each tuple: (query string, hint) — hint is used for logging only
# Claude does the real categorization. 10 queries × 20 weekdays ≈ 200 req/month (free tier)

CORE_QUERIES = [
    "creative director remote",
    "executive creative director remote",
    "head of creative remote",
    "VP creative remote",
    "head of brand remote",
    "AI creative director remote",
    "head of design remote",
]

# Wildcard = genuinely random/weird/fun jobs. Nothing to do with design or advertising.
WILDCARD_QUERIES = [
    "voice actor cartoon remote",
    "professional video game tester remote",
    "mystery shopper remote",
    "sommelier wine remote",
    "escape room designer remote",
    "ASMR content creator remote",
    "pet psychic animal communicator remote",
    "food taster taste tester remote",
    "happiness officer chief fun remote",
    "professional sleeper sleep researcher remote",
]

# Core/adjacent title patterns — anything matching these gets rerouted OUT of wildcard
CORE_ADJACENT_PATTERNS = [
    "creative director", "head of creative", "executive creative",
    "group creative", "vp creative", "vp of creative", "chief creative",
    "head of brand", "brand director", "director of creative",
    "head of design", "vp design", "vp of design", "director of design",
    "chief design", "design director", "graphic design director",
    "creative lead", "creative strategist", "brand strategist",
    "head of content", "vp content", "chief content",
    "ai director", "ai creative", "creative technologist",
    "head of marketing", "vp marketing", "chief marketing", "cmo",
    "filmmaker", "film director", "producer",
]

def is_core_adjacent(title):
    """Returns True if this job belongs in core/adjacent, not wildcard."""
    t = title.lower()
    return any(p in t for p in CORE_ADJACENT_PATTERNS)

# ─── HARD EXCLUDES (title must NOT contain these) ─────────────────────────────

HARD_EXCLUDES = [
    "software engineer", "backend engineer", "frontend engineer",
    "fullstack engineer", "devops engineer", "data engineer",
    "machine learning engineer", "security engineer", "platform engineer",
    "infrastructure engineer", "systems engineer",
    "developer", "programmer",
    "product designer", "ux designer", "ui designer", "ui/ux designer",
    "user experience designer", "interaction designer",
    "product design lead", "head of ux", "vp of ux", "director of ux",
    "video editor", "motion designer", "animator", "cinematographer", "videographer",
    "account executive", "sales director", "sales manager", "sales representative",
    "business development", "account management",
    "finance director", "operations director", "medical director",
    "clinical director", "data scientist", "data analyst",
    "junior", "intern", "entry level", "coordinator", "assistant creative",
    "customer support", "technical support", "help desk", "customer service",
]

# ─── CLAUDE SCORING PROMPT ────────────────────────────────────────────────────

TRAVIS_PROFILE = """
You are scoring remote job postings for Travis Shorrock, a senior Creative Director with 25+ years experience.

SEARCH CRITERIA — score based on how well the job meets ALL of these:

MUST-HAVES (failure on any = score 0-2 max):
- 100% remote. No on-site, no mandatory office days.
- Senior level only: Creative Director, Executive Creative Director, Group Creative Director,
  VP Creative, Head of Creative, ACD (senior IC), Head of Brand, VP Design, Head of Design,
  Director of Design, Chief Design Officer — or equivalent senior roles.
  Design leadership is fine IF it's brand, visual, or graphic design.
  UX Director, UI Director, Product Designer, Head of UX = score 0.
  Mid-level, junior, coordinator = score 0.
- Focus area must be one of:
    a) Traditional advertising campaigns (concept through execution)
    b) Brand identity, brand strategy, or brand design
    c) AI-powered creative tools or platforms
    d) Generative AI applications in advertising/marketing
    e) Creative technology innovation
- Timezone: Team must be primarily US Eastern or Central time (EST/CST).
  PST-heavy teams are borderline (2hrs off) — score lower but don't disqualify.
  European or Asian timezones = score 0. Latin America is fine.
- Compensation in USD or CAD. Other currencies = score lower.

TRAVIS'S BACKGROUND (use to assess fit):
- National CD at T&Pm 10yrs: Toyota Canada, TELUS — large-scale integrated campaigns
- CD at tms: Nissan North America, Diageo (Guinness, Smirnoff, Strongbow)
- Creative Group Head at Havas: Volvo Canada
- Deep hands-on AI: Midjourney, Runway, Higgsfield, ComfyUI, Claude Code
- TV production, OOH, digital, CRM, packaging — full integrated creative
- Built and led large creative departments from scratch

CATEGORY — assign one:
- CORE: Direct creative leadership (CD, ECD, GCD, VP Creative, Head of Creative, Head of Brand)
- ADJACENT: Roles Travis could excel at (AI Director, Creative Technologist, Head of Content, CMO, Design Director, Creative Strategist)
- WILDCARD: Anything outside his normal path but potentially compelling — AI companies, streaming, gaming, creative platforms, unusual titles

Score 9-10: Perfect match — senior creative leadership, advertising or AI/creative tech focus, remote confirmed, EST/CST timezone, USD/CAD comp
Score 7-8:  Strong match — meets most criteria, minor gaps
Score 5-6:  Good fit — right level and focus but some ambiguity on timezone or comp
Score 3-4:  Stretch — interesting but off-brief in one significant way
Score 1-2:  Weak — technically qualifies but poor fit
Score 0:    Disqualified — on-site, junior, wrong timezone, wrong field entirely

Respond ONLY with JSON: {"score": 7, "category": "CORE", "reason": "one punchy sentence"}
"""

WILDCARD_PROFILE = """
You're curating the WILDCARD column of a job board for Travis Shorrock. Travis is a 25-year
Creative Director with serious AI chops, about to move to Costa Rica. He's seen every
boring agency job there is. This column exists to show him something he's never considered.

The vibe is: "Wait — that's actually a job?"

Think creative director for a hot sauce brand. Think AI prompt artist for a video game studio.
Think brand voice consultant for a celebrity. Think travel content lead for a luxury adventure
company. Think creative director for a cannabis brand. Think narrative designer for an indie
game. Think comedy writer for a fintech app. Think creative lead for a surf brand in Tulum.

HIGH scores (7-10) go to jobs that are:
- Genuinely surprising or unexpected
- In a fun, niche, or unusual industry (gaming, cannabis, spirits, food, travel,
  outdoor/surf/ski, luxury, entertainment, animation, comics, music, fashion,
  sports, esports, space, pets, wellness, comedy)
- Creative with real freedom — not just "creative" in a corporate way
- Something Travis would screenshot and text to a friend saying "look at this"
- AI/generative creative roles at interesting companies
- Unusual titles: Chief Storyteller, Head of Vibes, Creative Futurist

LOW scores (1-3) go to jobs that are:
- Fine but forgettable — another brand manager role
- Standard corporate creative wrapped in fun-sounding language

SCORE 0 — kill it immediately:
- Customer support, tech support, help desk, call center
- Caregiver, elderly care, healthcare, therapy, nursing
- Tutoring, teaching, transcription, bookkeeping
- On-site required
- Anything that would make Travis stare at the ceiling

Always assign category: WILDCARD

Respond ONLY with JSON: {"score": 8, "category": "WILDCARD", "reason": "punchy one-liner that captures the fun factor — write it like you're texting a friend"}
"""

# ─── JSEARCH FETCHER ──────────────────────────────────────────────────────────

def fetch_jsearch(query, rapidapi_key, num_pages=1):
    """Fetch jobs from JSearch API (aggregates LinkedIn, Indeed, Glassdoor, ZipRecruiter)."""
    all_jobs = []
    for page in range(1, num_pages + 1):
        params = urllib.parse.urlencode({
            "query": query,
            "page": page,
            "num_pages": 1,
            "date_posted": "week",
            "remote_jobs_only": "true",
        })
        url = f"{JSEARCH_URL}?{params}"
        req = urllib.request.Request(url, headers={
            "X-RapidAPI-Key": rapidapi_key,
            "X-RapidAPI-Host": JSEARCH_HOST,
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())
            jobs = data.get("data", [])
            for j in jobs:
                title = (j.get("job_title") or "").strip()
                company = (j.get("employer_name") or "").strip()
                url_apply = j.get("job_apply_link") or j.get("job_url") or ""
                desc = (j.get("job_description") or "")[:600]
                salary = ""
                if j.get("job_min_salary") and j.get("job_max_salary"):
                    salary = f"${int(j['job_min_salary']):,}–${int(j['job_max_salary']):,} {j.get('job_salary_currency','USD')}/{j.get('job_salary_period','year')}"
                elif j.get("job_min_salary"):
                    salary = f"${int(j['job_min_salary']):,}+ {j.get('job_salary_currency','USD')}"

                if title and url_apply:
                    all_jobs.append({
                        "title": title,
                        "company": company,
                        "url": url_apply,
                        "description": desc,
                        "salary": salary,
                        "source": "JSearch",
                        "posted": j.get("job_posted_at_datetime_utc", ""),
                    })
            time.sleep(0.5)
        except Exception as e:
            print(f"    ⚠ JSearch [{query}] p{page} → {e}")
    return all_jobs

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
    """Kill anything that hints at hybrid or in-person, regardless of remote flag."""
    text = ((job.get("title") or "") + " " + (job.get("description") or "")).lower()
    return not any(signal in text for signal in HYBRID_SIGNALS)

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

# ─── CLAUDE SCORING ───────────────────────────────────────────────────────────

def score_batch(jobs, profile, label=""):
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
        prompt = f"""{profile}

JOB:
Title: {job.get('title', '')}
Company: {job.get('company', '')}
Salary: {job.get('salary', '')}
Description: {(job.get('description') or '')[:400]}"""

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

# ─── MAIN FETCH ───────────────────────────────────────────────────────────────

def fetch_all(rapidapi_key):
    all_jobs = []
    seen_urls = set()

    print("\n  Core/Adjacent queries:")
    for query in CORE_QUERIES:
        print(f"  → \"{query}\"")
        jobs = fetch_jsearch(query, rapidapi_key)
        filtered = [j for j in jobs if title_ok(j["title"]) and is_remote_clean(j) and j["url"] not in seen_urls]
        for j in filtered: seen_urls.add(j["url"])
        print(f"     {len(jobs)} fetched · {len(filtered)} kept")
        all_jobs.extend(filtered)

    print("\n  Wildcard queries:")
    wild_jobs = []
    for query in WILDCARD_QUERIES:
        print(f"  → \"{query}\"")
        jobs = fetch_jsearch(query, rapidapi_key)
        filtered = [j for j in jobs if title_ok(j["title"]) and is_remote_clean(j) and j["url"] not in seen_urls]
        for j in filtered: seen_urls.add(j["url"])
        # Reroute any core/adjacent titles that snuck in via wildcard queries
        rerouted, true_wild = [], []
        for j in filtered:
            if is_core_adjacent(j["title"]):
                rerouted.append(j)
            else:
                true_wild.append(j)
        if rerouted:
            print(f"     ↳ rerouted {len(rerouted)} core/adjacent jobs to main pipeline")
            all_jobs.extend(rerouted)
        print(f"     {len(jobs)} fetched · {len(true_wild)} wildcard · {len(rerouted)} rerouted")
        wild_jobs.extend(true_wild)

    return all_jobs, wild_jobs

# ─── PERSIST ──────────────────────────────────────────────────────────────────

def process_jobs(raw_jobs, wild_jobs):
    seen     = set(load_json(SEEN_FILE, []))
    existing = load_json(DATA_FILE, [])

    new_core = []
    for job in raw_jobs:
        jid = make_id(job["title"], job["company"])
        if jid in seen: continue
        job["id"]    = jid
        job["added"] = datetime.now(timezone.utc).isoformat()
        seen.add(jid)
        new_core.append(job)

    new_wild = []
    for job in wild_jobs:
        jid = make_id(job["title"], job["company"])
        if jid in seen: continue
        job["id"]    = jid
        job["added"] = datetime.now(timezone.utc).isoformat()
        seen.add(jid)
        new_wild.append(job)

    print(f"\n  → {len(new_core)} new core/adjacent to score...")
    if new_core:
        new_core = score_batch(new_core, TRAVIS_PROFILE, "core")

    print(f"\n  → {len(new_wild)} new wildcards to score...")
    if new_wild:
        new_wild = score_batch(new_wild, WILDCARD_PROFILE, "wildcard")

    all_new = [j for j in (new_core + new_wild) if (j.get("score") or 0) >= 1]
    print(f"\n  → {len(all_new)} total kept (score ≥ 1)")

    all_jobs = (all_new + existing)[:300]
    save_json(DATA_FILE, all_jobs)
    save_json(SEEN_FILE, list(seen))
    save_json(META_FILE, {
        "updated":     datetime.now(timezone.utc).isoformat(),
        "new_count":   len(all_new),
        "total_count": len(all_jobs),
    })
    return all_new, all_jobs

# ─── EMAIL ────────────────────────────────────────────────────────────────────

def build_html(new_jobs, all_jobs):
    today = datetime.now().strftime("%A, %B %d, %Y")
    core     = [j for j in new_jobs if j.get("category") == "CORE"]
    adjacent = [j for j in new_jobs if j.get("category") == "ADJACENT"]
    wildcard = [j for j in new_jobs if j.get("category") == "WILDCARD"]

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
    <span style="background:rgba(255,107,53,.12);border:1px solid rgba(255,107,53,.3);color:#FF6B35;padding:4px 12px;border-radius:20px;font-size:12px;">{len(wildcard)} Wildcard</span>
  </div>
  {section("Core Roles", core, "#00E5CC")}
  {section("Adjacent Roles", adjacent, "#B983FF")}
  {section("Wildcard — You Might Love These", wildcard, "#FF6B35")}
</div></body></html>"""

def send_email(new_jobs, all_jobs):
    user = os.environ.get("SMTP_USER", ""); pwd = os.environ.get("SMTP_PASS", "")
    to   = os.environ.get("TO_EMAIL", user)
    if not user or not pwd:
        print("  ⚠ Email skipped — no credentials"); return
    core = len([j for j in new_jobs if j.get("category") == "CORE"])
    adj  = len([j for j in new_jobs if j.get("category") == "ADJACENT"])
    wild = len([j for j in new_jobs if j.get("category") == "WILDCARD"])
    today = datetime.now().strftime("%b %d")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎯 Jobs {today} — {core} core · {adj} adjacent · {wild} wildcard"
    msg["From"] = user; msg["To"] = to
    msg.attach(MIMEText(build_html(new_jobs, all_jobs), "html"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls(); s.login(user, pwd); s.sendmail(user, to, msg.as_string())
        print(f"  ✓ Email → {to}")
    except Exception as e:
        print(f"  ⚠ Email failed: {e}")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

import urllib.parse  # add at top — needed for urlencode

if __name__ == "__main__":
    print("=" * 55)
    print("Travis Shorrock Job Scraper — JSearch Edition")
    print(datetime.now().strftime("%Y-%m-%d %H:%M UTC"))
    print("=" * 55)

    rapidapi_key = os.environ.get("RAPIDAPI_KEY", "")
    if not rapidapi_key:
        print("❌ RAPIDAPI_KEY not set — aborting")
        exit(1)

    print("\n[1/3] Fetching from JSearch (LinkedIn + Indeed + Glassdoor + ZipRecruiter)...")
    raw_jobs, wild_jobs = fetch_all(rapidapi_key)
    print(f"\n  Total: {len(raw_jobs)} core/adjacent · {len(wild_jobs)} wildcard candidates")

    print("\n[2/3] Scoring with Claude Haiku...")
    new_jobs, all_jobs = process_jobs(raw_jobs, wild_jobs)

    print("\n[3/3] Sending email...")
    if new_jobs:
        send_email(new_jobs, all_jobs)
    else:
        print("  → No new jobs today, skipping email")

    print("\n✅ Done.")
