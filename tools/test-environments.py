#!/usr/bin/env python3
"""Deployment regressions using isolated files and mocked external processes."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


local = module('local_macos', 'local-macos.py')
release = module('deploy_release', 'deploy-release.py')


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='genai-env-', dir='/private/tmp')
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'local'

    def tearDown(self):
        self.temp.cleanup()

    def args(self, **overrides):
        values = dict(local_root=self.runtime, environment='development', dsh_app=None, dsh_home=None,
                      plugin_repo=None, plugin_source=None, profile=None, gateway_mode=None,
                      compose_project=None, compose_override=None, bind_address=None, port=None, gateway_url=None,
                      gateway_key_file=None, private_source=None, skip_plugin=False, action='setup')
        values.update(overrides)
        return local.configure(argparse.Namespace(**values))

    def snapshot(self, args, **extra):
        self.runtime.mkdir(exist_ok=True)
        (self.runtime / '.genai-local').write_text('managed\n')
        local.write_json(self.runtime / 'environment.json', args.settings)
        local.write_json(self.runtime / 'sync-state.json', dict(environment=args.settings, gateway={'commit':'synthetic', 'dirty':True}, gateway_files={}, plugin_files={}, **extra))

    def test_authentication_before_first_website_login(self):
        import urllib.error
        key = self.root / 'synthetic.key'
        key.write_text('synthetic-key-' * 4)
        def request(path, supplied=None, base=None):
            if supplied != key.read_text():
                raise urllib.error.HTTPError('http://local', 401, 'Unauthorized', {}, None)
            self.assertEqual(path, '/v1/credentials/status')
            return {'logged_in': False}
        with patch.object(local, 'client_gateway', return_value=('http://local', key, False)), patch.object(local, 'request', request):
            result = local.check_auth(self.runtime / 'deploy')
        self.assertEqual(result['authentication'], 'passed')
        self.assertIsNone(result['models'])

    def test_connection_replacement_is_explicit_and_not_persisted(self):
        for explicit in [None, 'http://127.0.0.1:8000/v1']:
            args = self.args(gateway_url=explicit)
            with patch.object(local, 'client_gateway', return_value=('http://127.0.0.1:8000', Path('synthetic.key'), True)), patch.object(local, 'run') as run:
                local.sync_key(args, self.runtime / 'deploy')
            self.assertEqual('--replace-connection' in run.call_args.args, explicit is not None)
            self.assertNotIn('gateway_url', args.settings)

    def test_compose_override_is_persisted_and_used(self):
        override = self.root / 'network override.yaml'
        override.write_text('services: {}\n')
        args = self.args(environment='gateway', compose_override=override)
        self.snapshot(args)
        restored = self.args(environment=None)
        self.assertEqual(restored.settings['compose_override'], str(override))
        command, _ = local.compose_command(self.runtime / 'deploy')
        self.assertEqual(command[:6], ['docker', 'compose', '-f', str(self.runtime / 'deploy/compose.yaml'), '-f', str(override)])

    def test_missing_compose_override_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'override file'):
            self.args(environment='gateway', compose_override=self.root / 'missing.yaml')

    def test_development_ignores_inherited_daily_home(self):
        with patch.dict(os.environ, {'DSH_HOME': str(self.root / 'daily')}):
            args = self.args()
        self.assertEqual(local.dsh_environment(args)['DSH_HOME'], str(self.runtime / 'dsh'))
        self.assertEqual(args.settings['desktop_user_data'], str(self.runtime / 'desktop'))
        self.assertEqual(args.settings['gateway_mode'], 'managed')

    def test_update_reloads_saved_environment_without_shell_variables(self):
        args = self.args()
        self.snapshot(args)
        with patch.dict(os.environ, {'DSH_HOME': str(self.root / 'wrong')}):
            restored = self.args(environment=None)
        self.assertEqual(restored.settings, args.settings)

    def test_explicit_loopback_is_managed(self):
        args = self.args(gateway_url='http://127.0.0.1:8000/v1')
        self.snapshot(args)
        deploy = self.runtime / 'deploy'
        with patch.object(local, 'client_gateway', return_value=('http://127.0.0.1:8000', Path('unused'), True)), patch.object(local, 'gateway', return_value={'status':'alive'}) as build, patch.object(local, 'request') as request:
            local.prepare_gateway(deploy)
        build.assert_called_once_with(deploy)
        request.assert_not_called()

    def test_development_rejects_daily_target_and_external_mode(self):
        for changes in [dict(dsh_home=self.root/'daily'), dict(gateway_mode='external'),
                        dict(gateway_url='http://100.64.0.1:8000/v1'), dict(port=8001)]:
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                self.args(**changes)

    def test_client_requires_published_plugin_source(self):
        for source in (None, 'github:example/plugin#main', '/tmp/dev.tgz'):
            with self.subTest(source=source), self.assertRaises(RuntimeError):
                self.args(environment='client', plugin_source=source, gateway_url='http://100.64.0.1:8000/v1')

    def test_client_setup_does_not_require_docker_or_plugin_repository(self):
        app = self.root / 'App'
        cli = app / 'Contents/Resources/runtime/cli/bin/dsh'
        cli.parent.mkdir(parents=True); cli.touch()
        args = self.args(environment='client', dsh_app=app, plugin_source='github:example/plugin#v1.2.3', gateway_url='http://100.64.0.1:8000/v1')
        private = self.runtime / 'deploy/private'; private.mkdir(parents=True)
        (private / 'remote-gateway.key').write_text('synthetic-key-' * 4)
        with patch.object(local.shutil, 'which', side_effect=lambda name: '/usr/bin/git' if name == 'git' else None), patch.object(local, 'sync'), patch.object(local, 'deploy'), patch.object(local, 'run') as run:
            local.setup(args)
        run.assert_not_called()

    def test_gateway_deploy_needs_no_dsh_or_plugin_package(self):
        args = self.args(environment='gateway', dsh_app=self.root/'missing-app', plugin_repo=self.root/'missing-plugin')
        self.snapshot(args)
        with patch.object(local, 'prepare_gateway', return_value={'status':'alive'}), patch.object(local, 'check_auth', return_value={'authentication':'passed'}), patch.object(local, 'sync_key') as credentials, patch.object(local, 'run') as run:
            local.deploy(args)
        run.assert_not_called(); credentials.assert_not_called()
        self.assertTrue((self.runtime / 'deployment-checks.json').is_file())

    def test_gateway_start_rejected_before_any_dsh_mutation(self):
        args = self.args(environment='gateway')
        with patch.object(local, 'sync_key') as credentials, patch.object(local, 'prepare_gateway') as gateway, self.assertRaises(RuntimeError):
            local.start(args)
        credentials.assert_not_called(); gateway.assert_not_called()

    def test_gateway_failure_does_not_install_plugin_or_credentials(self):
        args = self.args(environment='client', plugin_source='github:example/plugin#v1.2.3', gateway_url='http://100.64.0.1:8000/v1')
        self.snapshot(args)
        with patch.object(local, 'prepare_gateway', side_effect=RuntimeError('unavailable')), patch.object(local, 'sync_key') as credentials, patch.object(local, 'run') as run, self.assertRaises(RuntimeError):
            local.deploy(args)
        credentials.assert_not_called(); run.assert_not_called()

    def test_snapshot_and_environment_tampering_stop_before_deployment(self):
        args = self.args(environment='gateway'); self.snapshot(args)
        state = local.read_json(self.runtime / 'sync-state.json'); state['environment'] = dict(args.settings, port=9000)
        local.write_json(self.runtime / 'sync-state.json', state)
        with patch.object(local, 'prepare_gateway') as deploy, self.assertRaises(RuntimeError):
            local.deploy(args)
        deploy.assert_not_called()
        state['environment'] = args.settings; state['gateway_files'] = {'file.txt':'wrong-digest'}
        (self.runtime / 'deploy').mkdir(); (self.runtime/'deploy/file.txt').write_text('changed')
        local.write_json(self.runtime/'sync-state.json', state)
        with patch.object(local, 'prepare_gateway') as deploy, self.assertRaises(RuntimeError):
            local.deploy(args)
        deploy.assert_not_called()

    def test_private_import_preserves_existing_and_omits_history(self):
        source = self.root/'private-source'; source.mkdir()
        (source/'gateway.key').write_text('synthetic-new')
        (source/'history.json').write_text('must not copy')
        deploy = self.runtime/'deploy'; (deploy/'private').mkdir(parents=True)
        (deploy/'private/gateway.key').write_text('synthetic-existing')
        local.import_private(source, deploy)
        self.assertEqual((deploy/'private/gateway.key').read_text(), 'synthetic-existing')
        self.assertFalse((deploy/'private/history.json').exists())

    def test_gateway_private_import_includes_conversation_ledger(self):
        source = self.root/'private-source'
        (source/'browser-credentials').mkdir(parents=True)
        (source/'browser-credentials/conversations.json').write_text('{"version":1,"groups":[],"deleted":[]}')
        deploy = self.runtime/'deploy'
        local.import_private(source, deploy, include_state=True)
        self.assertTrue((deploy/'private/browser-credentials/conversations.json').is_file())

    def test_compose_uses_separate_project_and_loopback(self):
        args = self.args(); self.snapshot(args)
        command, env = local.compose_command(self.runtime/'deploy')
        self.assertEqual(command[-1], 'shanghaitech-genai-dev')
        self.assertEqual(env['GENAI_BIND_ADDRESS'], '127.0.0.1')
        self.assertEqual(env['GENAI_HOST_PORT'], '8000')
        self.assertEqual(env['GENAI_IMAGE_TAG'], 'development')

    def test_recovered_instance_is_matched_by_complete_isolated_command(self):
        args = self.args(); self.snapshot(args)
        command = str(args.dsh_app / 'Contents/MacOS/DeepSeek Harness') + ' --user-data-dir=' + args.settings['desktop_user_data']
        rows = subprocess.CompletedProcess([], 0, stdout='12 /Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness\n12345 ' + command + '\n')
        with patch.object(local.subprocess, 'run', side_effect=[rows, subprocess.CompletedProcess([],0,stdout='')]), patch.object(local.os, 'getpgid', return_value=12345), patch.object(local.os, 'kill') as stop, patch.object(local.os, 'waitpid', side_effect=ChildProcessError), patch.object(local.os, 'killpg', side_effect=ProcessLookupError):
            local.stop_development_dsh(args)
        stop.assert_called_once_with(12345, local.signal.SIGTERM)

    def test_process_record_cannot_stop_daily_dsh(self):
        args = self.args(); self.snapshot(args)
        local.write_json(self.runtime/'dsh-process.json', {'pid':12345, 'dsh_home':args.settings['dsh_home'], 'desktop_user_data':args.settings['desktop_user_data']})
        daily = subprocess.CompletedProcess([], 0, stdout='/Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness\n')
        with patch.object(local.subprocess, 'run', return_value=daily), patch.object(local.os, 'killpg') as kill, self.assertRaises(RuntimeError):
            local.stop_development_dsh(args)
        kill.assert_not_called()


class ReleaseTests(unittest.TestCase):
    def spec(self, **changes):
        result = dict(repo='/srv/source', root='/srv/releases', tag='v1.2.3', bind_address='100.64.0.1', project='genai-stable', private_source=None)
        result.update(changes); return result

    def test_only_fixed_tag_and_absolute_remote_paths(self):
        release.validate(self.spec())
        for value in [dict(tag='main'), dict(tag='v1;rm'), dict(root='relative'), dict(project='invalid name')]:
            with self.subTest(value=value), self.assertRaises(ValueError): release.validate(self.spec(**value))

    def test_plan_does_not_connect_or_run_git(self):
        argv = ['deploy-release.py', '--host','example-host','--repo','/srv/source','--root','/srv/releases','--tag','v1.2.3','--bind-address','100.64.0.1','--plan']
        with patch.object(release.sys, 'argv', argv), patch.object(release, 'command') as run:
            release.main()
        run.assert_not_called()

    def test_draft_release_never_reaches_ssh(self):
        argv = ['deploy-release.py', '--host','example-host','--repo','/srv/source','--root','/srv/releases','--tag','v1.2.3','--bind-address','100.64.0.1']
        result = subprocess.CompletedProcess([],0,stdout=json.dumps({'isDraft':True,'tagName':'v1.2.3'}))
        with patch.object(release.sys,'argv',argv), patch.object(release,'command',return_value=result) as run, self.assertRaises(RuntimeError): release.main()
        self.assertEqual(run.call_count,1); self.assertEqual(run.call_args.args[0],'gh')

    def test_failed_deploy_restores_previous_release(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as tmp:
            root=Path(tmp); current='a'*40; previous='b'*40
            for commit in [current,previous]:
                entry=root/'releases'/commit/'tools/local-macos.py'; entry.parent.mkdir(parents=True);entry.write_text('--environment')
            private=root/'runtime/deploy/private'; private.mkdir(parents=True);(private/'gateway.key').write_text('synthetic')
            release.atomic_json(root/'deployment.json',{'commit':previous,'tag':'v1.2.2'})
            def git(repo,*args):
                if args[0]=='cat-file':return 'tag'
                if args[0]=='status':return ''
                return previous if str(repo).endswith(previous) else current
            calls=[]
            def run(*args,**kwargs):
                calls.append(args)
                if len(args)>1 and str(args[1]).endswith(current+'/tools/local-macos.py'):raise RuntimeError('synthetic build failure')
            with patch.object(release,'git',side_effect=git),patch.object(release,'command',side_effect=run),self.assertRaisesRegex(RuntimeError,'synthetic build failure'):
                release.apply_on_host(self.spec(root=str(root), compose_override='/srv/network override.yaml'))
            self.assertTrue(any(len(c)>1 and str(c[1]).endswith(previous+'/tools/local-macos.py') for c in calls))
            self.assertEqual(json.loads((root/'deployment.json').read_text())['commit'],previous)
            deployments = [c for c in calls if '--environment' in c]
            self.assertEqual(len(deployments), 2)
            for c in deployments:
                self.assertEqual(c[c.index('--compose-override') + 1], '/srv/network override.yaml')


if __name__ == '__main__':
    unittest.main()
