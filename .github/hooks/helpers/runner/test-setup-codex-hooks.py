import io
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


_script_directory = os.path.dirname(os.path.abspath(__file__))
_hooks_directory = os.path.dirname(os.path.dirname(_script_directory))
_repository_root = os.path.dirname(os.path.dirname(_hooks_directory))
_script = os.path.join(_repository_root, '.codex', 'setup-codex-hooks.py')
_setup = runpy.run_path(_script)
_run_tests = runpy.run_path(os.path.join(_hooks_directory, 'tests', 'helpers', 'hook-test-output.py'))['run_tests']
_FAKE_SERVER = '''import hashlib, json, os, sys, time
base = os.path.dirname(__file__)
state_path = os.path.join(base, 'state.json')
mode = open(os.path.join(base, 'mode.txt'), encoding='utf-8').read()
if mode == 'closed':
    sys.exit(1)
if mode == 'malformed':
    print('not JSON', flush=True)
    sys.exit(1)
if mode == 'timeout':
    time.sleep(60)
    sys.exit(1)
with open(state_path, encoding='utf-8') as file:
    state = json.load(file)
def merge(target, values):
    for key, value in values.items():
        if isinstance(value, dict):
            merge(target.setdefault(key, {}), value)
        else:
            target[key] = value
for line in sys.stdin.buffer:
    request = json.loads(line.decode('utf-8'))
    method, params = request['method'], request.get('params', {})
    with open(os.path.join(base, 'requests.jsonl'), 'a', encoding='utf-8', newline='\\n') as file:
        file.write(json.dumps(dict(request, argv=sys.argv[1:])) + '\\n')
    if 'id' not in request:
        continue
    result = {}
    if method == 'hooks/list':
        root = params['cwds'][0]
        path = os.path.join(root, '.codex', 'hooks.json').replace(os.sep, '/')
        project_key = os.path.normpath(root)
        if os.name == 'nt':
            project_key = project_key.translate(str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))
        trusted_project = state.get('projects', {}).get(project_key, {}).get('trust_level') == 'trusted'
        hooks = []
        if trusted_project:
            with open(path, encoding='utf-8') as file:
                config = json.load(file)
            for event, groups in config['hooks'].items():
                for i, group in enumerate(groups):
                    for j, handler in enumerate(group['hooks']):
                        key = f'{path}:{event}:{i}:{j}'
                        digest = 'sha256:' + hashlib.sha256(json.dumps(handler).encode()).hexdigest()
                        saved = state.get('hooks', {}).get('state', {}).get(key, {})
                        command = handler.get('commandWindows', handler['command']) if os.name == 'nt' else handler['command']
                        hooks.append({'sourcePath': path, 'eventName': event[0].lower() + event[1:],
                            'command': command, 'matcher': group.get('matcher'), 'async': handler.get('async', False),
                            'key': key, 'currentHash': digest, 'source': 'project', 'isManaged': False,
                            'enabled': saved.get('enabled', True),
                            'trustStatus': 'trusted' if saved.get('trusted_hash') == digest else 'untrusted'})
        if hooks:
            if mode == 'disabled': hooks[0]['enabled'] = False
            if mode == 'hash': hooks[0]['currentHash'] = 'invalid'
            if mode == 'managed': hooks[0]['isManaged'] = True
            if mode == 'mismatch': hooks[0]['command'] = 'unexpected command'
            if mode == 'missing': hooks.pop()
            if mode == 'duplicate': hooks[-1]['key'] = hooks[0]['key']
            if mode == 'changed':
                with open(path, 'a', encoding='utf-8', newline='\\n') as file: file.write(' ')
        foreign = {'sourcePath': os.path.join(base, 'other', 'hooks.json'),
                   'key': 'foreign', 'enabled': False, 'currentHash': 'sha256:' + 'f' * 64}
        if mode == 'foreign': hooks = []
        result = {'data': [{'hooks': hooks + [foreign], 'warnings': [],
                            'errors': ['policy error'] if mode == 'errors' else []}]}
    elif method == 'config/batchWrite':
        for edit in params['edits']:
            assert edit['mergeStrategy'] == 'upsert'
            target = state
            for key in edit['keyPath'].split('.'):
                target = target.setdefault(key, {})
            merge(target, edit['value'])
        if mode != 'no-persist':
            with open(state_path, 'w', encoding='utf-8', newline='\\n') as file:
                json.dump(state, file)
        result = {'status': 'ok', 'filePath': state_path}
    if mode == 'rpc-error':
        response = {'id': request['id'], 'error': {'code': -32601, 'message': 'unsupported method'}}
    else:
        response = {'id': request['id'], 'result': result}
    print(json.dumps({'method': 'notification'}), flush=True)
    print(json.dumps(response), flush=True)
'''


class SetupCodexHooksTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ghc-codex-setup-test-')
        self.addCleanup(temporary.cleanup)
        self.__temporary = os.path.realpath(temporary.name)
        environment = patch.dict(os.environ, TMPDIR=self.__temporary, TEMP=self.__temporary, TMP=self.__temporary)
        environment.start()
        self.addCleanup(environment.stop)
        self.__root = os.path.join(self.__temporary, 'clone 日本語 with spaces')
        os.makedirs(os.path.join(self.__root, '.codex'))
        shutil.copy(os.path.join(_repository_root, '.codex', 'hooks.json'), os.path.join(self.__root, '.codex'))
        self.__fake = os.path.join(self.__temporary, 'fake-server.py')
        with open(self.__fake, 'w', encoding='utf-8', newline='\n') as file:
            file.write(_FAKE_SERVER)
        self.__initial = {'model': 'keep-model', 'projects': {'other-repo': {'trust_level': 'untrusted'}},
                          'hooks': {'state': {'foreign': {'enabled': False, 'trusted_hash': 'keep-hash'}}}}
        self.__write_state(self.__initial)
        self.__mode('normal')

    def __mode(self, value):
        with open(os.path.join(self.__temporary, 'mode.txt'), 'w', encoding='utf-8', newline='\n') as file:
            file.write(value)

    def __write_state(self, state):
        with open(os.path.join(self.__temporary, 'state.json'), 'w', encoding='utf-8', newline='\n') as file:
            json.dump(state, file)

    def __read_state(self):
        with open(os.path.join(self.__temporary, 'state.json'), encoding='utf-8') as file:
            return json.load(file)

    def __requests(self):
        path = os.path.join(self.__temporary, 'requests.jsonl')
        if not os.path.exists(path):
            return []
        with open(path, encoding='utf-8') as file:
            return [json.loads(line) for line in file]

    def __run(self, check=False):
        output = io.StringIO()
        with redirect_stdout(output):
            _setup['_setup'](self.__root, [sys.executable, self.__fake], check=check)
        return output.getvalue()

    def test_setup_persists_only_checkout_trust_and_preserves_settings(self):
        self.assertIn('OK: 6 hooks', self.__run())
        state = self.__read_state()
        self.assertEqual(state['model'], 'keep-model')
        self.assertEqual(state['projects']['other-repo'], self.__initial['projects']['other-repo'])
        self.assertEqual(state['hooks']['state']['foreign'], self.__initial['hooks']['state']['foreign'])
        projects = {key.replace(os.sep, '/'): value for key, value in state['projects'].items()}
        # パスの表記揺れを除いて、このcloneだけが信頼されたことを検証する。
        root = os.path.normcase(self.__root).replace(os.sep, '/')
        self.assertEqual(projects[root], {'trust_level': 'trusted'})
        requests = self.__requests()
        writes = [r['params'] for r in requests if r['method'] == 'config/batchWrite']
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[0]['edits'][0]['keyPath'], 'projects')
        self.assertEqual(writes[1]['edits'][0]['keyPath'], 'hooks.state')
        self.assertEqual(set(writes[1]['edits'][0]['value']), set(state['hooks']['state']) - {'foreign'})
        self.assertTrue(all(set(v) == {'trusted_hash'} for v in writes[1]['edits'][0]['value'].values()))
        listings = [r for r in requests if r['method'] == 'hooks/list']
        self.assertNotIn('-c', listings[0]['argv'])
        self.assertNotIn('-c', listings[1]['argv'])
        self.assertNotIn('thread/start', [r['method'] for r in requests])
        self.assertEqual(len([r for r in requests if r['method'] == 'initialize']), 2)

    def test_repeat_setup_and_check(self):
        self.__run()
        before = self.__read_state()
        self.assertIn('OK: 6 hooks', self.__run())
        self.assertEqual(self.__read_state(), before)
        writes = sum(r['method'] == 'config/batchWrite' for r in self.__requests())
        self.assertIn('OK: 6 hooks', self.__run(check=True))
        self.assertEqual(sum(r['method'] == 'config/batchWrite' for r in self.__requests()), writes)
        self.assertEqual(self.__read_state(), before)

    def test_check_untrusted_project_does_not_save(self):
        with self.assertRaisesRegex(ValueError, 'Loaded hooks do not match'):
            self.__run(check=True)
        self.assertEqual(self.__read_state(), self.__initial)
        self.assertFalse(any(r['method'] == 'config/batchWrite' for r in self.__requests()))

    def test_unexpected_inventory_or_definition_change_does_not_trust_hooks(self):
        for mode in ('disabled', 'hash', 'managed', 'mismatch', 'missing', 'duplicate', 'foreign', 'errors', 'changed'):
            with self.subTest(mode=mode):
                self.__mode(mode)
                with self.assertRaises(ValueError):
                    self.__run()
                self.assertEqual(self.__read_state()['hooks'], self.__initial['hooks'])
                writes = [r for r in self.__requests() if r['method'] == 'config/batchWrite']
                self.assertTrue(all(r['params']['edits'][0]['keyPath'] == 'projects' for r in writes))

    def test_fresh_process_detects_failed_persistence(self):
        self.__mode('no-persist')
        with self.assertRaisesRegex(ValueError, 'Loaded hooks do not match'):
            self.__run()
        self.assertEqual(self.__read_state(), self.__initial)

    def test_changed_hash_needs_setup_again(self):
        self.__run()
        path = os.path.join(self.__root, '.codex', 'hooks.json')
        with open(path, encoding='utf-8') as file:
            config = json.load(file)
        config['hooks']['Stop'][0]['hooks'][0]['statusMessage'] = 'changed'
        with open(path, 'w', encoding='utf-8', newline='\n') as file:
            json.dump(config, file)
        with self.assertRaisesRegex(ValueError, 'not trusted'):
            self.__run(check=True)
        self.assertIn('OK: 6 hooks', self.__run())

    def test_rpc_failure_eof_malformed_and_timeout(self):
        for mode, expected in (('rpc-error', 'unsupported method'), ('closed', 'closed'),
                               ('malformed', 'invalid app-server response'), ('timeout', 'timed out')):
            with self.subTest(mode=mode):
                self.__mode(mode)
                with self.assertRaisesRegex(RuntimeError, expected):
                    with _setup['_server'](self.__root, [sys.executable, self.__fake], timeout=0.2):
                        self.fail('initialization should fail')

    def test_cli_help_does_not_require_codex(self):
        result = subprocess.run([sys.executable, _script, '--help'], capture_output=True, timeout=10, check=False,
                                env=dict(os.environ, TMPDIR=self.__temporary, TEMP=self.__temporary, TMP=self.__temporary))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'--check', result.stdout)

    def test_windows_project_key_matches_codex_external_format(self):
        # これは表示パスではなくCodexがネイティブ表記で要求する不透明な設定キー。
        with patch.object(os, 'name', 'nt'):
            self.assertEqual(_setup['_project_trust_key']('C:/Users/Üser/Repo'), r'c:\users\Üser\repo')


if __name__ == '__main__':
    _run_tests()
