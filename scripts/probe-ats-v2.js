#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

// List of architecture firms to probe
const firms = [
  'Gensler',
  'SOM',
  'Foster + Partners',
  'Perkins&Will',
  'NBBJ',
  'ZGF Architects',
  'HOK',
  'HKS Architects',
  'KPF',
  'SmithGroup',
  'Henning Larsen',
  'AECOM',
  'Stantec',
  'HDR',
  'Jacobs',
  'Cannon Design',
  'Sasaki',
  'IDEO',
  'WeWork',
  'Snøhetta',
  'BIG',
  'OMA',
  'MVRDV',
  'Studio Gang',
  'COOKFOX',
  'Ennead',
  'Bohlin Cywinski Jackson',
  'Olson Kundig',
  'Lake|Flato',
  'Diller Scofidio + Renfro',
  'Morphosis',
  'Kieran Timberlake',
  'Elkus Manfredi',
  'Miller Hull',
  'Michael Maltzan',
  'Mack Scogin',
  'Machado Silvetti',
  'Leers Weinzapfel',
  'Pelli Clarke',
  'Overland Partners',
  'SHOP Architects',
  'REX',
  'Adjaye Associates',
  'MAD Architects',
  'Heatherwick Studio',
  'WORKac',
  'LMN Architects',
  'Mithun',
  'DLR Group',
  'Leo A Daly',
  'Page',
  'EYP',
  'Thornton Tomasetti',
  'Populous',
  'WATG',
  'CallisonRTKL',
  'HGA'
];

// Enhanced slug generation
function generateSlugs(firmName) {
  const slugs = new Set();

  // Remove accents and normalize
  const normalized = firmName
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '');

  // Basic transformations
  const lower = normalized.toLowerCase();
  slugs.add(lower);
  
  // Replace special characters
  const spaced = lower.replace(/[&|+]/g, ' ');
  slugs.add(spaced.replace(/\s+/g, '-'));
  slugs.add(spaced.replace(/\s+/g, ''));
  slugs.add(spaced.trim());

  // Split on word boundaries
  const words = spaced.split(/\s+/).filter(w => w.length > 0);
  
  // First word
  if (words.length > 0) {
    slugs.add(words[0]);
  }

  // All combinations of words
  if (words.length > 1) {
    slugs.add(words.join('-'));
    slugs.add(words.join(''));
    slugs.add(words.slice(0, -1).join('-'));
    slugs.add(words.slice(0, -1).join(''));
  }

  // Acronym
  const acronym = words.map(w => w[0]).join('').toLowerCase();
  if (acronym.length > 1) {
    slugs.add(acronym);
  }

  // Remove "architects" and "associates"
  const cleaned = normalized.toLowerCase()
    .replace(/\s+architects?\s*$/i, '')
    .replace(/\s+associates?\s*$/i, '')
    .replace(/[&|+]/g, '-')
    .trim();
  
  if (cleaned !== lower) {
    slugs.add(cleaned);
    slugs.add(cleaned.replace(/-/g, ''));
    slugs.add(cleaned.replace(/-/g, ' '));
  }

  return Array.from(slugs).filter(s => s.length > 0);
}

// Test with timeout
async function fetchWithTimeout(url, timeout = 8000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);

  try {
    const response = await fetch(url, {
      headers: { 'Accept': 'application/json' },
      signal: controller.signal
    });
    clearTimeout(timeoutId);
    return response;
  } catch (error) {
    clearTimeout(timeoutId);
    throw error;
  }
}

// Test Greenhouse API
async function testGreenhouse(slug) {
  try {
    const url = `https://boards-api.greenhouse.io/v1/boards/${slug}/jobs`;
    const response = await fetchWithTimeout(url);

    if (response.ok) {
      const data = await response.json();
      if (data.jobs !== undefined) {
        return { found: true, jobs: data.jobs.length };
      }
    }
    return { found: false };
  } catch (error) {
    return { found: false, error: error.message };
  }
}

// Test Lever API
async function testLever(slug) {
  try {
    const url = `https://api.lever.co/v0/postings/${slug}?mode=json`;
    const response = await fetchWithTimeout(url);

    if (response.ok) {
      const data = await response.json();
      if (Array.isArray(data)) {
        return { found: true, count: data.length };
      }
    }
    return { found: false };
  } catch (error) {
    return { found: false, error: error.message };
  }
}

// Probe a single firm
async function probeFirm(firmName, verbose = false) {
  const slugs = generateSlugs(firmName);
  
  if (verbose) {
    console.log(`\n[${firmName}] Generated ${slugs.length} slug variations`);
  }

  for (const slug of slugs) {
    try {
      const ghResult = await testGreenhouse(slug);
      if (ghResult.found) {
        if (verbose) {
          console.log(`  ✓ FOUND: Greenhouse - "${slug}" (${ghResult.jobs} jobs)`);
        }
        return { ats: 'greenhouse', slug, count: ghResult.jobs };
      }

      const leverResult = await testLever(slug);
      if (leverResult.found) {
        if (verbose) {
          console.log(`  ✓ FOUND: Lever - "${slug}" (${leverResult.count} postings)`);
        }
        return { ats: 'lever', slug, count: leverResult.count };
      }

      if (verbose && slugs.indexOf(slug) < 3) {
        console.log(`  - Tried: ${slug}`);
      }
    } catch (error) {
      if (verbose && slugs.indexOf(slug) < 3) {
        console.log(`  × ${slug} (error: ${error.message})`);
      }
    }

    // Rate limiting
    await new Promise(resolve => setTimeout(resolve, 150));
  }

  if (verbose) {
    console.log(`  ✗ Not found`);
  }
  return null;
}

// Main execution
async function main() {
  const startTime = Date.now();
  console.log(`Starting enhanced ATS probe for ${firms.length} architecture firms...\n`);

  const results = {
    greenhouse: {},
    lever: {},
    unknown: []
  };

  for (let i = 0; i < firms.length; i++) {
    const firm = firms[i];
    const progress = `[${i + 1}/${firms.length}]`;
    process.stdout.write(`${progress} Probing ${firm}... `);

    const result = await probeFirm(firm, false);

    if (result) {
      const atsName = result.ats === 'greenhouse' ? 'GH' : 'LV';
      console.log(`✓ FOUND ${atsName}: "${result.slug}"`);
      
      if (result.ats === 'greenhouse') {
        results.greenhouse[firm] = result.slug;
      } else if (result.ats === 'lever') {
        results.lever[firm] = result.slug;
      }
    } else {
      console.log('✗ Not found');
      results.unknown.push(firm);
    }
  }

  // Write results
  const outputDir = path.join(__dirname, '..', 'data');
  if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
  }

  const outputPath = path.join(outputDir, 'ats-slugs.json');
  fs.writeFileSync(outputPath, JSON.stringify(results, null, 2));

  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);

  // Print summary
  console.log('\n' + '='.repeat(60));
  console.log('RESULTS SUMMARY');
  console.log('='.repeat(60));
  console.log(`Time elapsed: ${elapsed}s`);
  console.log(`Greenhouse: ${Object.keys(results.greenhouse).length} firms`);
  console.log(`Lever: ${Object.keys(results.lever).length} firms`);
  console.log(`Unknown/Not Found: ${results.unknown.length} firms`);
  console.log(`\nResults saved to: ${outputPath}`);

  if (Object.keys(results.greenhouse).length > 0) {
    console.log('\n--- Greenhouse firms ---');
    Object.entries(results.greenhouse).forEach(([firm, slug]) => {
      console.log(`  ${firm}: ${slug}`);
    });
  }

  if (Object.keys(results.lever).length > 0) {
    console.log('\n--- Lever firms ---');
    Object.entries(results.lever).forEach(([firm, slug]) => {
      console.log(`  ${firm}: ${slug}`);
    });
  }

  if (results.unknown.length > 0 && results.unknown.length <= 20) {
    console.log('\n--- Unknown/Not found ---');
    results.unknown.forEach(firm => {
      console.log(`  ${firm}`);
    });
  } else if (results.unknown.length > 20) {
    console.log(`\n--- Unknown/Not found (${results.unknown.length} firms) ---`);
    results.unknown.slice(0, 10).forEach(firm => {
      console.log(`  ${firm}`);
    });
    console.log(`  ... and ${results.unknown.length - 10} more`);
  }
}

main().catch(console.error);
