#!/usr/bin/env bash
set -euo pipefail

types='feat|fix|perf|refactor|docs|test|build|ci|chore|style|revert'
pattern="^(${types})(\([a-z0-9][a-z0-9-]*\))?!?: [^ ].*[^. ]$"
status=0

while IFS= read -r header || [ -n "$header" ]; do
  [ -z "$header" ] && continue
  case "$header" in
    Merge\ *|Revert\ \"*|fixup!\ *|squash!\ *) continue ;;
  esac
  if [ "${#header}" -gt 100 ]; then
    echo "too long (${#header} > 100 characters): $header" >&2
    status=1
  elif ! [[ "$header" =~ $pattern ]]; then
    echo "not a Conventional Commit: $header" >&2
    status=1
  fi
done

if [ "$status" -ne 0 ]; then
  echo "Use 'type(scope): description', for example 'fix(typeset): keep lettering inside its bubble'. Types: ${types//|/, }. CONTRIBUTING.md has the rules." >&2
fi
exit "$status"
