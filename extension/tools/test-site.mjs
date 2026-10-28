import { createServer } from 'node:http';

const PORT = Number(process.env.PORT ?? 8765);
const PAGES = Number(process.env.PAGES ?? 10);
const IMAGE_ORIGIN = process.env.IMAGE_ORIGIN ?? '';
const W = 900;
const H = 1300;
const SLOW = new Map((process.env.SLOW ?? '').split(',').filter(Boolean).map((kv) => kv.split('=')));
const SLOW_BYTES = Number(process.env.SLOW_BYTES ?? 400_000);
const SLOW_RATE = Number(process.env.SLOW_RATE ?? 20_000);
const STALL_AFTER = Number(process.env.STALL_AFTER ?? 40_000);
const TICK_MS = 100;
const served = [];

function pageSvg(n) {
  const hue = (n * 47) % 360;
  const panels = [0, 1, 2]
    .map((i) => `<rect x="40" y="${40 + i * 410}" width="${W - 80}" height="380" fill="hsl(${hue},40%,${92 - i * 6}%)" stroke="#222" stroke-width="6"/>`)
    .join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
<rect width="${W}" height="${H}" fill="#fff"/>${panels}
<text x="${W / 2}" y="${H / 2}" font-size="160" font-weight="900" text-anchor="middle" fill="#222">${n}</text>
<text x="${W - 120}" y="120" font-size="44" writing-mode="tb" fill="#222">ページ${n}です</text>
</svg>`;
}

const html = `<!doctype html><html><head><meta charset="utf-8"><title>test reader</title>
<style>body{margin:0;background:#333;font:14px system-ui;color:#eee} main{width:700px;margin:0 auto} img{display:block;width:700px;margin:0 0 24px}
#spa{border:3px solid #fc0}</style></head><body><main>
${Array.from({ length: PAGES }, (_, i) => `<img class="page" src="${IMAGE_ORIGIN}/pages/${i + 1}.svg" alt="page ${i + 1}">`).join('\n')}
<p>SPA element below (src swapped in place):</p>
<img id="spa" src="/pages/${PAGES + 1}.svg" alt="spa">
</main>
<script>
window.spaGo = (n) => { document.getElementById('spa').src = '/pages/' + n + '.svg'; };
window.resetSrc = (i) => { const img = document.querySelectorAll('img.page')[i]; img.setAttribute('src', '/pages/' + (i + 1) + '.svg'); };
</script></body></html>`;

function padded(n) {
  const svg = pageSvg(n);
  const pad = Math.max(0, SLOW_BYTES - Buffer.byteLength(svg) - 7);
  return Buffer.from(svg.replace('</svg>', `<!--${'x'.repeat(pad)}--></svg>`));
}

function serveSlow(req, res, n, mode) {
  const log = { page: n, mode, dest: req.headers['sec-fetch-dest'] ?? null, sent: 0, done: false };
  served.push(log);
  if (mode === 'silent') return;
  const body = padded(n);
  res.writeHead(200, { 'content-type': 'image/svg+xml', 'content-length': body.length, 'cache-control': 'no-store', 'access-control-allow-origin': '*' });
  const limit = mode === 'stall' ? Math.min(STALL_AFTER, body.length - 1) : body.length;
  const step = Math.max(1, Math.round((SLOW_RATE * TICK_MS) / 1000));
  const timer = setInterval(() => {
    const chunk = body.subarray(log.sent, Math.min(limit, log.sent + step));
    if (chunk.length) res.write(chunk);
    log.sent += chunk.length;
    if (log.sent < body.length) return;
    clearInterval(timer);
    log.done = true;
    res.end();
  }, TICK_MS);
  req.on('close', () => clearInterval(timer));
}

const bounceHtml = `<!doctype html><html><head><meta charset="utf-8"><title>redirecting images</title>
<style>body{margin:0;background:#333} img{display:block;width:700px;margin:0 auto 24px}</style></head><body>
<img class="page" id="to-local" src="${IMAGE_ORIGIN}/bounce/7.svg?to=${encodeURIComponent(`http://127.0.0.1:${PORT}/pages/7.svg`)}" alt="redirects to this computer">
<img class="page" id="to-web" src="${IMAGE_ORIGIN}/bounce/8.svg?to=${encodeURIComponent(`${IMAGE_ORIGIN}/pages/8.svg`)}" alt="redirects on the web">
</body></html>`;

createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname === '/bounce') {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    return res.end(bounceHtml);
  }
  const bounce = url.pathname.match(/^\/bounce\/(\d+)\.svg$/);
  if (bounce) {
    res.writeHead(302, { location: url.searchParams.get('to') ?? '/', 'cache-control': 'no-store' });
    return res.end();
  }
  if (url.pathname === '/served') {
    res.writeHead(200, { 'content-type': 'application/json', 'access-control-allow-origin': '*' });
    return res.end(JSON.stringify(served));
  }
  const m = url.pathname.match(/^\/pages\/(\d+)\.svg$/);
  const mode = m && (url.searchParams.get('mode') ?? SLOW.get(m[1]));
  if (mode && (url.searchParams.has('mode') || req.headers['sec-fetch-dest'] !== 'image')) return serveSlow(req, res, Number(m[1]), mode);
  if (m) {
    res.writeHead(200, { 'content-type': 'image/svg+xml', 'cache-control': SLOW.has(m[1]) ? 'no-store' : 'max-age=3600' });
    return res.end(pageSvg(Number(m[1])));
  }
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  res.end(html);
}).listen(PORT, '127.0.0.1', () => console.log(`test reader on http://127.0.0.1:${PORT}/`));
