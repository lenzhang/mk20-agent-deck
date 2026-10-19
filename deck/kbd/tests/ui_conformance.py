#!/usr/bin/env python3
"""Offline, stdlib only: every preset/model/profile through real Python and C writers."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile

KBD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KBD.parent))
from mk20deck.agentcfg import _atomic_write, net_config, provider_config
from mk20deck.catalogue import CATALOGUE

def run():
    catalogue = json.loads(subprocess.check_output([str(KBD/'tests/uicfg_cli'),'catalogue']))
    assert catalogue == [dict(id=p['id'],label=p['label'],base_url=p['base_url'],domestic=p['domestic'],
                              supported=p['adapter']=='openai',models=p['models']) for p in CATALOGUE]
    count = 0
    with tempfile.TemporaryDirectory(prefix='mk20-conformance-') as tmp:
        root = Path(tmp).resolve()
        cross = shutil.which('arm-linux-gnueabihf-gcc')
        if cross:
            subprocess.run([cross,'-std=c99','-Wall','-Wextra','-Werror','-O1','-static','-I',str(KBD),
                '-o',str(root/'ui_headless.armhf'),str(KBD/'tests/ui_headless.c'),
                *[str(KBD/s) for s in ['ui.c','ui_font.c','ui_font_render.c','uicfg.c','panels.c']]],check=True)
            print('ui armhf: static C99 compile passed')
        else:
            print('ui armhf: compiler unavailable; host-only build verified')
        secrets = root / 'secrets'; secrets.mkdir(mode=0o700)
        (secrets/'qwen.key').write_text('SECRET_SENTINEL_NEVER_RENDER')
        (secrets/'qwen.key').chmod(0)  # Writer must succeed without any read access.
        for i,p in enumerate(CATALOGUE):
            for j,m in enumerate(p['models']):
                for wifi,host,port,wp in [(0,'172.30.250.1',7890,''), (0,'proxy.example',1,''),
                    (0,'192.0.2.2',65535,''), (1,'172.30.250.1',7890,''),
                    (1,'172.30.250.1',7890,'http://wifi.example:65535')]:
                    full = dict(provider=p['id'],model=m['id'],profile='board_wifi' if wifi else 'mac_proxy',
                                proxy_port=port,proxy_host=host,wifi_proxy=wp)
                    out = root / 'c'; py = root / 'python'
                    subprocess.run([str(KBD/'tests/uicfg_cli'),str(out),str(i),str(j),str(wifi),str(port),host,wp],check=True)
                    expected = {'net.json':net_config(full),'provider.json':provider_config(full,'/data/mk20/secrets')}
                    for name,data in expected.items():
                        _atomic_write(str(py/name), data)
                        assert (out/name).read_bytes()==(py/name).read_bytes(), (p['id'],m['id'],wifi,name)
                        assert os.stat(out/name).st_mode & 0o777 == 0o600
                        assert b'SECRET_SENTINEL_NEVER_RENDER' not in (out/name).read_bytes()
                    assert not (out/'.apply-in-progress').exists()
                    subprocess.run([str(KBD/'tests/ui_load'),str(out/'provider.json'),str(out/'net.json')],check=True)
                    count+=1
        for s in ['中文', 'quote" slash\\ /\n\t\r\b\f\x01', '⟲⟳ ✓ …', '\x7f']:
            out = subprocess.check_output([str(KBD/'tests/uicfg_cli'),'quote',s])
            assert out == json.dumps(s, ensure_ascii=False).encode('utf-8')
    print(f'ui conformance: {count} provider/model/profile combinations, {2*count} exact files, actual agent loaders; 4 escaping cases')

if __name__=='__main__': run()
