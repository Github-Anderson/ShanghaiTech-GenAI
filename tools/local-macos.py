#!/usr/bin/env python3
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path, data):
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, indent=2) + '\n')
    temp.replace(path)


def target(root, relative):
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise RuntimeError('Unsafe managed path')
    dest = root / rel
    for part in (root, *dest.parents, dest):
        if part.is_symlink():
            raise RuntimeError('Symlinks are not allowed in managed paths')
    return dest


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def revision(repo):
    return {
        'commit': run('git', '-C', repo, 'rev-parse', 'HEAD', capture_output=True, text=True).stdout.strip(),
        'dirty': bool(run('git', '-C', repo, 'status', '--porcelain', capture_output=True).stdout),
    }


def sync_public(repo, destination, manifest, allow):
    destination.mkdir(parents=True, exist_ok=True)
    listed = run('git', '-C', repo, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', capture_output=True).stdout
    files = sorted(set(os.fsdecode(p) for p in listed.split(b'\0') if p))
    blocked = re.compile(r'(^|/)(private|browser-credentials|node_modules|__pycache__|\.git)(/|$)|(^|/)(\.env[^/]*|local\.json|credentials\.json|site-signing\.json)$|\.(key|pem|har|pyc|log)$')
    files = [p for p in files if re.search(allow, p) and not blocked.search(p) and target(repo, p).is_file()]
    for relative in files:
        dest = target(destination, relative)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target(repo, relative), dest)
    if manifest.exists():
        for relative in read_json(manifest):
            if not re.search(allow, relative) or blocked.search(relative):
                raise RuntimeError('Invalid previous manifest')
            dest = target(destination, relative)
            if relative not in files and dest.is_file():
                dest.unlink()
    write_json(manifest, files)
    return {p: digest(target(destination, p)) for p in files}


def initialize_private(deploy):
    private = target(deploy, 'private')
    credentials = target(private, 'browser-credentials')
    credentials.mkdir(parents=True, exist_ok=True)
    private.chmod(0o700)
    # The container runs as uid 10001; Docker Desktop bind mounts retain modes.
    credentials.chmod(0o777)
    config = target(private, 'container.json')
    if not config.exists():
        write_json(config, read_json(deploy / 'config/container.example.json'))
    key = target(private, 'gateway.key')
    if not key.exists():
        with key.open('x') as stream:
            stream.write(secrets.token_urlsafe(32))
    value = key.read_text().strip()
    if not 32 <= len(value) <= 4096 or re.search(r'[^\x21-\x7e]', value):
        raise RuntimeError('Invalid gateway key')
    # Parent directory is private to the user; files must be readable by container uid 10001.
    key.chmod(0o644)
    config.chmod(0o644)


def sync(args):
    repo = Path(__file__).resolve().parents[1]
    plugin = args.plugin_repo.resolve()
    local = args.local_root.absolute()
    for source in (repo, plugin):
        if local.resolve() == source or source in local.resolve().parents:
            raise RuntimeError('LocalRoot must be outside both repositories')
    if local.is_symlink():
        raise RuntimeError('LocalRoot must not be a symlink')
    local.mkdir(parents=True, exist_ok=True)
    marker = local / '.genai-local'
    if not marker.exists():
        if any(local.iterdir()):
            raise RuntimeError('Refusing nonempty unmarked directory')
        marker.write_text('ShanghaiTech GenAI local environment v1\n')
    state_path = local / 'sync-state.json'
    state_path.unlink(missing_ok=True)
    deploy = local / 'deploy'
    gateway_files = sync_public(repo, deploy, local / 'gateway-files.json', r'^(gateway/|config/container\.example\.json$|userscript/|tools/|[^/]+\.(ps1|sh|cjs|md)$|compose\.yaml$|LICENSE$)')
    plugin_dir = local / 'sources/plugin'
    plugin_files = sync_public(plugin, plugin_dir, local / 'plugin-files.json', r'^([^/]+\.(cjs|js|json|yml|svg|md)$|locale/[^/]+\.json$|LICENSE$)')
    initialize_private(deploy)
    packages = local / 'packages'
    packages.mkdir(exist_ok=True)
    package = packages / ('dsh-shanghaitech-genai-local-' + uuid.uuid4().hex + '.tgz')
    runtime = args.dsh_app / 'Contents/Resources/runtime/primary-runtime/dependencies'
    run(runtime / 'node/bin/node', runtime / 'pnpm/bin/pnpm.cjs', 'pack', '--out', package, '--json', cwd=plugin_dir, stdout=subprocess.DEVNULL)
    write_json(state_path, {'synced_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'gateway': revision(repo), 'plugin': revision(plugin), 'package': str(package), 'package_sha256': digest(package), 'gateway_files': gateway_files, 'plugin_files': plugin_files})
    update = local / 'Update.sh'
    q = shlex.quote
    options = f' --local-root {q(str(local))} --dsh-app {q(str(args.dsh_app))}'
    update.write_text('#!/bin/sh\nset -eu\n' + q(str(repo / 'tools/Sync-Local.sh')) + options + ' --plugin-repo ' + q(str(plugin)) + '\n' + q(str(repo / 'tools/Deploy-Local.sh')) + options + '\n')
    update.chmod(0o755)
    print('Synchronized source snapshot and unique plugin package; private files preserved.')


def request(path, key=None):
    headers = {'Authorization': 'Bearer ' + key} if key else {}
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000' + path, headers=headers), timeout=5) as response:
        return json.load(response)


def gateway(deploy):
    compose = ['docker', 'compose', '-f', str(deploy / 'compose.yaml'), '-p', 'shanghaitech-genai']
    run(*compose, 'config', '--quiet')
    run(*compose, 'up', '-d', '--build')
    expected = re.search(r'__version__\s*=\s*[\'"]([^\'"]+)', (deploy / 'gateway/gateway/__init__.py').read_text()).group(1)
    for _ in range(30):
        try:
            health = request('/healthz')
            if health.get('product') == 'ShanghaiTech GenAI' and health.get('version') == expected:
                print('Gateway health OK: ' + expected)
                return health
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError('Gateway health check failed')


def sync_key(args, deploy):
    env = dict(os.environ, ELECTRON_RUN_AS_NODE='1')
    run(args.dsh_app / 'Contents/MacOS/DeepSeek Harness', '--expose-internals', deploy / 'Sync-Key-mac.cjs', deploy / 'private/gateway.key', args.dsh_app / 'Contents/Resources', env=env)


def check_auth(deploy):
    key = (deploy / 'private/gateway.key').read_text().strip()
    for supplied in (None, 'invalid-gateway-key-for-test'):
        try:
            request('/v1/models', supplied)
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise
        else:
            raise RuntimeError('Unauthenticated model listing was accepted')
    models = request('/v1/models', key)
    credentials = request('/v1/credentials/status', key)
    print(f"Authentication OK: missing/invalid key rejected, valid key lists {len(models['data'])} models.")
    print(f"Website credentials present: {credentials['logged_in']}; upstream not validated.")
    return {'models': len(models['data']), 'credentials': credentials, 'authentication': 'passed'}


def deploy(args):
    local = args.local_root.resolve()
    if not (local / '.genai-local').is_file():
        raise RuntimeError('Run Sync-Local.sh first')
    state = read_json(local / 'sync-state.json')
    package = Path(state['package'])
    if digest(package) != state['package_sha256']:
        raise RuntimeError('Plugin package changed; sync again')
    for folder, name in [('deploy', 'gateway_files'), ('sources/plugin', 'plugin_files')]:
        for relative, value in state[name].items():
            if digest(target(local / folder, relative)) != value:
                raise RuntimeError('Snapshot changed; sync again')
    if not args.skip_plugin:
        profile = Path(os.environ.get('DSH_HOME', str(Path.home() / '.dsh'))) / 'profiles/desktop'
        backup = local / 'dsh-backups' / uuid.uuid4().hex
        backup.mkdir(parents=True)
        for name in ['package.json', 'pnpm-lock.yaml', 'cordis.patch.yml', 'cordis.yml']:
            if (profile / name).is_file():
                shutil.copy2(profile / name, backup / name)
        run(args.dsh_app / 'Contents/Resources/runtime/cli/bin/dsh', 'plugin', '--profile', 'desktop', 'add', package, '--prefer-offline')
        print('DSH desktop plugin updated; profile metadata backed up.')
    deploy_dir = local / 'deploy'
    health = gateway(deploy_dir)
    sync_key(args, deploy_dir)
    checks = check_auth(deploy_dir)
    write_json(local / 'deployment-checks.json', {'health': health, **checks})


def start(args):
    deploy_dir = Path(__file__).resolve().parents[1]
    gateway(deploy_dir)
    sync_key(args, deploy_dir)
    check_auth(deploy_dir)
    env = dict(os.environ, SHANGHAITECH_GENAI_BASE_URL='http://127.0.0.1:8000/v1')
    env.pop('ELECTRON_RUN_AS_NODE', None)
    logs = deploy_dir.parent / 'logs'
    logs.mkdir(exist_ok=True)
    error_log = logs / 'dsh.err.log'
    with error_log.open('ab') as err:
        error_log.chmod(0o600)
        process = subprocess.Popen([str(args.dsh_app / 'Contents/MacOS/DeepSeek Harness')], env=env, stdout=subprocess.DEVNULL, stderr=err, start_new_session=True)
    print(f'DSH launched: PID {process.pid}')


if __name__ == '__main__':
    workspace = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['sync', 'deploy', 'start', 'gateway'])
    parser.add_argument('--local-root', type=Path, default=workspace / 'local')
    parser.add_argument('--plugin-repo', type=Path, default=workspace / 'dsh-shanghaitech-genai')
    parser.add_argument('--dsh-app', type=Path, default=Path('/Applications/DeepSeek Harness.app'))
    parser.add_argument('--skip-plugin', action='store_true')
    args = parser.parse_args()
    if args.action == 'gateway':
        gateway(Path(__file__).resolve().parents[1])
    else:
        {'sync': sync, 'deploy': deploy, 'start': start}[args.action](args)
