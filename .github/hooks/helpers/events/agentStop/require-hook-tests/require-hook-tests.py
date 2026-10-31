import io
import json
import os
import re
import runpy
import sys
from pathlib import Path
from typing import cast


EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
SHELL_TOOL_NAMES = {'bash', 'powershell'}
# パッチで追加・更新・削除するファイルと移動先。
PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+?)\s*$', re.MULTILINE)
# リポジトリ内のhook関連ファイル。グループ1はリポジトリルート、グループ2は `.github/hooks/` からの相対パス。
HOOKS_PATH_PATTERN = re.compile(r'^(.*?)/\.github/hooks/(.+)$', re.IGNORECASE)
ALL_HOOKS_TEST = 'test-all-hooks.py'
CONFIGURATION_TEST = 'test-hook-configurations.py'
TEST_OUTPUT_TEST = 'test-hook-test-output.py'
RUNNER_TEST = 'test-run-hook.py'
REASON_PREFIX = 'Hook files were changed'
PYTHON_COMMAND = 'python' if sys.platform == 'win32' else 'python3'


def get_edited_paths(tool_name, arguments):
    if tool_name == 'apply_patch':
        return [match for text in collect_strings(arguments) for match in PATCH_FILE_PATTERN.findall(text)]
    if not isinstance(arguments, dict):
        return []
    # str_replace_editorのviewは閲覧なので対象外とする。
    if tool_name == 'str_replace_editor' and arguments.get('command') == 'view':
        return []
    path = arguments.get('path')
    return [path] if isinstance(path, str) and path.strip() else []


def collect_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from collect_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from collect_strings(item)


def get_turn_events(transcript_path):
    # ユーザーの最新の依頼以降の、編集ツールで変更したファイルとシェル実行を、完了した順に集める。
    # 戻り値は (('edit', パス) または ('shell', (コマンド, 終了コード)) のリスト, 本hookによる差し戻し済みか)。
    # hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
    events = []
    started = {}
    already_requested = False
    for event in runpy.run_path(
        str(Path(__file__).resolve().parents[3] / 'utilities' / 'read-jsonl-objects.py')
    )['read_jsonl_objects'](transcript_path):
        data = event.get('data', {})
        if not isinstance(data, dict):
            raise ValueError('Event data must be a JSON object.')
        event_type = event.get('type')

        if event_type == 'user.message':
            content = data.get('content')
            if data.get('delivery') != 'queued':
                events.clear()
                started.clear()
                already_requested = False
            elif isinstance(content, str) and content.startswith(REASON_PREFIX):
                already_requested = True
        elif event_type == 'tool.execution_start':
            call_id = data.get('toolCallId')
            tool_name = data.get('toolName')
            if isinstance(call_id, str) and isinstance(tool_name, str):
                started[call_id] = (tool_name.removeprefix('functions.'), data.get('arguments'))
        elif event_type == 'tool.execution_complete':
            call_id = data.get('toolCallId')
            if call_id not in started:
                continue
            tool_name, arguments = started.pop(call_id)
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    pass
            if tool_name in EDIT_TOOL_NAMES and data.get('success') is True:
                events.extend(('edit', path) for path in get_edited_paths(tool_name, arguments))
            elif (tool_name in SHELL_TOOL_NAMES and isinstance(arguments, dict)
                    and isinstance(arguments.get('command'), str)):
                shell_execution = data.get('shellExecution')
                exit_code = shell_execution.get('exitCode') if isinstance(shell_execution, dict) else None
                # 拒否などで実行されなかった場合は終了コードが記録されないため、成功とみなさない。
                events.append(('shell', (arguments['command'], exit_code if type(exit_code) is int else None)))
    return events, already_requested


def get_requirement(path, cwd):
    # 変更したファイルに必要なテストを返す。戻り値は (要件のキー, 情報) またはNone。
    # 情報は、label・test（実行するテストのパス）・accepted（成功とみなすテストファイル名）・readme（確認を促すREADME）。
    normalized = os.path.normpath(os.path.join(cwd, path)).replace('\\', '/')
    match = HOOKS_PATH_PATTERN.match(normalized)
    if not match:
        return None
    root, relative = match.groups()
    hooks_directory = os.path.join(root, '.github', 'hooks')
    parts = relative.split('/')
    if len(parts) == 5 and parts[:2] == ['helpers', 'events']:
        event_name, hook_name, filename = parts[2:]
        # 説明だけの変更ではテストを求めない。
        if filename.lower().endswith('.md'):
            return None
        hook_directory = os.path.join(hooks_directory, 'helpers', 'events', event_name, hook_name)
        # hook自体を削除した場合は対象外とする。
        if not os.path.isdir(hook_directory):
            return None
        test_name = f'test-{hook_name}.py'
        readme = os.path.join(hook_directory, 'README.md')
        is_implementation = not filename.startswith('test-')
        return (os.path.normcase(hook_directory), {
            'label': f'{event_name}/{hook_name}',
            'test': os.path.join(hook_directory, test_name),
            'accepted': (test_name, ALL_HOOKS_TEST),
            'readme': readme if is_implementation and os.path.isfile(readme) else None,
        })
    if len(parts) >= 2 and parts[0] == 'helpers' and parts[1] == 'utilities':
        return (os.path.normcase(os.path.join(hooks_directory, 'helpers', 'utilities')), {
            'label': 'shared utilities (helpers/utilities)',
            'test': os.path.join(hooks_directory, 'tests', ALL_HOOKS_TEST),
            'accepted': (ALL_HOOKS_TEST,),
            'readme': None,
        })
    if len(parts) == 3 and parts[:2] == ['helpers', 'runner'] and not parts[2].lower().endswith('.md'):
        runner_directory = os.path.join(hooks_directory, 'helpers', 'runner')
        readme = os.path.join(runner_directory, 'README.md')
        return (os.path.normcase(runner_directory), {
            'label': 'hook runner (helpers/runner)',
            'test': os.path.join(runner_directory, RUNNER_TEST),
            'accepted': (RUNNER_TEST, ALL_HOOKS_TEST),
            'readme': readme if not parts[2].startswith('test-') and os.path.isfile(readme) else None,
        })
    if len(parts) == 1 and parts[0].lower().endswith('.json'):
        return (os.path.normcase(os.path.join(hooks_directory, 'configurations')), {
            'label': 'hook configurations',
            'test': os.path.join(hooks_directory, 'tests', CONFIGURATION_TEST),
            'accepted': (CONFIGURATION_TEST, ALL_HOOKS_TEST),
            'readme': None,
        })
    if len(parts) >= 2 and parts[0] == 'tests' and not parts[-1].lower().endswith('.md'):
        # 一括実行の探索対象外である共通出力処理と、一括実行・設定検証のテスト自体。
        if parts[1] == 'helpers':
            test_name = TEST_OUTPUT_TEST
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


def is_test_run(command, accepted):
    # `xtest-a.py` などの語中や、`test-a.py` に対する `test-b-a.py` などは除く。
    return any(re.search(r'(?<![\w.-])' + re.escape(name) + r'(?![\w.-])', command) for name in accepted)


def get_unverified(events, cwd):
    # 戻り値は (テストが通っていない要件のリスト, 確認を促すREADMEのリスト)。
    # 要件は、最後に変更した後でテストが成功していなければ未確認とする。
    requirements = {}
    status = {}
    edited_paths = set()
    readmes = {}
    for kind, value in events:
        if kind == 'edit':
            edited_paths.add(os.path.normcase(os.path.normpath(os.path.join(cwd, value))))
            requirement = get_requirement(value, cwd)
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
            if is_test_run(command, info['accepted']):
                status[key] = 'passed' if exit_code == 0 else (
                    'did not complete' if exit_code is None else f'failed with exit code {exit_code}'
                )
    unverified = [(info, status[key]) for key, info in requirements.items() if status[key] != 'passed']
    unchanged_readmes = sorted(
        readme for readme in readmes.values() if os.path.normcase(readme) not in edited_paths
    )
    return unverified, unchanged_readmes


def get_display_path(path, cwd):
    # cwd配下のファイルは相対パスで示し、理由を短くする。
    # Windowsでドライブが異なる場合、commonpathはValueErrorになるため絶対パスのまま示す。
    try:
        if os.path.normcase(os.path.commonpath([cwd, path])) == os.path.normcase(cwd):
            return os.path.relpath(path, cwd).replace('\\', '/')
    except ValueError:
        pass
    return path


def get_block_reason(unverified, unchanged_readmes, cwd):
    lines = [f"{REASON_PREFIX} since the user's latest request, but their tests have not passed since the last change:"]
    for info, status in unverified:
        test = get_display_path(info['test'], cwd)
        if os.path.isfile(info['test']):
            lines.append(f"- {info['label']} ({status}): run `{PYTHON_COMMAND} {test}`")
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
            + ', '.join(get_display_path(readme, cwd) for readme in unchanged_readmes)
        )
    return '\n'.join(lines)


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    try:
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            print('agentStop hook input is empty.', file=sys.stderr)
            return 2
        hook_input = json.loads(hook_input_json)
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        print(f'Failed to parse agentStop hook input: {ex}', file=sys.stderr)
        return 2

    if not isinstance(hook_input, dict):
        print('agentStop hook input must be a JSON object.', file=sys.stderr)
        return 2

    # サブエージェントの停止は対象外。依頼の完了を報告するのは親のため。
    if hook_input.get('agentId'):
        return 0

    cwd = hook_input.get('cwd')
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        print('agentStop hook cwd must be an absolute path.', file=sys.stderr)
        return 2
    cwd = os.path.normpath(cwd)

    transcript_path = hook_input.get('transcriptPath')
    if not isinstance(transcript_path, str) or not transcript_path.strip():
        print('agentStop hook transcriptPath must be a non-empty string.', file=sys.stderr)
        return 2

    if not os.path.isfile(transcript_path):
        print(f'Transcript path is not a file or could not be verified as a file: {transcript_path}', file=sys.stderr)
        return 2

    try:
        # 自身のhook.startが書き込まれるまで最大5秒待ち、最後のツール実行の結果を書き込み済みにする。
        runpy.run_path(
            str(Path(__file__).resolve().parents[3] / 'utilities' / 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        events, already_requested = get_turn_events(transcript_path)
    except (OSError, ValueError) as ex:
        print(f'Failed to inspect hook tests: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、テストを実行できない場合に停止できなくなるのを防ぐ。
    if already_requested:
        return 0

    unverified, unchanged_readmes = get_unverified(events, cwd)
    if unverified:
        print(json.dumps(
            {'decision': 'block', 'reason': get_block_reason(unverified, unchanged_readmes, cwd)},
            ensure_ascii=False, separators=(',', ':'),
        ))
    return 0


if __name__ == '__main__':
    sys.exit(main())
