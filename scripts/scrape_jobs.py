#!/usr/bin/env python3
"""
Travis Shorrock — Automated Job Scraper
Runs daily via GitHub Actions (7AM EST, Mon-Fri).
Sources validated April 2026 — all confirmed returning live data.
"""

import json, os, re, hashlib, smtplib, urllib.request, urllib.parse
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ─── SEARCH CONFIG ──────────────────────────────────────────────────────────────

TITLE_KEYWORDS = [
    # ── Core CD roles ──────────────────────────────────────────────────────
    "creative director", "head of creative",
    "vp creative", "vp of creative", "vice president creative",
    "vp, creative", "svp creative", "evp creative",
    "director of creative", "executive creative director",
    "group creative director", "global creative director",
    "chief creative officer", "cco",
    "fractional creative director", "ai creative director",
    "associate creative director",
    # ── Brand / design leadership ──────────────────────────────────────────
    "head of brand", "head of brand design", "head of brand creative",
    "brand creative director", "brand director",
    "vp brand", "director of brand",
    # ── AI / production ────────────────────────────────────────────────────
    "ai creative", "ai director", "ai producer",
    "ai production director", "ai content director",
    "generative creative",
    # ── Adjacent — senior roles Travis would crush ─────────────────────────
    "chief marketing officer", "cmo",
    "vp marketing", "vp of marketing",
    "head of marketing",
    "creative strategist",               # senior strategy + creative
    "head of content",                   # content leadership
    "vp content", "chief content officer",
    "head of growth creative",
    "fractional cdo",                    # fractional chief design officer
]

DESC_KEYWORDS = [
    "creative director", "head of creative", "head of brand",
]

# Hard title excludes
TITLE_EXCLUDES = [
    # UX / Product — not Travis's world
    "ux director", "ux designer", "product designer",
    "ui director", "ui/ux", "ux/ui",
    "design director",
    "user experience director",
    # Technical
    "technical director", "engineering director",
    "data director", "analytics director",
    # Sales / account roles — completely wrong
    "account executive", "account manager", "account director",
    "account management", "technical account",
    "sales director", "sales manager", "sales executive",
    "business development", "revenue",
    # Finance / Ops
    "finance director", "operations director",
    "medical director", "clinical director",
    # Junior
    "junior", "intern", "entry level", "coordinator",
    "assistant creative",
]

EXCLUDE_KEYWORDS = [
    "hybrid only", "on-site required", "must be in office",
    "relocation required", "pst hours only",
    "$40/hr", "$50/hr", "$60,000", "$70,000", "$75,000", "$80,000",
    "must be based in", "must reside in",
]

DATA_FILE  = Path("data/jobs.json")
SEEN_FILE  = Path("data/seen_ids.json")
META_FILE  = Path("data/meta.json")

# ─── SOURCES (all validated live as of April 2026) ────────────────────────────

SOURCES = [
    {
        "name": "We Work Remotely",
        "url":  "https://weworkremotely.com/remote-jobs.rss",
        "type": "rss",
    },
    {
        "name": "Remote OK Design",
        "url":  "https://remoteok.com/remote-design-jobs.json",
        "type": "remoteok",
    },
    {
        "name": "Remote OK Marketing",
        "url":  "https://remoteok.com/remote-marketing-jobs.json",
        "type": "remoteok",
    },
    {
        "name": "Himalayas",
        "url":  "https://himalayas.app/jobs/rss",
        "type": "rss",
    },
    {
        "name": "Remotive",
        "url":  "https://remotive.com/api/remote-jobs?limit=100",
        "type": "remotive",
    },
    {
        "name": "Jobicy",
        "url":  "https://jobicy.com/?feed=job_feed&job_types=full-time",
        "type": "rss",
    },
    {
        "name": "Arbeitnow",
        "url":  "https://www.arbeitnow.com/api/job-board-api",
        "type": "arbeitnow",
    },
    {
        "name": "Authentic Jobs",
        "url":  "https://authenticjobs.com/feed/",
        "type": "rss",
    },
]

# ─── MANUAL BOARDS (deep-linked in every email + dashboard) ──────────────────

MANUAL_BOARDS = [
    ("LinkedIn — CD Remote",         "https://www.linkedin.com/jobs/search/?keywords=creative+director+AI&f_WT=2&f_TPR=r86400&location=United+States"),
    ("LinkedIn — Head of Creative",  "https://www.linkedin.com/jobs/search/?keywords=head+of+creative+remote&f_WT=2&f_TPR=r86400"),
    ("Built In — Remote CD",         "https://builtin.com/jobs/remote/design-ux/search/creative-director"),
    ("Remote Rocketship",            "https://www.remoterocketship.com/jobs/creative-director/"),
    ("FlexJobs CD",                  "https://www.flexjobs.com/remote-jobs/creative-director"),
    ("Wellfound (AngelList)",        "https://wellfound.com/role/r/creative-director"),
    ("JustRemote Design",            "https://justremote.co/remote-design-jobs"),
    ("Working Nomads Design",        "https://www.workingnomads.com/jobs?category=design"),
    ("Roboflow — CD Open Role",      "https://jobs.ashbyhq.com/roboflow"),
    ("Superside — AI CD Role",       "https://careers.superside.com/jobs/ai-creative-director"),
    ("Curious Refuge AI Jobs",       "https://curiousrefuge.com/ai-jobs-board"),
    ("The AI Job Board",             "https://theaijobboard.com"),
    ("Contra — Fractional",          "https://contra.com/opportunities"),
    ("Toptal Creative",              "https://www.toptal.com/creative"),
    ("Hubstaff Talent",              "https://hubstafftalent.net/search/jobs?q=creative+director"),
    ("Daily Remote",                 "https://dailyremote.com/remote-creative-director-jobs"),
    ("Remote Circle",                "https://remotecircle.com"),
    ("Remote.co",                    "https://remote.co/remote-jobs/design/"),
]

# ─── HELPERS ──────────────────────────────────────────────────────────────────

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
        print(f"    ⚠ {url[:55]} → {e}")
        return None

def strip_tags(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()

# ─── PARSERS ──────────────────────────────────────────────────────────────────

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
                         "url": url, "description": tag("description")[:500],
                         "source": src, "date": tag("pubDate")})
    return jobs

def parse_remotive(text, src):
    jobs = []
    try:
        for j in json.loads(text).get("jobs", []):
            jobs.append({"title": j.get("title",""), "company": j.get("company_name",""),
                         "url": j.get("url",""), "source": src,
                         "description": strip_tags(j.get("description",""))[:500],
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
                         "description": strip_tags(j.get("description",""))[:500],
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
                         "description": strip_tags(j.get("description",""))[:500],
                         "date": j.get("created_at","")})
    except Exception as e:
        print(f"    ⚠ arbeitnow: {e}")
    return jobs

# ─── RELEVANCE + SCORING ──────────────────────────────────────────────────────

def is_relevant(job):
    title = (job.get("title") or "").lower()
    desc  = (job.get("description") or "").lower()

    # Hard title excludes — skip entirely
    if any(kw in title for kw in TITLE_EXCLUDES):
        return False

    # Hard keyword excludes anywhere
    if any(kw in title + " " + desc for kw in EXCLUDE_KEYWORDS):
        return False

    # Title match = confirmed relevant
    if any(kw in title for kw in TITLE_KEYWORDS):
        return True

    # Description match = weaker signal — only if title is reasonably senior
    if any(kw in desc for kw in DESC_KEYWORDS):
        # Extra check: description-only matches must not look junior
        if any(j in title for j in ["junior","associate","assistant","coordinator","intern"]):
            return False
        return True

    return False

def score_job(job):
    text  = f"{job.get('title','')} {job.get('description','')}".lower()
    score = 0
    for pts, kws in {
        3: ["creative director", "head of creative", "ai creative director", "executive creative"],
        2: ["ai", "generative", "midjourney", "runway", "fully remote", "100% remote", "head of brand"],
        1: ["tech", "dtc", "startup", "saas", "remote-first", "async", "film", "streaming", "fractional"],
    }.items():
        for kw in kws:
            if kw in text: score += pts
    return min(score, 10)

# ─── FETCH ALL ────────────────────────────────────────────────────────────────

def fetch_all_jobs():
    all_jobs = []
    parsers = {"rss": parse_rss, "remotive": parse_remotive,
               "remoteok": parse_remoteok, "arbeitnow": parse_arbeitnow}
    for src in SOURCES:
        print(f"  → {src['name']}")
        content = fetch_url(src["url"])
        if not content: continue
        raw  = parsers.get(src["type"], lambda c,n: [])(content, src["name"])
        hits = [j for j in raw if is_relevant(j)]
        print(f"     {len(raw)} fetched · {len(hits)} matched")
        for h in hits:
            print(f"     ✓ {h['title']} @ {h['company']}")
        all_jobs.extend(hits)
    return all_jobs

# ─── PERSIST ──────────────────────────────────────────────────────────────────

def load_json(path, default):
    try: return json.loads(Path(path).read_text())
    except: return default

def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))

def process_jobs(raw_jobs):
    seen     = set(load_json(SEEN_FILE, []))
    existing = load_json(DATA_FILE, [])
    new_jobs = []
    for job in raw_jobs:
        jid = make_id(job["title"], job["company"])
        if jid in seen: continue
        job.update({"id": jid, "score": score_job(job), "added": datetime.now(timezone.utc).isoformat()})
        seen.add(jid)
        new_jobs.append(job)
    all_jobs = (new_jobs + existing)[:300]
    save_json(DATA_FILE, all_jobs)
    save_json(SEEN_FILE, list(seen))
    save_json(META_FILE, {
        "updated": datetime.now(timezone.utc).isoformat(),
        "new_count": len(new_jobs), "total_count": len(all_jobs),
        "boards": [{"name": n, "url": u} for n, u in MANUAL_BOARDS],
    })
    print(f"\n  ✓ {len(new_jobs)} new · {len(all_jobs)} total")
    return new_jobs, all_jobs

# ─── EMAIL ────────────────────────────────────────────────────────────────────

def build_html(new_jobs, all_jobs):
    today = datetime.now().strftime("%A, %B %d, %Y")
    top   = sorted(new_jobs, key=lambda j: j["score"], reverse=True)[:20]
    hot   = [j for j in top if j["score"] >= 5]

    def row(j):
        dots = "●" * min(j["score"],5) + "○" * max(0, 5-min(j["score"],5))
        bg   = "#0d2218" if j["score"] >= 5 else "#0d1420"
        bdr  = "#00c9a7" if j["score"] >= 5 else "#1c2a3a"
        sal  = f' · <span style="color:#f5a623;font-size:11px;">{j["salary"]}</span>' if j.get("salary") else ""
        return f"""<tr>
          <td style="padding:12px 10px;border-bottom:1px solid #1c2a3a;background:{bg};border-left:3px solid {bdr};">
            <a href="{j['url']}" style="color:#00c9a7;font-weight:600;font-size:14px;text-decoration:none;">{j['title']}</a><br>
            <span style="color:#778899;font-size:12px;">{j['company']} · {j['source']}</span>{sal}
          </td>
          <td style="padding:12px 10px;border-bottom:1px solid #1c2a3a;background:{bg};color:#f5a623;white-space:nowrap;">{dots}</td>
        </tr>"""

    rows = "".join(row(j) for j in top) if top else """<tr><td colspan="2" style="padding:32px;text-align:center;color:#445566;">No new matches today — check the manual boards below.</td></tr>"""
    boards = "".join(f'<a href="{u}" style="display:inline-block;margin:3px;padding:4px 10px;background:#111927;color:#778899;border:1px solid #1c2a3a;border-radius:3px;font-size:11px;text-decoration:none;font-family:monospace;">{n}</a>' for n,u in MANUAL_BOARDS)

    return f"""<!DOCTYPE html><html><body style="margin:0;background:#080c14;font-family:Helvetica,Arial,sans-serif;color:#c8d8e8;">
<div style="max-width:640px;margin:0 auto;padding:28px 20px;">
  <div style="border-bottom:2px solid #00c9a7;padding-bottom:16px;margin-bottom:24px;">
    <div style="font-size:10px;letter-spacing:3px;color:#00c9a7;font-family:monospace;margin-bottom:6px;">DAILY JOB BRIEF · REMOTE ONLY</div>
    <div style="font-size:26px;font-weight:700;color:#fff;">Travis Shorrock</div>
    <div style="font-size:12px;color:#556677;margin-top:4px;">{today} · EST/CST · $150K+ USD</div>
  </div>
  <div style="display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap;">
    <span style="background:rgba(0,201,167,.15);border:1px solid rgba(0,201,167,.3);color:#00c9a7;padding:4px 12px;border-radius:20px;font-size:12px;">{len(new_jobs)} New Today</span>
    <span style="background:rgba(245,166,35,.12);border:1px solid rgba(245,166,35,.3);color:#f5a623;padding:4px 12px;border-radius:20px;font-size:12px;">{len(hot)} High Match</span>
    <span style="background:#111927;border:1px solid #1c2a3a;color:#556677;padding:4px 12px;border-radius:20px;font-size:12px;">{len(all_jobs)} Total Tracked</span>
  </div>
  <table style="width:100%;border-collapse:collapse;margin-bottom:28px;">{rows}</table>
  <div style="margin-bottom:24px;">
    <div style="font-size:10px;letter-spacing:2px;color:#445566;font-family:monospace;margin-bottom:10px;">CHECK THESE MANUALLY</div>
    {boards}
  </div>
  <div style="border-top:1px solid #1c2a3a;padding-top:14px;font-size:10px;color:#334455;font-family:monospace;text-align:center;">GitHub Actions · 7AM EST Mon–Fri · $0/month</div>
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
    msg["Subject"] = f"🎯 Job Brief {today} — {len(new_jobs)} new {'role' if len(new_jobs)==1 else 'roles'}"
    msg["From"] = user; msg["To"] = to
    msg.attach(MIMEText(build_html(new_jobs, all_jobs), "html"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls(); s.login(user, pwd); s.sendmail(user, to, msg.as_string())
        print(f"  ✓ Email sent → {to}")
    except Exception as e:
        print(f"  ⚠ Email failed: {e}")

# ─── RUN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print(f"Travis Shorrock Job Scraper")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 50)
    print("\n[1/3] Fetching...")
    raw = fetch_all_jobs()
    print("\n[2/3] Processing...")
    new_jobs, all_jobs = process_jobs(raw)
    print("\n[3/3] Emailing...")
    send_email(new_jobs, all_jobs)
    print("\n✅ Done.")
