#!/usr/bin/env python3
import argparse
import datetime
import hashlib
import json
import os
import signal
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import urllib.parse
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
    client_config = target(private, 'dsh-gateway.json')
    if not client_config.exists():
        write_json(client_config, {'base_url': '', 'key_file': 'remote-gateway.key'})
    client_config.chmod(0o600)
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


def import_private(source, deploy, include_state=False):
    if source is None:
        return
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise RuntimeError('Private source directory does not exist')
    private = target(deploy, 'private')
    private.mkdir(parents=True, exist_ok=True)
    private.chmod(0o700)
    names = ['gateway.key', 'container.json', 'browser-credentials/credentials.json', 'browser-credentials/site-signing.json']
    if include_state:
        names.append('browser-credentials/conversations.json')
    for name in names:
        src, dst = target(source, name), target(private, name)
        if src.is_file() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            dst.chmod(0o644)  # Private parent; readable by the gateway container uid.


def sync(args):
    repo = Path(__file__).resolve().parents[1]
    local = args.local_root
    sources = [repo] + ([args.plugin_repo] if not args.skip_plugin and not args.plugin_source else [])
    for source in sources:
        if local.resolve() == source or source in local.resolve().parents:
            raise RuntimeError('LocalRoot must be outside source repositories')
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
    write_json(local / 'environment.json', args.settings)
    deploy = local / 'deploy'
    gateway_files = sync_public(repo, deploy, local / 'gateway-files.json', r'^(gateway/|config/container\.example\.json$|userscript/|tools/|[^/]+\.(ps1|sh|cjs|md)$|compose\.yaml$|LICENSE$)')
    import_private(args.private_source, deploy, include_state=args.settings['kind'] == 'gateway')
    initialize_private(deploy)
    config = deploy / 'private/dsh-gateway.json'
    if args.settings['gateway_mode'] == 'managed':
        current = read_json(config)
        expected = {'base_url': managed_base(args.settings) + '/v1', 'key_file': 'gateway.key'}
        if current.get('base_url') and current != expected:
            raise RuntimeError('Managed gateway configuration must match its bind address, port and local key')
        write_json(config, expected)
    state = {'synced_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'gateway': revision(repo), 'environment': args.settings, 'gateway_files': gateway_files,
             'plugin': None, 'plugin_files': {}, 'package': None}
    if not args.skip_plugin and not args.plugin_source:
        plugin_dir = local / 'sources/plugin'
        state['plugin_files'] = sync_public(args.plugin_repo, plugin_dir, local / 'plugin-files.json', r'^([^/]+\.(cjs|js|json|yml|svg|md)$|locale/[^/]+\.json$|LICENSE$)')
        packages = local / 'packages'
        packages.mkdir(exist_ok=True)
        package = packages / ('dsh-shanghaitech-genai-local-' + uuid.uuid4().hex + '.tgz')
        runtime = args.dsh_app / 'Contents/Resources/runtime/primary-runtime/dependencies'
        run(runtime / 'node/bin/node', runtime / 'pnpm/bin/pnpm.cjs', 'pack', '--out', package, '--json',
            cwd=plugin_dir, env=dsh_environment(args), stdout=subprocess.DEVNULL)
        state.update(plugin=revision(args.plugin_repo), package=str(package), package_sha256=digest(package))
    write_json(state_path, state)
    options = ['--local-root', str(local), '--plugin-repo', str(args.plugin_repo)]
    if args.skip_plugin:
        options.append('--skip-plugin')
    q = shlex.quote
    update = local / 'Update.sh'
    update.write_text('#!/bin/sh\nset -eu\n' + q(str(repo / 'tools/Sync-Local.sh')) + ' ' + shlex.join(options) + '\n' + q(str(repo / 'tools/Deploy-Local.sh')) + ' ' + shlex.join(options) + '\n')
    update.chmod(0o755)
    print('Synchronized current source snapshot; environment and private files preserved.')


def client_gateway(deploy):
    config = deploy / 'private/dsh-gateway.json'
    if not config.exists():
        return 'http://127.0.0.1:8000', deploy / 'private/gateway.key', False
    data = read_json(config)
    address = data.get('base_url')
    if address is None or (isinstance(address, str) and not address.strip()):
        return 'http://127.0.0.1:8000', deploy / 'private/gateway.key', False
    try:
        url = urllib.parse.urlsplit(address.strip())
        _ = url.port
    except (ValueError, AttributeError):
        raise RuntimeError('Invalid DSH gateway base_url') from None
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise RuntimeError('Invalid DSH gateway base_url: use an HTTP(S) URL without credentials, query or fragment')
    base = urllib.parse.urlunsplit(url).rstrip('/')
    if base.endswith('/v1'):
        base = base[:-3]
    return base, target(deploy / 'private', data['key_file']), True


def request(path, key=None, base='http://127.0.0.1:8000'):
    headers = {'Authorization': 'Bearer ' + key} if key else {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(base + path, headers=headers), timeout=10) as response:
        return json.load(response)


def prepare_gateway(deploy):
    base, _, configured = client_gateway(deploy)
    settings = environment_for(deploy)
    if settings.get('gateway_mode', 'external' if configured else 'managed') == 'managed':
        return gateway(deploy)
    health = request('/healthz', base=base)
    if health.get('product') != 'ShanghaiTech GenAI' or health.get('status') != 'alive':
        raise RuntimeError('Configured DSH gateway health check failed')
    print('Configured gateway health OK: ' + str(health.get('version')))
    return health


def gateway(deploy):
    settings = environment_for(deploy)
    compose, env = compose_command(deploy)
    run(*compose, 'config', '--quiet', env=env)
    run(*compose, 'up', '-d', '--build', env=env)
    expected = re.search(r'__version__\s*=\s*[\'"]([^\'"]+)', (deploy / 'gateway/gateway/__init__.py').read_text()).group(1)
    for _ in range(30):
        try:
            health = request('/healthz', base=managed_base(settings))
            if health.get('product') == 'ShanghaiTech GenAI' and health.get('version') == expected:
                print('Gateway health OK: ' + expected)
                return health
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError('Gateway health check failed')


def sync_key(args, deploy):
    env = dict(dsh_environment(args), ELECTRON_RUN_AS_NODE='1')
    _, key_file, _ = client_gateway(deploy)
    options = ['--replace-connection'] if args.gateway_url is not None else []
    run(args.dsh_app / 'Contents/MacOS/DeepSeek Harness', '--expose-internals', deploy / 'Sync-Key-mac.cjs', key_file, args.dsh_app / 'Contents/Resources', *options, env=env)


def check_auth(deploy):
    base, key_file, _ = client_gateway(deploy)
    key = key_file.read_text().strip()
    for supplied in (None, 'invalid-gateway-key-for-test'):
        try:
            request('/v1/models', supplied, base)
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise
        else:
            raise RuntimeError('Unauthenticated model listing was accepted')
    credentials = request('/v1/credentials/status', key, base)
    model_count = None
    if credentials['logged_in']:
        models = request('/v1/models', key, base)
        model_count = len(models['data'])
    print("Authentication OK: missing/invalid key rejected, valid key accepted.")
    if model_count is None:
        print("Import website credentials in the gateway page to load the model catalog.")
    else:
        print(f"Website model catalog: {model_count} models; real generation not validated.")
    return {'models': model_count, 'credentials': credentials, 'authentication': 'passed'}


def deploy(args):
    local = args.local_root
    if not (local / '.genai-local').is_file():
        raise RuntimeError('Run Sync-Local.sh first')
    state = read_json(local / 'sync-state.json')
    if state.get('environment', args.settings) != args.settings:
        raise RuntimeError('Environment changed; sync again')
    for folder, name in [('deploy', 'gateway_files'), ('sources/plugin', 'plugin_files')]:
        for relative, value in state.get(name, {}).items():
            if digest(target(local / folder, relative)) != value:
                raise RuntimeError('Snapshot changed; sync again')
    package = args.plugin_source or state.get('package')
    if not args.skip_plugin and not args.plugin_source:
        if not package or digest(Path(package)) != state.get('package_sha256'):
            raise RuntimeError('Plugin package missing or changed; sync again')
    deploy_dir = local / 'deploy'
    # Check the gateway before changing the selected client profile.
    health = prepare_gateway(deploy_dir)
    checks = check_auth(deploy_dir)
    if not args.skip_plugin:
        ensure_development_profile(args)
        profile = Path(args.settings['dsh_home']) / 'profiles' / args.settings['profile']
        backup = local / 'dsh-backups' / uuid.uuid4().hex
        backup.mkdir(parents=True)
        for name in ['package.json', 'pnpm-lock.yaml', 'cordis.patch.yml', 'cordis.yml']:
            if (profile / name).is_file():
                shutil.copy2(profile / name, backup / name)
        run(args.dsh_app / 'Contents/Resources/runtime/cli/bin/dsh', 'plugin', '--profile',
            args.settings['profile'], 'add', package, '--prefer-offline', env=dsh_environment(args))
        sync_key(args, deploy_dir)
        print('Plugin installed in selected DSH home: ' + args.settings['dsh_home'])
    write_json(local / 'deployment-checks.json', {'health': health, **checks,
               'gateway': state['gateway'], 'plugin': state.get('plugin'),
               'plugin_source': args.plugin_source, 'environment': args.settings})


def deployment_root(args):
    here = Path(__file__).resolve().parents[1]
    return here if (here / 'private').is_dir() else args.local_root.resolve() / 'deploy'


def setup(args):
    if not shutil.which('git'):
        raise RuntimeError('git is required')
    if args.settings['gateway_mode'] == 'managed':
        if not shutil.which('docker'):
            raise RuntimeError('docker is required for a managed gateway')
        run('docker', 'info', stdout=subprocess.DEVNULL)
    if not args.skip_plugin:
        runtime = args.dsh_app / 'Contents/Resources/runtime'
        if not (runtime / 'cli/bin/dsh').is_file():
            raise RuntimeError('DSH installation missing; specify --dsh-app')
        if not args.plugin_source and not (args.plugin_repo / 'package.json').is_file():
            raise RuntimeError('Plugin repository missing; specify --plugin-repo')
    sync(args)
    private = args.local_root / 'deploy/private'
    if args.gateway_url is not None:
        key_name = 'gateway.key' if args.settings['gateway_mode'] == 'managed' else 'remote-gateway.key'
        config = {'base_url': args.gateway_url, 'key_file': key_name}
        if args.gateway_key_file:
            source = args.gateway_key_file.expanduser().resolve()
            dest = private / key_name
            if source != dest.resolve():
                shutil.copyfile(source, dest)
            dest.chmod(0o600)
        write_json(private / 'dsh-gateway.json', config)
        (private / 'dsh-gateway.json').chmod(0o600)
    elif args.gateway_key_file:
        raise RuntimeError('--gateway-key-file requires --gateway-url')
    _, key_file, _ = client_gateway(private.parent)
    key = key_file.read_text().strip()
    if not 32 <= len(key) <= 4096 or re.search(r'[^\x21-\x7e]', key):
        raise RuntimeError('Invalid gateway key file')
    deploy(args)
    print('Runtime directory: ' + str(args.local_root))
    if not args.skip_plugin:
        print('Start selected DSH: ' + shlex.quote(str(private.parent / 'Start.sh')))


def start(args):
    stop_development_dsh(args)
    if args.settings['kind'] == 'gateway':
        raise RuntimeError('Gateway-only environments do not launch DSH')
    deploy_dir = deployment_root(args)
    prepare_gateway(deploy_dir)
    sync_key(args, deploy_dir)
    check_auth(deploy_dir)
    base, _, _ = client_gateway(deploy_dir)
    env = dict(dsh_environment(args), SHANGHAITECH_GENAI_BASE_URL=base + '/v1')
    host = urllib.parse.urlsplit(base).hostname
    for name in ('NO_PROXY', 'no_proxy'):
        env[name] = ','.join(filter(None, [env.get(name, ''), host, '127.0.0.1', 'localhost']))
    env.pop('ELECTRON_RUN_AS_NODE', None)
    process = launch_desktop(args, env)
    time.sleep(3)
    if process.poll() is not None:
        raise RuntimeError('DSH exited during startup; check selected environment logs and instance isolation')
    print(f'DSH launched: PID {process.pid}; home: ' + args.settings['dsh_home'])


def launch_desktop(args, env=None):
    logs = args.local_root / 'logs'
    logs.mkdir(exist_ok=True)
    error_log = logs / 'dsh.err.log'
    env = dict(env or dsh_environment(args))
    env.pop('ELECTRON_RUN_AS_NODE', None)
    command = [str(args.dsh_app / 'Contents/MacOS/DeepSeek Harness')]
    if args.settings.get('desktop_user_data'):
        command.append('--user-data-dir=' + args.settings['desktop_user_data'])
    with error_log.open('ab') as err:
        error_log.chmod(0o600)
        process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=err, start_new_session=True)
    write_json(args.local_root / 'dsh-process.json', {'pid': process.pid, 'dsh_home': args.settings['dsh_home'],
               'desktop_user_data': args.settings.get('desktop_user_data')})
    return process


def stop_development_dsh(args):
    if args.settings['kind'] != 'development':
        return
    record = args.local_root / 'dsh-process.json'
    expected = str(args.dsh_app / 'Contents/MacOS/DeepSeek Harness') + ' --user-data-dir=' + args.settings['desktop_user_data']
    pid = None
    if record.exists():
        data = read_json(record)
        if data.get('dsh_home') != args.settings['dsh_home'] or data.get('desktop_user_data') != args.settings['desktop_user_data']:
            raise RuntimeError('DSH process record belongs to a different environment')
        pid = data['pid']
        if type(pid) is not int or pid <= 1:
            raise RuntimeError('Invalid DSH process record')
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip() != expected:
            raise RuntimeError('Recorded PID is not the selected development DSH')
        if result.returncode != 0:
            pid = None
    if pid is None:
        # Desktop can relaunch itself after recovery. Match the entire isolated
        # launch command; never identify a target by application name alone.
        result = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, check=True)
        matches = [int(parts[0]) for line in result.stdout.splitlines()
                   if len(parts := line.strip().split(None, 1)) == 2 and parts[1] == expected]
        if len(matches) > 1:
            raise RuntimeError('Multiple development Desktop instances; inspect before updating')
        pid = matches[0] if matches else None
    if pid is not None:
        if os.getpgid(pid) != pid:
            raise RuntimeError('Development DSH must own a separate process group')
        # Let Desktop flush and close its host before stopping helper processes.
        os.kill(pid, signal.SIGTERM)
        for _ in range(250):
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            state = subprocess.run(['ps', '-axo', 'pgid=,stat='], capture_output=True, text=True, check=True)
            live = [parts for line in state.stdout.splitlines()
                    if len(parts := line.split()) == 2 and parts[0] == str(pid) and not parts[1].startswith('Z')]
            if not live:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError('Development DSH did not stop; inspect it before updating')
    record.unlink(missing_ok=True)


def ensure_development_profile(args):
    if args.settings['kind'] != 'development':
        return
    stop_development_dsh(args)
    profile = Path(args.settings['dsh_home']) / 'profiles/desktop/package.json'
    if profile.is_file():
        return
    process = launch_desktop(args)
    try:
        for _ in range(90):
            if process.poll() is not None:
                raise RuntimeError('Development DSH exited before initializing its isolated profile')
            if profile.is_file():
                time.sleep(2)
                return
            time.sleep(0.5)
        raise RuntimeError('Timed out initializing the development DSH profile')
    finally:
        stop_development_dsh(args)



def environment_for(deploy):
    path = deploy.parent / 'environment.json'
    return read_json(path) if path.is_file() else {}


def managed_base(settings):
    host = settings.get('bind_address', '127.0.0.1')
    if host == '0.0.0.0':
        host = '127.0.0.1'
    return 'http://' + host + ':' + str(settings.get('port', 8000))


def compose_command(deploy):
    settings = environment_for(deploy)
    env = dict(os.environ, GENAI_BIND_ADDRESS=settings.get('bind_address', '127.0.0.1'),
               GENAI_HOST_PORT=str(settings.get('port', 8000)),
               GENAI_IMAGE_TAG=settings.get('image_tag', '1.1.0'))
    command = ['docker', 'compose', '-f', str(deploy / 'compose.yaml')]
    if settings.get('compose_override'):
        command += ['-f', settings['compose_override']]
    return command + ['-p', settings.get('compose_project', 'shanghaitech-genai')], env


def dsh_environment(args):
    env = dict(os.environ, DSH_HOME=args.settings['dsh_home'])
    if args.settings['kind'] == 'development':
        env['TMPDIR'] = '/private/tmp'
    return env


def configure(args):
    here = Path(__file__).resolve().parents[1]
    default = (Path.cwd() / 'local' if args.environment == 'development' else
               here.parent if (here / 'private').is_dir() else Path.home() / '.local/share/shanghaitech-genai')
    args.local_root = (args.local_root or default).expanduser().absolute()
    stored = args.local_root / 'environment.json'
    old = read_json(stored) if stored.is_file() else {}
    kind = args.environment or old.get('kind', 'standard')
    if old and args.environment and args.environment != old['kind']:
        raise RuntimeError('Cannot change the purpose of an existing environment')
    args.dsh_app = (args.dsh_app or Path(old.get('dsh_app', '/Applications/DeepSeek Harness.app'))).expanduser().resolve()
    args.plugin_repo = (args.plugin_repo or Path(old.get('plugin_repo', str(here.parent / 'dsh-shanghaitech-genai')))).expanduser().resolve()
    args.plugin_source = args.plugin_source or old.get('plugin_source')
    if args.plugin_source and not re.fullmatch(r'github:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?', args.plugin_source):
        raise RuntimeError('Client plugin source must name a fixed GitHub release tag')
    if kind == 'client' and not args.plugin_source:
        raise RuntimeError('Client installation requires --plugin-source github:owner/repository#vX.Y.Z')
    if args.gateway_url is not None:
        selected_external = bool(args.gateway_url.strip())
    else:
        selected_external = client_gateway(args.local_root / 'deploy')[2]
    mode = args.gateway_mode or old.get('gateway_mode') or ('managed' if kind in ('development', 'gateway') else 'external' if selected_external else 'managed')
    home = str((args.dsh_home or Path(old.get('dsh_home', os.environ.get('DSH_HOME') or str(Path.home() / '.dsh')))).expanduser().resolve())
    settings = dict(old, schema=1, kind=kind, gateway_mode=mode,
                    dsh_app=str(args.dsh_app), plugin_repo=str(args.plugin_repo), plugin_source=args.plugin_source,
                    dsh_home=home, profile=args.profile or old.get('profile', 'desktop'),
                    compose_project=args.compose_project or old.get('compose_project', 'shanghaitech-genai'),
                    bind_address=args.bind_address or old.get('bind_address', '127.0.0.1'),
                    port=args.port if args.port is not None else old.get('port', 8000),
                    image_tag=re.search(r'__version__\s*=\s*[\'"]([^\'"]+)', (here / 'gateway/gateway/__init__.py').read_text()).group(1))
    override = args.compose_override or old.get('compose_override')
    if override:
        override = Path(override).expanduser().resolve()
        if not override.is_file():
            raise RuntimeError('Compose override file does not exist')
        settings['compose_override'] = str(override)
    if kind == 'development':
        if args.dsh_home and args.dsh_home.expanduser().resolve() != (args.local_root / 'dsh').resolve():
            raise RuntimeError('Development DSH home must be local-root/dsh')
        if mode != 'managed' or settings['port'] != 8000 or settings['bind_address'] != '127.0.0.1':
            raise RuntimeError('Development gateway must be managed on 127.0.0.1:8000')
        if args.gateway_url not in (None, 'http://127.0.0.1:8000/v1', 'http://127.0.0.1:8000'):
            raise RuntimeError('Development gateway URL must be loopback port 8000')
        if args.gateway_key_file or args.plugin_source:
            raise RuntimeError('Development uses a local key and current plugin source')
        settings.update(dsh_home=str(args.local_root / 'dsh'), desktop_user_data=str(args.local_root / 'desktop'),
                        compose_project=args.compose_project or old.get('compose_project', 'shanghaitech-genai-dev'), image_tag='development')
    if kind == 'client' and mode != 'external':
        raise RuntimeError('Client-only environment requires an external gateway')
    if settings['profile'] != 'desktop':
        raise RuntimeError('DSH Desktop currently requires the desktop profile; isolate it with --dsh-home')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', settings['compose_project']):
        raise RuntimeError('Invalid Compose project name')
    import ipaddress
    if ipaddress.ip_address(settings['bind_address']).version != 4 or not 1 <= settings['port'] <= 65535:
        raise RuntimeError('Use an IPv4 bind address and a valid port')
    args.skip_plugin = args.skip_plugin or kind == 'gateway'
    args.settings = settings
    return args


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['sync', 'deploy', 'start', 'gateway', 'setup'])
    parser.add_argument('--local-root', type=Path)
    parser.add_argument('--environment', choices=['development', 'client', 'gateway', 'standard'])
    parser.add_argument('--dsh-home', type=Path)
    parser.add_argument('--profile')
    parser.add_argument('--gateway-mode', choices=['managed', 'external'])
    parser.add_argument('--compose-project')
    parser.add_argument('--compose-override', type=Path, help='Existing additional Compose file; persisted for subsequent updates')
    parser.add_argument('--bind-address')
    parser.add_argument('--port', type=int)
    parser.add_argument('--plugin-repo', type=Path)
    parser.add_argument('--plugin-source', help='Fixed published GitHub tag for client-only installation')
    parser.add_argument('--dsh-app', type=Path)
    parser.add_argument('--skip-plugin', action='store_true', help='Do not pack, install or configure DSH')
    parser.add_argument('--gateway-url')
    parser.add_argument('--gateway-key-file', type=Path)
    parser.add_argument('--private-source', type=Path, help='Import missing private files from an existing runtime; never overwrite')
    args = configure(parser.parse_args())
    if args.action == 'gateway':
        gateway(deployment_root(args))
    else:
        {'sync': sync, 'deploy': deploy, 'start': start, 'setup': setup}[args.action](args)


if __name__ == '__main__':
    main()
