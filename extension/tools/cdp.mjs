import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function launchChromium({ profile, port, extension, args = [] }) {
  await mkdir(profile, { recursive: true });
  const proc = spawn(
    process.env.CHROMIUM ?? 'chromium',
    [
      `--user-data-dir=${profile}`,
      `--remote-debugging-port=${port}`,
      `--load-extension=${extension}`,
      '--no-first-run',
      '--no-default-browser-check',
      '--window-size=1280,1000',
      '--disable-renderer-backgrounding',
      '--disable-backgrounding-occluded-windows',
      '--disable-background-timer-throttling',
      ...args,
      'about:blank',
    ],
    { stdio: 'ignore', detached: true },
  );
  for (let i = 0; i < 50; i++) {
    try {
      await fetch(`http://127.0.0.1:${port}/json/version`);
      return proc;
    } catch {
      await sleep(200);
    }
  }
  throw new Error('chromium did not start');
}

export async function targets(port) {
  return (await fetch(`http://127.0.0.1:${port}/json`)).json();
}

export async function waitTarget(port, pred, timeout = 15000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const t = (await targets(port)).find(pred);
    if (t) return t;
    await sleep(200);
  }
  throw new Error('target not found');
}

export async function openTab(port, url) {
  const res = await fetch(`http://127.0.0.1:${port}/json/new?${url}`, { method: 'PUT' });
  return res.json();
}

export async function attach(target) {
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r, j) => ((ws.onopen = r), (ws.onerror = j)));
  let id = 0;
  const pending = new Map();
  const listeners = [];
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
    } else if (m.method) listeners.forEach((l) => l(m));
  };
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const mid = ++id;
      pending.set(mid, { resolve, reject });
      ws.send(JSON.stringify({ id: mid, method, params }));
    });
  const evaluate = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, userGesture: true });
    if (r.exceptionDetails) throw new Error(`evaluate failed: ${r.exceptionDetails.exception?.description ?? r.exceptionDetails.text}`);
    return r.result.value;
  };
  const screenshot = async (path) => {
    const { data } = await send('Page.captureScreenshot', { format: 'png' });
    await writeFile(path, Buffer.from(data, 'base64'));
    return path;
  };
  const waitFor = async (expression, timeout = 20000, interval = 150) => {
    const end = Date.now() + timeout;
    let last;
    while (Date.now() < end) {
      last = await evaluate(expression).catch((e) => e.message);
      if (last === true || (last && typeof last === 'object' && last.ok)) return last;
      await sleep(interval);
    }
    throw new Error(`timed out waiting for: ${expression}\nlast: ${JSON.stringify(last)}`);
  };
  const key = async (type, keyName, code, keyCode) =>
    send('Input.dispatchKeyEvent', { type, key: keyName, code, windowsVirtualKeyCode: keyCode, modifiers: type === 'keyDown' && keyName === 'Alt' ? 1 : 0 });
  return { ws, send, evaluate, screenshot, waitFor, key, on: (l) => listeners.push(l), close: () => ws.close() };
}

export function check(results, ok, label, detail) {
  results.push({ ok: !!ok, label, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${label}${detail !== undefined ? ` ${JSON.stringify(detail)}` : ''}`);
}

export async function extensionWorker(port, name, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    for (const t of await targets(port)) {
      if (t.type !== 'service_worker' || !t.url.startsWith('chrome-extension://')) continue;
      const s = await attach(t);
      const n = await s.evaluate(`globalThis.chrome?.runtime?.getManifest?.().name`).catch(() => undefined);
      if (n === name) return { session: s, target: t, extId: new URL(t.url).host };
      s.close();
    }
    await sleep(300);
  }
  throw new Error(`service worker for ${name} not found`);
}

export async function chipNodes(page, selector) {
  const { root } = await page.send('DOM.getDocument', { depth: -1, pierce: true });
  const find = (n) => (n.nodeName === 'YOMIKAE-CHIPS' ? n : (n.children ?? []).map(find).find(Boolean));
  const shadowRoot = find(root)?.shadowRoots?.[0];
  if (!shadowRoot) return [];
  const { nodeIds } = await page.send('DOM.querySelectorAll', { nodeId: shadowRoot.nodeId, selector });
  return Promise.all(
    nodeIds.map(async (nodeId) => {
      const { outerHTML } = await page.send('DOM.getOuterHTML', { nodeId });
      const box = await page.send('DOM.getBoxModel', { nodeId }).then((b) => b.model.content, () => null);
      return { outerHTML, box, visible: !!box };
    }),
  );
}

export async function waitChip(page, selector, pred = (n) => n.visible, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const hit = (await chipNodes(page, selector)).find(pred);
    if (hit) return hit;
    await sleep(150);
  }
  throw new Error(`timed out waiting for chip node ${selector}`);
}

export const center = (quad) => ({ x: (quad[0] + quad[4]) / 2, y: (quad[1] + quad[5]) / 2 });
export const mouse = (page, type, { x, y }, { held = false, modifiers = 0 } = {}) =>
  page.send('Input.dispatchMouseEvent', { type, x, y, modifiers, button: type === 'mouseMoved' && !held ? 'none' : 'left', buttons: held || type === 'mousePressed' ? 1 : 0, clickCount: type === 'mouseMoved' ? 0 : 1 });
