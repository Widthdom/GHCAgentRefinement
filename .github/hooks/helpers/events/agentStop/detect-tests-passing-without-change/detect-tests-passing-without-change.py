import io
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast


EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
SHELL_TOOL_NAMES = {'bash', 'powershell'}
# パッチで追加・更新・削除するファイルと移動先。
PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+?)\s*$', re.MULTILINE)
# テスト実行とみなすコマンド。verify-completion-claims hookと同じ基準で、`latest` などの語中は除く。
TEST_COMMAND_PATTERN = re.compile(
    r'(?<![a-z0-9])(?:tests?|pytest|unittest|jest|vitest|mocha|rspec|phpunit|ctest|tox|pester)(?![a-z])',
    re.IGNORECASE,
)
# 説明だけのファイル。テストの結果を変えないため、実装の変更に数えない。
DOCUMENT_EXTENSIONS = ('.md', '.markdown', '.rst', '.txt', '.adoc')
REASON_PREFIX = "The tests still pass without this request's implementation changes"
GIT_TIMEOUT_SECONDS = 10
# hook定義のtimeoutSec（180秒）より短くし、Copilotにランナーごと止められる前にテストを打ち切る。
TEST_TIMEOUT_SECONDS = 120
MAX_LISTED_FILES = 10
MAX_COMMAND_LENGTH = 200


def load_test_file_patterns():
    # テストファイルの判定は、block-test-weakening hookの定義を正本として共有する。
    definitions_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'preToolUse', 'block-test-weakening', 'test-weakening-definitions.json',
    )
    with open(definitions_path, encoding='utf-8') as file:
        definitions = json.load(file)
    patterns = definitions.get('testFilePatterns') if isinstance(definitions, dict) else None
    if not isinstance(patterns, list) or not patterns or not all(isinstance(p, str) for p in patterns):
        raise ValueError(f'testFilePatterns must be a non-empty string array: {definitions_path}')
    try:
        return [re.compile(pattern) for pattern in patterns]
    except re.error as ex:
        raise ValueError(f'testFilePatterns has an invalid regular expression: {ex}') from None


def collect_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from collect_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from collect_strings(item)


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


def get_turn(transcript_path):
    # ユーザーの最新の依頼について、開始時刻、編集ツールで変更したファイル、シェルで実行したコマンドを集める。
    # 戻り値は (開始時刻, 変更したファイルのリスト, (ツール名, コマンド, 終了コード) のリスト, 本hookによる差し戻し済みか)。
    # hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。
    started_at = None
    edited_paths = []
    shells = []
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
                timestamp = event.get('timestamp')
                started_at = timestamp if isinstance(timestamp, str) and timestamp.strip() else None
                edited_paths.clear()
                shells.clear()
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
                edited_paths.extend(get_edited_paths(tool_name, arguments))
            elif (tool_name in SHELL_TOOL_NAMES and isinstance(arguments, dict)
                    and isinstance(arguments.get('command'), str)):
                shell_execution = data.get('shellExecution')
                exit_code = shell_execution.get('exitCode') if isinstance(shell_execution, dict) else None
                # 拒否などで実行されなかった場合は終了コードが記録されないため、成功とみなさない。
                shells.append((tool_name, arguments['command'], exit_code if type(exit_code) is int else None))
    return started_at, edited_paths, shells, already_requested


def run_git(cwd, arguments):
    # gitがない、リポジトリ外、または失敗した場合はNoneを返す。検査できないだけなので異常終了にはしない。
    try:
        result = subprocess.run(
            ['git', *arguments], cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode('utf-8', errors='replace')


def classify_edited_paths(edited_paths, cwd, repository_root, test_file_patterns):
    # 変更したファイルを、リポジトリルートからの `/` 区切りの相対パスで、テストファイルと実装ファイルに分ける。
    # リポジトリ外のファイルと説明だけのファイルは除く。
    test_paths = {}
    implementation_paths = {}
    for path in edited_paths:
        # macOSの `/var` → `/private/var` やWindowsの短いファイル名など、gitが返すルートと表記をそろえる。
        absolute = os.path.realpath(os.path.join(cwd, path))
        try:
            relative = os.path.relpath(absolute, repository_root).replace(os.sep, '/')
        except ValueError:
            # Windowsでドライブが異なる場合はリポジトリ外。
            continue
        if relative == '..' or relative.startswith('../') or relative.lower().endswith(DOCUMENT_EXTENSIONS):
            continue
        target = test_paths if any(p.search(relative) for p in test_file_patterns) else implementation_paths
        target.setdefault(os.path.normcase(relative), relative)
    return list(test_paths.values()), list(implementation_paths.values())


def choose_test_command(shells, test_paths):
    # 最後のテスト実行が成功していなければ、verify-completion-claims hookが扱うため対象外とする。
    # 変更したテストファイルを名指しした成功済みのテスト実行があれば、より狭く速いそれを優先する。
    test_runs = [(tool, command, code) for tool, command, code in shells if TEST_COMMAND_PATTERN.search(command)]
    if not test_runs or test_runs[-1][2] != 0:
        return None
    names = {os.path.basename(path) for path in test_paths}
    for tool, command, code in reversed(test_runs):
        if code == 0 and any(name in command for name in names):
            return tool, command
    return test_runs[-1][0], test_runs[-1][1]


def prepare_baseline_tree(repository_root, baseline, worktree, implementation_paths):
    # 依頼開始時点のコミットをworktreeに取り出し、今回の依頼で変更した実装ファイル以外を現在の内容にそろえる。
    # 依頼中のコミットや依頼前からの未コミットの変更も現在の内容として持ち込み、実装ファイルの変更だけを取り除く。
    if run_git(repository_root, ['worktree', 'add', '--detach', '--quiet', worktree, baseline]) is None:
        return False
    changed = run_git(repository_root, ['diff', '--name-only', '-z', '--no-renames', baseline])
    untracked = run_git(repository_root, ['ls-files', '-z', '--others', '--exclude-standard'])
    if changed is None or untracked is None:
        return False
    reverted = {os.path.normcase(path) for path in implementation_paths}
    for relative in {*changed.split('\0'), *untracked.split('\0')} - {''}:
        if os.path.normcase(relative) in reverted:
            continue
        source = os.path.join(repository_root, *relative.split('/'))
        destination = os.path.join(worktree, *relative.split('/'))
        if os.path.isfile(source):
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copy2(source, destination)
        elif not os.path.lexists(source) and os.path.isfile(destination):
            os.remove(destination)
    return True


def run_test_command(tool_name, command, cwd):
    # テストを実行し、終了コードを返す。起動できない場合やタイムアウトした場合はNoneを返す。
    if tool_name == 'powershell':
        arguments = ['powershell' if os.name == 'nt' else 'pwsh', '-NoProfile', '-NonInteractive', '-Command', command]
    else:
        arguments = ['bash', '-c', command]
    try:
        return subprocess.run(
            arguments, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TEST_TIMEOUT_SECONDS, check=False,
        ).returncode
    except (OSError, subprocess.TimeoutExpired):
        return None


def get_root_spellings(repository_root, cwd):
    # コマンド中のリポジトリルートの表記として、gitが返す表記と、cwdの表記からたどったルートを返す。
    # macOSの `/var` と `/private/var` やWindowsの短いファイル名のように、同じフォルダーでも表記が異なることがあるため。
    relative_cwd = os.path.relpath(os.path.realpath(cwd), repository_root)
    cwd_root = cwd
    if relative_cwd != os.curdir:
        for _ in relative_cwd.split(os.sep):
            cwd_root = os.path.dirname(cwd_root)
    roots = {repository_root, cwd_root}
    return relative_cwd, sorted({*roots, *(root.replace(os.sep, '/') for root in roots)}, key=len, reverse=True)


def passes_without_change(repository_root, cwd, baseline, implementation_paths, tool_name, command):
    # 実装ファイルだけを依頼開始時点に戻した複製でテストを再実行し、成功したかを返す。実行できなければNoneを返す。
    temporary = tempfile.mkdtemp(prefix='ghc-tests-passing-without-change-')
    worktree = os.path.join(temporary, 'worktree')
    try:
        if not prepare_baseline_tree(repository_root, baseline, worktree, implementation_paths):
            return None
        relative_cwd, root_spellings = get_root_spellings(repository_root, cwd)
        # 空のフォルダーはgitが管理しないため、cwdにあたるフォルダーが複製にないことがある。
        test_cwd = os.path.normpath(os.path.join(worktree, relative_cwd))
        os.makedirs(test_cwd, exist_ok=True)
        # コマンドが絶対パスでリポジトリを指していても、複製の側を実行するよう置き換える。
        for root in root_spellings:
            command = command.replace(root, worktree if os.sep in root else worktree.replace(os.sep, '/'))
        exit_code = run_test_command(tool_name, command, test_cwd)
        return None if exit_code is None else exit_code == 0
    finally:
        run_git(repository_root, ['worktree', 'remove', '--force', worktree])
        shutil.rmtree(temporary, ignore_errors=True)
        run_git(repository_root, ['worktree', 'prune'])


def get_block_reason(command, implementation_paths):
    if len(command) > MAX_COMMAND_LENGTH:
        command = command[:MAX_COMMAND_LENGTH] + '...'
    lines = [
        f'{REASON_PREFIX}. The last passing test command ({command}) was re-run on a copy of the repository '
        'where only these implementation files were restored to their state before this request '
        '(test files kept as they are now), and it still exited with code 0:'
    ]
    lines.extend(f'- {path}' for path in implementation_paths[:MAX_LISTED_FILES])
    if len(implementation_paths) > MAX_LISTED_FILES:
        lines.append(f'- ... and {len(implementation_paths) - MAX_LISTED_FILES} more')
    lines.append(
        'No test fails without the change, so the tests do not verify it. Add or strengthen a test that fails '
        'without the change and passes with it. If the change intentionally preserves behavior '
        '(for example, refactoring), state that explicitly in the final response instead.'
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

    # サブエージェントの停止は対象外。最終応答として報告されるのは親の応答のため。
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
        test_file_patterns = load_test_file_patterns()
        # 自身のhook.startが書き込まれるまで最大5秒待ち、最後のツール実行の結果を書き込み済みにする。
        runpy.run_path(
            str(Path(__file__).resolve().parents[3] / 'utilities' / 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))
        started_at, edited_paths, shells, already_requested = get_turn(transcript_path)
    except (OSError, ValueError) as ex:
        print(f'Failed to inspect tests passing without change: {ex}', file=sys.stderr)
        return 2

    # 同じ依頼で差し戻すのは1回だけにし、振る舞いを変えない変更でも停止できるようにする。
    if already_requested or started_at is None or not edited_paths:
        return 0

    repository_root = run_git(cwd, ['rev-parse', '--show-toplevel'])
    if repository_root is None:
        return 0
    repository_root = os.path.realpath(repository_root.strip())

    test_paths, implementation_paths = classify_edited_paths(edited_paths, cwd, repository_root, test_file_patterns)
    if not test_paths or not implementation_paths:
        return 0
    chosen = choose_test_command(shells, test_paths)
    if chosen is None:
        return 0

    # 依頼中にコミットしていても戻せるよう、HEADではなく依頼開始時点の最後のコミットを基準にする。
    baseline = run_git(repository_root, ['rev-list', '-1', '--first-parent', f'--before={started_at}', 'HEAD'])
    if not baseline or not baseline.strip():
        return 0

    if passes_without_change(repository_root, cwd, baseline.strip(), implementation_paths, *chosen):
        print(json.dumps(
            {'decision': 'block', 'reason': get_block_reason(chosen[1], implementation_paths)},
            ensure_ascii=False, separators=(',', ':'),
        ))
    return 0


if __name__ == '__main__':
    sys.exit(main())
