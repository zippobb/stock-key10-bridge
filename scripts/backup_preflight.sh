#!/usr/bin/env bash
set -euo pipefail

backup_root="${1:-.}"

if [[ ! -d "${backup_root}" ]]; then
  echo "backup root not found: ${backup_root}" >&2
  exit 2
fi

cd "${backup_root}"

blocked_names_regex='(^|/)(\.env($|\.)|.*\.(pem|key|p12|pfx)$|.*credentials.*|.*secrets?.*|.*token.*|.*_api_key.*)'
blocked_content_regex='(TUSHARE_TOKEN|WIND_API_KEY|GITHUB_TOKEN|Authorization:[[:space:]]*Bearer|BEGIN[[:space:]]+(RSA|OPENSSH|EC)[[:space:]]+PRIVATE[[:space:]]+KEY)'

name_hits="$(find . -type f -print | sed 's#^\./##' | grep -Ei "${blocked_names_regex}" || true)"
if [[ -n "${name_hits}" ]]; then
  echo "blocked sensitive filenames:" >&2
  echo "${name_hits}" >&2
  exit 3
fi

content_hits="$(rg -l -i --hidden --glob '!.git/**' --glob '!scripts/backup_preflight.sh' "${blocked_content_regex}" . || true)"
if [[ -n "${content_hits}" ]]; then
  echo "possible secrets found in file contents:" >&2
  echo "${content_hits}" >&2
  exit 4
fi

if ! command -v git-lfs >/dev/null 2>&1 && ! git lfs version >/dev/null 2>&1; then
  echo "git-lfs is required before backing up market data" >&2
  exit 5
fi

oversized_untracked="$(find . -type f -size +95M -not -path './.git/*' -print | while read -r file; do
  if ! git check-attr filter -- "${file}" | grep -q 'filter: lfs'; then
    echo "${file}"
  fi
done)"

if [[ -n "${oversized_untracked}" ]]; then
  echo "files larger than 95MB are not tracked by Git LFS:" >&2
  echo "${oversized_untracked}" >&2
  exit 6
fi

echo "backup preflight passed"
