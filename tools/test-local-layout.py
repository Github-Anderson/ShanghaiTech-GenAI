#!/usr/bin/env python3
"""Exercise a relocated checkout and the default runtime under an isolated HOME."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--gateway-repo', type=Path, required=True)
parser.add_argument('--plugin-repo', type=Path, required=True)
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix='genai-layout-', dir='/private/tmp') as temp:
    root = Path(temp)
    source = root / 'checkout with spaces'
    gateway = source / 'ShanghaiTech-GenAI'
    plugin = source / 'dsh-shanghaitech-genai'
    for src, dest in ((args.gateway_repo, gateway), (args.plugin_repo, plugin)):
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns('node_modules', '__pycache__'))
    home = root / 'independent home'
    home.mkdir()
    env = dict(os.environ, HOME=str(home))
    subprocess.run([str(gateway / 'tools/Sync-Local.sh')], cwd='/private/tmp', env=env, check=True)
    runtime = home / '.local/share/shanghaitech-genai'
    assert not (source / 'local').exists()
    assert json.loads((runtime / 'deploy/private/dsh-gateway.json').read_text())['base_url'] == ''
    key = (runtime / 'deploy/private/gateway.key').read_bytes()
    state = json.loads((runtime / 'sync-state.json').read_text())
    assert Path(state['package']).is_file()
    assert str(gateway) in (runtime / 'Update.sh').read_text()
    config = runtime / 'deploy/private/dsh-gateway.json'
    config.write_text(json.dumps({'base_url':'https://example.invalid/v1','key_file':'remote.key'}))
    subprocess.run([str(gateway / 'tools/Sync-Local.sh')], cwd='/private/tmp', env=env, check=True)
    assert json.loads(config.read_text())['base_url'] == 'https://example.invalid/v1'
    assert (runtime / 'deploy/private/gateway.key').read_bytes() == key
    assert not (source / 'local').exists()
    print('PASS: relocated repositories, paths with spaces, unrelated working directory, user runtime default, packaging and private configuration preserved.')
