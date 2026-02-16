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

// Generate slug variations for a firm name
function generateSlugs(firmName) {
  const slugs = new Set();

  // Basic lowercase
  const basic = firmName.toLowerCase().replace(/[&]/g, 'and');
  slugs.add(basic);

  // Replace spaces and special chars with hyphens
  slugs.add(basic.replace(/[\s+|]/g, '-'));

  // Without hyphens/spaces
  slugs.add(basic.replace(/[\s+|-]/g, ''));

  // Get initials for acronyms
  const words = firmName.split(/[\s+&|]/);
  if (words.length > 1) {
    const acronym = words.map(w => w.charAt(0).toLowerCase()).join('');
    slugs.add(acronym);
  }

  // Handle specific patterns
  if (firmName.includes('&')) {
    slugs.add(basic.replace(/and/g, '-'));
  }

  if (firmName.includes('+')) {
    slugs.add(basic.replace(/\+/g, 'and'));
    slugs.add(basic.replace(/\+/g, '-'));
  }

  // Word variations
  if (firmName.includes('Architects')) {
    const withoutArchitects = firmName.replace(/\s*Architects\s*/, '').toLowerCase();
    slugs.add(withoutArchitects.replace(/[\s+&|]/g, '-'));
    slugs.add(withoutArchitects.replace(/[\s+&|]/g, ''));
  }

  if (firmName.includes('Associates')) {
    const withoutAssociates = firmName.replace(/\s*Associates\s*/, '').toLowerCase();
    slugs.add(withoutAssociates.replace(/[\s+&|]/g, '-'));
    slugs.add(withoutAssociates.replace(/[\s+&|]/g, ''));
  }

  // Remove accents
  const normalized = basic.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  slugs.add(normalized);
  slugs.add(normalized.replace(/[\s+&|]/g, '-'));

  return Array.from(slugs);
}

// Test Greenhouse API
async function testGreenhouse(slug) {
  try {
    const url = `https://boards-api.greenhouse.io/v1/boards/${slug}/jobs`;
    const response = await fetch(url, {
      headers: { 'Accept': 'application/json' },
      timeout: 5000
    });

    if (response.status === 200) {
      const data = await response.json();
      // Valid if it returns jobs array
      if (data.jobs !== undefined) {
        return true;
      }
    }
    return false;
  } catch (error) {
    return false;
  }
}

// Test Lever API
async function testLever(slug) {
  try {
    const url = `https://api.lever.co/v0/postings/${slug}?mode=json`;
    const response = await fetch(url, {
      headers: { 'Accept': 'application/json' },
      timeout: 5000
    });

    if (response.status === 200) {
      const data = await response.json();
      // Valid if it returns postings array
      if (Array.isArray(data)) {
        return true;
      }
    }
    return false;
  } catch (error) {
    return false;
  }
}

// Main probing function
async function probeFirm(firmName) {
  const slugs = generateSlugs(firmName);

  console.log(`\nProbing ${firmName}...`);
  console.log(`Testing ${slugs.length} slug variations`);

  for (const slug of slugs) {
    try {
      // Test Greenhouse
      const ghResult = await testGreenhouse(slug);
      if (ghResult) {
        console.log(`  ✓ FOUND: Greenhouse - "${slug}"`);
        return { ats: 'greenhouse', slug };
      }

      // Test Lever
      const leverResult = await testLever(slug);
      if (leverResult) {
        console.log(`  ✓ FOUND: Lever - "${slug}"`);
        return { ats: 'lever', slug };
      }

      // Log attempt
      console.log(`  - Tried: ${slug}`);
    } catch (error) {
      // Continue to next slug
    }
  }

  console.log(`  ✗ Not found`);
  return null;
}

// Main execution
async function main() {
  console.log(`Starting ATS probe for ${firms.length} architecture firms...\n`);

  const results = {
    greenhouse: {},
    lever: {},
    unknown: []
  };

  for (let i = 0; i < firms.length; i++) {
    const firm = firms[i];
    const result = await probeFirm(firm);

    if (result) {
      if (result.ats === 'greenhouse') {
        results.greenhouse[firm] = result.slug;
      } else if (result.ats === 'lever') {
        results.lever[firm] = result.slug;
      }
    } else {
      results.unknown.push(firm);
    }

    // Rate limiting - be respectful
    await new Promise(resolve => setTimeout(resolve, 100));
  }

  // Ensure output directory exists
  const outputDir = path.join(__dirname, '..', 'data');
  if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
  }

  // Write results
  const outputPath = path.join(outputDir, 'ats-slugs.json');
  fs.writeFileSync(outputPath, JSON.stringify(results, null, 2));

  // Print summary
  console.log('\n' + '='.repeat(60));
  console.log('RESULTS SUMMARY');
  console.log('='.repeat(60));
  console.log(`Greenhouse: ${Object.keys(results.greenhouse).length} firms`);
  console.log(`Lever: ${Object.keys(results.lever).length} firms`);
  console.log(`Unknown/Not Found: ${results.unknown.length} firms`);
  console.log(`\nResults saved to: ${outputPath}`);

  console.log('\nGreenhouse firms:');
  Object.entries(results.greenhouse).forEach(([firm, slug]) => {
    console.log(`  ${firm}: ${slug}`);
  });

  console.log('\nLever firms:');
  Object.entries(results.lever).forEach(([firm, slug]) => {
    console.log(`  ${firm}: ${slug}`);
  });

  if (results.unknown.length > 0) {
    console.log('\nUnknown/Not found:');
    results.unknown.forEach(firm => {
      console.log(`  ${firm}`);
    });
  }
}

main().catch(console.error);
