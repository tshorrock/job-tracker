# Job Tracker

Automated daily job scraper for remote Creative Director / AI creative roles. Runs via GitHub Actions, scrapes 8 sources, scores by relevance, deduplicates, and emails a daily digest.

## Quick Start

1. Push this repo to GitHub
2. Enable GitHub Pages (Settings > Pages > Deploy from branch: `main`, folder: `/`)
3. Add 3 secrets (Settings > Secrets > Actions):
   - `SMTP_USER` — Gmail address
   - `SMTP_PASS` — Gmail App Password (16 chars)
   - `TO_EMAIL` — where to send the digest
4. Trigger manually: Actions > Daily Job Scraper > Run workflow

Dashboard: `https://tshorrock.github.io/job-tracker/`

## How It Works

- **Schedule**: Mon-Fri 7AM EST (noon UTC)
- **Sources**: We Work Remotely, Remote OK, Himalayas, Remotive, Jobicy, Arbeitnow, Authentic Jobs
- **Scoring**: 0-10 based on keyword relevance
- **Dedup**: `seen_ids.json` tracks all jobs ever seen
- **Cost**: $0 (GitHub Actions free tier)
- **Dependencies**: None (pure Python stdlib)
