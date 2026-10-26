import { spawnSync } from 'node:child_process';
import { readdirSync } from 'node:fs';
import { isTest, localFiles } from './local-adapters.mjs';

const kit = new URL('../src/adapter.ts', import.meta.url).href;
const data = (js) => `data:text/javascript,${encodeURIComponent(js)}`;
const hook = data(`export const resolve = (s, c, next) => (s === '#adapter' ? { url: ${JSON.stringify(kit)}, shortCircuit: true } : next(s, c));`);
const register = data(`import { register } from 'node:module'; register(${JSON.stringify(hook)});`);
const files = [
  ...['test', 'adapters'].flatMap((d) => readdirSync(new URL(`../${d}`, import.meta.url)).filter(isTest).map((f) => `${d}/${f}`)),
  ...localFiles(isTest),
];
const run = spawnSync(process.execPath, ['--import', register, '--experimental-strip-types', '--no-warnings', '--test', ...files], { stdio: 'inherit' });
process.exit(run.status ?? 1);
