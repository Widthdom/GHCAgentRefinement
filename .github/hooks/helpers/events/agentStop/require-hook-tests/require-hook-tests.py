import io
import json
import os
import re
import runpy
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_turn_events = runpy.run_path(os.path.join(_utilities_directory, 'get-turn-events', 'get-turn-events.py'))
_get_edited_paths = runpy.run_path(os.path.join(_utilities_directory, 'get-edited-paths', 'get-edited-paths.py'))['get_edited_paths']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']
_check_test_runs = runpy.run_path(os.path.join(_utilities_directory, 'check-test-runs', 'check-test-runs.py'))


_EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
_SHELL_TOOL_NAMES = {'bash', 'powershell'}
# リポジトリ内のhook関連ファイル。グループ1はリポジトリルート、グループ2は `.github/hooks/` からの相対パス。
_HOOKS_PATH_PATTERN = re.compile(r'^(.*?)/\.github/hooks/(.+)$', re.IGNORECASE)
_ALL_HOOKS_TEST = 'test-all-hooks.py'
_CONFIGURATION_TEST = 'test-hook-configurations.py'
_TEST_OUTPUT_HELPER = 'hook-test-output.py'
_TEST_OUTPUT_TEST = 'test-hook-test-output.py'
_RUNNER_TEST = 'test-run-hook.py'
_REASON_PREFIX = 'Hook files were changed'
_PYTHON_COMMAND = 'python' if sys.platform == 'win32' else 'python3'


def _get_turn_events(transcript_path):
    # ユーザーの最新の依頼以降の、編集ツールで変更したファイルとシェル実行を、完了した順に集める。
    # 戻り値は (('edit', パス) または ('shell', (コマンド, 終了コード)) のリスト, 本hookによる差し戻し済みか)。
    turn_events, already_requested = _turn_events['get_latest_turn'](transcript_path, _REASON_PREFIX)
    events = []
    for tool_name, arguments, completion in _turn_events['get_tool_calls'](turn_events):
        if completion is None:
            continue
        arguments = _unwrap_tool_args(arguments)
        if tool_name in _EDIT_TOOL_NAMES and completion.get('success') is True:
            events.extend(('edit', path) for path in _get_edited_paths(tool_name, arguments))
        elif (tool_name in _SHELL_TOOL_NAMES and isinstance(arguments, dict)
                and isinstance(arguments.get('command'), str)):
            # 拒否などで実行されなかった場合は終了コードが記録されないため、成功とみなさない。
            events.append(('shell', (arguments['command'], _turn_events['get_shell_exit_code'](completion))))
    return events, already_requested


def _get_requirement(path, cwd):
    # 変更したファイルに必要なテストを返す。戻り値は (要件のキー, 情報) またはNone。
    # 情報は、label・test（実行するテストのパス）・accepted（成功とみなすテストファイル名）・readme（確認を促すREADME）。
    normalized = os.path.normpath(os.path.join(cwd, path)).replace(os.sep, '/')
    match = _HOOKS_PATH_PATTERN.match(normalized)
    if not match:
        return None
    root, relative = match.groups()
    hooks_directory = os.path.join(root, '.github', 'hooks')
    parts = relative.split('/')
    # 共通hookは helpers/events、このリポジトリ専用のhookは helpers/-repo-events に置く。
    if len(parts) == 5 and parts[0] == 'helpers' and parts[1] in ('events', '-repo-events'):
        event_name, hook_name, filename = parts[2:]
        # 説明だけの変更ではテストを求めない。
        if filename.lower().endswith('.md'):
            return None
        hook_directory = os.path.join(hooks_directory, 'helpers', parts[1], event_name, hook_name)
        # hook自体を削除した場合は対象外とする。
        if not os.path.isdir(hook_directory):
            return None
        test_name = f'test-{hook_name}.py'
        readme = os.path.join(hook_directory, 'README.md')
        is_implementation = not filename.startswith('test-')
        return (os.path.normcase(hook_directory), {
            'label': f'{event_name}/{hook_name}',
            'test': os.path.join(hook_directory, test_name),
            'accepted': (test_name, _ALL_HOOKS_TEST),
            'readme': readme if is_implementation and os.path.isfile(readme) else None,
        })
    if len(parts) >= 2 and parts[0] == 'helpers' and parts[1] == 'utilities':
        return (os.path.normcase(os.path.join(hooks_directory, 'helpers', 'utilities')), {
            'label': 'shared utilities (helpers/utilities)',
            'test': os.path.join(hooks_directory, 'tests', _ALL_HOOKS_TEST),
            'accepted': (_ALL_HOOKS_TEST,),
            'readme': None,
        })
    if len(parts) == 3 and parts[:2] == ['helpers', 'runner'] and not parts[2].lower().endswith('.md'):
        runner_directory = os.path.join(hooks_directory, 'helpers', 'runner')
        readme = os.path.join(runner_directory, 'README.md')
        return (os.path.normcase(runner_directory), {
            'label': 'hook runner (helpers/runner)',
            'test': os.path.join(runner_directory, _RUNNER_TEST),
            'accepted': (_RUNNER_TEST, _ALL_HOOKS_TEST),
            'readme': readme if not parts[2].startswith('test-') and os.path.isfile(readme) else None,
        })
    if len(parts) == 1 and parts[0].lower().endswith('.json'):
        return (os.path.normcase(os.path.join(hooks_directory, 'configurations')), {
            'label': 'hook configurations',
            'test': os.path.join(hooks_directory, 'tests', _CONFIGURATION_TEST),
            'accepted': (_CONFIGURATION_TEST, _ALL_HOOKS_TEST),
            'readme': None,
        })
    if len(parts) >= 2 and parts[0] == 'tests' and not parts[-1].lower().endswith('.md'):
        # 個別のテストが読み込むその他の共通処理は、それを使う個別のテストを含む一括実行で確かめる。
        if parts[1] == 'helpers' and parts[-1] not in (_TEST_OUTPUT_HELPER, _TEST_OUTPUT_TEST):
            return (os.path.normcase(os.path.join(hooks_directory, 'tests', 'helpers')), {
                'label': 'test helpers (tests/helpers)',
                'test': os.path.join(hooks_directory, 'tests', _ALL_HOOKS_TEST),
                'accepted': (_ALL_HOOKS_TEST,),
                'readme': None,
            })
        # 一括実行の探索対象外である共通出力処理と、一括実行・設定検証のテスト自体。
        if parts[1] == 'helpers':
            test_name = _TEST_OUTPUT_TEST
            test_path = os.path.join(hooks_directory, 'tests', 'helpers', test_name)
        else:
            test_name = parts[-1]
            test_path = os.path.join(hooks_directory, 'tests', test_name)
        if not os.path.isfile(test_path):
            return None
        return (os.path.normcase(test_path), {
            'label': f'tests/{"helpers/" if parts[1] == "helpers" else ""}{test_name}',
            'test': test_path,
            'accepted': (test_name,),
            'readme': None,
        })
    return None


def _get_test_pattern(accepted):
    # `xtest-a.py` などの語中や、`test-a.py` に対する `test-b-a.py` などは除く。
    return re.compile('|'.join(r'(?<![\w.-])' + re.escape(name) + r'(?![\w.-])' for name in accepted))


def _get_test_status(command, exit_code, accepted):
    # テストファイルを実行していなければNone。`cat`・`git add` などの読むだけのコマンドは実行とみなさない。
    # 後に `;`・`||` で別のコマンドが続くなど、終了コードがテストの結果を表さない場合は成功とみなさない。
    pattern = _get_test_pattern(accepted)
    if not _check_test_runs['is_test_command'](command, pattern):
        return None
    if exit_code is None:
        return 'did not complete'
    if exit_code != 0:
        return f'failed with exit code {exit_code}'
    if not _check_test_runs['is_test_result_reported'](command, pattern):
        return 'exit status hidden by a following ";", "||" or "&", or a pipe without pipefail'
    return 'passed'


def _get_unverified(events, cwd):
    # 戻り値は (テストが通っていない要件のリスト, 確認を促すREADMEのリスト)。
    # 要件は、最後に変更した後でテストが成功していなければ未確認とする。
    requirements = {}
    status = {}
    edited_paths = set()
    readmes = {}
    for kind, value in events:
        if kind == 'edit':
            edited_paths.add(os.path.normcase(os.path.normpath(os.path.join(cwd, value))))
            requirement = _get_requirement(value, cwd)
            if requirement is None:
                continue
            key, info = requirement
            requirements[key] = info
            status[key] = 'not run'
            if info['readme'] is not None:
                readmes[key] = info['readme']
            continue
        command, exit_code = value
        for key, info in requirements.items():
            test_status = _get_test_status(command, exit_code, info['accepted'])
            if test_status is not None:
                status[key] = test_status
    unverified = [(info, status[key]) for key, info in requirements.items() if status[key] != 'passed']
    unchanged_readmes = sorted(
        readme for readme in readmes.values() if os.path.normcase(readme) not in edited_paths
    )
    return unverified, unchanged_readmes


def _get_block_reason(unverified, unchanged_readmes, cwd):
    lines = [f"{_REASON_PREFIX} since the user's latest request, but their tests have not passed since the last change:"]
    for info, status in unverified:
        test = _get_display_path(info['test'], cwd)
        if os.path.isfile(info['test']):
            lines.append(f"- {info['label']} ({status}): run `{_PYTHON_COMMAND} {test}`")
        else:
            lines.append(
                f"- {info['label']}: the test file `{test}` does not exist. "
                'Create it following .github/skills/create-hook/references/hook-conventions.md, then run it.'
            )
    lines.append(
        'Run them now and fix any failures before finishing. '
        'If you cannot run them, state explicitly in the final response that they were not run.'
    )
    if unchanged_readmes:
        lines.append(
            'Also check whether these READMEs need updating for the implementation changes: '
            + ', '.join(_get_display_path(readme, cwd) for readme in unchanged_readmes)
        )
    return '\n'.join(lines)


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()
        # サブエージェントの停止は対象外。依頼の完了を報告するのは親のため。
        if hook_input.get('agentId'):
            return 0
        cwd = os.path.normpath(_hook_input['get_cwd'](hook_input))
        transcript_path = _hook_input['get_transcript_path'](hook_input)

        # 自身のhook.startが書き込まれるまで最大5秒待ち、最後のツール実行の結果を書き込み済みにする。
        runpy.run_path(
            os.path.join(_utilities_directory, 'wait-for-agent-stop-event', 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        events, already_requested = _get_turn_events(transcript_path)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to inspect hook tests: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、テストを実行できない場合に停止できなくなるのを防ぐ。
    if already_requested:
        return 0

    unverified, unchanged_readmes = _get_unverified(events, cwd)
    if unverified:
        print(json.dumps(
            {'decision': 'block', 'reason': _get_block_reason(unverified, unchanged_readmes, cwd)},
            ensure_ascii=False, separators=(',', ':'),
        ))
    return 0


if __name__ == '__main__':
    sys.exit(main())
