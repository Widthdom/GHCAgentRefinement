import io
import json
import os
import subprocess
import sys
from typing import cast

GIT_TIMEOUT_SECONDS = 5
# gitディレクトリ内の目印と、それが示す途中の操作。worktreeごとのgitディレクトリに置かれる。
# rebase-applyは `git am` でも使われるため、`applying` の有無で区別する。
OPERATION_MARKERS = [
    ('MERGE_HEAD', 'a merge'),
    ('rebase-merge', 'a rebase'),
    ('CHERRY_PICK_HEAD', 'a cherry-pick'),
    ('REVERT_HEAD', 'a revert'),
    ('BISECT_LOG', 'a bisect'),
]
ADDITIONAL_CONTEXT = (
    'The Git repository at the session start is in the following state: {message}\n'
    'At the start of your first response, tell the user about this in the language you respond in. '
    'Do not continue, abort, or resolve the operation, and do not switch branches, unless the user asks you to. '
    'Keep this state in mind before editing files or running Git commands that change history.'
)


def run_git(cwd, *args):
    # gitがない、タイムアウトした場合はNoneを返す。終了コードは呼び出し元で判定する。
    try:
        return subprocess.run(
            ['git', *args], cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def get_in_progress_operations(git_directory):
    operations = [
        description for marker, description in OPERATION_MARKERS
        if os.path.exists(os.path.join(git_directory, marker))
    ]
    rebase_apply = os.path.join(git_directory, 'rebase-apply')
    if os.path.isdir(rebase_apply):
        operations.append('a git am' if os.path.exists(os.path.join(rebase_apply, 'applying')) else 'a rebase')
    return operations


def get_git_state_messages(cwd):
    result = run_git(cwd, 'rev-parse', '--absolute-git-dir')
    # gitがない、またはリポジトリ外の場合は何も知らせない。
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return []
    git_directory = result.stdout.strip()

    messages = []
    operations = get_in_progress_operations(git_directory)
    if operations:
        messages.append(f'{" and ".join(operations).capitalize()} is in progress.')

    # 未解決の衝突は `git ls-files -u` にステージ番号付きで現れる。1ファイルにつき最大3行あるため、パスで数える。
    unmerged = run_git(cwd, '-c', 'core.quotePath=false', 'ls-files', '--unmerged')
    if unmerged is not None and unmerged.returncode == 0:
        paths = sorted({line.split('\t', 1)[1] for line in unmerged.stdout.splitlines() if '\t' in line})
        if paths:
            shown = ', '.join(paths[:10]) + (f', and {len(paths) - 10} more' if len(paths) > 10 else '')
            messages.append(f'{len(paths)} file(s) have unresolved conflicts: {shown}.')

    # rebaseやbisectの途中はHEADが切り離されるのが通常のため、途中の操作がないときだけ知らせる。
    if not operations:
        symbolic = run_git(cwd, 'symbolic-ref', '--quiet', 'HEAD')
        if symbolic is not None and symbolic.returncode == 1:
            head = run_git(cwd, 'rev-parse', '--short', 'HEAD')
            commit = head.stdout.strip() if head is not None and head.returncode == 0 else 'unknown'
            messages.append(
                f'HEAD is detached at {commit}. New commits will not belong to any branch.'
            )
    return messages


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace')

    try:
        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')
        cwd = hook_input.get('cwd')
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError('cwd must be a non-empty string.')
        if not os.path.isabs(cwd):
            raise ValueError('cwd must be an absolute path.')
        if not os.path.isdir(cwd):
            raise ValueError('cwd must be an existing directory.')
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to check the Git state: {ex}', file=sys.stderr)
        return 2

    messages = get_git_state_messages(cwd)
    if not messages:
        return 0
    message = '\n'.join(messages)
    print(message, file=sys.stderr)
    # 標準エラーはユーザーに表示されないことがある。sessionStartで解釈される出力はadditionalContextだけのため、
    # エージェントに渡してユーザーへ伝えさせ、正常終了する。
    print(json.dumps(
        {'additionalContext': ADDITIONAL_CONTEXT.format(message=message)},
        ensure_ascii=False, separators=(',', ':'),
    ))
    return 0


# このファイルが直接実行されたときだけ main() を呼び出す。
# import されただけでは main() は呼び出されない。
if __name__ == '__main__':
    sys.exit(main())
