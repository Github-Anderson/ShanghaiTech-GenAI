#!/usr/bin/env python3
"""Deploy a published gateway tag over SSH; no DSH installation is needed.

Use --plan to inspect the exact target without connecting. The remote host must
already have the repository, Docker, Python 3 and protected gateway credentials.
"""
import argparse
import datetime
import ipaddress
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys


def command(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def git(repo, *args):
    return command('git', '-C', repo, *args, capture_output=True, text=True).stdout.strip()


def validate(spec):
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?', spec['tag']):
        raise ValueError('Use a fixed vX.Y.Z release tag')
    for name in ('repo', 'root', 'private_source', 'compose_override'):
        if spec.get(name) and not Path(spec[name]).is_absolute():
            raise ValueError(name + ' must be an absolute remote path')
    if ipaddress.ip_address(spec['bind_address']).version != 4:
        raise ValueError('Use an IPv4 bind address')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', spec['project']):
        raise ValueError('Invalid Compose project')


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def apply_on_host(spec):
    validate(spec)
    repo, root = Path(spec['repo']), Path(spec['root'])
    tag = spec['tag']
    # Fetch only the requested tag. Never reset, pull or checkout the operator's branch.
    command('git', '-C', repo, 'fetch', '--no-tags', 'origin', 'refs/tags/' + tag + ':refs/tags/' + tag)
    if git(repo, 'cat-file', '-t', 'refs/tags/' + tag) != 'tag':
        raise RuntimeError('Release tag must be annotated')
    commit = git(repo, 'rev-parse', 'refs/tags/' + tag + '^{commit}')
    if spec.get('expected_commit') and commit != spec['expected_commit']:
        raise RuntimeError('Remote tag differs from the published tag verified by the client')
    checkout = root / 'releases' / commit
    runtime = root / 'runtime'
    if checkout.exists():
        if git(checkout, 'rev-parse', 'HEAD') != commit or git(checkout, 'status', '--porcelain'):
            raise RuntimeError('Existing release checkout has changed')
    else:
        checkout.parent.mkdir(parents=True, exist_ok=True)
        command('git', 'clone', '--no-hardlinks', '--no-checkout', repo, checkout)
        command('git', '-C', checkout, 'checkout', '--detach', commit)
    entry = checkout / 'tools/local-macos.py'
    if not entry.is_file() or '--environment' not in entry.read_text():
        raise RuntimeError('This release does not include gateway-only deployment support')
    private = runtime / 'deploy/private'
    source = Path(spec['private_source']) if spec.get('private_source') else private
    if not (private / 'gateway.key').is_file() and not (source / 'gateway.key').is_file():
        raise RuntimeError('Existing gateway key required; specify --private-source')
    manifest = root / 'deployment.json'
    previous = json.loads(manifest.read_text()) if manifest.exists() else None
    def deploy(release, import_source=None):
        args = [sys.executable, str(release / 'tools/local-macos.py'), 'setup', '--environment', 'gateway',
                '--local-root', str(runtime), '--bind-address', spec['bind_address'], '--compose-project', spec['project']]
        if spec.get('compose_override'):
            args += ['--compose-override', spec['compose_override']]
        if import_source:
            args += ['--private-source', str(import_source)]
        command(*args)
    try:
        deploy(checkout, source if source != private else None)
        checks = json.loads((runtime / 'deployment-checks.json').read_text())
        if checks['gateway']['commit'] != commit or checks['gateway']['dirty']:
            raise RuntimeError('Deployed snapshot is not the selected clean release commit')
    except Exception:
        # Rebuild the previous immutable checkout against preserved private data.
        if previous:
            prior = root / 'releases' / previous['commit']
            if git(prior, 'rev-parse', 'HEAD') != previous['commit'] or git(prior, 'status', '--porcelain'):
                raise RuntimeError('Deployment failed; previous checkout must be inspected before rollback') from None
            deploy(prior)
            print('Previous gateway release restored.', flush=True)
        raise
    record = {'tag': tag, 'commit': commit, 'previous_commit': previous['commit'] if previous else None,
              'deployed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'bind_address': spec['bind_address'], 'project': spec['project'],
              'compose_override': spec.get('compose_override'), 'checks': checks}
    atomic_json(manifest, record)
    history = root / 'history'
    history.mkdir(exist_ok=True)
    atomic_json(history / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json'), record)
    print(json.dumps({'tag': tag, 'commit': commit, 'health': checks['health'], 'authentication': checks['authentication']}))


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--on-host':
        apply_on_host(json.loads(sys.argv[2]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--root', required=True, help='Separate remote release/deployment directory')
    parser.add_argument('--tag', required=True)
    parser.add_argument('--bind-address', required=True)
    parser.add_argument('--project', default='shanghaitech-genai')
    parser.add_argument('--private-source')
    parser.add_argument('--compose-override', help='Absolute remote path to an existing additional Compose file')
    parser.add_argument('--plan', action='store_true', help='Print target only; no SSH, fetch or deployment')
    args = parser.parse_args()
    spec = {k: v for k, v in vars(args).items() if k not in ('host', 'plan')}
    validate(spec)
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]*', args.host):
        parser.error('Use an SSH hostname or alias')
    if args.plan:
        print(json.dumps({'host': args.host, **spec}, indent=2))
        return
    # Confirm a published GitHub Release before any remote mutation.
    release = command('gh', 'release', 'view', args.tag, '--json', 'isDraft,tagName',
                      cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    metadata = json.loads(release.stdout)
    if metadata['isDraft'] or metadata['tagName'] != args.tag:
        raise RuntimeError('Publish the selected release before deploying it')
    repository = Path(__file__).resolve().parents[1]
    refs = git(repository, 'ls-remote', '--tags', 'origin', 'refs/tags/' + args.tag, 'refs/tags/' + args.tag + '^{}')
    commits = [line.split()[0] for line in refs.splitlines() if line.endswith('refs/tags/' + args.tag + '^{}')]
    if len(commits) != 1 or not re.fullmatch(r'[0-9a-f]{40,64}', commits[0]):
        raise RuntimeError('Published release must resolve to an annotated remote tag')
    spec['expected_commit'] = commits[0]
    remote = shlex.join(['python3', '-', '--on-host', json.dumps(spec)])
    command('ssh', '-o', 'BatchMode=yes', args.host, remote, input=Path(__file__).read_text(), text=True)


if __name__ == '__main__':
    main()
