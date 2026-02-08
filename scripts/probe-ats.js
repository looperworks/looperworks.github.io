#!/usr/bin/env node

const fs = require('fs');
const path = require('path');
const https = require('https');
const http = require('http');

const DATA_FILE = path.join(__dirname, '../data/firms-base.json');
const CONCURRENCY = 5;
const BATCH_DELAY = 200; // ms between batches

// Slug generation function
function generateSlugCandidates(firm) {
  const candidates = new Set();
  const { name, website } = firm;

  // Extract domain from website if available
  if (website) {
    try {
      const url = new URL(website);
      const domain = url.hostname.replace('www.', '').split('.')[0];
      if (domain && domain.length > 0) {
        candidates.add(domain.toLowerCase());
      }
    } catch (e) {
      // Invalid URL, skip
    }
  }

  // Clean the firm name
  let cleanName = name
    .toLowerCase()
    .trim();

  // Remove common suffixes, storing them for variants
  const suffixes = ['inc.', 'inc', 'llc', 'architecture', 'architects', 'design', 'group', 'firm', 'company'];
  let baseName = cleanName;
  let removedSuffix = false;

  for (const suffix of suffixes) {
    if (cleanName.endsWith(suffix)) {
      baseName = cleanName.substring(0, cleanName.length - suffix.length).trim();
      removedSuffix = true;
      break;
    }
  }

  // Strategy 1: Extract acronyms from parentheses
  const acronymMatch = name.match(/\(([A-Z]+(?:\s+[A-Z]+)*)\)/);
  if (acronymMatch) {
    const acronym = acronymMatch[1].toLowerCase().replace(/\s+/g, '');
    candidates.add(acronym);
  }

  // Strategy 2: Base name with various transformations
  if (baseName !== cleanName) {
    candidates.add(baseName);
  }

  // Strategy 3: Replace special characters
  let normalized = cleanName
    .replace(/\s+&\s+/g, '-and-') // " & " -> "-and-"
    .replace(/\s*&\s*/g, '') // Remove & entirely
    .replace(/\s*\+\s*/g, '-and-') // Replace + with -and-
    .replace(/\s*\|\s*/g, '') // Remove |
    .replace(/[(),.\[\]]/g, '') // Remove parentheses, commas, dots, brackets
    .replace(/\s+/g, '-') // Replace spaces with hyphens
    .replace(/-+/g, '-') // Collapse multiple hyphens
    .replace(/^-+|-+$/g, ''); // Remove leading/trailing hyphens

  candidates.add(normalized);

  // Strategy 4: Without hyphens
  candidates.add(normalized.replace(/-/g, ''));

  // Strategy 5: Try splitting by common delimiters and taking first word
  const firstWord = cleanName.split(/[\s&|+,()]+/)[0];
  if (firstWord && firstWord.length > 1) {
    candidates.add(firstWord);
  }

  // Strategy 6: Handle "and" variant
  const withAnd = normalized.replace(/and/g, 'and').replace(/-and-/g, '-and-');
  candidates.add(withAnd);

  // Filter out empty strings and sort by length (prefer shorter, simpler slugs)
  return Array.from(candidates)
    .filter(s => s && s.length > 0)
    .sort((a, b) => {
      // Prefer shorter slugs and those with fewer hyphens
      if (a.length !== b.length) return a.length - b.length;
      return (a.match(/-/g) || []).length - (b.match(/-/g) || []).length;
    });
}

// HTTP request wrapper with timeout
function makeRequest(url, timeout = 5000) {
  return new Promise((resolve) => {
    const protocol = url.startsWith('https') ? https : http;
    const req = protocol.get(url, { timeout }, (res) => {
      let data = '';
      res.on('data', (chunk) => {
        data += chunk;
      });
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          resolve({
            status: res.statusCode,
            data: parsed,
            error: null
          });
        } catch (e) {
          resolve({
            status: res.statusCode,
            data: null,
            error: 'Invalid JSON'
          });
        }
      });
    });

    req.on('error', (error) => {
      resolve({
        status: null,
        data: null,
        error: error.message
      });
    });

    req.on('timeout', () => {
      req.destroy();
      resolve({
        status: null,
        data: null,
        error: 'Timeout'
      });
    });
  });
}

// Probe Greenhouse API
async function probeGreenhouse(slug) {
  const url = `https://boards-api.greenhouse.io/v1/boards/${slug}/jobs`;
  const result = await makeRequest(url);

  if (result.status === 200 && result.data && Array.isArray(result.data.jobs)) {
    return { found: true, slug };
  }
  return { found: false };
}

// Probe Lever API
async function probeLever(slug) {
  const url = `https://api.lever.co/v0/postings/${slug}?mode=json`;
  const result = await makeRequest(url);

  if (result.status === 200 && Array.isArray(result.data)) {
    return { found: true, slug };
  }
  return { found: false };
}

// Process firm with concurrency control
async function processFirm(firm, index) {
  const candidates = generateSlugCandidates(firm);

  console.log(`\n[${index + 1}] ${firm.name}`);
  console.log(`    Slug candidates: ${candidates.slice(0, 3).join(', ')}${candidates.length > 3 ? '...' : ''}`);

  let foundGreenhouse = false;
  let foundLever = false;

  // Try each slug candidate
  for (const candidate of candidates) {
    if (!foundGreenhouse) {
      const gh = await probeGreenhouse(candidate);
      if (gh.found) {
        console.log(`    ✓ Found on Greenhouse: ${candidate}`);
        firm.greenhouse_slug = candidate;
        foundGreenhouse = true;
      }
    }

    if (!foundLever) {
      const lv = await probeLever(candidate);
      if (lv.found) {
        console.log(`    ✓ Found on Lever: ${candidate}`);
        firm.lever_slug = candidate;
        foundLever = true;
      }
    }

    // Stop if both found or exhausted candidates
    if (foundGreenhouse && foundLever) break;
  }

  // If no match found via API, set best slug candidate for later validation
  if (!foundGreenhouse && !foundLever && candidates.length > 0) {
    firm.greenhouse_slug = candidates[0];
    console.log(`    ○ No API match. Setting greenhouse_slug to: ${candidates[0]} (for manual validation)`);
  }

  return {
    greenhouse: foundGreenhouse,
    lever: foundLever
  };
}

// Main function with concurrency control
async function main() {
  try {
    console.log('Reading firms data...');
    const data = JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'));
    console.log(`Loaded ${data.length} firms\n`);

    const results = {
      greenhouse: 0,
      lever: 0,
      noMatch: 0
    };

    // Process in batches with concurrency limit
    for (let i = 0; i < data.length; i += CONCURRENCY) {
      const batch = data.slice(i, i + CONCURRENCY);
      const promises = batch.map((firm, idx) =>
        processFirm(firm, i + idx)
      );

      const batchResults = await Promise.all(promises);

      batchResults.forEach(result => {
        if (result.greenhouse) results.greenhouse++;
        else if (result.lever) results.lever++;
        else results.noMatch++;
      });

      // Delay between batches to avoid overwhelming APIs
      if (i + CONCURRENCY < data.length) {
        await new Promise(resolve => setTimeout(resolve, BATCH_DELAY));
      }
    }

    // Write updated data back to file
    console.log('\n\nWriting updated data...');
    fs.writeFileSync(DATA_FILE, JSON.stringify(data, null, 2));
    console.log('Data written to ' + DATA_FILE);

    // Print summary
    console.log('\n' + '='.repeat(60));
    console.log('SUMMARY');
    console.log('='.repeat(60));
    console.log(`Total firms processed: ${data.length}`);
    console.log(`Firms matched to Greenhouse: ${results.greenhouse}`);
    console.log(`Firms matched to Lever: ${results.lever}`);
    console.log(`Firms with no match: ${results.noMatch}`);
    console.log('='.repeat(60));

  } catch (error) {
    console.error('Error:', error);
    process.exit(1);
  }
}

main();
