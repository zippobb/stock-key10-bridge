#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "必须使用root回滚" >&2
  exit 1
fi

VERSIONS_DIR=/opt/stock-automation/versions
BACKUP=$(find "${VERSIONS_DIR}" -maxdepth 1 -type f -name 'previous_pre_v2_*.tar.gz' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)
if [[ -z ${BACKUP} || ! -f ${BACKUP} ]]; then
  echo "未找到上一版本备份" >&2
  exit 1
fi
sha256sum -c "${BACKUP}.sha256"
systemctl stop stock-key10-daily.timer stock-key10-github-publish.timer
tar -C / -xzf "${BACKUP}"
systemctl daemon-reload
systemctl enable --now stock-key10-daily.timer
systemctl enable --now stock-key10-github-publish.timer
systemctl cat stock-key10-daily.service
echo "已回滚至：${BACKUP}"
