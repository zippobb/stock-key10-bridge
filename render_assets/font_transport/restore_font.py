"""Restore pinned font from connector-readable UTF-8 chunks; fail closed."""
import base64, gzip, hashlib, json
from pathlib import Path

EXPECTED = 'e4bca8df123ce01b104780f576ea1a58b9a5ff1662a91124b6d3180cb6c88212'
root = Path(__file__).resolve().parent
m = json.loads((root / 'manifest.json').read_text())
assert m['font_sha256'] == EXPECTED
chunks = []
for item in m['parts']:
    assert Path(item['path']).name == item['path']
    data = (root / item['path']).read_bytes()
    assert len(data) == item['size'], item['path']
    assert hashlib.sha256(data).hexdigest() == item['sha256'], item['path']
    chunks.append(data.strip())
font = gzip.decompress(base64.b64decode(b''.join(chunks), validate=True))
assert len(font) == m['font_size']
assert hashlib.sha256(font).hexdigest() == EXPECTED
target = root.parent / 'wqy-microhei.ttc'
tmp = target.with_suffix('.verified.tmp')
tmp.write_bytes(font)
with tmp.open('rb') as f:
    assert hashlib.file_digest(f, 'sha256').hexdigest() == EXPECTED
tmp.replace(target)
print('FONT_VERIFIED ' + EXPECTED)
