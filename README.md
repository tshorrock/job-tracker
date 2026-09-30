# Job Tracker

Daily scraper for remote senior creative leadership roles (CD / ECD / GCD / Head of Creative / Brand / AI creative).
Runs Mon–Fri at about 4:30am Costa Rica time on GitHub Actions, scores every new role with Claude, updates the
dashboard and emails a brief.

Dashboard: https://tshorrock.github.io/job-tracker/

## Where jobs come from

| Source | Cost | Secret needed |
|---|---|---|
| LinkedIn via Apify (Fantastic.jobs "Advanced LinkedIn Job Search API"), pre-filtered to remote + Director/Exec | ~$5/mo, covered by Apify's free plan | `APIFY_TOKEN` |
| LinkedIn guest search (backup), US + Canada, 2 pages per query | free | — |
| LinkedIn job-alert emails in Gmail | free | `GMAIL_*` |
| Company careers pages (Greenhouse / Lever / Ashby), list in `data/watchlist.json` | free | — |
| Adzuna, JSearch (every 3rd day), RemoteOK, Remotive, We Work Remotely | free | `ADZUNA_*`, `RAPIDAPI_KEY` |

## How scoring works

1. Cheap filters first: title, duplicates (remembered 60 days), obvious hybrid/on-site, explicit US-only.
2. Full job descriptions are fetched for new LinkedIn jobs (the pay range and location rules are usually at the bottom).
3. Claude (`claude-sonnet-5`, structured outputs) reads the whole posting and reports facts: lane, seniority,
   remote, where candidates can live, timezone, pay, background fit, a one-line headline, red flags.
4. The score is calculated in code from those facts (see `compute_score`), so it's consistent and explainable.
   Hybrid/on-site, US-only and junior roles are capped low; Americas/worldwide-open and $150K+ get a bump.
5. Your ☆ Save / ✕ Dismiss clicks are written to `data/feedback.json` (via the worker) and fed to the scorer as examples.

## Settings you can change without code

- `data/watchlist.json` — add/remove companies (ats = greenhouse | lever | ashby, slug = name in their careers URL)
- Repo → Settings → Secrets and variables → Actions → **Variables**:
  - `APIFY_LIMIT` (default 35 jobs/day — keeps you inside Apify's free $5)
  - `SCORING_MODEL` (default `claude-sonnet-5`; `claude-haiku-4-5-20251001` is cheaper, less sharp)

## Worker (⚡ Evaluate, Run Now, Save/Dismiss sync)

`worker.js` on Cloudflare. Deploy with `npx wrangler deploy`. Secrets: `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`
(fine-grained token for this repo with **Actions: read/write** and **Contents: read/write**).

## Application builder (paste a URL, get a tailored package)

Three ways to run it:
1. **Dashboard:** click 📄 on any job card, or paste any job URL into the box and hit *Build application*.
2. **GitHub:** Actions → *Build Application* → Run workflow → paste the URL (optionally paste the job text if the page won't load).
3. **Claude chat / Claude Code:** give Claude the URL. It writes the tailoring itself and runs
   `python apply/build_application.py "<url>" --tailoring t.json --research r.json`.

What you get by email (never committed, since this repo is public):
- Resume (PDF for most portals, DOCX for Workday/iCIMS/Taleo) and cover letter, both one page, tailored to the role
- `brief.md`: honest fit read, screening questions to expect, likely hiring leader and recruiter, LinkedIn searches
  for your 1st/2nd-degree connections there, the hiring-leader note, a referral ask and a follow-up, questions for the first call

Guardrails built in: content comes only from `apply/career.json` (bullets are picked by ID, never invented);
automatic checks for one page, widows and single words on a line, em dashes, career-length numbers, AI tool names,
location giveaways and banned phrases.

**To change the base content, edit `apply/career.json`.** Every future application picks it up.
