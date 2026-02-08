#!/usr/bin/env node
/**
 * Threshold Job Pipeline
 * Fetches live job listings from Greenhouse, Lever, and JSearch APIs.
 * Merges them with the curated firm database and outputs mapvoid/firms.json.
 *
 * Usage:
 *   node fetch-jobs.js
 *
 * Environment variables:
 *   JSEARCH_API_KEY - RapidAPI key for JSearch (optional)
 */

const fs = require('fs');
const path = require('path');

// ─── Config ───
const BASE_DIR = path.resolve(__dirname, '..');
const FIRMS_BASE = path.join(BASE_DIR, 'data', 'firms-base.json');
const OUTPUT = path.join(BASE_DIR, 'mapvoid', 'firms.json');
const DISCOVERIES = path.join(BASE_DIR, 'data', 'jsearch-discoveries.json');

const CONCURRENCY = 5;
const DELAY_MS = 300;
const TIMEOUT_MS = 8000;

// ─── Helpers ───
function timeAgo(dateStr) {
  const now = new Date();
  const then = new Date(dateStr);
  const diffMs = now - then;
  const days = Math.floor(diffMs / 86400000);
  if (days === 0) return 'Today';
  if (days === 1) return '1 day ago';
  if (days < 7) return days + ' days ago';
  if (days < 14) return '1 week ago';
  if (days < 30) return Math.floor(days / 7) + ' weeks ago';
  if (days < 60) return '1 month ago';
  return Math.floor(days / 30) + ' months ago';
}

async function fetchWithTimeout(url, opts = {}) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const resp = await fetch(url, { ...opts, signal: controller.signal });
    clearTimeout(id);
    return resp;
  } catch (e) {
    clearTimeout(id);
    return null;
  }
}

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

// Process items in batches with concurrency limit
async function batchProcess(items, fn, concurrency = CONCURRENCY) {
  const results = [];
  for (let i = 0; i < items.length; i += concurrency) {
    const batch = items.slice(i, i + concurrency);
    const batchResults = await Promise.all(batch.map(fn));
    results.push(...batchResults);
    if (i + concurrency < items.length) await sleep(DELAY_MS);
  }
  return results;
}

// ─── Greenhouse ───
async function fetchGreenhouseJobs(slug) {
  if (!slug) return [];
  const url = `https://boards-api.greenhouse.io/v1/boards/${slug}/jobs`;
  const resp = await fetchWithTimeout(url);
  if (!resp || !resp.ok) return [];
  try {
    const data = await resp.json();
    if (!data.jobs || !Array.isArray(data.jobs)) return [];
    return data.jobs.map(j => ({
      title: j.title || 'Untitled',
      type: extractType(j),
      salary: 'See listing',
      posted: j.updated_at ? timeAgo(j.updated_at) : 'Recently',
      url: j.absolute_url || ''
    }));
  } catch {
    return [];
  }
}

function extractType(ghJob) {
  // Try to infer type from Greenhouse job metadata
  if (ghJob.metadata) {
    for (const m of ghJob.metadata) {
      if (m.name && m.name.toLowerCase().includes('type') && m.value) return m.value;
    }
  }
  // Check departments
  if (ghJob.departments && ghJob.departments.length > 0) {
    const dept = ghJob.departments[0].name || '';
    if (/intern/i.test(dept)) return 'Internship';
    if (/part[\s-]?time/i.test(dept)) return 'Part-time';
    if (/contract/i.test(dept)) return 'Contract';
  }
  // Check title
  const title = (ghJob.title || '').toLowerCase();
  if (/intern/i.test(title)) return 'Internship';
  if (/part[\s-]?time/i.test(title)) return 'Part-time';
  if (/contract/i.test(title)) return 'Contract';
  return 'Full-time';
}

// ─── Lever ───
async function fetchLeverJobs(slug) {
  if (!slug) return [];
  const url = `https://api.lever.co/v0/postings/${slug}?mode=json`;
  const resp = await fetchWithTimeout(url);
  if (!resp || !resp.ok) return [];
  try {
    const data = await resp.json();
    if (!Array.isArray(data)) return [];
    return data.map(j => ({
      title: j.text || 'Untitled',
      type: (j.categories && j.categories.commitment) || 'Full-time',
      salary: 'See listing',
      posted: j.createdAt ? timeAgo(new Date(j.createdAt).toISOString()) : 'Recently',
      url: j.hostedUrl || ''
    }));
  } catch {
    return [];
  }
}

// ─── JSearch ───
async function fetchJSearchJobs(apiKey) {
  if (!apiKey) {
    console.log('  ⏭  No JSEARCH_API_KEY — skipping JSearch');
    return [];
  }

  const queries = [
    'architect jobs united states',
    'landscape architect jobs united states',
    'urban designer jobs united states'
  ];

  const allJobs = [];
  for (const q of queries) {
    console.log(`  🔍 JSearch: "${q}"`);
    const url = `https://jsearch.p.rapidapi.com/search?query=${encodeURIComponent(q)}&page=1&num_pages=2&country=us&date_posted=week`;
    const resp = await fetchWithTimeout(url, {
      headers: {
        'X-RapidAPI-Key': apiKey,
        'X-RapidAPI-Host': 'jsearch.p.rapidapi.com'
      }
    });
    if (!resp || !resp.ok) {
      console.log(`    ⚠ JSearch query failed (status: ${resp ? resp.status : 'timeout'})`);
      continue;
    }
    try {
      const data = await resp.json();
      if (data.data && Array.isArray(data.data)) {
        allJobs.push(...data.data);
        console.log(`    ✓ ${data.data.length} results`);
      }
    } catch {
      console.log('    ⚠ Parse error');
    }
    await sleep(500); // respect rate limits
  }
  return allJobs;
}

// Normalize company name for fuzzy matching
function normalize(name) {
  return (name || '')
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '')
    .replace(/(inc|llc|architects|architecture|design|studio|group|associates|partnership|consulting)$/g, '');
}

// Match JSearch results to existing firms
function matchJSearchToFirms(jsearchJobs, firms) {
  const firmIndex = new Map();
  for (const f of firms) {
    firmIndex.set(normalize(f.name), f);
  }

  const matched = [];
  const unmatched = [];

  for (const j of jsearchJobs) {
    const employer = j.employer_name || '';
    const key = normalize(employer);
    const firm = firmIndex.get(key);

    const job = {
      title: j.job_title || 'Untitled',
      type: j.job_employment_type || 'Full-time',
      salary: j.job_min_salary && j.job_max_salary
        ? `$${Math.round(j.job_min_salary / 1000)}K–$${Math.round(j.job_max_salary / 1000)}K`
        : 'See listing',
      posted: j.job_posted_at_datetime_utc ? timeAgo(j.job_posted_at_datetime_utc) : 'Recently',
      url: j.job_apply_link || ''
    };

    if (firm) {
      matched.push({ firm, job });
    } else {
      unmatched.push({
        employer: employer,
        city: j.job_city || '',
        state: j.job_state || '',
        country: j.job_country || '',
        job
      });
    }
  }

  return { matched, unmatched };
}

// ─── Main Pipeline ───
async function main() {
  console.log('━━━ Threshold Job Pipeline ━━━\n');

  // Load base data
  console.log('📂 Loading firms-base.json...');
  const firms = JSON.parse(fs.readFileSync(FIRMS_BASE, 'utf8'));
  console.log(`   ${firms.length} firms loaded\n`);

  // Clear all existing jobs
  for (const f of firms) f.jobs = [];

  // Track stats
  let ghHits = 0, ghJobs = 0;
  let lvHits = 0, lvJobs = 0;
  let jsHits = 0, jsJobs = 0;

  // ── Greenhouse pass ──
  const ghFirms = firms.filter(f => f.greenhouse_slug);
  console.log(`🌿 Greenhouse: probing ${ghFirms.length} firms...`);

  await batchProcess(ghFirms, async (firm) => {
    const jobs = await fetchGreenhouseJobs(firm.greenhouse_slug);
    if (jobs.length > 0) {
      firm.jobs.push(...jobs);
      ghHits++;
      ghJobs += jobs.length;
    }
  });
  console.log(`   ✓ ${ghHits} firms responded, ${ghJobs} jobs found\n`);

  // ── Lever pass ──
  const lvFirms = firms.filter(f => f.lever_slug);
  console.log(`⚡ Lever: probing ${lvFirms.length} firms...`);

  await batchProcess(lvFirms, async (firm) => {
    const jobs = await fetchLeverJobs(firm.lever_slug);
    if (jobs.length > 0) {
      firm.jobs.push(...jobs);
      lvHits++;
      lvJobs += jobs.length;
    }
  });
  console.log(`   ✓ ${lvHits} firms responded, ${lvJobs} jobs found\n`);

  // ── JSearch pass ──
  const apiKey = process.env.JSEARCH_API_KEY || '';
  console.log('🔎 JSearch: querying job aggregator...');
  const jsearchRaw = await fetchJSearchJobs(apiKey);
  console.log(`   ${jsearchRaw.length} raw results\n`);

  if (jsearchRaw.length > 0) {
    const { matched, unmatched } = matchJSearchToFirms(jsearchRaw, firms);
    console.log(`   Matched to existing firms: ${matched.length}`);
    console.log(`   New/unmatched employers: ${unmatched.length}`);

    // Add matched jobs to firms
    for (const { firm, job } of matched) {
      // Avoid duplicates by title
      if (!firm.jobs.some(j => j.title === job.title)) {
        firm.jobs.push(job);
        jsJobs++;
      }
    }
    jsHits = matched.length;

    // Save discoveries for manual review
    if (unmatched.length > 0) {
      fs.writeFileSync(DISCOVERIES, JSON.stringify(unmatched, null, 2));
      console.log(`   Saved ${unmatched.length} discoveries to jsearch-discoveries.json`);
    }
  }

  // ── Output ──
  // Strip internal fields (greenhouse_slug, lever_slug) from output
  const output = firms.map(f => ({
    id: f.id,
    name: f.name,
    city: f.city,
    state: f.state,
    lat: f.lat,
    lng: f.lng,
    size: f.size,
    discipline: f.discipline,
    specialties: f.specialties,
    jobs: f.jobs,
    website: f.website,
    about: f.about
  }));

  fs.writeFileSync(OUTPUT, JSON.stringify(output));
  const sizeMB = (Buffer.byteLength(JSON.stringify(output)) / 1048576).toFixed(2);

  console.log('\n━━━ Summary ━━━');
  console.log(`Greenhouse: ${ghHits} firms, ${ghJobs} jobs`);
  console.log(`Lever:      ${lvHits} firms, ${lvJobs} jobs`);
  console.log(`JSearch:    ${jsHits} matched, ${jsJobs} jobs`);
  console.log(`Total firms with jobs: ${firms.filter(f => f.jobs.length > 0).length}`);
  console.log(`Total job listings: ${firms.reduce((s, f) => s + f.jobs.length, 0)}`);
  console.log(`Output: mapvoid/firms.json (${sizeMB} MB)`);
  console.log('━━━━━━━━━━━━━━━━');
}

main().catch(err => {
  console.error('Pipeline failed:', err);
  process.exit(1);
});
