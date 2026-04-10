#!/usr/bin/env python3
"""
Travis Shorrock — AI-Powered Job Scraper
Runs daily via GitHub Actions (7AM EST, Mon-Fri).

Two-pass system:
  Pass 1: Broad keyword fetch from all sources (catches everything)
  Pass 2: Claude scores each job 0-10 for Travis specifically
  
Cost: ~$0.05-0.10/day max. Claude only scores jobs that pass basic title filter.
"""

import json, os, re, hashlib, smtplib, urllib.request, urllib.parse, time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ─── CONFIG ────────────────────────────────────────────────────────────────────

DATA_FILE  = Path("data/jobs.json")
SEEN_FILE  = Path("data/seen_ids.json")
META_FILE  = Path("data/meta.json")

# Broad first-pass title filter — wide net, Claude handles precision
BROAD_TITLES = [
    # Core CD
    "creative director", "head of creative", "creative lead",
    "associate creative director", "group creative director",
    "executive creative", "chief creative", "global creative director",
    # Brand
    "head of brand", "brand director", "vp brand", "director of brand",
    "brand creative", "brand strategist",
    # VP / C-suite creative
    "vp creative", "vp of creative", "svp creative",
    "chief marketing officer", "cmo",
    "vp marketing", "vp of marketing", "head of marketing",
    # AI / production
    "ai director", "ai creative", "ai producer", "ai production director",
    "generative creative",
    # Strategy / content leadership
    "creative strategist", "head of content", "vp content",
    "chief content officer", "director of creative",
    "fractional creative", "fractional cdo",
    # Design leadership (senior only — graphic/brand CD level)
    "design director",     # brand-side design director OK
    "head of design",      # senior enough to be relevant
]

# Hard excludes — NEVER pass these through regardless of anything else
HARD_EXCLUDES = [
    # Engineering / tech
    "engineer", "engineering", "developer", "software", "backend",
    "frontend", "fullstack", "devops", "data scientist", "machine learning",
    "infrastructure", "platform engineer", "security engineer",
    # Wrong design disciplines
    "product designer", "ux designer", "ui designer", "ui/ux",
    "product design", "user experience designer", "interaction designer",
    # Video / production (individual contributor, not director)
    "video editor", "video producer", "motion designer", "animator",
    "cinematographer", "videographer", "editor",
    # Sales / business
    "account executive", "account manager", "account director",
    "sales director", "sales manager", "business development",
    "account management", "technical account", "revenue manager",
    # Finance / ops / other
    "finance director", "operations director", "medical director",
    "clinical director", "data director", "analytics director",
    "technical director",
    # Junior
    "junior", "intern", "entry level", "coordinator",
    "assistant creative",
]

# Travis's profile for Claude scoring
TRAVIS_PROFILE = """
Travis Shorrock is a Creative Director with 25+ years experience looking for FULLY REMOTE work.
Background: National CD at T&Pm (Toyota Canada, TELUS, 10 yrs), CD at tms (Nissan NA, Diageo), 
Creative Group Head at Havas (Volvo Canada). Deep AI tools expertise: Midjourney, Runway, 
Higgsfield, ComfyUI, Claude Code. Currently in Toronto, moving to Costa Rica Aug 2026.

Requirements (ALL must be met for a high score):
- REMOTE WORK: Must be remote or remote-optional. Score 0 ONLY if the job explicitly 
  requires on-site, hybrid with mandatory office days, or relocation. 
  "Remote or hybrid" / "remote with occasional travel" is ACCEPTABLE — score normally.
  Pure on-site or "must be in [city]" = score 0.
- EST or CST timezone overlap only. Score 0 if explicitly PST-only.
- $150K+ USD. Score lower if salary listed and clearly below this.
- Senior creative leadership level — CD, ACD, Head of, VP, Director minimum.
- Creative/brand work — NOT engineering, UX, product design, or sales.

Travis's strengths to match against:
- 25 years brand, campaign, integrated creative (Toyota, TELUS, Diageo, Nissan, Volvo)
- Proven at scale — 1000+ assets/month, 280+ dealerships, global accounts
- Deep AI tools: Midjourney, Runway, Higgsfield, ComfyUI, Claude Code
- TV production, OOH, digital, CRM, packaging — full integrated creative
- Team builder and mentor — built departments from scratch

AUTOMATIC SCORE 0:
- Explicitly on-site or mandatory in-office (not just "office available")
- PST-only timezone
- Engineering, sales, UX, product design, data roles
- Junior, intern, coordinator level

Score 8-10: Senior creative leadership, remote-friendly, right pay, strong Travis fit
Score 5-7: Good fit, most criteria met, some ambiguity on pay or remote status
Score 3-4: Interesting stretch — adjacent role or unclear requirements
Score 1-2: Wrong field, wrong level, or likely on-site
Score 0: Disqualified per above rules
"""

# ─── SOURCES ───────────────────────────────────────────────────────────────────

SOURCES = [
    {"name": "We Work Remotely",   "url": "https://weworkremotely.com/remote-jobs.rss",              "type": "rss"},
    {"name": "Remote OK Design",   "url": "https://remoteok.com/remote-design-jobs.json",             "type": "remoteok"},
    {"name": "Remote OK Marketing","url": "https://remoteok.com/remote-marketing-jobs.json",          "type": "remoteok"},
    {"name": "Remote OK Exec",     "url": "https://remoteok.com/remote-exec-jobs.json",               "type": "remoteok"},
    {"name": "Himalayas",          "url": "https://himalayas.app/jobs/rss",                           "type": "rss"},
    {"name": "Remotive",           "url": "https://remotive.com/api/remote-jobs?limit=100",           "type": "remotive"},
    {"name": "Jobicy",             "url": "https://jobicy.com/?feed=job_feed&job_types=full-time",    "type": "rss"},
    {"name": "Arbeitnow",          "url": "https://www.arbeitnow.com/api/job-board-api",              "type": "arbeitnow"},
    {"name": "Authentic Jobs",     "url": "https://authenticjobs.com/feed/",                          "type": "rss"},
    {"name": "WWR Design",         "url": "https://weworkremotely.com/categories/remote-design-jobs.rss", "type": "rss"},
    {"name": "WWR Marketing",      "url": "https://weworkremotely.com/categories/remote-marketing-jobs.rss","type": "rss"},
]

MANUAL_BOARDS = [
    ("LinkedIn — CD Remote",         "https://www.linkedin.com/jobs/search/?keywords=creative+director+AI&f_WT=2&f_TPR=r86400"),
    ("LinkedIn — Head of Creative",  "https://www.linkedin.com/jobs/search/?keywords=head+of+creative+remote&f_WT=2"),
    ("LinkedIn — VP Creative",       "https://www.linkedin.com/jobs/search/?keywords=vp+creative+remote&f_WT=2"),
    ("Built In — Remote CD",         "https://builtin.com/jobs/remote/design-ux/search/creative-director"),
    ("Remote Rocketship",            "https://www.remoterocketship.com/jobs/creative-director/"),
    ("FlexJobs CD",                  "https://www.flexjobs.com/remote-jobs/creative-director"),
    ("Wellfound (AngelList)",        "https://wellfound.com/role/r/creative-director"),
    ("JustRemote Design",            "https://justremote.co/remote-design-jobs"),
    ("Working Nomads",               "https://www.workingnomads.com/jobs?category=design"),
    ("Roboflow — CD Role",           "https://jobs.ashbyhq.com/roboflow"),
    ("Superside — AI CD",            "https://careers.superside.com/jobs/ai-creative-director"),
    ("Curious Refuge AI Jobs",       "https://curiousrefuge.com/ai-jobs-board"),
    ("The AI Job Board",             "https://theaijobboard.com"),
    ("Contra — Fractional",          "https://contra.com/opportunities"),
    ("Toptal Creative",              "https://www.toptal.com/creative"),
    ("Daily Remote",                 "https://dailyremote.com/remote-creative-director-jobs"),
    ("Remote Circle",                "https://remotecircle.com"),
    ("Hubstaff Talent",              "https://hubstafftalent.net/search/jobs?q=creative+director"),
]

# ─── HELPERS ───────────────────────────────────────────────────────────────────

def make_id(title, company):
    return hashlib.md5(f"{title.lower().strip()}{company.lower().strip()}".encode()).hexdigest()[:12]

def fetch_url(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Accept": "application/rss+xml, application/json, text/xml, */*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"    ⚠ fetch failed: {url[:55]} → {e}")
        return None

def strip_tags(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()

# ─── PARSERS ───────────────────────────────────────────────────────────────────

def parse_rss(xml, src):
    jobs = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL):
        def tag(t):
            m = re.search(rf"<{t}[^>]*>(.*?)</{t}>", item, re.DOTALL | re.IGNORECASE)
            return strip_tags(m.group(1)) if m else ""
        title = tag("title")
        url   = (tag("link") or tag("guid")).strip()
        if title and url:
            jobs.append({"title": title, "company": tag("author") or tag("dc:creator") or src,
                         "url": url, "description": tag("description")[:600],
                         "source": src, "date": tag("pubDate")})
    return jobs

def parse_remotive(text, src):
    jobs = []
    try:
        for j in json.loads(text).get("jobs", []):
            jobs.append({"title": j.get("title",""), "company": j.get("company_name",""),
                         "url": j.get("url",""), "source": src,
                         "description": strip_tags(j.get("description",""))[:600],
                         "date": j.get("publication_date","")})
    except Exception as e:
        print(f"    ⚠ remotive: {e}")
    return jobs

def parse_remoteok(text, src):
    jobs = []
    try:
        for j in json.loads(text):
            if not isinstance(j, dict): continue
            jobs.append({"title": j.get("position",""), "company": j.get("company",""),
                         "url": j.get("url",""), "source": src,
                         "description": strip_tags(j.get("description",""))[:600],
                         "salary": j.get("salary",""), "date": j.get("date","")})
    except Exception as e:
        print(f"    ⚠ remoteok: {e}")
    return jobs

def parse_arbeitnow(text, src):
    jobs = []
    try:
        for j in json.loads(text).get("data", []):
            if not j.get("remote"): continue
            jobs.append({"title": j.get("title",""), "company": j.get("company_name",""),
                         "url": j.get("url",""), "source": src,
                         "description": strip_tags(j.get("description",""))[:600],
                         "date": j.get("created_at","")})
    except Exception as e:
        print(f"    ⚠ arbeitnow: {e}")
    return jobs

# ─── PASS 1: BROAD KEYWORD FILTER ──────────────────────────────────────────────

def broad_match(job):
    title = (job.get("title") or "").lower()
    # Hard exclude first
    if any(kw in title for kw in HARD_EXCLUDES):
        return False
    # Must match at least one broad title keyword
    return any(kw in title for kw in BROAD_TITLES)

def fetch_all():
    parsers = {"rss": parse_rss, "remotive": parse_remotive,
               "remoteok": parse_remoteok, "arbeitnow": parse_arbeitnow}
    all_jobs = []
    for src in SOURCES:
        print(f"  → {src['name']}")
        content = fetch_url(src["url"])
        if not content: continue
        raw  = parsers.get(src["type"], lambda c,n: [])(content, src["name"])
        hits = [j for j in raw if broad_match(j)]
        print(f"     {len(raw)} fetched · {len(hits)} broad matches")
        for h in hits: print(f"     ✓ {h['title']} @ {h['company']}")
        all_jobs.extend(hits)
    return all_jobs

# ─── PASS 2: CLAUDE AI SCORING ─────────────────────────────────────────────────

def score_with_claude(jobs):
    """
    Send each job to Claude for scoring. Returns jobs with ai_score added.
    Only called for jobs that passed broad_match filter.
    Cost: ~$0.001 per job scored.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  ⚠ No ANTHROPIC_API_KEY — falling back to keyword scoring")
        for job in jobs:
            job["score"] = keyword_score(job)
            job["score_method"] = "keyword"
        return jobs

    scored = []
    for job in jobs:
        title = job.get("title", "")
        company = job.get("company", "")
        desc = (job.get("description") or "")[:400]
        salary = job.get("salary", "")

        prompt = f"""Score this job for Travis Shorrock (0-10). Respond with ONLY a JSON object like:
{{"score": 7, "reason": "one sentence why"}}

{TRAVIS_PROFILE}

JOB:
Title: {title}
Company: {company}
Salary: {salary}
Description: {desc}"""

        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                data=json.dumps({
                    "model": "claude-haiku-4-5-20251001",  # cheapest model
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": prompt}]
                }).encode()
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())
            
            text = data["content"][0]["text"].strip()
            # Parse JSON response
            result = json.loads(re.search(r'\{.*\}', text, re.DOTALL).group())
            job["score"]        = int(result.get("score", 3))
            job["score_reason"] = result.get("reason", "")
            job["score_method"] = "claude"
            print(f"     scored {job['score']}/10 — {title[:50]}")
            time.sleep(0.3)  # gentle rate limiting

        except Exception as e:
            print(f"     ⚠ scoring failed for '{title}': {e}")
            job["score"]        = keyword_score(job)
            job["score_method"] = "keyword_fallback"

        scored.append(job)

    return scored

def keyword_score(job):
    """Fallback scoring when Claude API unavailable."""
    text  = f"{job.get('title','')} {job.get('description','')}".lower()
    score = 0
    for pts, kws in {
        3: ["creative director", "head of creative", "executive creative", "chief creative"],
        2: ["ai", "generative", "midjourney", "runway", "fully remote", "100% remote", "head of brand"],
        1: ["tech", "dtc", "startup", "saas", "remote-first", "async", "fractional"],
    }.items():
        for kw in kws:
            if kw in text: score += pts
    return min(score, 10)

# ─── DEDUP + PERSIST ───────────────────────────────────────────────────────────

def load_json(path, default):
    try: return json.loads(Path(path).read_text())
    except: return default

def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))

def process_jobs(raw_jobs):
    seen     = set(load_json(SEEN_FILE, []))
    existing = load_json(DATA_FILE, [])
    
    # Deduplicate
    new_jobs = []
    for job in raw_jobs:
        jid = make_id(job["title"], job["company"])
        if jid in seen: continue
        job["id"]    = jid
        job["added"] = datetime.now(timezone.utc).isoformat()
        seen.add(jid)
        new_jobs.append(job)

    print(f"\n  → {len(new_jobs)} new jobs to score with Claude...")
    
    # Score new jobs with Claude
    if new_jobs:
        new_jobs = score_with_claude(new_jobs)

    # Filter out low scores (0-2 = irrelevant)
    new_jobs = [j for j in new_jobs if (j.get("score") or 0) >= 3]
    print(f"  → {len(new_jobs)} jobs scored 3+ kept")

    all_jobs = (new_jobs + existing)[:300]
    save_json(DATA_FILE, all_jobs)
    save_json(SEEN_FILE, list(seen))
    save_json(META_FILE, {
        "updated":     datetime.now(timezone.utc).isoformat(),
        "new_count":   len(new_jobs),
        "total_count": len(all_jobs),
        "boards":      [{"name": n, "url": u} for n, u in MANUAL_BOARDS],
    })
    print(f"  ✓ {len(new_jobs)} new · {len(all_jobs)} total")
    return new_jobs, all_jobs

# ─── EMAIL ─────────────────────────────────────────────────────────────────────

def build_html(new_jobs, all_jobs):
    today = datetime.now().strftime("%A, %B %d, %Y")
    top   = sorted(new_jobs, key=lambda j: j.get("score",0), reverse=True)[:20]
    hot   = [j for j in top if (j.get("score",0)) >= 7]

    def row(j):
        score = j.get("score", 0)
        dots  = "●" * min(score,5) + "○" * max(0, 5-min(score,5))
        bg    = "#0d2218" if score >= 7 else "#0d1420"
        bdr   = "#00e5b4" if score >= 7 else "#1c2a3a"
        reason = j.get("score_reason","")
        reason_html = f'<br><span style="color:#445566;font-size:10px;font-style:italic;">{reason}</span>' if reason else ""
        sal   = f' · <span style="color:#f5a623;font-size:11px;">{j["salary"]}</span>' if j.get("salary") else ""
        return f"""<tr>
          <td style="padding:12px 10px;border-bottom:1px solid #1c2a3a;background:{bg};border-left:3px solid {bdr};">
            <a href="{j['url']}" style="color:#00e5b4;font-weight:700;font-size:14px;text-decoration:none;">{j['title']}</a><br>
            <span style="color:#778899;font-size:12px;">{j['company']} · {j['source']}</span>{sal}{reason_html}
          </td>
          <td style="padding:12px 10px;border-bottom:1px solid #1c2a3a;background:{bg};color:#f5a623;white-space:nowrap;font-size:13px;">{dots}</td>
        </tr>"""

    rows   = "".join(row(j) for j in top) if top else """<tr><td colspan="2" style="padding:32px;text-align:center;color:#445566;">No new matches today — check manual boards below.</td></tr>"""
    boards = "".join(f'<a href="{u}" style="display:inline-block;margin:3px;padding:5px 11px;background:#111927;color:#778899;border:1px solid #1c2a3a;border-radius:4px;font-size:11px;text-decoration:none;font-family:monospace;">{n}</a>' for n,u in MANUAL_BOARDS)

    return f"""<!DOCTYPE html><html><body style="margin:0;background:#080c14;font-family:Helvetica,Arial,sans-serif;color:#c8d8e8;">
<div style="max-width:660px;margin:0 auto;padding:28px 20px;">
  <div style="border-bottom:2px solid #00e5b4;padding-bottom:16px;margin-bottom:24px;">
    <div style="font-size:10px;letter-spacing:3px;color:#00e5b4;font-family:monospace;margin-bottom:8px;">DAILY JOB BRIEF · REMOTE ONLY · AI-SCORED</div>
    <div style="font-size:26px;font-weight:700;color:#fff;">Travis Shorrock</div>
    <div style="font-size:12px;color:#556677;margin-top:4px;">{today} · EST/CST · $150K+ USD</div>
  </div>
  <div style="display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap;">
    <span style="background:rgba(0,229,180,.15);border:1px solid rgba(0,229,180,.3);color:#00e5b4;padding:4px 12px;border-radius:20px;font-size:12px;">{len(new_jobs)} New Today</span>
    <span style="background:rgba(245,166,35,.12);border:1px solid rgba(245,166,35,.3);color:#f5a623;padding:4px 12px;border-radius:20px;font-size:12px;">{len(hot)} High Match (7+)</span>
    <span style="background:#111927;border:1px solid #1c2a3a;color:#556677;padding:4px 12px;border-radius:20px;font-size:12px;">{len(all_jobs)} Total Tracked</span>
  </div>
  <table style="width:100%;border-collapse:collapse;margin-bottom:28px;">{rows}</table>
  <div style="margin-bottom:24px;">
    <div style="font-size:10px;letter-spacing:2px;color:#445566;font-family:monospace;margin-bottom:10px;">CHECK THESE MANUALLY EVERY DAY</div>
    {boards}
  </div>
  <div style="border-top:1px solid #1c2a3a;padding-top:14px;font-size:10px;color:#334455;font-family:monospace;text-align:center;">Claude Haiku AI scoring · GitHub Actions · 7AM EST Mon–Fri</div>
</div></body></html>"""

def send_email(new_jobs, all_jobs):
    user = os.environ.get("SMTP_USER","")
    pwd  = os.environ.get("SMTP_PASS","")
    to   = os.environ.get("TO_EMAIL", user)
    if not user or not pwd:
        print("  ⚠ Email skipped (secrets not set)")
        return
    today = datetime.now().strftime("%b %d")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎯 Job Brief {today} — {len(new_jobs)} new · AI-scored"
    msg["From"] = user; msg["To"] = to
    msg.attach(MIMEText(build_html(new_jobs, all_jobs), "html"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls(); s.login(user, pwd); s.sendmail(user, to, msg.as_string())
        print(f"  ✓ Email → {to}")
    except Exception as e:
        print(f"  ⚠ Email failed: {e}")

# ─── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print(f"Travis Shorrock Job Scraper — AI Edition")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 55)

    print("\n[1/4] Fetching from all sources...")
    raw = fetch_all()
    print(f"\n  → {len(raw)} total broad matches across all sources")

    print("\n[2/4] Deduplicating + AI scoring...")
    new_jobs, all_jobs = process_jobs(raw)

    print("\n[3/4] Sending email...")
    send_email(new_jobs, all_jobs)

    print("\n✅ Done.")
