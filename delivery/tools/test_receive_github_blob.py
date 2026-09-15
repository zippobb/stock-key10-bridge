import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class TransportTests(unittest.TestCase):
    def run_case(self, raw, sent=None, bad_hash=False, existing=False):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "input.json"
            if existing:
                target.write_bytes(b"existing")
            sha = hashlib.sha256(raw).hexdigest()
            blob = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("receive_github_blob.py")), str(target), str(len(raw)), blob, "0" * 64 if bad_hash else sha], input=raw if sent is None else sent, capture_output=True, timeout=5)
            if bad_hash or existing or sent is not None:
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(target.read_bytes() if target.exists() else None, b"existing" if existing else None)
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(target.read_bytes(), raw)
                self.assertEqual(result.stdout.decode().strip(), sha)

    def test_large_utf8(self): self.run_case(('股票中文\\n' * 40000 + '\n').encode())
    def test_no_final_newline(self): self.run_case(b'{"a":1}')
    def test_crlf(self): self.run_case(b'{\r\n"a":1\r\n}\r\n')
    def test_truncation(self): self.run_case(b'12345', sent=b'123')
    def test_hash_mismatch(self): self.run_case(b'12345', bad_hash=True)
    def test_no_overwrite(self): self.run_case(b'12345', existing=True)


if __name__ == '__main__': unittest.main()
