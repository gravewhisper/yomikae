import { createHash } from 'node:crypto';
import { createServer } from 'node:http';

const PORT = Number(process.env.PORT ?? 7878);
const DELAY_MS = Number(process.env.MOCK_DELAY_MS ?? 1500);
const TRANSLATOR_DOWN = process.env.MOCK_TRANSLATOR_DOWN === '1';
const INTERNAL_PAGE = process.env.MOCK_INTERNAL_PAGE;
const pages = new Map();
const stats = { posts: 0, cacheHits: 0, byContext: {}, requests: [] };

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

function renderSvg(original, size, pageIndex) {
  const { w, h, mime } = size;
  const bw = Math.round(w * 0.42);
  const bh = Math.round(h * 0.09);
  const fs = Math.round(bh * 0.28);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
<image href="data:${mime};base64,${original.toString('base64')}" width="${w}" height="${h}"/>
<rect width="${w}" height="${h}" fill="#3b82f6" fill-opacity="0.18"/>
<ellipse cx="${w / 2}" cy="${Math.round(h * 0.12)}" rx="${bw / 2}" ry="${bh / 2}" fill="#fff" stroke="#111" stroke-width="4"/>
<text x="${w / 2}" y="${Math.round(h * 0.12)}" font-family="Comic Sans MS, sans-serif" font-size="${fs}" font-weight="700" text-anchor="middle" dominant-baseline="middle" fill="#111">TRANSLATED p${pageIndex ?? '?'}</text>
</svg>`;
}

const json = (res, status, obj) => {
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(JSON.stringify(obj));
};

const server = createServer(async (req, res) => {
  res.setHeader('access-control-allow-origin', '*');
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
  if (req.method === 'POST' && url.pathname === '/v1/pages') {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const fields = parseMultipart(Buffer.concat(chunks), req.headers['content-type'] ?? '');
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
    const cached = pages.has(pageId);
    if (cached) stats.cacheHits++;
    else {
      await new Promise((r) => setTimeout(r, DELAY_MS));
      pages.set(pageId, { svg: renderSvg(image, size, pageIndex) });
    }
    console.log(`${new Date().toISOString()} POST page ${pageIndex} ctx=${contextKey} ${size.w}x${size.h} cached=${cached}`);
    return json(res, 200, {
      page_id: pageId,
      width: size.w,
      height: size.h,
      rendered_url: `/v1/pages/${pageId}/rendered.webp`,
      bubbles: [{ box: [0, 0, 10, 10], source: '原文', translation: 'Original text', vertical: true }],
      timings_ms: { total: cached ? 0 : DELAY_MS },
      cached,
    });
  }
  json(res, 404, { error: 'internal', detail: 'not found' });
});

server.listen(PORT, '127.0.0.1', () => console.log(`mock Yomikae server on http://127.0.0.1:${PORT} (delay ${DELAY_MS}ms)`));
