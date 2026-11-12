import io
import json
import os
import runpy
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_git = runpy.run_path(os.path.join(_utilities_directory, 'run-git', 'run-git.py'))


_GIT_TIMEOUT_SECONDS = 5
# gitの実行全体の期限。hookの `timeoutSec`（10秒）より前に打ち切り、確かめられない状態は知らせない。
_GIT_DEADLINE = _git['create_deadline'](7)
# gitディレクトリ内の目印と、それが示す途中の操作。worktreeごとのgitディレクトリに置かれる。
# rebase-applyは `git am` でも使われるため、`applying` の有無で区別する。
_OPERATION_MARKERS = [
    ('MERGE_HEAD', 'a merge'),
    ('rebase-merge', 'a rebase'),
    ('CHERRY_PICK_HEAD', 'a cherry-pick'),
    ('REVERT_HEAD', 'a revert'),
    ('BISECT_LOG', 'a bisect'),
]
_ADDITIONAL_CONTEXT = (
    'The Git repository at the session start is in the following state: {message}\n'
    'At the start of your first response, tell the user about this in the language you respond in. '
    'Do not continue, abort, or resolve the operation, and do not switch branches, unless the user asks you to. '
    'Keep this state in mind before editing files or running Git commands that change history.'
)


def _get_in_progress_operations(git_directory):
    operations = [
        description for marker, description in _OPERATION_MARKERS
        if os.path.exists(os.path.join(git_directory, marker))
    ]
    rebase_apply = os.path.join(git_directory, 'rebase-apply')
    if os.path.isdir(rebase_apply):
        operations.append('a git am' if os.path.exists(os.path.join(rebase_apply, 'applying')) else 'a rebase')
    return operations


def _get_git_state_messages(cwd):
    git_directory = (_git['get_git_output'](
        cwd, ['rev-parse', '--absolute-git-dir'], _GIT_TIMEOUT_SECONDS, _GIT_DEADLINE,
    ) or '').strip()
    # gitがない、またはリポジトリ外の場合は何も知らせない。
    if not git_directory:
        return []

    messages = []
    operations = _get_in_progress_operations(git_directory)
    if operations:
        messages.append(f'{" and ".join(operations).capitalize()} is in progress.')

    # 未解決の衝突は `git ls-files -u` にステージ番号付きで現れる。1ファイルにつき最大3行あるため、パスで数える。
    unmerged = _git['get_git_output'](
        cwd, ['-c', 'core.quotePath=false', 'ls-files', '--unmerged'], _GIT_TIMEOUT_SECONDS, _GIT_DEADLINE,
    )
    if unmerged is not None:
        paths = sorted({line.split('\t', 1)[1] for line in unmerged.splitlines() if '\t' in line})
        if paths:
            shown = ', '.join(paths[:10]) + (f', and {len(paths) - 10} more' if len(paths) > 10 else '')
            messages.append(f'{len(paths)} file(s) have unresolved conflicts: {shown}.')

    # rebaseやbisectの途中はHEADが切り離されるのが通常のため、途中の操作がないときだけ知らせる。
    if not operations:
        symbolic = _git['run_git'](cwd, ['symbolic-ref', '--quiet', 'HEAD'], _GIT_TIMEOUT_SECONDS, _GIT_DEADLINE)
        if symbolic is not None and symbolic.returncode == 1:
            head = _git['get_git_output'](cwd, ['rev-parse', '--short', 'HEAD'], _GIT_TIMEOUT_SECONDS, _GIT_DEADLINE)
            commit = head.strip() if head is not None else 'unknown'
            messages.append(
                f'HEAD is detached at {commit}. New commits will not belong to any branch.'
            )
    return messages


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()
        cwd = _hook_input['get_cwd'](hook_input)
        if not os.path.isdir(cwd):
            raise ValueError('cwd must be an existing directory.')
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to check the Git state: {ex}', file=sys.stderr)
        return 2

    messages = _get_git_state_messages(cwd)
    if not messages:
        return 0
    message = '\n'.join(messages)
    print(message, file=sys.stderr)
    # 標準エラーはユーザーに表示されないことがある。sessionStartで解釈される出力はadditionalContextだけのため、
    # エージェントに渡してユーザーへ伝えさせ、正常終了する。
    print(json.dumps(
        {'additionalContext': _ADDITIONAL_CONTEXT.format(message=message)},
        ensure_ascii=False, separators=(',', ':'),
    ))
    return 0


# このファイルが直接実行されたときだけ main() を呼び出す。
# import されただけでは main() は呼び出されない。
if __name__ == '__main__':
    sys.exit(main())
