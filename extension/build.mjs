import { build } from 'esbuild';
import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { basename, resolve } from 'node:path';
import { isAdapter, localFiles } from './tools/local-adapters.mjs';

const extraHosts = (process.env.ML_EXTRA_HOSTS ?? '').split(',').filter(Boolean);
const outdir = process.env.ML_OUTDIR ?? 'dist';
const localAdapters = localFiles(isAdapter);

const localAdaptersPlugin = {
  name: 'local-adapters',
  setup(build) {
    build.onResolve({ filter: /^#adapter$/ }, () => ({ path: resolve('src/adapter.ts') }));
    build.onLoad({ filter: /[\\/]src[\\/]local-adapters\.ts$/ }, () => ({
      loader: 'ts',
      contents: [...localAdapters.map((f, i) => `import a${i} from ${JSON.stringify(f)};`), `export const localAdapters = [${localAdapters.map((_, i) => `a${i}`).join(', ')}];`].join('\n'),
    }));
  },
};

await rm(outdir, { recursive: true, force: true });
await mkdir(outdir, { recursive: true });
await build({
  entryPoints: { background: 'src/background.ts', content: 'src/content.ts', popup: 'src/popup.ts', sidepanel: 'src/sidepanel.ts' },
  outdir,
  bundle: true,
  format: 'iife',
  target: 'chrome116',
  logLevel: 'warning',
  plugins: [localAdaptersPlugin],
});
await cp('static', outdir, { recursive: true });
const manifest = JSON.parse(await readFile('static/manifest.json', 'utf8'));
manifest.host_permissions.push(...extraHosts);
await writeFile(`${outdir}/manifest.json`, JSON.stringify(manifest, null, 2));
const notes = [localAdapters.length && `local adapters: ${localAdapters.map((f) => basename(f, '.ts')).join(', ')}`, extraHosts.length && `extra hosts: ${extraHosts.join(', ')}`].filter(Boolean);
console.log(`built ${outdir}${notes.length ? ` (${notes.join('; ')})` : ''}`);
