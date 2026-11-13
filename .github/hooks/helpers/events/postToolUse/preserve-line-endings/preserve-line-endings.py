import io
import os
import runpy
import sqlite3
import sys
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_hook_output = runpy.run_path(os.path.join(_utilities_directory, 'write-hook-output', 'write-hook-output.py'))
_get_path_sha256_digest = runpy.run_path(os.path.join(
    _utilities_directory, 'get-sha256-digest', 'get-sha256-digest.py',
))['get_path_sha256_digest']
_get_state_path = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get-state-path.py'))['get_state_path']
_git = runpy.run_path(os.path.join(_utilities_directory, 'run-git', 'run-git.py'))
_get_edited_paths = runpy.run_path(os.path.join(_utilities_directory, 'get-edited-paths', 'get-edited-paths.py'))['get_edited_paths']
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']
_unwrap_tool_args = runpy.run_path(os.path.join(_utilities_directory, 'unwrap-tool-args', 'unwrap-tool-args.py'))['unwrap_tool_args']
_filter_new_notifications = runpy.run_path(os.path.join(
    _utilities_directory, 'filter-new-notifications', 'filter-new-notifications.py',
))['filter_new_notifications']


# SDKがファイル書き込みとして扱う組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
_EDIT_TOOL_NAMES = {'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
_UTF8_BOM = b'\xef\xbb\xbf'
_GIT_TIMEOUT_SECONDS = 5
# gitの実行全体の期限。ファイルが多くても、hookの `timeoutSec`（10秒）より前に打ち切り、比べられない分は対象外とする。
_GIT_DEADLINE = _git['create_deadline'](7)
_FORMAT_CONTEXT = (
    'The following edited files no longer match the file format of their committed version: {changes}. '
    'Editing tools can silently rewrite line endings, the byte order mark, or the final newline when '
    'a whole file is rewritten, which turns a small edit into a whole-file diff. Unless the user asked '
    'for this format change, restore the original format while keeping your intended content changes, '
    'for example by rewriting the file with the original line endings, and verify the result with git diff.'
)


def _get_committed_content(path):
    # HEADのファイルを、チェックアウト時と同じ変換（core.autocrlfや.gitattributesのeol）を通して取得する。
    # 変換前の内容と比べると、WindowsのautocrlfでCRLFになった作業ツリーを誤って変更扱いしてしまう。
    # gitがない、リポジトリ外、HEADにないファイル（新規・管理外）は比較できないためNoneを返す。
    result = _git['run_git'](
        os.path.dirname(path), ['cat-file', '--filters', f'HEAD:./{os.path.basename(path)}'],
        _GIT_TIMEOUT_SECONDS, _GIT_DEADLINE,
    )
    if result is None or result.returncode != 0:
        return None
    return result.stdout


def _get_line_ending_style(content):
    crlf = content.count(b'\r\n')
    lf = content.count(b'\n') - crlf
    if crlf and lf:
        return 'mixed CRLF/LF'
    if crlf:
        return 'CRLF'
    if lf:
        return 'LF'
    return None


def _ends_with_newline(content):
    return content.endswith(b'\n')


def _get_format_changes(committed, current):
    # NUL文字を含むファイルはバイナリまたはUTF-16などとみなし、改行の判定をしない。
    if b'\0' in committed or b'\0' in current:
        return []
    changes = []
    committed_style = _get_line_ending_style(committed)
    current_style = _get_line_ending_style(current)
    # 元から混在しているファイルと、改行が1つもない内容は統一の基準がないため比べない。
    if committed_style in ('CRLF', 'LF') and current_style and current_style != committed_style:
        changes.append(f'line endings changed from {committed_style} to {current_style}')
    if committed.startswith(_UTF8_BOM) != current.startswith(_UTF8_BOM):
        changes.append('UTF-8 BOM removed' if committed.startswith(_UTF8_BOM) else 'UTF-8 BOM added')
    # 空ファイルには末尾の改行という概念がないため、どちらかが空なら比べない。
    if committed.removeprefix(_UTF8_BOM) and current.removeprefix(_UTF8_BOM):
        if _ends_with_newline(committed) != _ends_with_newline(current):
            changes.append('final newline removed' if _ends_with_newline(committed) else 'final newline added')
    return changes


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()

        state_path = _get_state_path('preserve-line-endings', hook_input.get('sessionId'))

        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)

        tool_name = tool_name.removeprefix('functions.')
        if tool_name not in _EDIT_TOOL_NAMES:
            return 0
        # 相対パスはcwd基準で解決し、同じファイルを別表記でも同一と判定する。
        # gitは大文字小文字を区別するため、問い合わせ・読み込み・表示には正規化前のパスを使い、normcaseは照合だけに使う。
        paths = {}
        for path in _get_edited_paths(tool_name, _unwrap_tool_args(tool_args)):
            real_path = os.path.realpath(os.path.join(cwd, path))
            paths.setdefault(os.path.normcase(real_path), real_path)

        results = []
        for normalized_path, path in sorted(paths.items()):
            # 削除されたファイルと、パッチの移動元は比較対象がない。
            if not os.path.isfile(path):
                continue
            committed = _get_committed_content(path)
            if committed is None:
                continue
            with open(path, 'rb') as file:
                current = file.read()
            # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
            file_key = _get_path_sha256_digest(normalized_path)
            changes = _get_format_changes(committed, current)
            results.append((file_key, (path, changes), changes))
        # hookは呼び出しごとに別プロセスなので、通知済みの変更をセッションUUID別に保存する。
        # DBファイル自体は自動削除しない。
        notifications = []
        # 崩れたファイルがあるか、解消した記録を消す必要がある場合だけ状態を開く。
        # 形式が崩れない通常の編集では状態ファイルを作らない。
        if any(changes for _, _, changes in results) or (results and os.path.isfile(state_path)):
            notifications = _filter_new_notifications(state_path, results)
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to check line endings: {ex}', file=sys.stderr)
        return 2

    if notifications:
        cwd = os.path.realpath(cwd)
        changes = '; '.join(
            f'{_get_display_path(path, cwd)} ({", ".join(file_changes)})' for path, file_changes in notifications
        )
        # SDK形式のトップレベルadditionalContext。正常な介入なので終了コードは0。
        _hook_output['write_additional_context'](_FORMAT_CONTEXT.format(changes=changes))
    return 0


if __name__ == '__main__':
    sys.exit(main())
