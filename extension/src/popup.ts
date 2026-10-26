import { getServerUrl, parseHealth } from './contract';
import type { TabCommand } from './messages';
import { parseSiteMode } from './adapters';
import { disableSite, enableSite, getSiteMode, setSiteMode, siteAccess, siteForPattern, siteForUrl, type Site, type SiteAccess } from './sites';
import { defaultChoice, parseWork, setWorkChoice, workChoice, type Work, type WorkChoice } from './works';

const START_COMMAND = 'cd server && .venv/bin/uvicorn yomikae.app:app --host 127.0.0.1 --port 7878';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

async function showServer(): Promise<void> {
  const box = $('server');
  const title = $('server-title');
  const detail = $('server-detail');
  const cmd = $('server-cmd');
  const server = await getServerUrl();
  try {
    const res = await fetch(`${server}/v1/health`, { signal: AbortSignal.timeout(2500) });
    const health = parseHealth(await res.json());
    if (health.ok) {
      box.dataset.state = 'ok';
      title.textContent = 'Local server ready';
      detail.textContent = `${health.translator} · ${health.gpu}`;
    } else {
      box.dataset.state = 'degraded';
      title.textContent = 'Local server running, translator unavailable';
      detail.textContent = health.detail;
    }
  } catch {
    box.dataset.state = 'down';
    title.textContent = 'Local server not running. From your clone, run:';
    detail.textContent = server === 'http://127.0.0.1:7878' ? '' : `Expected at ${server}`;
    cmd.textContent = START_COMMAND;
    cmd.hidden = false;
  }
}

async function targetTab(): Promise<chrome.tabs.Tab | undefined> {
  const forced = Number(new URLSearchParams(location.search).get('tab'));
  if (forced) return chrome.tabs.get(forced);
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function showSite(): Promise<void> {
  const tab = await targetTab();
  const site = tab?.url ? siteForUrl(tab.url) : undefined;
  if (!tab?.id || !site) {
    $('site-unsupported').hidden = false;
    return;
  }
  const toggle = $<HTMLInputElement>('site-toggle');
  const mode = $<HTMLSelectElement>('site-mode');
  $('site-name').textContent = site.label;
  const workToggle = $<HTMLInputElement>('work-toggle');
  let access = await siteAccess(site);
  let work = await tabWork(tab.id);
  const render = () => {
    toggle.checked = access.state !== 'off';
    mode.hidden = !toggle.checked;
    showAccess(access);
    showWork(toggle.checked ? work : undefined);
  };
  const refresh = async () => {
    access = await siteAccess(site);
    work = await tabWork(tab.id!);
    render();
  };
  const chooseWork = (choice: WorkChoice) => {
    if (!work) return;
    const changed = { ...work, choice: choice === defaultChoice(work.language) ? undefined : choice };
    work = changed;
    render();
    void setWorkChoice(changed, choice);
  };
  mode.value = await getSiteMode(site);
  render();
  $('site').hidden = false;
  toggle.addEventListener('change', () => void onToggle(toggle, site, tab.id!).then(refresh));
  $('site-grant').addEventListener('click', () => {
    if (access.state === 'partial') void chrome.permissions.request({ origins: access.missing }).then(refresh);
  });
  workToggle.addEventListener('change', () => chooseWork(workToggle.checked ? 'on' : 'off'));
  $('work-anyway').addEventListener('click', () => chooseWork('on'));
  mode.addEventListener('change', () => {
    const picked = parseSiteMode(mode.value);
    if (picked) void setSiteMode(site, picked);
  });
}

async function tabWork(tabId: number): Promise<Work | undefined> {
  const answer: unknown = await chrome.tabs.sendMessage(tabId, { type: 'work' } satisfies TabCommand).catch(() => undefined);
  return parseWork(answer);
}

function showWork(work: Work | undefined): void {
  $('work').hidden = !work;
  if (!work) return;
  const on = workChoice(work) === 'on';
  $<HTMLInputElement>('work-toggle').checked = on;
  $('work-english').hidden = on || work.choice !== undefined;
}

function showAccess(access: SiteAccess): void {
  $('site-access').hidden = access.state !== 'partial';
  if (access.state !== 'partial') return;
  const hosts = access.missing.map((o) => siteForPattern(o)?.label ?? o).join(', ');
  $('site-access-text').textContent = `Images on this site come from ${hosts}.`;
}

async function onToggle(toggle: HTMLInputElement, site: Site, tabId: number): Promise<void> {
  toggle.disabled = true;
  try {
    if (toggle.checked) {
      const granted = await chrome.permissions.request({ origins: site.origins });
      if (granted) await enableSite(site);
      else toggle.checked = false;
    } else {
      await disableSite(site);
      await chrome.tabs.sendMessage(tabId, { type: 'disable' } satisfies TabCommand).catch(() => undefined);
    }
  } finally {
    toggle.disabled = false;
  }
}

void showServer();
void showSite();
