import { readFileSync } from 'node:fs';
import { createServer } from 'node:https';
import { loadMocks } from './local-adapters.mjs';

const PORT = Number(process.env.PORT ?? 8790);
const CERT_DIR = process.env.CERT_DIR;
const requests = [];
const localMocks = await loadMocks();

export const TWEETS = Array.from({ length: 24 }, (_, i) => ({ id: String(9000 + i), user: `artist${i % 3}`, media: `Gfeed${String(i).padStart(2, '0')}AAx_q` }));
export const THREAD = { id: '100', user: 'artist', media: ['GthrA1AAx_q', 'GthrA2AAx_q', 'GthrA3AAx_q', 'GthrA4AAx_q'] };
export const SINGLES = [
  { id: '200', user: 'artist', media: ['GsolA1AAx_q'] },
  { id: '300', user: 'artist', media: ['GsolB1AAx_q'] },
];
export const TALL = { id: '400', user: 'artist', media: ['GtalA1AAx_q'] };
const TALL_RATIO = 510 / 162.55;
const STATUSES = [THREAD, ...SINGLES, TALL];
export const PNG_ORIGINAL = new Set(['Gfeed01AAx_q']);
export const ARTWORKS = {
  5001: { date: '2026/10/24/20/00/54/', pages: [[1200, 1700], [1200, 1700], [1200, 1700], [1200, 1700], [1200, 1700]] },
  5002: { date: '2026/10/24/21/00/00/', pages: [[800, 9000]] },
  5003: { date: '2026/10/24/22/00/00/', pages: [[1200, 1700], [1200, 1700]] },
};
function svg(w, h, label, hue) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
<rect width="${w}" height="${h}" fill="hsl(${hue},45%,88%)"/>
<rect x="20" y="20" width="${w - 40}" height="${h - 40}" fill="none" stroke="#222" stroke-width="8"/>
<text x="${w / 2}" y="${Math.min(h / 2, 400)}" font-size="${Math.round(w / 12)}" font-weight="900" text-anchor="middle" fill="#222">${label}</text>
</svg>`;
}

const hueOf = (s) => [...s].reduce((a, c) => (a * 31 + c.charCodeAt(0)) % 360, 7);

const send = (res, status, type, body, extra = {}) => {
  res.writeHead(status, { 'content-type': type, 'cache-control': 'max-age=3600', ...extra });
  res.end(body);
  return true;
};

const RNW_CSS = `.ph{position:relative;overflow:hidden;background:#222} .rnw-pos{position:absolute;inset:0} .rnw{position:absolute;inset:0;overflow:hidden;transform:translate(0,0);z-index:0}
.rnw-bg{position:absolute;inset:0;background-size:cover;background-position:center;background-repeat:no-repeat;z-index:-1}
.rnw img{position:absolute;inset:0;width:100%;height:100%;opacity:0;z-index:-1} #modal .rnw img{z-index:auto}`;

const RNW_JS = `function rnwImage(holder, url) {
  holder.dataset.url = url;
  let fill = holder.querySelector('.rnw');
  if (!fill) {
    const pos = document.createElement('div');
    pos.className = 'rnw-pos';
    pos.innerHTML = '<div class="rnw" data-testid="tweetPhoto" aria-label="Image" style="margin:0px"><div class="rnw-bg"></div></div>';
    holder.append(pos);
    fill = pos.firstChild;
  }
  fill.firstChild.style.backgroundImage = '';
  fill.querySelector('img')?.remove();
  const probe = new Image();
  probe.onload = () => {
    if (holder.dataset.url !== url) return;
    const extra = Math.max(0, (holder.clientWidth * probe.naturalHeight / probe.naturalWidth - holder.clientHeight) / 2);
    fill.style.margin = extra ? '-' + (extra / holder.clientWidth) * 100 + '% 0' : '0px';
    fill.firstChild.style.backgroundImage = 'url("' + url + '")';
    const img = document.createElement('img');
    img.alt = 'Image';
    img.draggable = true;
    img.src = url;
    fill.append(img);
  };
  probe.src = url;
}
document.querySelectorAll('.ph[data-src]').forEach((h) => rnwImage(h, h.dataset.src));
document.addEventListener('mouseover', (e) => {
  for (const a of document.querySelectorAll('article')) a.style.backgroundColor = a.contains(e.target) ? 'rgba(255,255,255,.03)' : '';
});`;

const mediaUrl = (m, name) => `https://pbs.twimg.com/media/${m}?format=jpg&name=${name}`;

const photoLink = (t, m, n, name) =>
  `<a href="/${t.user}/status/${t.id}/photo/${n + 1}" role="link" onclick="return window.openPhoto(event, ${n + 1})"><div class="ph" aria-label="Image" data-src="${mediaUrl(m, name)}"></div></a>`;

const tallPhoto = (t, m) => `<div class="tall-frame"><div class="tall-cap"><div class="rel"><div class="rel">${photoLink(t, m, 0, 'small')}</div></div></div></div>`;

const article = (t, media, { grid = false, tall = false } = {}) => `<article data-tweet="${t.id}">
  <div class="head"><img class="avatar" src="https://pbs.twimg.com/profile_images/1/${t.user}_normal.jpg" width="40" height="40">
  <a href="/${t.user}/status/${t.id}"><time>1h</time></a></div>
  ${tall ? tallPhoto(t, media[0]) : `<div class="${grid ? 'grid' : 'media'}">${media.map((m, n) => photoLink(t, m, n, grid ? 'small' : 'medium')).join('')}</div>`}
</article>`;

const page = (title, css, body, script = '') => `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title>
<style>body{margin:0;background:#000;color:#eee;font:14px system-ui} main{width:600px;margin:0 auto} article{border-bottom:1px solid #333;padding:12px}
.head{display:flex;gap:8px;align-items:center;margin-bottom:8px} .avatar{border-radius:50%} .media .ph{width:560px;height:400px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:2px} .grid .ph{width:278px;height:278px}
.tall-frame{position:relative;overflow:hidden;border:1px solid #333;border-radius:16px;width:fit-content} .tall-cap{position:relative;max-width:162.55px} .rel, .tall-frame a{position:relative;display:block}
.tall-frame .ph{width:162.55px;height:510px}
#modal{position:fixed;inset:0;background:rgba(0,0,0,.9);display:flex} #modal .viewer{flex:1;display:flex;align-items:center;justify-content:center}
#modal .photo{height:90vh;aspect-ratio:1/1.4;background:none} #modal .photo .rnw-bg{background-size:contain} #modal aside{width:300px;border-left:1px solid #333;padding:12px}
${RNW_CSS}${css}</style></head>
<body>${body}<div id="layers"></div><script>${RNW_JS}\n${script}</script></body></html>`;

function feedHtml() {
  const slots = 6;
  const height = 520;
  return page(
    'Home / X',
    `#feed{position:relative;height:${TWEETS.length * height}px} #feed article{position:absolute;left:0;right:0;height:${height - 25}px;box-sizing:border-box}`,
    `<main><div id="feed"></div></main>`,
    `const TWEETS = ${JSON.stringify(TWEETS)};
const H = ${height};
const feed = document.getElementById('feed');
const pool = [];
window.recycles = 0;
function make() {
  const el = document.createElement('article');
  el.innerHTML = '<div class="head"><img class="avatar" width="40" height="40"><a><time>1h</time></a></div><div class="media"><a><div class="ph" aria-label="Image"></div></a></div>';
  feed.append(el);
  return el;
}
function fill(el, i) {
  const t = TWEETS[i];
  if (el.dataset.index === String(i)) return;
  if (el.dataset.index !== undefined) window.recycles++;
  el.dataset.index = i;
  el.dataset.tweet = t.id;
  el.style.top = (i * H) + 'px';
  el.querySelector('.avatar').src = 'https://pbs.twimg.com/profile_images/1/' + t.user + '_normal.jpg';
  el.querySelector('.head a').href = '/' + t.user + '/status/' + t.id;
  el.querySelector('.media a').href = '/' + t.user + '/status/' + t.id + '/photo/1';
  rnwImage(el.querySelector('.media .ph'), 'https://pbs.twimg.com/media/' + t.media + '?format=jpg&name=medium');
}
function render() {
  const first = Math.max(0, Math.floor(scrollY / H) - 1);
  const wanted = [];
  for (let i = first; i < Math.min(TWEETS.length, first + ${slots}); i++) wanted.push(i);
  const free = pool.filter((el) => !wanted.includes(Number(el.dataset.index)));
  for (const i of wanted) {
    if (pool.some((el) => Number(el.dataset.index) === i)) continue;
    fill(free.pop() ?? (pool.push(make()), pool[pool.length - 1]), i);
  }
}
addEventListener('scroll', render, { passive: true });
render();
window.slotFor = (i) => pool.find((el) => Number(el.dataset.index) === i);
window.unmount = (i) => { const el = window.slotFor(i); pool.splice(pool.indexOf(el), 1); el.remove(); };
window.remount = () => render();
window.openPhoto = () => false;`,
  );
}

function statusHtml(t) {
  const reply = { id: String(Number(t.id) + 1), user: 'reader' };
  return page(
    `${t.user} on X`,
    '',
    `<main>${article(t, t.media, { grid: t.media.length > 1, tall: t === TALL })}${article(reply, [])}</main>`,
    `const T = ${JSON.stringify(t)};
const base = '/' + T.user + '/status/' + T.id;
function showPhoto(n) {
  document.getElementById('modal')?.remove();
  const modal = document.createElement('div');
  modal.id = 'modal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.innerHTML = '<div class="viewer"><div class="ph photo" aria-label="Image"></div></div><aside>' + T.user + '</aside>';
  document.getElementById('layers').append(modal);
  rnwImage(modal.querySelector('.photo'), 'https://pbs.twimg.com/media/' + T.media[n - 1] + '?format=jpg&name=medium');
}
window.openPhoto = (e, n) => {
  e.preventDefault();
  history.pushState({}, '', base + '/photo/' + n);
  showPhoto(n);
  return false;
};
window.closePhoto = () => { document.getElementById('modal')?.remove(); history.pushState({}, '', base); };
const direct = location.pathname.match(/\\/photo\\/(\\d+)$/);
if (direct) showPhoto(Number(direct[1]));`,
  );
}

function artworkHtml(id) {
  const work = ARTWORKS[id];
  const imgs = work.pages
    .map(([w, h], p) => `<div class="pg"><img src="https://i.pximg.net/img-master/img/${work.date}${id}_p${p}_master1200.jpg" width="${Math.round(w * (600 / w))}" height="${Math.round(h * (600 / w))}" alt="${id}_p${p}"></div>`)
    .join('');
  return page(
    `pixiv artwork ${id}`,
    '.pg img{display:block;width:600px;height:auto;margin-bottom:16px} .related img{width:90px;height:90px}',
    `<main><div class="author"><img src="https://i.pximg.net/user-profile/img/2019/05/13/23/50/47/1_50.png" width="40" height="40"></div>${imgs}
<div class="related"><img src="https://i.pximg.net/c/250x250_80_a2/custom-thumb/img/${work.date}${id}_p0_custom1200.jpg"></div></main>`,
  );
}

function pximg(url, req, res) {
  if (!(req.headers.referer ?? '').startsWith('https://www.pixiv.net/')) return send(res, 403, 'text/html', '<h1>403 Forbidden</h1>');
  const m = url.pathname.match(/^\/(?:c\/[^/]+\/)?(img-master|img-original|custom-thumb|user-profile)\/img\/(?:\d+\/){6}(\d+)_(?:p(\d+))?/);
  if (!m) return send(res, 404, 'text/plain', 'not found');
  if (m[1] === 'custom-thumb' || m[1] === 'user-profile') return send(res, 200, 'image/svg+xml', svg(250, 250, 'thumb', 30));
  const work = ARTWORKS[m[2]];
  const p = Number(m[3]);
  const size = work?.pages[p];
  if (!size) return send(res, 404, 'text/plain', 'not found');
  const original = m[1] === 'img-original';
  if (original && !url.pathname.endsWith('.png')) return send(res, 404, 'text/plain', 'not found');
  const [w, h] = original ? size : [Math.min(1200, size[0]), Math.round(size[1] * (Math.min(1200, size[0]) / size[0]))];
  send(res, 200, 'image/svg+xml', svg(w, h, `${m[2]} p${p} ${original ? 'orig' : 'm1200'}`, hueOf(`${m[2]}${p}`)));
}

function twimg(url, res) {
  if (url.pathname.startsWith('/profile_images/')) return send(res, 200, 'image/svg+xml', svg(48, 48, '', 200));
  const id = url.pathname.match(/^\/media\/([\w-]+)$/)?.[1];
  if (!id) return send(res, 404, 'text/plain', 'not found');
  const format = url.searchParams.get('format');
  const name = url.searchParams.get('name') ?? 'medium';
  if (name === 'orig' && format !== (PNG_ORIGINAL.has(id) ? 'png' : 'jpg')) return send(res, 404, 'text/plain', 'not found');
  const side = { small: 680, medium: 1200, large: 2048, orig: 1400, '4096x4096': 1400, '900x900': 900 }[name] ?? 1200;
  const [width, height] = TALL.media.includes(id) ? [Math.round(side / TALL_RATIO), side] : [side, Math.round(side * 1.4)];
  send(res, 200, 'image/svg+xml', svg(width, height, `${id} ${name}`, hueOf(id)));
}

function pixivAjax(id, res) {
  const work = ARTWORKS[id];
  if (!work) return send(res, 404, 'application/json', JSON.stringify({ error: true, message: 'not found', body: [] }));
  const body = work.pages.map(([width, height], p) => ({
    urls: {
      thumb_mini: `https://i.pximg.net/c/128x128/img-master/img/${work.date}${id}_p${p}_square1200.jpg`,
      small: `https://i.pximg.net/c/540x540_70/img-master/img/${work.date}${id}_p${p}_master1200.jpg`,
      regular: `https://i.pximg.net/img-master/img/${work.date}${id}_p${p}_master1200.jpg`,
      original: `https://i.pximg.net/img-original/img/${work.date}${id}_p${p}.png`,
    },
    width,
    height,
  }));
  send(res, 200, 'application/json', JSON.stringify({ error: false, message: '', body }));
}

function handle(req, res) {
  const url = new URL(req.url, `https://${req.headers.host}`);
  const host = url.hostname;
  if (url.pathname === '/__requests') return send(res, 200, 'application/json', JSON.stringify(requests), { 'cache-control': 'no-store' });
  requests.push({ host, path: url.pathname + url.search, dest: req.headers['sec-fetch-dest'] ?? null, referer: req.headers.referer ?? null, status: 0 });
  const log = requests[requests.length - 1];
  res.on('finish', () => (log.status = res.statusCode));
  if (host === 'pbs.twimg.com') return twimg(url, res);
  if (host === 'i.pximg.net') return pximg(url, req, res);
  if (host === 'x.com') {
    if (url.pathname === '/home') return send(res, 200, 'text/html; charset=utf-8', feedHtml(), { 'cache-control': 'no-store' });
    const status = STATUSES.find((t) => new RegExp(`^/${t.user}/status/${t.id}(?:/photo/\\d+)?$`).test(url.pathname));
    if (status) return send(res, 200, 'text/html; charset=utf-8', statusHtml(status), { 'cache-control': 'no-store' });
  }
  if (localMocks.some((m) => m.handle(url, req, res, { send, svg, hueOf }))) return;
  if (host === 'www.pixiv.net') {
    const ajax = url.pathname.match(/^\/ajax\/illust\/(\d+)\/pages$/);
    if (ajax) return pixivAjax(ajax[1], res);
    const art = url.pathname.match(/^\/(?:en\/)?artworks\/(\d+)$/);
    if (art && ARTWORKS[art[1]]) return send(res, 200, 'text/html; charset=utf-8', artworkHtml(art[1]), { 'cache-control': 'no-store' });
  }
  send(res, 404, 'text/plain', 'not found');
}

if (CERT_DIR) {
  createServer({ key: readFileSync(`${CERT_DIR}/key.pem`), cert: readFileSync(`${CERT_DIR}/cert.pem`) }, handle).listen(PORT, '127.0.0.1', () =>
    console.log(`site mock (${['x.com', 'pbs.twimg.com', 'www.pixiv.net', 'i.pximg.net', ...localMocks.flatMap((m) => m.hosts)].join(', ')}) on https://127.0.0.1:${PORT}/`),
  );
}
