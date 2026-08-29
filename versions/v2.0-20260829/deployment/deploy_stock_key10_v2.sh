#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "必须使用root部署" >&2
  exit 1
fi

PACKAGE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SCANNER_DIR=/opt/stock-automation/scanner
PYTHON_BIN=${SCANNER_DIR}/venv/bin/python
PILLOW_WHEEL=${PACKAGE_DIR}/wheels/pillow-11.3.0-cp310-cp310-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl
SYSTEMD_DIR=/etc/systemd/system
VERSIONS_DIR=/opt/stock-automation/versions
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_PATH=${VERSIONS_DIR}/previous_pre_v2_${STAMP}.tar.gz
RUN_NOW=false
if [[ ${1:-} == "--run-now" ]]; then
  RUN_NOW=true
fi

test -d "${SCANNER_DIR}"
test -x "${PYTHON_BIN}"
test -f "${SCANNER_DIR}/config/observation_pool.csv"
test -f "${PACKAGE_DIR}/scanner/stock_key10_v2_daily.py"
test -f "${PACKAGE_DIR}/scanner/config/strategy_v2.json"
test -f "${PACKAGE_DIR}/scanner/config/NotoSansSC-Regular.ttf"
test -f "${PILLOW_WHEEL}"

if [[ $(uname -m) != "x86_64" ]]; then
  echo "离线Pillow轮包仅适用于x86_64，当前架构：$(uname -m)" >&2
  exit 1
fi
"${PYTHON_BIN}" - <<'PY'
import sys
if sys.version_info[:2] != (3, 10):
    raise SystemExit(f"V2.0离线依赖要求Python 3.10，当前为{sys.version.split()[0]}")
PY
"${PYTHON_BIN}" -m pip --version >/dev/null

(
  cd "${PACKAGE_DIR}"
  sha256sum -c SHA256SUMS
)

"${PYTHON_BIN}" -m py_compile \
  "${PACKAGE_DIR}/scanner/stock_key10_v2_common.py" \
  "${PACKAGE_DIR}/scanner/stock_key10_v2_data.py" \
  "${PACKAGE_DIR}/scanner/stock_key10_v2_scoring.py" \
  "${PACKAGE_DIR}/scanner/stock_key10_v2_daily.py" \
  "${PACKAGE_DIR}/scanner/stock_key10_v2_finalize.py" \
  "${PACKAGE_DIR}/scanner/render_key10_v2.py" \
  "${PACKAGE_DIR}/scanner/export_key10_v2_bundle.py" \
  "${PACKAGE_DIR}/scanner/build_key10_v2_review_template.py"
"${PYTHON_BIN}" -m json.tool "${PACKAGE_DIR}/scanner/config/strategy_v2.json" >/dev/null

mkdir -p "${VERSIONS_DIR}"
TMP_BACKUP=$(mktemp "${VERSIONS_DIR}/.previous_pre_v2.XXXXXX.tar.gz")
tar -C / -czf "${TMP_BACKUP}" \
  --exclude='opt/stock-automation/scanner/venv' \
  --exclude='opt/stock-automation/scanner/data' \
  --exclude='opt/stock-automation/scanner/output' \
  --exclude='opt/stock-automation/scanner/logs' \
  --exclude='*.env' \
  --exclude='*token*' \
  opt/stock-automation/scanner \
  etc/systemd/system/stock-key10-daily.service \
  etc/systemd/system/stock-key10-daily.timer \
  etc/systemd/system/stock-key10-github-publish.timer \
  etc/systemd/system/stock-key10-daily.service.d 2>/dev/null || {
    rm -f -- "${TMP_BACKUP}"
    echo "旧版本备份失败，未修改系统" >&2
    exit 1
  }
mv -- "${TMP_BACKUP}" "${BACKUP_PATH}"
sha256sum "${BACKUP_PATH}" > "${BACKUP_PATH}.sha256"

PIL_PREEXISTED=false
if "${PYTHON_BIN}" -c 'from PIL import Image' >/dev/null 2>&1; then
  PIL_PREEXISTED=true
else
  if ! "${PYTHON_BIN}" -m pip install --no-index --no-deps "${PILLOW_WHEEL}"; then
    "${PYTHON_BIN}" -m pip uninstall -y Pillow >/dev/null 2>&1 || true
    echo "离线Pillow安装失败；业务系统未修改，旧版备份已保留：${BACKUP_PATH}" >&2
    exit 1
  fi
fi

"${PYTHON_BIN}" - <<'PY'
import PIL
from PIL import Image
print(f"Pillow预检通过：{PIL.__version__}")
PY

if ! PYTHONPATH="${PACKAGE_DIR}/scanner" \
  STOCK_V2_FONT="${PACKAGE_DIR}/scanner/config/NotoSansSC-Regular.ttf" \
  "${PYTHON_BIN}" "${PACKAGE_DIR}/scanner/tests/run_tests.py"; then
  if ! ${PIL_PREEXISTED}; then
    "${PYTHON_BIN}" -m pip uninstall -y Pillow >/dev/null 2>&1 || true
  fi
  echo "V2.0回归测试失败；业务系统未修改，旧版备份已保留：${BACKUP_PATH}" >&2
  exit 1
fi

MUTATION_STARTED=true
rollback_on_error() {
  local exit_code=$?
  trap - ERR
  set +e
  if ${MUTATION_STARTED}; then
    echo "部署写入阶段失败，正在恢复上一版本：${BACKUP_PATH}" >&2
    systemctl stop stock-key10-daily.timer stock-key10-github-publish.timer
    rm -f -- \
      "${SCANNER_DIR}/stock_key10_v2_common.py" \
      "${SCANNER_DIR}/stock_key10_v2_data.py" \
      "${SCANNER_DIR}/stock_key10_v2_scoring.py" \
      "${SCANNER_DIR}/stock_key10_v2_daily.py" \
      "${SCANNER_DIR}/stock_key10_v2_finalize.py" \
      "${SCANNER_DIR}/render_key10_v2.py" \
      "${SCANNER_DIR}/export_key10_v2_bundle.py" \
      "${SCANNER_DIR}/build_key10_v2_review_template.py" \
      "${SCANNER_DIR}/config/strategy_v2.json" \
      "${SCANNER_DIR}/VERSION.json"
    tar -C / -xzf "${BACKUP_PATH}"
    systemctl daemon-reload
    systemctl enable --now stock-key10-daily.timer
    systemctl enable --now stock-key10-github-publish.timer
  fi
  exit "${exit_code}"
}
trap rollback_on_error ERR

install -m 0755 "${PACKAGE_DIR}/scanner/stock_key10_v2_common.py" "${SCANNER_DIR}/"
install -m 0755 "${PACKAGE_DIR}/scanner/stock_key10_v2_data.py" "${SCANNER_DIR}/"
install -m 0755 "${PACKAGE_DIR}/scanner/stock_key10_v2_scoring.py" "${SCANNER_DIR}/"
install -m 0755 "${PACKAGE_DIR}/scanner/stock_key10_v2_daily.py" "${SCANNER_DIR}/"
install -m 0755 "${PACKAGE_DIR}/scanner/stock_key10_v2_finalize.py" "${SCANNER_DIR}/"
install -m 0755 "${PACKAGE_DIR}/scanner/render_key10_v2.py" "${SCANNER_DIR}/"
install -m 0755 "${PACKAGE_DIR}/scanner/export_key10_v2_bundle.py" "${SCANNER_DIR}/"
install -m 0755 "${PACKAGE_DIR}/scanner/build_key10_v2_review_template.py" "${SCANNER_DIR}/"
install -m 0644 "${PACKAGE_DIR}/scanner/config/strategy_v2.json" "${SCANNER_DIR}/config/strategy_v2.json"
install -m 0644 "${PACKAGE_DIR}/scanner/config/NotoSansSC-Regular.ttf" "${SCANNER_DIR}/config/NotoSansSC-Regular.ttf"
install -m 0644 "${PACKAGE_DIR}/VERSION.json" "${SCANNER_DIR}/VERSION.json"

python3 - "${SCANNER_DIR}/config/observation_pool.csv" <<'PY'
import csv
import os
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
with path.open("r", encoding="utf-8-sig", newline="") as handle:
    reader = csv.DictReader(handle)
    rows = list(reader)
    fields = list(reader.fieldnames or [])
if len(rows) != 237:
    raise SystemExit(f"正式观察池必须为237只，实际{len(rows)}只")
if "ts_code" not in fields or len({row["ts_code"] for row in rows}) != 237:
    raise SystemExit("正式观察池ts_code缺失或重复")
focus_fields = {"is_focus", "focus", "focus_rank", "pool_type"}
fields = [field for field in fields if field not in focus_fields]
fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{field: row.get(field, "") for field in fields} for row in rows])
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
except Exception:
    if os.path.exists(temporary):
        os.unlink(temporary)
    raise
PY

rm -f -- "${SCANNER_DIR}/config/focus_pool.csv"
rm -f -- \
  "${SYSTEMD_DIR}/stock-key10-daily.service.d/10-v11-shadow.conf" \
  "${SYSTEMD_DIR}/stock-key10-daily.service.d/20-v12-fusion.conf" \
  "${SYSTEMD_DIR}/stock-key10-daily.service.d/export.conf"

install -m 0644 "${PACKAGE_DIR}/systemd/stock-key10-daily.service" "${SYSTEMD_DIR}/stock-key10-daily.service"
install -m 0644 "${PACKAGE_DIR}/systemd/stock-key10-daily.timer" "${SYSTEMD_DIR}/stock-key10-daily.timer"
install -m 0644 "${PACKAGE_DIR}/systemd/stock-key10-github-publish.timer" "${SYSTEMD_DIR}/stock-key10-github-publish.timer"

systemd-analyze verify "${SYSTEMD_DIR}/stock-key10-daily.service" "${SYSTEMD_DIR}/stock-key10-daily.timer"
systemctl daemon-reload
systemctl enable --now stock-key10-daily.timer
systemctl enable --now stock-key10-github-publish.timer

if ${RUN_NOW}; then
  systemctl start stock-key10-daily.service
fi

while IFS= read -r -d '' old_backup; do
  rm -f -- "${old_backup}" "${old_backup}.sha256"
done < <(find "${VERSIONS_DIR}" -maxdepth 1 -type f -name 'previous_pre_v2_*.tar.gz' ! -path "${BACKUP_PATH}" -print0)

systemctl cat stock-key10-daily.service
systemctl list-timers stock-key10-daily.timer stock-key10-github-publish.timer --no-pager
trap - ERR
echo "V2.0部署完成；服务器保留的上一版本备份：${BACKUP_PATH}"
