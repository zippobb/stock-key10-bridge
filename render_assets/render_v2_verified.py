"""Pinned rendering entrypoint. Visual inspection is still mandatory."""
import argparse, hashlib, importlib.util, json
from pathlib import Path
from PIL import Image

def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',required=True)
    p.add_argument('--input-sha256',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--next-trade-date',required=True)
    a=p.parse_args()
    root=Path(__file__).resolve().parent
    c=json.loads((root/'render_contract_v2.json').read_text())
    source=Path(a.input)
    if digest(source)!=a.input_sha256: raise RuntimeError('INPUT_HASH_MISMATCH')
    renderer=root/c['renderer']['filename']; font=root/c['font']['filename']
    if digest(renderer)!=c['renderer']['sha256']: raise RuntimeError('RENDERER_HASH_MISMATCH')
    if digest(font)!=c['font']['sha256']: raise RuntimeError('FONT_HASH_MISMATCH')
    data=json.loads(source.read_text())
    if data.get('future_data_rows_used')!=0: raise RuntimeError('INVALID_INPUT')
    spec=importlib.util.spec_from_file_location('pinned_renderer',renderer)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    result=module.draw_page(data,Path(a.output),a.next_trade_date,module.SimplifiedFontText(font))
    with Image.open(a.output) as im:
        if im.mode!='RGB' or im.size!=(1024,2048): raise RuntimeError('INVALID_PNG')
    if digest(source)!=a.input_sha256: raise RuntimeError('INPUT_CHANGED')
    result.update(input_sha256=a.input_sha256,png_sha256=digest(Path(a.output)),visual_check='pending')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__': main()
