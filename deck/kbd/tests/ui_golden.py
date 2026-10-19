#!/usr/bin/env python3
"""PPM goldens are gzip-compressed losslessly; compare the entire decompressed P6.
Only explicit `make ui-golden` writes goldens. No Pillow/system font dependency.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

KBD = Path(__file__).resolve().parents[1]
GOLD = KBD/'tests/golden/ui'
a=argparse.ArgumentParser(); a.add_argument('--regenerate',action='store_true'); args=a.parse_args()
with tempfile.TemporaryDirectory(prefix='mk20-ui-render-') as tmp:
    out=Path(tmp)
    subprocess.run([str(KBD/'tests/ui_headless'),str(out)],check=True)
    paths=sorted(out.glob('*/*.ppm'))
    manifest={'format':'P6, RGB565 expansion floor(r5*255/31,g6*255/63,b5*255/31), gzip mtime=0',
              'fixture_sha256':hashlib.sha256((KBD/'tests/ui_headless.c').read_bytes()).hexdigest(),
              'fixed_labels_sha256':hashlib.sha256((KBD/'ui_labels.inc').read_bytes()).hexdigest(),
              'glyphs':[f'U+{int(cp,16):04X}' for cp in re.findall(r'\{0x([0-9a-f]+),', (KBD/'ui_font.c').read_text())],
              'files':sorted(str(p.relative_to(out)) for p in paths)}
    expected=json.dumps(manifest,ensure_ascii=False,indent=2)+'\n'
    if args.regenerate:
        for old in GOLD.glob('*/*.ppm.gz'): old.unlink()
        for path in paths:
            target=GOLD/(str(path.relative_to(out))+'.gz'); target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(gzip.compress(path.read_bytes(),compresslevel=9,mtime=0))
        (GOLD/'manifest.json').write_text(expected)
    else:
        assert (GOLD/'manifest.json').read_text()==expected, 'golden manifest stale: review and run make ui-golden'
        assert sorted(str(p.relative_to(GOLD))[:-3] for p in GOLD.glob('*/*.ppm.gz'))==manifest['files']
        for path in paths:
            assert b'SECRET_SENTINEL_NEVER_RENDER' not in path.read_bytes()
            rel=path.relative_to(out)
            assert gzip.decompress((GOLD/(str(rel)+'.gz')).read_bytes())==path.read_bytes(), f'golden mismatch: {rel}'
    print(f'ui goldens: {len(paths)} byte-exact P6 files ({"regenerated" if args.regenerate else "verified"})')
