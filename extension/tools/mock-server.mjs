import { createHash } from 'node:crypto';
import { createServer } from 'node:http';

const PORT = Number(process.env.PORT ?? 7878);
const DELAY_MS = Number(process.env.MOCK_DELAY_MS ?? 1500);
const TRANSLATOR_DOWN = process.env.MOCK_TRANSLATOR_DOWN === '1';
const INTERNAL_PAGE = process.env.MOCK_INTERNAL_PAGE;
const pages = new Map();
const works = new Map();
const stats = { posts: 0, cacheHits: 0, updated: 0, byContext: {}, requests: [], retranslates: [], namePuts: 0 };
const NAME_JA = '在藤';
const SEEDED = [
  { ja: '和音', en: 'Kazune', reading: 'かずね', source: 'furigana', kind: 'name' },
  { ja: '月光流', en: 'Gekko Style', reading: 'げっこうりゅう', source: 'furigana', kind: 'term' },
];

function work(contextKey) {
  let w = works.get(contextKey);
  if (!w) works.set(contextKey, (w = { stored: SEEDED.map((n) => ({ ...n })), version: 0 }));
  return w;
}

const spelling = (w) => w.stored.find((n) => n.ja === NAME_JA)?.en ?? 'Zaito';

function castOf(contextKey) {
  const w = work(contextKey);
  const auto = w.stored.some((n) => n.ja === NAME_JA) ? [] : [{ ja: `${NAME_JA}さん、こんにちは`, en: 'Zaito-san', reading: '', source: 'auto', kind: 'name' }];
  const order = { user: 0, furigana: 1 };
  return { context_key: contextKey, names: [...[...w.stored].sort((a, b) => order[a.source] - order[b.source]), ...auto] };
}

function storeName(w, n) {
  const entry = { ja: n.ja ?? '', en: n.en, reading: n.reading ?? '', source: 'user', kind: n.kind === 'term' ? 'term' : 'name' };
  w.stored = w.stored.filter((x) => (entry.ja && x.ja ? x.ja !== entry.ja : x.en !== entry.en));
  w.stored.push(entry);
  w.version++;
}

const BUBBLE_BOXES = [
  [0.08, 0.2, 0.48, 0.3],
  [0.52, 0.5, 0.92, 0.6],
  [0.08, 0.78, 0.48, 0.88],
];

function lineFor(page, i, w) {
  if (i === 0) return { source: `${NAME_JA}さん、こんにちは`, translation: `Hello, ${spelling(w)}-san!` };
  return { source: `ページ${page.pageIndex ?? '?'}の${i + 1}行目`, translation: `Page ${page.pageIndex ?? '?'}, line ${i + 1}.` };
}

function translateBubbles(page, w, only) {
  page.bubbles = BUBBLE_BOXES.map((f, i) => {
    const old = page.bubbles?.[i];
    if (old && only && !only.includes(i)) return old;
    const box = [Math.round(f[0] * page.size.w), Math.round(f[1] * page.size.h), Math.round(f[2] * page.size.w), Math.round(f[3] * page.size.h)];
    return { box, ...lineFor(page, i, w), vertical: false };
  });
  page.version = w.version;
}

function publicPage(page, cached, extra = {}) {
  return {
    page_id: page.id,
    width: page.size.w,
    height: page.size.h,
    rendered_url: `/v1/pages/${page.id}/rendered.webp${page.rev ? `?v=${page.rev}` : ''}`,
    bubbles: page.bubbles,
    timings_ms: { total: cached ? 0 : DELAY_MS },
    cached,
    ...extra,
  };
}

async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return Buffer.concat(chunks);
}

function imageSize(buf) {
  const ascii = (a, b) => buf.toString('latin1', a, b);
  if (ascii(0, 4) === 'RIFF' && ascii(8, 12) === 'WEBP') {
    const kind = ascii(12, 16);
    if (kind === 'VP8X') return { w: 1 + buf.readUIntLE(24, 3), h: 1 + buf.readUIntLE(27, 3), mime: 'image/webp' };
    if (kind === 'VP8 ') return { w: buf.readUInt16LE(26) & 0x3fff, h: buf.readUInt16LE(28) & 0x3fff, mime: 'image/webp' };
    if (kind === 'VP8L') {
      const b = buf.readUInt32LE(21);
      return { w: 1 + (b & 0x3fff), h: 1 + ((b >> 14) & 0x3fff), mime: 'image/webp' };
    }
  }
  if (buf[0] === 0x89 && ascii(1, 4) === 'PNG') return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20), mime: 'image/png' };
  if (buf[0] === 0xff && buf[1] === 0xd8) {
    let i = 2;
    while (i < buf.length) {
      const marker = buf[i + 1];
      const len = buf.readUInt16BE(i + 2);
      if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) {
        return { w: buf.readUInt16BE(i + 7), h: buf.readUInt16BE(i + 5), mime: 'image/jpeg' };
      }
      i += 2 + len;
    }
  }
  const head = ascii(0, Math.min(buf.length, 2000));
  const svg = head.match(/<svg[^>]*\swidth="(\d+)"[^>]*\sheight="(\d+)"/);
  if (svg) return { w: Number(svg[1]), h: Number(svg[2]), mime: 'image/svg+xml' };
  return undefined;
}

function parseMultipart(body, contentType) {
  const boundary = contentType.match(/boundary=(?:"([^"]+)"|([^;]+))/);
  if (!boundary) return {};
  const sep = Buffer.from(`--${boundary[1] ?? boundary[2]}`);
  const fields = {};
  let start = body.indexOf(sep);
  while (start !== -1) {
    const next = body.indexOf(sep, start + sep.length);
    if (next === -1) break;
    const part = body.subarray(start + sep.length + 2, next - 2);
    const split = part.indexOf('\r\n\r\n');
    const headers = part.subarray(0, split).toString();
    const name = headers.match(/name="([^"]+)"/)?.[1];
    if (name) fields[name] = part.subarray(split + 4);
    start = next;
  }
  return fields;
}

const escapeXml = (t) => t.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

function renderSvg(original, page) {
  const { w, h, mime } = page.size;
  const bw = Math.round(w * 0.42);
  const bh = Math.round(h * 0.09);
  const fs = Math.round(bh * 0.28);
  const bubbles = (page.bubbles ?? [])
    .map(({ box: [x0, y0, x1, y1], translation }) => {
      const size = Math.round((y1 - y0) * 0.24);
      return `<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" rx="${size}" fill="#fff" stroke="#111" stroke-width="3"/>
<text x="${(x0 + x1) / 2}" y="${(y0 + y1) / 2}" font-family="Comic Sans MS, sans-serif" font-size="${size}" font-weight="700" text-anchor="middle" dominant-baseline="middle" fill="#111">${escapeXml(translation)}</text>`;
    })
    .join('\n');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
<image href="data:${mime};base64,${original.toString('base64')}" width="${w}" height="${h}"/>
<rect width="${w}" height="${h}" fill="#3b82f6" fill-opacity="0.18"/>
<ellipse cx="${w / 2}" cy="${Math.round(h * 0.1)}" rx="${bw / 2}" ry="${bh / 2}" fill="#fff" stroke="#111" stroke-width="4"/>
<text x="${w / 2}" y="${Math.round(h * 0.1)}" font-family="Comic Sans MS, sans-serif" font-size="${fs}" font-weight="700" text-anchor="middle" dominant-baseline="middle" fill="#111">TRANSLATED p${page.pageIndex ?? '?'}${page.rev ? ` v${page.rev}` : ''}</text>
${bubbles}
</svg>`;
}

const badRequest = (res, detail) => json(res, 400, { error: 'bad_request', detail });

const json = (res, status, obj) => {
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(JSON.stringify(obj));
};

const server = createServer(async (req, res) => {
  res.setHeader('access-control-allow-origin', '*');
  res.setHeader('access-control-allow-methods', 'GET, POST, PUT, DELETE');
  res.setHeader('access-control-allow-headers', 'content-type');
  if (req.method === 'OPTIONS') return res.end();
  const url = new URL(req.url, 'http://x');
  if (req.method === 'GET' && url.pathname === '/v1/health') {
    stats.requests.push('health');
    return json(res, 200, TRANSLATOR_DOWN ? { ok: false, detail: 'mock translator down' } : { ok: true, version: '0.0.0-mock', translator: 'mock', gpu: 'none' });
  }
  if (req.method === 'GET' && url.pathname === '/mock/stats') return json(res, 200, stats);
  const rendered = url.pathname.match(/^\/v1\/pages\/([0-9a-f]{64})\/rendered\.webp$/);
  if (req.method === 'GET' && rendered) {
    const page = pages.get(rendered[1]);
    if (!page) return json(res, 404, { error: 'internal', detail: 'unknown page' });
    res.writeHead(200, { 'content-type': 'image/svg+xml' });
    return res.end(page.svg);
  }
  if (url.pathname === '/v1/names' || url.pathname === '/v1/names/entry') {
    const contextKey = url.searchParams.get('context_key');
    if (!contextKey) return badRequest(res, 'context_key is required');
    if (req.method === 'GET' && url.pathname === '/v1/names') return json(res, 200, castOf(contextKey));
    if (req.method !== 'PUT') return json(res, 404, { error: 'not_found', detail: 'not found' });
    let body;
    try {
      body = JSON.parse((await readBody(req)).toString());
    } catch {
      return badRequest(res, 'body is not JSON');
    }
    const w = work(contextKey);
    stats.namePuts++;
    stats.requests.push(`names ${url.pathname === '/v1/names' ? 'put' : 'entry'}`);
    if (url.pathname === '/v1/names/entry') storeName(w, body);
    else {
      if (!Array.isArray(body?.names)) return badRequest(res, 'names must be a list');
      w.stored = body.names.filter((n) => n.source !== 'auto').map((n) => ({ ja: n.ja ?? '', en: n.en, reading: n.reading ?? '', source: n.source ?? 'user', kind: n.kind === 'term' ? 'term' : 'name' }));
      w.version++;
    }
    return json(res, 200, castOf(contextKey));
  }
  const retranslate = url.pathname.match(/^\/v1\/pages\/([0-9a-f]{64})\/retranslate$/);
  if (req.method === 'POST' && retranslate) {
    const fields = parseMultipart(await readBody(req), req.headers['content-type'] ?? '');
    const image = fields.image;
    const size = image && imageSize(image);
    if (!size) return json(res, 400, { error: 'bad_image', detail: 'could not read image size' });
    if (createHash('sha256').update(image).digest('hex') !== retranslate[1]) return badRequest(res, 'page_id does not match the image');
    let request;
    try {
      request = JSON.parse(fields.request?.toString() ?? '');
    } catch {
      return badRequest(res, 'request is not JSON');
    }
    const contextKey = request.context_key ?? '';
    const w = work(contextKey);
    let page = pages.get(retranslate[1]);
    if (!page) {
      page = { id: retranslate[1], size, pageIndex: request.page_index, contextKey, rev: 0 };
      translateBubbles(page, w);
      pages.set(page.id, page);
    }
    const bubbles = request.bubbles;
    if (!Array.isArray(bubbles) || !bubbles.length || bubbles.some((i) => !Number.isInteger(i) || i < 0 || i >= page.bubbles.length)) return badRequest(res, 'bubbles must index the page’s bubbles');
    if ((request.hint ?? '').length > 300) return badRequest(res, 'hint is too long');
    if ((request.names ?? []).length > 20) return badRequest(res, 'too many names');
    for (const n of request.names ?? []) storeName(w, n);
    await new Promise((r) => setTimeout(r, DELAY_MS));
    translateBubbles(page, w, bubbles);
    for (const i of bubbles) if (request.hint) page.bubbles[i] = { ...page.bubbles[i], translation: `${page.bubbles[i].translation} (${request.hint})` };
    page.rev++;
    page.svg = renderSvg(image, page);
    stats.retranslates.push({ pageIndex: page.pageIndex, request });
    stats.requests.push(`retranslate ${page.pageIndex}`);
    console.log(`${new Date().toISOString()} RETRANSLATE page ${page.pageIndex} bubbles=${bubbles.join(',')}`);
    return json(res, 200, publicPage(page, false));
  }
  if (req.method === 'POST' && url.pathname === '/v1/pages') {
    const fields = parseMultipart(await readBody(req), req.headers['content-type'] ?? '');
    const image = fields.image;
    const size = image && imageSize(image);
    if (!size) return json(res, 400, { error: 'bad_image', detail: 'could not read image size' });
    if (TRANSLATOR_DOWN) return json(res, 503, { error: 'translator_unavailable', detail: 'mock translator down' });
    const pageId = createHash('sha256').update(image).digest('hex');
    const contextKey = fields.context_key?.toString() ?? '';
    const pageIndex = fields.page_index ? Number(fields.page_index.toString()) : undefined;
    if (fields.page_index?.toString() === INTERNAL_PAGE) return json(res, 500, { error: 'internal', detail: 'mock internal error' });
    stats.posts++;
    stats.requests.push(`page ${pageIndex}`);
    (stats.byContext[contextKey] ??= []).push(pageIndex);
    const w = work(contextKey);
    let page = pages.get(pageId);
    const cached = !!page;
    let updated = false;
    if (page) {
      stats.cacheHits++;
      if (page.version !== w.version) {
        const before = page.bubbles[0].translation;
        translateBubbles(page, w, [0]);
        updated = page.bubbles[0].translation !== before;
        if (updated) {
          page.rev++;
          page.svg = renderSvg(image, page);
          stats.updated++;
        }
      }
    } else {
      await new Promise((r) => setTimeout(r, DELAY_MS));
      page = { id: pageId, size, pageIndex, contextKey, rev: 0 };
      translateBubbles(page, w);
      page.svg = renderSvg(image, page);
      pages.set(pageId, page);
    }
    console.log(`${new Date().toISOString()} POST page ${pageIndex} ctx=${contextKey} ${size.w}x${size.h} cached=${cached} updated=${updated}`);
    return json(res, 200, publicPage(page, cached, updated ? { updated: true } : {}));
  }
  json(res, 404, { error: 'internal', detail: 'not found' });
});

server.listen(PORT, '127.0.0.1', () => console.log(`mock Yomikae server on http://127.0.0.1:${PORT} (delay ${DELAY_MS}ms)`));
