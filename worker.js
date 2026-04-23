/**
 * Travis Shorrock — Job Evaluation Worker
 * Cloudflare Worker: proxies Anthropic API calls from the dashboard
 * Deploy: wrangler deploy
 * Secret: wrangler secret put ANTHROPIC_API_KEY
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

const TRAVIS_EVAL_PROMPT = `You are evaluating a remote job posting for Travis Shorrock — a senior Creative Director with 25+ years experience relocating to Costa Rica (CST/UTC-6) in August 2026.

TRAVIS'S BACKGROUND:
- National CD at T&Pm (10 yrs): Toyota Canada, TELUS — large-scale integrated campaigns, 1000+ assets/month
- CD at tms (6.5 yrs): Nissan North America, Diageo (Guinness, Smirnoff, Strongbow) — TV, OOH, packaging, CRM
- Creative Group Head at Havas: Volvo Canada — award-winning TV, print, digital
- AI tools (hands-on daily): Midjourney, Runway, Higgsfield, ComfyUI, Claude Code
- Built and led large creative departments from scratch
- One TV spot ranked 4th globally for effectiveness by Kantar
- Awards: LIA, New York Festivals, ADCC, Communication Arts, Graphis

REQUIREMENTS (hard filters):
- 100% remote — no hybrid, no office days, no travel requirements
- Senior level only: CD, ECD, GCD, VP Creative, Head of Creative, Head of Brand, or equivalent
- EST/CST timezone teams only (PST is borderline; Europe/Asia = no)
- $150K+ USD target compensation
- No UX/UI/product design roles

Evaluate the job and respond ONLY with a JSON object (no markdown, no preamble):
{
  "score": <0-10 integer>,
  "category": <"CORE" | "ADJACENT" | "WILDCARD">,
  "verdict": "<2-3 sentence honest assessment — be direct, not diplomatic>",
  "cover_letter_hook": "<opening paragraph of a cover letter — punchy, specific to this role and company, written in Travis's voice: confident, direct, slightly irreverent. 3-4 sentences max.>",
  "cv_angle": "<one sentence on how Travis should position his background for this specific role>",
  "talking_points": ["<point 1>", "<point 2>", "<point 3>"],
  "red_flags": ["<flag 1 if any>"],
  "salary_note": "<brief note on compensation expectations for this role/company if detectable>"
}`;

export default {
  async fetch(request, env) {
    // Preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: CORS });
    }

    if (request.method !== 'POST') {
      return new Response('Method not allowed', { status: 405, headers: CORS });
    }

    const { pathname } = new URL(request.url);

    if (pathname === '/run') {
      try {
        const r = await fetch('https://api.github.com/repos/tshorrock/job-tracker/actions/workflows/daily_scrape.yml/dispatches', {
          method: 'POST',
          headers: { 'Accept': 'application/vnd.github+json', 'Authorization': `Bearer ${env.GITHUB_TOKEN}`, 'Content-Type': 'application/json', 'User-Agent': 'job-eval-worker' },
          body: JSON.stringify({ ref: 'main' }),
        });
        if (r.status === 204 || r.ok) return new Response(JSON.stringify({ ok: true }), { headers: { ...CORS, 'Content-Type': 'application/json' } });
        throw new Error(`GitHub API ${r.status}: ${await r.text()}`);
      } catch (err) {
        return new Response(JSON.stringify({ error: err.message }), { status: 500, headers: { ...CORS, 'Content-Type': 'application/json' } });
      }
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return new Response(JSON.stringify({ error: 'Invalid JSON' }), {
        status: 400, headers: { ...CORS, 'Content-Type': 'application/json' }
      });
    }

    const { title, company, description, salary, url } = body;

    if (!title || !company) {
      return new Response(JSON.stringify({ error: 'Missing title or company' }), {
        status: 400, headers: { ...CORS, 'Content-Type': 'application/json' }
      });
    }

    const jobBlock = [
      `Title: ${title}`,
      `Company: ${company}`,
      salary ? `Salary: ${salary}` : '',
      url ? `URL: ${url}` : '',
      description ? `\nDescription:\n${description.slice(0, 1200)}` : '',
    ].filter(Boolean).join('\n');

    try {
      const anthropicRes = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {
          'x-api-key': env.ANTHROPIC_API_KEY,
          'anthropic-version': '2023-06-01',
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          model: 'claude-haiku-4-5-20251001',
          max_tokens: 1200,
          messages: [{
            role: 'user',
            content: `${TRAVIS_EVAL_PROMPT}\n\nJOB TO EVALUATE:\n${jobBlock}`
          }]
        })
      });

      if (!anthropicRes.ok) {
        const err = await anthropicRes.text();
        throw new Error(`Anthropic API error ${anthropicRes.status}: ${err}`);
      }

      const data = await anthropicRes.json();
      const text = data.content[0].text.trim();

      // Extract JSON robustly
      const match = text.match(/\{[\s\S]*\}/);
      if (!match) throw new Error('No JSON in response');
      const result = JSON.parse(match[0]);

      return new Response(JSON.stringify(result), {
        headers: { ...CORS, 'Content-Type': 'application/json' }
      });

    } catch (err) {
      return new Response(JSON.stringify({ error: err.message }), {
        status: 500, headers: { ...CORS, 'Content-Type': 'application/json' }
      });
    }
  }
};
