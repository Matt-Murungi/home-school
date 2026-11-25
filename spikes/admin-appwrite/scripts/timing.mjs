// Measures full page responses (server query + render) for the sessions table.
// Pass criterion from D01: a page of the 10,000-row table renders in under 2 s.
// Usage: start the app (npm run build && npm start), then node scripts/timing.mjs [baseUrl]
const BASE = process.argv[2] ?? "http://localhost:3000";
const LIMIT_MS = 2000;
const pages = [
  "/sessions?page=1",
  "/sessions?page=2",
  "/sessions?page=100",
  "/sessions?page=200", // last page of 10,000 at 50 per page: deepest offset
  "/sessions?status=disputed&page=1",
  "/sessions?status=confirmed&page=20",
];

const results = [];
for (const path of pages) {
  const times = [];
  let info = "";
  for (let i = 0; i < 5; i++) {
    const t = performance.now();
    const res = await fetch(BASE + path);
    const html = await res.text();
    times.push(performance.now() - t);
    if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
    info = html.match(/data-testid="timing"[^>]*>([^<]+(?:<!-- -->[^<]*)*)</)?.[1]?.replaceAll("<!-- -->", "") ?? "";
  }
  times.sort((a, b) => a - b);
  const worst = Math.round(times.at(-1));
  results.push({ path, median_ms: Math.round(times[2]), worst_ms: worst, pass: worst < LIMIT_MS, page_info: info.trim() });
}
console.table(results);
const failed = results.filter((r) => !r.pass);
console.log(failed.length ? `FAIL: ${failed.length} page(s) over ${LIMIT_MS} ms` : `PASS: every page under ${LIMIT_MS} ms`);
process.exit(failed.length ? 1 : 0);
