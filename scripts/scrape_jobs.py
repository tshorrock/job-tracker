#!/usr/bin/env python3
"""
Travis Shorrock — AI-Powered Job Scraper
Three category lanes:
  CORE     — CD, ACD, Head of Creative, Brand Director, VP Creative
  ADJACENT — Graphic Design Director, Creative Strategist, CMO, AI Director, etc.
  WILDCARD — Unusual remote roles Claude thinks Travis would find interesting
"""

import json, os, re, hashlib, smtplib, urllib.request, time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

DATA_FILE  = Path("data/jobs.json")
SEEN_FILE  = Path("data/seen_ids.json")
META_FILE  = Path("data/meta.json")

# ─── BROAD TITLE FILTER (wide net — Claude handles precision) ──────────────────

CORE_TITLES = [
    "creative director", "head of creative", "associate creative director",
    "group creative director", "executive creative director",
    "vp creative", "vp of creative", "svp creative", "evp creative",
    "chief creative officer", "cco",
    "fractional creative director", "ai creative director",
    "head of brand", "head of brand design", "head of brand creative",
    "brand creative director", "brand director",
    "vp brand", "director of brand", "director of creative",
    "global creative director",
    # Design leadership (brand/visual — not UX/UI)
    "head of design", "vp of design", "vp design",
    "director of design", "svp design", "evp design",
    "chief design officer", "cdo",
    "head of visual design", "head of graphic design",
]

ADJACENT_TITLES = [
    "design director", "graphic design director",
    "creative strategist", "brand strategist",
    "head of marketing", "vp marketing", "vp of marketing",
    "chief marketing officer", "cmo",
    "head of content", "vp content", "chief content officer",
    "ai director", "ai creative", "ai producer", "ai production director",
    "generative creative", "fractional cdo",
    "creative lead", "head of growth creative",
]

# Wildcard — no pre-filter, Claude evaluates everything that doesn't match core/adjacent
# Just add some extra titles that could be interesting
WILDCARD_TITLES = [
    "chief storyteller", "head of culture", "creative technologist",
    "experience director", "head of innovation", "brand experience",
    "editorial director", "head of studio", "studio director",
    "narrative director", "head of partnerships", "director of community",
    "creative producer", "content director", "director of strategy",
    # AI/tech creative roles
    "ai filmmaker", "generative", "prompt engineer", "creative ai",
    "head of ai", "ai content", "creative automation",
    # Streaming/entertainment
    "showrunner", "head of original", "creative development",
]

ALL_BROAD = CORE_TITLES + ADJACENT_TITLES + WILDCARD_TITLES

# Hard excludes — never pass through regardless
HARD_EXCLUDES = [
    "engineer", "engineering", "developer", "software", "backend",
    "frontend", "fullstack", "devops", "data scientist", "machine learning",
    "security engineer", "platform engineer",
    "product designer", "ux designer", "ui designer", "ui/ux",
    "user experience designer", "interaction designer",
    "video editor", "video producer", "motion designer", "animator",
    "cinematographer", "videographer",
    "account executive", "account manager", "sales director", "sales manager",
    "business development", "account management", "technical account",
    "finance director", "operations director", "medical director",
    "clinical director", "data director",
    "junior", "intern", "entry level", "coordinator", "assistant creative",
]

# ─── TRAVIS PROFILE FOR CLAUDE ─────────────────────────────────────────────────

TRAVIS_PROFILE = """
You are scoring remote job postings for Travis Shorrock, a senior Creative Director with 25+ years experience.

SEARCH CRITERIA — score based on how well the job meets ALL of these:

MUST-HAVES (failure on any = score 0-2 max):
- 100% remote. No on-site, no mandatory office days.
- Senior level only: Creative Director, Executive Creative Director, Group Creative Director,
  VP Creative, Head of Creative, ACD (senior IC), Head of Brand, VP Design, Head of Design,
  Director of Design, Chief Design Officer — or equivalent senior roles.
  IMPORTANT: Design leadership is fine IF it's brand, visual, or graphic design.
  UX Director, UI Director, Product Designer, Head of UX = score 0. Not his world.
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

IMPORTANT: All jobs come from remote-only job boards so assume remote unless explicitly stated otherwise.

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

# ─── SOURCES ──────────────────────────────────────────────────────────────────

SOURCES = [
    {"name": "We Work Remotely",    "url": "https://weworkremotely.com/remote-jobs.rss",                   "type": "rss"},
    {"name": "WWR Design",          "url": "https://weworkremotely.com/categories/remote-design-jobs.rss", "type": "rss"},
    {"name": "WWR Marketing",       "url": "https://weworkremotely.com/categories/remote-marketing-jobs.rss","type": "rss"},
    {"name": "Remote OK Design",    "url": "https://remoteok.com/remote-design-jobs.json",                 "type": "remoteok"},
    {"name": "Remote OK Marketing", "url": "https://remoteok.com/remote-marketing-jobs.json",              "type": "remoteok"},
    {"name": "Remote OK Exec",      "url": "https://remoteok.com/remote-exec-jobs.json",                   "type": "remoteok"},
    {"name": "Himalayas",           "url": "https://himalayas.app/jobs/rss",                               "type": "rss"},
    {"name": "Remotive",            "url": "https://remotive.com/api/remote-jobs?limit=100",               "type": "remotive"},
    {"name": "Jobicy",              "url": "https://jobicy.com/?feed=job_feed&job_types=full-time",        "type": "rss"},
    {"name": "Arbeitnow",           "url": "https://www.arbeitnow.com/api/job-board-api",                  "type": "arbeitnow"},
    {"name": "Authentic Jobs",      "url": "https://authenticjobs.com/feed/",                              "type": "rss"},
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

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def make_id(title, company):
    # Normalize aggressively so same job from different sources = same ID
    t = re.sub(r'[^a-z0-9]', '', (title or '').lower())[:30]
    c = re.sub(r'[^a-z0-9]', '', (company or '').lower())[:20]
    return hashlib.md5(f"{t}{c}".encode()).hexdigest()[:12]

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

def strip_tags(t):
    return re.sub(r"<[^>]+>", "", t or "").strip()

# ─── PARSERS ──────────────────────────────────────────────────────────────────

def parse_rss(xml, src):
    jobs = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL):
        def tag(t):
            m = re.search(rf"<{t}[^>]*>(.*?)</{t}>", item, re.DOTALL | re.IGNORECASE)
            return strip_tags(m.group(1)) if m else ""
        title = tag("title"); url = (tag("link") or tag("guid")).strip()
        if title and url:
            jobs.append({"title": title, "company": tag("author") or tag("dc:creator") or src,
                         "url": url, "description": tag("description")[:600], "source": src})
    return jobs

def parse_remotive(text, src):
    jobs = []
    try:
        for j in json.loads(text).get("jobs", []):
            jobs.append({"title": j.get("title",""), "company": j.get("company_name",""),
                         "url": j.get("url",""), "source": src,
                         "description": strip_tags(j.get("description",""))[:600]})
    except: pass
    return jobs

def parse_remoteok(text, src):
    jobs = []
    try:
        for j in json.loads(text):
            if not isinstance(j, dict): continue
            jobs.append({"title": j.get("position",""), "company": j.get("company",""),
                         "url": j.get("url",""), "source": src,
                         "salary": j.get("salary",""),
                         "description": strip_tags(j.get("description",""))[:600]})
    except: pass
    return jobs

def parse_arbeitnow(text, src):
    jobs = []
    try:
        for j in json.loads(text).get("data", []):
            if not j.get("remote"): continue
            jobs.append({"title": j.get("title",""), "company": j.get("company_name",""),
                         "url": j.get("url",""), "source": src,
                         "description": strip_tags(j.get("description",""))[:600]})
    except: pass
    return jobs

# ─── BROAD FILTER ─────────────────────────────────────────────────────────────

def broad_match(job):
    title = (job.get("title") or "").lower()
    desc  = (job.get("description") or "").lower()
    if any(kw in title for kw in HARD_EXCLUDES):
        return False
    if "remote" not in title and "remote" not in desc:
        return False
    return any(kw in title for kw in ALL_BROAD)

# ─── CLAUDE SCORING ───────────────────────────────────────────────────────────

def score_with_claude(jobs):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  ⚠ No ANTHROPIC_API_KEY — keyword fallback")
        for job in jobs:
            job["score"] = keyword_score(job)
            job["category"] = guess_category(job)
            job["score_method"] = "keyword"
        return jobs

    scored = []
    for job in jobs:
        title   = job.get("title", "")
        company = job.get("company", "")
        desc    = (job.get("description") or "")[:400]
        salary  = job.get("salary", "")

        prompt = f"""{TRAVIS_PROFILE}

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
            job["category"]     = result.get("category", guess_category(job)).upper()
            job["score_reason"] = result.get("reason", "")
            job["score_method"] = "claude"
            print(f"     [{job['score']}/10] [{job['category']}] {title[:50]}")
            time.sleep(0.3)

        except Exception as e:
            print(f"     ⚠ scoring failed: {e}")
            job["score"]        = keyword_score(job)
            job["category"]     = guess_category(job)
            job["score_method"] = "keyword_fallback"

        scored.append(job)
    return scored

WILDCARD_PROFILE = """
You are curating the WILDCARD column of a job dashboard for Travis Shorrock — a 25yr Creative 
Director with deep AI skills, moving to Costa Rica. This column is called "Wildcard" for a reason.

The vibe: jobs that would make him go "huh, that's interesting" or "wait, that's actually a job?"
Think: surprising, fun, low-pressure, creative freedom, good story at a dinner party.

Examples of HIGH scores (7-10):
- AI prompt artist for a gaming studio
- Creative director for a cannabis brand
- Voiceover work for an audiobook platform
- Narrative designer for an indie game
- Travel content creator for a luxury yacht company
- Comedy writer for a tech startup
- Head of culture at a weird startup
- Mystery shopper for luxury brands (remote version)
- Creative consultant for a celebrity
- Documentary project creative lead
- Trend forecaster for a fashion brand
- Illustrator/typographer for a niche publisher
- Sound designer for an app
- Creative director for a surf or outdoor brand
- Spiritual/wellness content creative lead
- Kids content creative director
- Esports creative lead
- Metaverse experience designer

Examples of LOW scores (1-3):
- Anything corporate, boring, or stressful
- Anything requiring skills he doesn't have (coding, medical, legal)
- High accountability senior roles (those belong in Core)

Score 0 for:
- On-site required
- MLM, cold calling, data entry, scams
- Pure commission sales

The question to ask yourself: would Travis smile reading this job description?
If yes → high score. If "meh, another job" → low score.

Always assign category: WILDCARD

Respond ONLY with JSON: {"score": 8, "category": "WILDCARD", "reason": "one punchy sentence explaining the smile factor"}
"""

# Wildcard-specific sources — broader, weirder feeds
WILDCARD_SOURCES = [
    {"name": "Remote OK",      "url": "https://remoteok.com/remote-jobs.json",              "type": "remoteok"},
    {"name": "WWR All",        "url": "https://weworkremotely.com/remote-jobs.rss",          "type": "rss"},
    {"name": "Remotive All",   "url": "https://remotive.com/api/remote-jobs?limit=100",      "type": "remotive"},
    {"name": "Arbeitnow",      "url": "https://www.arbeitnow.com/api/job-board-api",         "type": "arbeitnow"},
]

# Wildcard broad filter — anything creative, content, AI, media, entertainment
WILDCARD_BROAD = [
    # Voice / performance
    "voiceover", "voice actor", "narrator", "on-camera", "presenter", "host",
    "podcast host", "video host", "live host", "streamer", "emcee",
    # AI creative
    "prompt engineer", "ai artist", "ai filmmaker", "generative artist",
    "ai trainer", "ai evaluator", "ai tester", "beta tester",
    # Entertainment / gaming / animation
    "game designer", "game writer", "narrative designer", "level designer",
    "animation director", "comic", "graphic novelist", "toy designer",
    "showrunner", "story editor", "writers room", "script",
    # Unusual senior creative
    "chief storyteller", "head of culture", "creative futurist",
    "imagineer", "creative coach", "creative educator",
    "artist in residence", "creative entrepreneur",
    # Content / lifestyle / travel
    "travel writer", "travel content", "lifestyle creator",
    "food writer", "culture writer", "creative writer",
    # Community / facilitation
    "community host", "creative facilitator", "workshop",
    # Random fun stuff
    "futurist", "trend forecaster", "cool hunter",
    "mystery shopper", "brand ambassador", "talent scout",
    "casting", "creative producer", "production designer",
    "set designer", "prop designer", "costume designer",
    "museum", "gallery", "curator", "archivist",
    "documentary", "photographer", "photo editor",
    "illustrator", "typographer", "motion", "vfx",
    "sound designer", "music supervisor", "creative director games",
    "esports", "metaverse", "virtual", "avatar",
    "influencer", "creator economy", "substack",
    "cookbook", "food stylist", "recipe developer",
    "sommelier", "spirits", "cannabis creative",
    "surf", "ski", "outdoor", "adventure", "wellness",
    "yoga", "meditation", "mindfulness content",
    "astrology", "tarot", "spiritual",
    "tattoo", "fashion", "streetwear", "sneaker",
    "luxury", "yacht", "private aviation",
    "comedy writer", "joke writer", "humor",
    "children", "kids content", "family",
    "pet", "animal", "wildlife",
    "space", "science communicator", "nerd",
]

# Wildcard hard excludes
WILDCARD_EXCLUDES = [
    "data entry", "cold call", "commission only", "mlm",
    "pyramid", "insurance agent", "mortgage", "real estate agent",
    "engineer", "developer", "software", "devops",
    "junior", "intern", "entry level",
]

def wildcard_match(job):
    title = (job.get("title") or "").lower()
    desc  = (job.get("description") or "").lower()

    # Hard exclude — if it would qualify for Core or Adjacent, it's NOT a wildcard
    if broad_match(job):
        return False

    # Exclude typical corporate/advertising/marketing roles
    NOT_WILDCARD = [
        "product designer", "ux designer", "ui designer",
        "product manager", "product marketing", "marketing manager",
        "marketing director", "account manager", "account executive",
        "sales", "business development", "engineer", "developer",
        "data analyst", "data scientist", "finance", "operations",
        "recruiter", "hr manager", "customer success",
        "seo", "sem", "paid media", "growth hacker",
    ]
    if any(kw in title for kw in NOT_WILDCARD):
        return False

    if any(kw in title for kw in WILDCARD_EXCLUDES):
        return False

    # Must be remote
    if "remote" not in title and "remote" not in desc:
        return False

    # Must match something genuinely interesting/unusual
    return any(kw in title or kw in desc[:300] for kw in WILDCARD_BROAD)

def fetch_wildcards():
    """Separate pass for wildcard jobs — broader sources, different filter."""
    parsers = {"rss": parse_rss, "remotive": parse_remotive,
               "remoteok": parse_remoteok, "arbeitnow": parse_arbeitnow}
    all_jobs = []
    for src in WILDCARD_SOURCES:
        print(f"  [wildcard] → {src['name']}")
        content = fetch_url(src["url"])
        if not content: continue
        raw  = parsers.get(src["type"], lambda c,n: [])(content, src["name"])
        # Exclude anything already caught by main filter
        hits = [j for j in raw if wildcard_match(j) and not broad_match(j)]
        if hits:
            print(f"     {len(hits)} wildcard candidates")
            for h in hits[:5]: print(f"     ? {h['title'][:55]} @ {h['company']}")
        all_jobs.extend(hits)
    return all_jobs

def score_wildcards(jobs):
    """Score wildcard jobs with the fun/quirky prompt."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        for job in jobs:
            job["score"] = 4
            job["category"] = "WILDCARD"
            job["score_method"] = "keyword"
        return jobs

    scored = []
    for job in jobs:
        prompt = f"""{WILDCARD_PROFILE}

JOB:
Title: {job.get('title','')}
Company: {job.get('company','')}
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
            job["score"]        = int(result.get("score", 4))
            job["category"]     = "WILDCARD"
            job["score_reason"] = result.get("reason", "")
            job["score_method"] = "claude"
            if job["score"] >= 5:
                print(f"     🃏 {job['score']}/10 — {job['title'][:50]}")
                print(f"        {job['score_reason'][:80]}")
            time.sleep(0.3)
        except Exception as e:
            print(f"     ⚠ wildcard scoring failed: {e}")
            job["score"] = 3
            job["category"] = "WILDCARD"
            job["score_method"] = "keyword_fallback"
        scored.append(job)
    return scored
    text = f"{job.get('title','')} {job.get('description','')}".lower()
    score = 0
    for pts, kws in {
        3: ["creative director", "head of creative", "executive creative"],
        2: ["ai", "generative", "midjourney", "runway", "fully remote", "head of brand"],
        1: ["tech", "dtc", "startup", "remote-first", "fractional"],
    }.items():
        for kw in kws:
            if kw in text: score += pts
    return min(score, 10)

def guess_category(job):
    title = (job.get("title") or "").lower()
    if any(kw in title for kw in CORE_TITLES):
        return "CORE"
    if any(kw in title for kw in ADJACENT_TITLES):
        return "ADJACENT"
    return "WILDCARD"

# ─── FETCH ────────────────────────────────────────────────────────────────────

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
        print(f"     {len(raw)} fetched · {len(hits)} matched")
        for h in hits: print(f"     ✓ {h['title'][:55]} @ {h['company']}")
        all_jobs.extend(hits)
    return all_jobs

# ─── PERSIST ──────────────────────────────────────────────────────────────────

def load_json(path, default):
    try: return json.loads(Path(path).read_text())
    except: return default

def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str))

def process_jobs(raw_jobs, wild_jobs=None):
    seen     = set(load_json(SEEN_FILE, []))
    existing = load_json(DATA_FILE, [])

    # Deduplicate core/adjacent
    new_jobs = []
    for job in raw_jobs:
        jid = make_id(job["title"], job["company"])
        if jid in seen: continue
        job["id"]    = jid
        job["added"] = datetime.now(timezone.utc).isoformat()
        seen.add(jid)
        new_jobs.append(job)

    # Deduplicate wildcards
    new_wilds = []
    for job in (wild_jobs or []):
        jid = make_id(job["title"], job["company"])
        if jid in seen: continue
        job["id"]    = jid
        job["added"] = datetime.now(timezone.utc).isoformat()
        seen.add(jid)
        new_wilds.append(job)

    print(f"\n  → {len(new_jobs)} new core/adjacent to score...")
    if new_jobs:
        new_jobs = score_with_claude(new_jobs)

    print(f"\n  → {len(new_wilds)} new wildcards to score...")
    if new_wilds:
        new_wilds = score_wildcards(new_wilds)

    # Log scores
    for j in new_jobs + new_wilds:
        print(f"     SCORE {j.get('score',0)}/10 [{j.get('category','?')}] {j.get('title','')[:50]}")

    # Keep scored 1+
    all_new = [j for j in (new_jobs + new_wilds) if (j.get("score") or 0) >= 1]
    print(f"  → {len(all_new)} total kept")

    all_jobs = (all_new + existing)[:300]
    save_json(DATA_FILE, all_jobs)
    save_json(SEEN_FILE, list(seen))
    save_json(META_FILE, {
        "updated":     datetime.now(timezone.utc).isoformat(),
        "new_count":   len(all_new),
        "total_count": len(all_jobs),
        "boards":      [{"name": n, "url": u} for n, u in MANUAL_BOARDS],
    })
    print(f"  ✓ {len(all_new)} new · {len(all_jobs)} total")
    return all_new, all_jobs

# ─── EMAIL ────────────────────────────────────────────────────────────────────

def build_html(new_jobs, all_jobs):
    today = datetime.now().strftime("%A, %B %d, %Y")
    core     = [j for j in new_jobs if j.get("category") == "CORE"]
    adjacent = [j for j in new_jobs if j.get("category") == "ADJACENT"]
    wildcard = [j for j in new_jobs if j.get("category") == "WILDCARD"]

    def row(j, color):
        sc   = j.get("score", 0)
        dots = "●" * min(sc,5) + "○" * max(0,5-min(sc,5))
        reason = j.get("score_reason","")
        return f"""<tr>
          <td style="padding:12px 10px;border-bottom:1px solid #1c2a3a;">
            <a href="{j['url']}" style="color:{color};font-weight:700;font-size:14px;text-decoration:none;">{j['title']}</a><br>
            <span style="color:#778899;font-size:12px;">{j['company']} · {j['source']}</span>
            {f'<br><span style="color:#445566;font-size:11px;font-style:italic;">{reason}</span>' if reason else ''}
          </td>
          <td style="padding:12px 10px;border-bottom:1px solid #1c2a3a;color:{color};white-space:nowrap;">{dots}</td>
        </tr>"""

    def section(title, jobs, color):
        if not jobs: return ""
        rows = "".join(row(j, color) for j in sorted(jobs, key=lambda j: j.get("score",0), reverse=True)[:10])
        return f"""
        <div style="margin-bottom:28px;">
          <div style="font-size:10px;letter-spacing:3px;color:{color};font-family:monospace;text-transform:uppercase;margin-bottom:12px;">{title}</div>
          <table style="width:100%;border-collapse:collapse;">{rows}</table>
        </div>"""

    boards = "".join(f'<a href="{u}" style="display:inline-block;margin:3px;padding:4px 10px;background:#111927;color:#778899;border:1px solid #1c2a3a;border-radius:4px;font-size:11px;text-decoration:none;font-family:monospace;">{n}</a>' for n,u in MANUAL_BOARDS)

    return f"""<!DOCTYPE html><html><body style="margin:0;background:#080c14;font-family:Helvetica,Arial,sans-serif;color:#c8d8e8;">
<div style="max-width:660px;margin:0 auto;padding:28px 20px;">
  <div style="border-bottom:2px solid #d4f244;padding-bottom:16px;margin-bottom:24px;">
    <div style="font-size:10px;letter-spacing:3px;color:#d4f244;font-family:monospace;margin-bottom:8px;">DAILY JOB BRIEF · AI-SCORED · REMOTE ONLY</div>
    <div style="font-size:26px;font-weight:700;color:#fff;">Travis Shorrock</div>
    <div style="font-size:12px;color:#556677;margin-top:4px;">{today}</div>
  </div>
  <div style="display:flex;gap:10px;margin-bottom:24px;flex-wrap:wrap;">
    <span style="background:rgba(212,242,68,.12);border:1px solid rgba(212,242,68,.3);color:#d4f244;padding:4px 12px;border-radius:20px;font-size:12px;">{len(core)} Core</span>
    <span style="background:rgba(240,165,0,.12);border:1px solid rgba(240,165,0,.3);color:#f0a500;padding:4px 12px;border-radius:20px;font-size:12px;">{len(adjacent)} Adjacent</span>
    <span style="background:rgba(167,139,250,.12);border:1px solid rgba(167,139,250,.3);color:#a78bfa;padding:4px 12px;border-radius:20px;font-size:12px;">{len(wildcard)} Wildcard</span>
  </div>
  {section("Core Roles", core, "#d4f244")}
  {section("Adjacent Roles", adjacent, "#f0a500")}
  {section("Wildcard — You Might Love These", wildcard, "#a78bfa")}
  <div style="margin-top:24px;">
    <div style="font-size:10px;letter-spacing:2px;color:#445566;font-family:monospace;margin-bottom:10px;">CHECK THESE MANUALLY</div>
    {boards}
  </div>
</div></body></html>"""

def send_email(new_jobs, all_jobs):
    user = os.environ.get("SMTP_USER",""); pwd = os.environ.get("SMTP_PASS","")
    to   = os.environ.get("TO_EMAIL", user)
    if not user or not pwd:
        print("  ⚠ Email skipped"); return
    core = len([j for j in new_jobs if j.get("category")=="CORE"])
    adj  = len([j for j in new_jobs if j.get("category")=="ADJACENT"])
    wild = len([j for j in new_jobs if j.get("category")=="WILDCARD"])
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
        print(f"  ⚠ Email: {e}")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("="*55)
    print(f"Travis Shorrock Job Scraper — 3-Lane Edition")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    print("="*55)

    print("\n[1/4] Fetching core + adjacent jobs...")
    raw = fetch_all()
    print(f"\n  → {len(raw)} total matches")

    print("\n[2/4] Fetching wildcard jobs...")
    wild_raw = fetch_wildcards()
    print(f"\n  → {len(wild_raw)} wildcard candidates")

    print("\n[3/4] Scoring everything with Claude...")
    new_jobs, all_jobs = process_jobs(raw, wild_raw)

    print("\n[4/4] Emailing...")
    send_email(new_jobs, all_jobs)
    print("\n✅ Done.")
