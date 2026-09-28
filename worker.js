/**
 * Travis Shorrock — Job Tracker Worker (Cloudflare)
 *   POST /          → evaluate one job with Claude (the ⚡ button)
 *   POST /run       → trigger the GitHub Action ("Run Now")
 *   POST /feedback  → record a Save / Dismiss in data/feedback.json so it syncs across
 *                     devices and teaches the daily scorer what you like
 * Deploy:  wrangler deploy
 * Secrets: wrangler secret put ANTHROPIC_API_KEY
 *          wrangler secret put GITHUB_TOKEN   (fine-grained token on this repo: Actions + Contents read/write)
 */

const REPO = 'tshorrock/job-tracker';
const MODEL = 'claude-sonnet-5';
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};
const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { ...CORS, 'Content-Type': 'application/json' } });

const EVAL_PROMPT = `You're helping Travis Shorrock decide whether to apply for a job, and giving him a head start if he does.

WHO HE IS
- Senior Creative Director, 30+ years in agencies. Lives in Nosara, Costa Rica (Central time, UTC-6). Canadian.
  Wants 100% remote, Americas hours. Target $150K+ USD.
- National CD at T&Pm (10 yrs): Toyota Canada, TELUS — large integrated campaigns, 1,000+ assets a month.
- CD at tms (6.5 yrs): Nissan North America, Diageo (Guinness, Smirnoff, Strongbow) — TV, OOH, packaging, CRM.
- Creative Group Head at Havas: Volvo Canada. A TV spot ranked 4th globally for effectiveness by Kantar.
- Built and led big creative departments from scratch. Awards: LIA, NY Festivals, ADCC, Communication Arts, Graphis.
- Hands-on daily with AI creative tools: Midjourney, Runway, Higgsfield, ComfyUI, Claude Code.

HOW TO JUDGE
Read the whole posting, including the fine print at the bottom about location, pay and remote rules.
Be direct, not diplomatic. If it's US-only, hybrid, or junior, say so plainly.

HOW HE WRITES (for the cover letter opening)
Conversational, dry, a little self-deprecating, allergic to corporate language. Confident without bragging.
Use contractions. NEVER use em dashes. No AI tells ("I'm thrilled", "passionate", "leverage", "delve",
"in today's fast-paced world", "I am writing to express"). Specific to this company and role.`;

const EVAL_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['score', 'category', 'verdict', 'can_he_be_hired', 'cover_letter_hook', 'cv_angle',
             'talking_points', 'red_flags', 'salary_note'],
  properties: {
    score: { type: 'integer', description: '0-10 overall worth-applying score' },
    category: { type: 'string', enum: ['CORE', 'ADJACENT', 'WILDCARD'] },
    verdict: { type: 'string', description: '2-3 honest sentences' },
    can_he_be_hired: { type: 'string', description: 'One sentence on remote, location and timezone eligibility from Costa Rica' },
    cover_letter_hook: { type: 'string', description: 'Opening paragraph, 3-4 sentences, in his voice, no em dashes' },
    cv_angle: { type: 'string', description: 'One sentence on how to position his background' },
    talking_points: { type: 'array', items: { type: 'string' } },
    red_flags: { type: 'array', items: { type: 'string' } },
    salary_note: { type: 'string' },
  },
};

async function evaluate(body, env) {
  const { title, company, description, salary, url, location, where } = body;
  if (!title) return json({ error: 'Missing title' }, 400);
  const jobBlock = [
    `Title: ${title}`, `Company: ${company || ''}`,
    location || where ? `Location: ${[location, where].filter(Boolean).join(' / ')}` : '',
    salary ? `Pay: ${salary}` : '', url ? `URL: ${url}` : '',
    description ? `\nFull posting:\n${String(description).slice(0, 12000)}` : '\n(No description available.)',
  ].filter(Boolean).join('\n');

  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: { 'x-api-key': env.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json' },
    body: JSON.stringify({
      model: MODEL, max_tokens: 2000, system: EVAL_PROMPT,
      messages: [{ role: 'user', content: `JOB TO EVALUATE:\n${jobBlock}` }],
      output_config: { format: { type: 'json_schema', schema: EVAL_SCHEMA } },
    }),
  });
  if (!res.ok) throw new Error(`Anthropic API ${res.status}: ${(await res.text()).slice(0, 300)}`);
  const data = await res.json();
  const text = (data.content || []).filter(b => b.type === 'text').map(b => b.text).join('');
  const r = JSON.parse(text.match(/\{[\s\S]*\}/)[0]);
  r.cover_letter_hook = (r.cover_letter_hook || '').replace(/\s*[—–]\s*/g, ', ');  // belt and braces: no em dashes
  return json(r);
}

async function runWorkflow(env) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/daily_scrape.yml/dispatches`, {
    method: 'POST',
    headers: { Accept: 'application/vnd.github+json', Authorization: `Bearer ${env.GITHUB_TOKEN}`,
               'Content-Type': 'application/json', 'User-Agent': 'job-eval-worker' },
    body: JSON.stringify({ ref: 'main' }),
  });
  if (r.status === 204 || r.ok) return json({ ok: true });
  throw new Error(`GitHub API ${r.status}: ${await r.text()}`);
}

// Save / Dismiss → data/feedback.json in the repo
async function feedback(body, env) {
  const { action, id, title, company } = body;
  if (!id || !['save', 'unsave', 'dismiss', 'undismiss'].includes(action)) return json({ error: 'bad request' }, 400);
  const api = `https://api.github.com/repos/${REPO}/contents/data/feedback.json`;
  const gh = { Accept: 'application/vnd.github+json', Authorization: `Bearer ${env.GITHUB_TOKEN}`, 'User-Agent': 'job-eval-worker' };
  for (let attempt = 0; attempt < 3; attempt++) {
    const cur = await fetch(api, { headers: gh });
    let fb = { saved: {}, dismissed: {} }, sha;
    if (cur.ok) {
      const f = await cur.json();
      sha = f.sha;
      fb = JSON.parse(decodeURIComponent(escape(atob(f.content.replace(/\n/g, '')))));
      fb.saved ||= {}; fb.dismissed ||= {};
    }
    const entry = { title: title || '', company: company || '', at: new Date().toISOString() };
    if (action === 'save') { fb.saved[id] = entry; delete fb.dismissed[id]; }
    if (action === 'unsave') delete fb.saved[id];
    if (action === 'dismiss') { fb.dismissed[id] = entry; delete fb.saved[id]; }
    if (action === 'undismiss') delete fb.dismissed[id];
    const content = btoa(unescape(encodeURIComponent(JSON.stringify(fb, null, 1))));
    const put = await fetch(api, {
      method: 'PUT', headers: { ...gh, 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: `feedback: ${action} ${title || id}`, content, sha,
                             committer: { name: 'Job Bot', email: 'bot@job-tracker.local' } }),
    });
    if (put.ok) return json({ ok: true });
    if (put.status !== 409 && put.status !== 422) throw new Error(`GitHub API ${put.status}: ${await put.text()}`);
  }
  throw new Error('feedback write conflicted 3 times');
}

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') return new Response(null, { headers: CORS });
    if (request.method !== 'POST') return new Response('Method not allowed', { status: 405, headers: CORS });
    const { pathname } = new URL(request.url);
    try {
      if (pathname === '/run') return await runWorkflow(env);
      const body = await request.json().catch(() => ({}));
      if (pathname === '/feedback') return await feedback(body, env);
      return await evaluate(body, env);
    } catch (err) {
      return json({ error: err.message }, 500);
    }
  },
};
