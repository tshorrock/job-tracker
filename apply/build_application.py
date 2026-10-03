#!/usr/bin/env python3
"""
Paste a job URL -> get a complete, tailored application package.

  python apply/build_application.py "<job url>"            # needs ANTHROPIC_API_KEY
  python apply/build_application.py "<job url>" --text posting.txt   # if the page can't be fetched
  python apply/build_application.py "<job url>" --mock    # offline test, no Claude calls

What it does
  1. Reads the posting (LinkedIn, Greenhouse, Lever, Ashby, or any page).
  2. Researches the company and who likely owns the hire (Claude + web search).
  3. Tailors from apply/career.json ONLY: picks the right base version, chooses and orders bullets,
     skills, writes the cover letter and outreach emails. It can't invent facts: bullets are picked
     by ID from the master file.
  4. Checks the output: one-page resume, no widows, no em dashes, no career-length numbers,
     no AI tool names, no location giveaways. Fixes what it can, flags the rest.
  5. Renders PDF + Word versions and writes brief.md (who to contact, emails, what to check).
  6. Emails the package to you (if SMTP secrets are set) and saves it under applications/.
"""
import json, os, re, sys, argparse, datetime, urllib.request, urllib.parse, urllib.error, smtplib, html as htmllib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import render  # noqa: E402

CAREER = json.loads((HERE / "career.json").read_text())
MODEL = os.environ.get("APPLY_MODEL") or "claude-sonnet-5"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"

# ─── 1. READ THE POSTING ─────────────────────────────────────────────────────

def _get(url, timeout=25, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")

def _text(h):
    from bs4 import BeautifulSoup
    s = BeautifulSoup(htmllib.unescape(h or ""), "html.parser")
    for t in s(["script", "style", "noscript", "svg"]): t.decompose()
    return re.sub(r"\n{3,}", "\n\n", s.get_text("\n", strip=True))

def fetch_posting(url):
    """Returns dict(title, company, location, salary, description, source)."""
    u = urllib.parse.urlparse(url)
    host, path = u.netloc.lower(), u.path
    # LinkedIn
    m = re.search(r"(?:currentJobId=|/jobs/view/(?:[^/?]*-)?)(\d{8,})", url)
    if "linkedin.com" in host and m:
        from bs4 import BeautifulSoup
        s = BeautifulSoup(_get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}"), "html.parser")
        g = lambda sel: (s.select_one(sel).get_text(" ", strip=True) if s.select_one(sel) else "")
        return {"title": g(".topcard__title") or g("h2"), "company": g(".topcard__org-name-link"),
                "location": g(".topcard__flavor--bullet"), "salary": g(".compensation__salary"),
                "description": g(".show-more-less-html__markup"), "source": "LinkedIn"}
    # Greenhouse (boards / job-boards / embedded gh_jid)
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", url)
    if m:
        j = json.loads(_get(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}?pay_transparency=true"))
        pay = (j.get("pay_input_ranges") or [{}])[0]
        sal = f"${pay.get('min_cents',0)//100:,}–${pay.get('max_cents',0)//100:,} {pay.get('currency_type','')}" if pay.get("min_cents") else ""
        return {"title": j.get("title", ""), "company": m.group(1), "location": (j.get("location") or {}).get("name", ""),
                "salary": sal, "description": _text(j.get("content", "")), "source": "Greenhouse"}
    # Lever
    m = re.search(r"jobs\.lever\.co/([\w-]+)/([\w-]{20,})", url)
    if m:
        j = json.loads(_get(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}"))
        desc = "\n".join([j.get("descriptionPlain", "")] + [f"{l.get('text')}:\n{_text(l.get('content'))}" for l in j.get("lists", [])] + [j.get("additionalPlain", "")])
        sr = j.get("salaryRange") or {}
        return {"title": j.get("text", ""), "company": m.group(1), "location": (j.get("categories") or {}).get("location", ""),
                "salary": f"{sr.get('min')}–{sr.get('max')} {sr.get('currency','')}" if sr else "", "description": desc, "source": "Lever"}
    # Ashby
    m = re.search(r"jobs\.ashbyhq\.com/([\w.-]+)/([\w-]{20,})", url)
    if m:
        d = json.loads(_get(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}?includeCompensation=true"))
        for j in d.get("jobs", []):
            if m.group(2) in (j.get("jobUrl", "") + j.get("id", "")):
                return {"title": j.get("title", ""), "company": m.group(1), "location": j.get("location", ""),
                        "salary": (j.get("compensation") or {}).get("compensationTierSummary", ""),
                        "description": j.get("descriptionPlain", ""), "source": "Ashby"}
    # Anything else: read the page
    page = _get(url)
    title = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    gh = re.search(r"gh_jid=(\d+)", url)
    return {"title": htmllib.unescape(title.group(1)).strip() if title else "", "company": host.replace("www.", "").split(".")[0],
            "location": "", "salary": "", "description": _text(page)[:15000], "source": host, "gh_jid": gh.group(1) if gh else ""}

# ─── 2 + 3. CLAUDE ───────────────────────────────────────────────────────────

def claude(messages, system=None, schema=None, tools=None, max_tokens=4000):
    body = {"model": MODEL, "max_tokens": max_tokens, "messages": messages}
    if system: body["system"] = system
    if schema: body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    if tools: body["tools"] = tools
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

def research(posting, url):
    """Company snapshot + likely hiring leader + recruiter, via web search. Never fatal."""
    prompt = f"""A senior creative director is applying for this role. Research it on the web and report back.

Role: {posting['title']}
Company (may be a slug or an intermediary like Jobgether): {posting['company']}
Posting URL: {url}
Posting excerpt: {posting['description'][:2500]}

Find, citing a URL for each item:
1. The real hiring company (if the posting is from an intermediary, say so and whether the client is identifiable).
2. What the company does, size, and 2-3 recent things relevant to brand, marketing or creative (launches, rebrands, new CMO, funding, AI content work) from the last 12 months.
3. Who this role most likely reports to (name + title + LinkedIn URL if public), e.g. CMO, VP Brand, CCO, Head of Creative.
4. A talent/recruiting contact for marketing or creative roles if public.
5. The company's email address format if publicly documented (e.g. first@company.com). Say "unknown" if not found. Never guess silently.

Return ONLY a JSON object inside <json></json> tags with keys:
real_company, is_intermediary (bool), company_summary, recent_signals (list of {{text, url}}),
likely_hiring_leader ({{name, title, linkedin, confidence: high|medium|low, source}}),
other_contacts (list of {{name, title, linkedin, role_in_hire, source}}),
email_format ({{pattern, confidence, source}})."""
    for tool_ver in ("web_search_20260209", "web_search_20250305"):
        try:
            txt = claude([{"role": "user", "content": prompt}],
                         tools=[{"type": tool_ver, "name": "web_search", "max_uses": 8}], max_tokens=6000)
            m = re.search(r"<json>(.*?)</json>", txt, re.S)
            return json.loads(m.group(1)) if m else {"raw": txt}
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:300]
            print(f"  research ({tool_ver}) HTTP {e.code}: {msg}")
            if e.code != 400: break
        except Exception as e:
            print(f"  research failed: {e}"); break
    return {}

def tailor_schema():
    job_ids = {j["key"]: list(j["bullets"].keys()) for j in CAREER["jobs"]}
    S = CAREER["skills"]
    arr = lambda enum, lo=0, hi=12: {"type": "array", "items": {"type": "string", "enum": enum}}
    email = {"type": "object", "additionalProperties": False, "required": ["to", "subject", "body"],
             "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}}
    return {
        "type": "object", "additionalProperties": False,
        "required": ["company_display", "role_display", "variant", "fit_read", "tagline", "profile", "bullets",
                     "skills_creative", "skills_ai", "skills_leadership", "skills_tools", "brands", "cover_letter",
                     "hiring_leader_email", "referral_ask", "follow_up", "knockout_notes",
                     "check_before_sending", "questions_for_first_call", "keywords_matched"],
        "properties": {
            "company_display": {"type": "string", "description": "Company name for the letter; the intermediary name if the client is unknown"},
            "role_display": {"type": "string"},
            "variant": {"type": "string", "enum": list(CAREER["variants"].keys())},
            "fit_read": {"type": "string", "description": "Two honest lines: worth applying?, the angle, the biggest gap."},
            "tagline": {"type": "string", "description": "3 short phrases joined by '  ·  ', starting with 'Creative Director' unless the posting's exact title is truer"},
            "profile": {"type": "string", "description": "2-3 sentences, max 330 characters, adapted from the chosen variant's profile using ONLY facts in career.json, echoing the posting's language"},
            "bullets": {"type": "object", "additionalProperties": False, "required": list(job_ids),
                        "properties": {k: arr(v) for k, v in job_ids.items()},
                        "description": "Ordered bullet IDs per job, most relevant first. shorrock 2-3, tpm 4-5, tms 2-3, havas 1."},
            "skills_creative": arr(S["creative"]), "skills_ai": arr(S["ai"]), "skills_leadership": arr(S["leadership"]), "skills_tools": arr(S["tools"]),
            "brands": arr(CAREER["brands"]),
            "cover_letter": {"type": "array", "items": {"type": "string"},
                             "description": "Paragraphs. First is the greeting ('Hello,' or 'Hi <First name>,' if the hiring leader is known with high confidence). Last is 'Thanks,'. 170-240 words total."},
            "hiring_leader_email": email, "referral_ask": email, "follow_up": email,
            "knockout_notes": {"type": "array", "items": {"type": "string"}, "description": "Screening questions likely on the form (location, work authorization, salary, years of experience) and how to answer honestly"},
            "check_before_sending": {"type": "array", "items": {"type": "string"}},
            "questions_for_first_call": {"type": "array", "items": {"type": "string"}},
            "keywords_matched": {"type": "array", "items": {"type": "string"}, "description": "Posting terms now reflected in the resume"},
        }}

def tailor(posting, url, intel, feedback=""):
    system = ("You tailor job applications for Travis Shorrock. You may ONLY use facts from CAREER below. "
              "Bullets are chosen by ID; you cannot write new resume bullets. The profile, cover letter and emails may be "
              "written freshly but every fact in them must come from CAREER.\n\nRULES:\n- " + "\n- ".join(CAREER["rules"]) +
              "\n\nEMAILS: hiring_leader_email is 50-125 words to the likely hiring leader (use their name if known, else "
              "'[Name]'), with one specific hook about the company, one proof point, a small ask, and a link to "
              "travisshorrock.com. referral_ask is to a friendly connection at the company ('[Name]'), short and easy "
              "to say yes to. follow_up is sent 5-7 business days later, 2-3 sentences. Subjects are short and plain."
              "\n\nCAREER:\n" + json.dumps(CAREER, ensure_ascii=False))
    user = (f"POSTING URL: {url}\nTITLE: {posting['title']}\nCOMPANY: {posting['company']}\nLOCATION: {posting['location']}\n"
            f"PAY: {posting['salary']}\n\nDESCRIPTION:\n{posting['description'][:14000]}\n\n"
            f"RESEARCH ON THE COMPANY AND CONTACTS:\n{json.dumps(intel, ensure_ascii=False)[:6000]}")
    if feedback:
        user += f"\n\nYOUR PREVIOUS DRAFT HAD THESE PROBLEMS. FIX THEM:\n{feedback}"
    return json.loads(claude([{"role": "user", "content": user}], system=system, schema=tailor_schema(), max_tokens=6000))

# ─── 4. CHECKS ───────────────────────────────────────────────────────────────

TOOL_NAMES = r"\b(midjourney|runway|higgsfield|comfyui|claude|kling|veo|elevenlabs|seedance|magnific|n8n|chatgpt|openai|sora|nano banana|weavy|flux|stable diffusion)\b"
AGE_RE = r"\b(\d{2}\+?\s*(years|yrs)|decades?|a decade|ten years|twenty years|since (19|20)\d\d)\b"
LOC_RE = r"\b(costa rica|nosara|guanacaste)\b"
BANNED = r"\b(thrilled|excited|passionate|leverage|delve|i am writing to|great fit|perfect fit)\b"

def lint(t):
    """Return list of problems in the prose (letter, profile, emails)."""
    prose = {"profile": t["profile"], "cover letter": "\n".join(t["cover_letter"])}
    for k in ("hiring_leader_email", "referral_ask", "follow_up"):
        prose[k] = t[k]["subject"] + "\n" + t[k]["body"]
    probs = []
    for name, txt in prose.items():
        low = txt.lower()
        for rx, what in ((TOOL_NAMES, "names an AI tool"), (AGE_RE, "states career length / dates you"),
                         (LOC_RE, "reveals location"), (BANNED, "uses a banned phrase")):
            for m in re.finditer(rx, low):
                if what.startswith("states") and re.search(r"six-year|six straight years|tested since 2008", low[max(0, m.start()-12):m.end()+8]):
                    continue
                probs.append(f"{name} {what}: '{m.group(0)}'")
    words = len(" ".join(t["cover_letter"]).split())
    if not 150 <= words <= 260: probs.append(f"cover letter is {words} words; keep it 170-240")
    return probs

def scrub_dashes(t):
    fix = lambda s: re.sub(r"\s*[—–]\s*", ", ", s)
    t["profile"] = fix(t["profile"]); t["cover_letter"] = [fix(p) for p in t["cover_letter"]]
    for k in ("hiring_leader_email", "referral_ask", "follow_up"):
        t[k]["body"] = fix(t[k]["body"]); t[k]["subject"] = fix(t[k]["subject"])
    return t

# ─── 5. ASSEMBLE + RENDER ────────────────────────────────────────────────────

def assemble(t, posting):
    jobs = []
    for j in CAREER["jobs"]:
        ids = [i for i in t["bullets"].get(j["key"], []) if i in j["bullets"]]
        ids = list(dict.fromkeys(ids)) or list(j["bullets"])[:1]
        jobs.append({"title": j["title"], "company": j["company"], "dates": j["dates"], "bullets": [j["bullets"][i] for i in ids]})
    pick = lambda chosen, allowed: [s for s in chosen if s in allowed] or allowed[:6]
    today = datetime.date.today()
    return {
        "person": CAREER["person"], "tagline": t["tagline"], "profile": t["profile"], "jobs": jobs,
        "skills": [("Creative", pick(t.get("skills_creative", []), CAREER["skills"]["creative"])[:7]),
                   ("AI production", pick(t.get("skills_ai", []), CAREER["skills"]["ai"])[:4]),
                   ("Leadership & ops", pick(t["skills_leadership"], CAREER["skills"]["leadership"])[:6]),
                   ("Tools", CAREER["skills"]["tools"])],
        "brands": pick(t["brands"], CAREER["brands"])[:12], "awards": CAREER["awards"], "education": CAREER["education"],
        "letter": t["cover_letter"], "date": today.strftime("%B %Y"),
        "re": f"Re: {t['role_display']}  ·  {t['company_display']}",
    }

def trim_to_one_page(d):
    """Drop the least important bullet from the longest job (keeping minimums)."""
    mins = {0: 2, 1: 3, 2: 1, 3: 1}
    order = sorted(range(len(d["jobs"])), key=lambda i: -len(d["jobs"][i]["bullets"]))
    for i in order:
        if len(d["jobs"][i]["bullets"]) > mins.get(i, 1):
            d["jobs"][i]["bullets"].pop(); return True
    return False

def slug(s): return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40]

# ─── 6. BRIEF + EMAIL ────────────────────────────────────────────────────────

def people_links(company, title):
    q = lambda s: urllib.parse.quote(s)
    net = "&network=%5B%22F%22%2C%22S%22%5D"
    return [
        ("Your 1st/2nd-degree connections at the company", f"https://www.linkedin.com/search/results/people/?keywords={q(company)}{net}"),
        ("Marketing / creative leaders there", f"https://www.linkedin.com/search/results/people/?keywords={q(company + ' chief marketing officer OR VP brand OR head of creative OR creative director')}"),
        ("Recruiters there", f"https://www.linkedin.com/search/results/people/?keywords={q(company + ' recruiter OR talent')}"),
        ("Google: who leads brand there", f"https://www.google.com/search?q={q(company + ' CMO OR VP Brand OR Head of Creative site:linkedin.com/in')}"),
    ]

def brief_md(url, posting, intel, t, d, layout, issues, stem):
    L = []
    L.append(f"# {t['role_display']} · {t['company_display']}\n")
    L.append(f"**Posting:** {url}  \n**Base version used:** {CAREER['variants'][t['variant']]['label']}  \n**Built:** {datetime.date.today()}\n")
    L.append(f"## Honest read\n{t['fit_read']}\n")
    L.append("## Do this, in this order\n1. Find a warm path in (links below). If you know someone, send the referral ask first and wait a day.\n"
             "2. Apply with the **Resume.pdf**. Attach the cover letter if there's a field.\n"
             "3. Same day, send the hiring-leader note (below).\n4. Follow up in 5-7 business days if you hear nothing.\n")
    if t["knockout_notes"]:
        L.append("## Screening questions to expect\n" + "\n".join(f"- {x}" for x in t["knockout_notes"]) +
                 "\n- Location: answer truthfully. If asked where you're based and it's a hard filter, don't tick a box that isn't true. "
                 "When it comes up with a person, the line is: *\"I'm on Central time and work fully remote. I contract through my own company, "
                 "or you can bring me on through an employer-of-record like Deel or Remote, which is simple on your side.\"*\n")
    L.append("## Who to contact\n")
    hl = intel.get("likely_hiring_leader") or {}
    if hl.get("name"):
        L.append(f"- **Likely hiring leader:** {hl.get('name')}, {hl.get('title','')} ({hl.get('confidence','?')} confidence) {hl.get('linkedin','')}  \n  Source: {hl.get('source','')}")
    for c in intel.get("other_contacts") or []:
        L.append(f"- {c.get('name')}, {c.get('title','')}: {c.get('role_in_hire','')} {c.get('linkedin','')}")
    ef = intel.get("email_format") or {}
    if ef.get("pattern") and ef.get("pattern") != "unknown":
        L.append(f"- **Email format:** {ef.get('pattern')} ({ef.get('confidence','?')} confidence; source {ef.get('source','')}). Verify before sending.")
    if intel.get("is_intermediary"):
        L.append(f"- Posted by an intermediary. Real company: {intel.get('real_company') or 'not identifiable'}.")
    L.append("\nSearch links:")
    for label, link in people_links(intel.get("real_company") or t["company_display"], posting["title"]):
        L.append(f"- [{label}]({link})")
    if intel.get("company_summary"):
        L.append(f"\n**About them:** {intel['company_summary']}")
    for s in intel.get("recent_signals") or []:
        L.append(f"- {s.get('text')} ({s.get('url')})")
    for k, label in (("hiring_leader_email", "Note to the hiring leader"), ("referral_ask", "Referral ask (to someone you know there)"), ("follow_up", "Follow-up (5-7 business days later)")):
        L.append(f"\n## {label}\n**To:** {t[k]['to']}  \n**Subject:** {t[k]['subject']}\n\n{t[k]['body']}\n")
    L.append("## Check before sending\n" + "\n".join(f"- [ ] {x}" for x in t["check_before_sending"] + issues))
    L.append("\n## Questions for the first call\n" + "\n".join(f"- {x}" for x in t["questions_for_first_call"]))
    L.append(f"\n## What changed from your base\n- Tagline: {t['tagline']}\n- Keywords matched: {', '.join(t['keywords_matched'])}\n"
             f"- Layout: resume {layout['resume'].get('pages')} page, letter {layout['letter'].get('pages')} page, "
             f"widows {len(layout['resume']['widows']) + len(layout['letter']['widows'])}")
    return "\n".join(L)

def email_package(outdir, stem, t, brief):
    user, pwd = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
    to = os.environ.get("TO_EMAIL") or user
    if not (user and pwd):
        print("  (email skipped: SMTP_USER/SMTP_PASS not set)"); return
    msg = MIMEMultipart()
    msg["Subject"] = f"📄 Application ready: {t['role_display']} · {t['company_display']}"
    msg["From"], msg["To"] = user, to
    try:
        import markdown as md_lib
        body = md_lib.markdown(brief)
    except ImportError:
        body = f"<pre style='white-space:pre-wrap'>{htmllib.escape(brief)}</pre>"
    msg.attach(MIMEText(f"<html><body style='font-family:Helvetica,Arial;max-width:680px'>{body}</body></html>", "html", "utf-8"))
    for f in sorted(Path(outdir).glob(f"{stem} - *")):
        if f.suffix == ".pdf":
            part = MIMEApplication(f.read_bytes(), Name=f.name)
            part["Content-Disposition"] = f'attachment; filename="{f.name}"'
            msg.attach(part)
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls(); s.login(user, pwd); s.sendmail(user, to, msg.as_string())
    print(f"  ✓ package emailed to {to}")

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url"); ap.add_argument("--text", help="file with the posting text, if the page can't be read")
    ap.add_argument("--tailoring", "--mock", dest="mock", help="JSON file with a finished tailoring (used when Claude in chat does the writing; skips the API)")
    ap.add_argument("--research", help="JSON file with company/contact research (optional, used with --tailoring)")
    ap.add_argument("--print-schema", action="store_true", help="print the tailoring JSON schema and exit")
    ap.add_argument("--out", default=str(ROOT / "applications")); ap.add_argument("--no-email", action="store_true")
    a = ap.parse_args()
    if a.print_schema:
        print(json.dumps(tailor_schema(), indent=1)); return

    print("[1/6] Reading the posting…")
    try:
        posting = fetch_posting(a.url)
    except Exception as e:
        print(f"  couldn't read the page ({e})"); posting = {"title": "", "company": "", "location": "", "salary": "", "description": "", "source": ""}
    if a.text:
        posting["description"] = Path(a.text).read_text()
    if len(posting["description"]) < 300:
        sys.exit("Couldn't read the job description. Save the posting text to a file and re-run with --text file.txt")
    print(f"  {posting['title']} · {posting['company']} ({posting['source']}, {len(posting['description'])} chars)")

    if a.mock:
        intel = json.loads(Path(a.research).read_text()) if a.research else {}
        t = scrub_dashes(json.loads(Path(a.mock).read_text()))
    else:
        print("[2/6] Researching the company and contacts…")
        intel = research(posting, a.url)
        print(f"  hiring leader found: {'yes' if (intel.get('likely_hiring_leader') or {}).get('name') else 'no'}")  # names stay out of public logs
        print("[3/6] Tailoring from your master content…")
        t = scrub_dashes(tailor(posting, a.url, intel))
        probs = lint(t)
        if probs:
            print(f"  fixing {len(probs)} style issue(s)")
            t = scrub_dashes(tailor(posting, a.url, intel, feedback="\n".join(probs)))

    stem = f"Travis Shorrock"
    folder = Path(a.out) / f"{datetime.date.today()}-{slug(t['company_display'])}-{slug(t['role_display'])}"
    print("[4/6] Rendering and checking layout…")
    d = assemble(t, posting)
    layout = render.render_all(d, folder, stem)
    for _ in range(6):
        if (layout["resume"].get("pages") or 1) <= 1: break
        if not trim_to_one_page(d): break
        layout = render.render_all(d, folder, stem)
    # Lists (skills, brands) fix themselves: drop the lowest-priority item until no lone item is left on a line.
    for _ in range(8):
        fixed = False
        for w in layout["resume"]["widows"]:
            for lst in [v for _, v in d["skills"]] + [d["brands"]]:
                if len(lst) > 3 and w.rstrip().endswith(lst[-1]) and all(x in w for x in lst[:2]):
                    lst.pop(); fixed = True; break
        if not fixed: break
        layout = render.render_all(d, folder, stem)
    widows = layout["resume"]["widows"] + layout["letter"]["widows"]
    if widows and not a.mock:
        print(f"  fixing {len(widows)} widow(s)")
        fb = "These lines end with a single word on the last line. Reword each by adding or removing 2-4 words:\n" + "\n".join(f"- {w}" for w in widows)
        t2 = scrub_dashes(tailor(posting, a.url, intel, feedback=fb))
        t["profile"], t["cover_letter"] = t2["profile"], t2["cover_letter"]
        d["profile"], d["letter"] = t["profile"], t["cover_letter"]
        layout = render.render_all(d, folder, stem)
        widows = layout["resume"]["widows"] + layout["letter"]["widows"]
    issues = [f"Widow to fix by hand: \"…{w[-60:]}\"" for w in widows] + [f"Style check: {p}" for p in lint(t)]
    if (layout["resume"].get("pages") or 1) > 1: issues.append("Resume runs past one page")

    print("[5/6] Writing the brief…")
    brief = brief_md(a.url, posting, intel, t, d, layout, issues, stem)
    (folder / "brief.md").write_text(brief)
    (folder / "posting.txt").write_text(f"{a.url}\n{posting['title']} · {posting['company']} · {posting['location']} · {posting['salary']}\n\n{posting['description']}")
    (folder / "tailoring.json").write_text(json.dumps({"tailoring": t, "research": intel}, indent=1, ensure_ascii=False))
    print(f"  saved to {folder}")

    print("[6/6] Sending…")
    if not a.no_email:
        try: email_package(folder, stem, t, brief)
        except Exception as e: print(f"  email failed: {e}")
    print(f"\nDone. Issues flagged: {len(issues)} (listed in brief.md)")
    return folder

if __name__ == "__main__":
    main()
