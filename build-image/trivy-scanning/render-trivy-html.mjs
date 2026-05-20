import fs from 'node:fs/promises';

const [,, jsonPath, htmlPath] = process.argv;

if (!jsonPath || !htmlPath) {
  console.error('Usage: node render-trivy-html.mjs <jsonPath> <htmlPath>');
  process.exit(1);
}

const severityRank = {
  CRITICAL: 0,
  HIGH: 1,
  MEDIUM: 2,
  LOW: 3,
  UNKNOWN: 4,
};

const text = await fs.readFile(jsonPath, 'utf8');
const data = JSON.parse(text);
const results = Array.isArray(data)
  ? data
  : Array.isArray(data?.Results)
    ? data.Results
    : [data];

const esc = (s = '') => String(s)
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

function targetLabel(result) {
  return result?.Target || 'Trivy scan';
}

function licenseSeverity(license) {
  const value = String(license || '').toUpperCase();
  if (!value) return 'UNKNOWN';
  if (
    value.includes('AGPL') ||
    (value.includes('GPL') && !value.includes('LGPL')) ||
    value.includes('ARTISTIC') ||
    value.includes('SSPL') ||
    value.includes('CPAL')
  ) {
    return 'HIGH';
  }
  if (
    value.includes('LGPL') ||
    value.includes('CDDL') ||
    value.includes('CPL') ||
    value.includes('EPL') ||
    value.includes('EUPL') ||
    value.includes('MPL') ||
    value.includes('ODBL') ||
    value.includes('UNLICENSE') ||
    value.includes('MIT') ||
    value.includes('APACHE') ||
    value.includes('BSD') ||
    value.includes('ISC') ||
    value.includes('ZLIB') ||
    value.includes('UNENCUMBERED') ||
    value.includes('PERMISSIVE') ||
    value.includes('NOTICE')
  ) {
    return 'LOW';
  }
  return 'UNKNOWN';
}

function sortVulns(a, b) {
  const sa = (a.Severity || '').toUpperCase();
  const sb = (b.Severity || '').toUpperCase();
  const ra = severityRank[sa] ?? 99;
  const rb = severityRank[sb] ?? 99;
  if (ra !== rb) return ra - rb;
  return String(a.VulnerabilityID || '').localeCompare(String(b.VulnerabilityID || ''));
}

function renderTable(headers, rows) {
  return `<table><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

let html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Trivy Detailed Report</title>
<style>
body{font-family:Arial,sans-serif;margin:32px;background:#f8fafc;color:#111827}
.card{max-width:1400px;background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:24px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
h1,h2{margin-top:0}
code{background:#eef2ff;padding:2px 6px;border-radius:6px}
table{border-collapse:collapse;width:100%;margin:16px 0 28px}
th,td{border:1px solid #e5e7eb;padding:8px 10px;text-align:left;vertical-align:top;font-size:14px;line-height:1.35}
th{background:#f3f4f6;position:sticky;top:0}
.muted{color:#6b7280}
.section{margin-top:24px}
</style>
</head>
<body>
<div class="card">
<h1>Trivy Detailed Report</h1>
`;

for (const result of results) {
  const vulnerabilities = Array.isArray(result?.Vulnerabilities) ? [...result.Vulnerabilities].sort(sortVulns) : [];
  const packages = Array.isArray(result?.Packages) ? result.Packages : [];
  const licenseRows = [];

  for (const pkg of packages) {
    if (!Array.isArray(pkg?.Licenses)) continue;
    for (const lic of pkg.Licenses) {
      licenseRows.push({
        severity: licenseSeverity(lic),
        pkg: pkg?.Name || '',
        licenses: lic || '',
      });
    }
  }

  licenseRows.sort((a, b) => {
    const ra = severityRank[a.severity] ?? 99;
    const rb = severityRank[b.severity] ?? 99;
    if (ra !== rb) return ra - rb;
    return String(a.pkg).localeCompare(String(b.pkg));
  });

  html += `<div class="section"><div class="muted">Target: <code>${esc(targetLabel(result))}</code></div>`;

  if (vulnerabilities.length) {
    html += `<h2>Vulnerabilities</h2>`;
    html += renderTable(
      ['Severity', 'ID', 'Package', 'Installed', 'Fixed', 'Title'],
      vulnerabilities.map((v) => [
        v.Severity || '',
        v.VulnerabilityID || '',
        v.PkgName || '',
        v.InstalledVersion || '',
        v.FixedVersion || '',
        v.Title || '',
      ]),
    );
  }

  if (licenseRows.length) {
    html += `<h2>Licenses</h2>`;
    html += renderTable(
      ['Severity', 'Package', 'Licenses'],
      licenseRows.map((r) => [r.severity, r.pkg, r.licenses]),
    );
  }

  html += `</div>`;
}

html += `</div></body></html>`;

await fs.writeFile(htmlPath, html, 'utf8');
