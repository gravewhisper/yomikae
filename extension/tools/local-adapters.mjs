import { existsSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';

const own = resolve(import.meta.dirname, '../adapters.local');
const external = process.env.YOMIKAE_ADAPTERS && resolve(process.env.YOMIKAE_ADAPTERS);
if (external && !existsSync(external)) throw new Error(`YOMIKAE_ADAPTERS points at ${external}, which does not exist`);

export const adapterDirs = [...new Set([own, external].filter((d) => d && existsSync(d)))];

export const localFiles = (wanted) => adapterDirs.flatMap((d) => readdirSync(d).filter(wanted).sort().map((f) => join(d, f)));

export const isAdapter = (f) => f.endsWith('.ts') && !f.endsWith('.test.ts') && !f.endsWith('.d.ts');
export const isTest = (f) => f.endsWith('.test.ts');
export const isMock = (f) => f.endsWith('.mock.mjs');

export const loadMocks = () => Promise.all(localFiles(isMock).map((f) => import(f)));
