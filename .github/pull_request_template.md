## What this changes

## Why

## Checks

- [ ] Server tests pass: `CUDA_VISIBLE_DEVICES= PYTHONPATH=$PWD .venv/bin/python -m pytest -q`
- [ ] Extension checks pass: `pnpm test`, `pnpm typecheck` and `pnpm build`
- [ ] Docs updated where behavior changed, and `PIPELINE_VERSION` raised if output changed
- [ ] Before and after numbers included, if translation or lettering changed
- [ ] No code comments, model files, datasets or manga images added
