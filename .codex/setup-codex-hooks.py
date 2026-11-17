import argparse
import json
import ntpath
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from contextlib import contextmanager


_script_directory = os.path.dirname(os.path.abspath(__file__))
_repository_root = os.path.dirname(_script_directory)
_TIMEOUT_SECONDS = 30


class _AppServer:
    def __init__(self, process, timeout):
        self.__process = process
        self.__timeout = timeout
        self.__messages = queue.Queue()
        self.__request_id = 0
        self.__reader = threading.Thread(target=self.__read, daemon=True)
        self.__reader.start()

    def __read(self):
        try:
            for line in self.__process.stdout:
                self.__messages.put(json.loads(line.decode('utf-8')))
        except (OSError, ValueError) as ex:
            self.__messages.put(ex)
        finally:
            self.__messages.put(None)

    def notify(self, method, params=None):
        self.__send({'method': method, 'params': params or {}})

    def __send(self, message):
        self.__process.stdin.write(json.dumps(message, ensure_ascii=False).encode('utf-8') + b'\n')
        self.__process.stdin.flush()

    def request(self, method, params):
        self.__request_id += 1
        self.__send({'id': self.__request_id, 'method': method, 'params': params})
        deadline = time.monotonic() + self.__timeout
        while True:
            try:
                message = self.__messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise RuntimeError(f'{method}: Codex app-server timed out.') from None
            if message is None:
                raise RuntimeError(f'{method}: Codex app-server closed. See its stderr above.')
            if isinstance(message, Exception) or not isinstance(message, dict):
                raise RuntimeError(f'{method}: invalid app-server response: {message}')
            if message.get('id') != self.__request_id:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'{method}: Codex app-server timed out.')
                continue
            if 'error' in message:
                raise RuntimeError(f'{method}: {message["error"]}')
            if not isinstance(message.get('result'), dict):
                raise RuntimeError(f'{method}: missing result object.')
            return message['result']

    def finish(self):
        self.__reader.join(timeout=5)


@contextmanager
def _server(root, codex, timeout=_TIMEOUT_SECONDS):
    command = [*codex, 'app-server']
    with subprocess.Popen(command, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE) as process:
        client = _AppServer(process, timeout)
        try:
            client.request('initialize', {
                'clientInfo': {'name': 'ghc-hook-setup', 'version': '1.0'},
                'capabilities': {'experimentalApi': True},
            })
            client.notify('initialized')
            yield client
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            client.finish()


def _signature(hook):
    return (hook['eventName'], hook['command'], hook.get('matcher'), hook.get('async', False))


def _expected_hooks(config):
    expected = Counter()
    for event, groups in config['hooks'].items():
        for group in groups:
            for handler in group['hooks']:
                if handler['type'] != 'command':
                    raise ValueError('setup supports only command hooks in this checkout.')
                command = handler.get('commandWindows', handler['command']) if os.name == 'nt' else handler['command']
                expected[(event[0].lower() + event[1:], command, group.get('matcher'), handler.get('async', False))] += 1
    if not expected:
        raise ValueError('hooks.json contains no hooks.')
    return expected


def _list_hooks(client, root, path, expected):
    result = client.request('hooks/list', {'cwds': [root]})
    entries = result.get('data')
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError('hooks/list did not return one inventory.')
    entry = entries[0]
    for warning in entry.get('warnings', []):
        print(f'Codex warning: {warning}', file=sys.stderr)
    if entry.get('errors'):
        raise ValueError(f'hooks/list errors: {entry["errors"]}')
    hooks = [hook for hook in entry['hooks'] if isinstance(hook.get('sourcePath'), str)
             and os.path.normcase(os.path.abspath(hook['sourcePath'])).replace(os.sep, '/') == path]
    if Counter(_signature(hook) for hook in hooks) != expected:
        raise ValueError('Loaded hooks do not match this checkout. Check project trust, Codex version, '
                         'managed policy, and hooks/list diagnostics. Linked worktrees may load the main checkout.')
    if len({hook['key'] for hook in hooks}) != len(hooks):
        raise ValueError('hooks/list returned duplicate hook keys.')
    for hook in hooks:
        if (not isinstance(hook['key'], str) or not hook['key']
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', hook['currentHash'])
                or hook.get('isManaged') is not False or hook.get('source') != 'project'):
            raise ValueError('hooks/list returned unexpected hook metadata.')
        if hook.get('enabled') is not True:
            raise ValueError(f'{hook["eventName"]} is disabled. Enable it explicitly in /hooks first.')
    return hooks


def _write_config(client, key, value):
    result = client.request('config/batchWrite', {
        'edits': [{'keyPath': key, 'value': value, 'mergeStrategy': 'upsert'}],
        'reloadUserConfig': True,
    })
    if result.get('status') != 'ok':
        raise ValueError(f'Codex did not confirm the config write: {result}')
    path = result.get('filePath', 'Codex user config').replace(os.sep, '/')
    print(f'Saved {key}: {path}', flush=True)


def _project_trust_key(root):
    # Codexの外部設定キーはWindowsではネイティブ区切り＋ASCII小文字。
    # 表示・照合用パスと違い、このキーを / 区切りで保存するとCodexが認識しない。
    if os.name == 'nt':
        return ntpath.normpath(root).translate(str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))
    return root


def _setup(root, codex, check=False):
    root = os.path.realpath(root).replace(os.sep, '/')
    path = os.path.join(root, '.codex', 'hooks.json')
    with open(path, 'rb') as file:
        snapshot = file.read()
    expected = _expected_hooks(json.loads(snapshot.decode('utf-8')))
    match_path = os.path.normcase(os.path.abspath(path)).replace(os.sep, '/')
    if not check:
        with _server(root, codex) as client:
            # hooks/listは未信頼のcloneのhookを返さないため、先にプロジェクトを信頼する。
            # スレッドやモデルは起動せず、hookのコマンドも実行しない。
            _write_config(client, 'projects', {_project_trust_key(root): {'trust_level': 'trusted'}})
            hooks = _list_hooks(client, root, match_path, expected)
            print(f'Project: {root}', flush=True)
            for hook in hooks:
                print(f'{hook["eventName"]}: {hook["command"]}', flush=True)
            with open(path, 'rb') as file:
                if file.read() != snapshot:
                    raise ValueError('hooks.json changed during setup. Run setup again after reviewing it.')
            # Codex自身の保存処理で対象フィールドだけをマージする。他の設定を再生成しない。
            _write_config(client, 'hooks.state', {
                hook['key']: {'trusted_hash': hook['currentHash']} for hook in hooks
            })
    # 新しいプロセスで永続化を確認する。
    with _server(root, codex) as client:
        hooks = _list_hooks(client, root, match_path, expected)
        if any(hook.get('trustStatus') != 'trusted' for hook in hooks):
            raise ValueError('Some hooks are not trusted. Run setup, or review them in /hooks.')
        print(f'OK: {len(hooks)} hooks are enabled and trusted for {root}', flush=True)


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', newline='\n')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace', newline='\n')
    parser = argparse.ArgumentParser(description='Trust this checkout and its current Codex hooks in your user config.')
    parser.add_argument('--check', action='store_true', help='check persisted hook trust without changing settings')
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 14):
            raise ValueError('Python 3.14 or later is required.')
        codex = shutil.which('codex')
        if codex is None:
            raise ValueError('codex was not found on PATH. Install the Codex CLI first.')
        if shutil.which('git') is None:
            raise ValueError('git was not found on PATH.')
        _setup(_repository_root, [codex], check=args.check)
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired) as ex:
        print(f'Failed to set up Codex hooks: {ex}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
