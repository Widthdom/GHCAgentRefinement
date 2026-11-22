import argparse
import os
import sqlite3
import sys
import unicodedata


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
sys.path[:0] = [
    os.path.join(_utilities_directory, 'read_hook_input'),
    os.path.join(_utilities_directory, 'write_hook_output'),
    os.path.join(_utilities_directory, 'truncate_text'),
    os.path.join(_utilities_directory, 'get_sha256_digest'),
    os.path.join(_utilities_directory, 'read_definitions'),
    os.path.join(_utilities_directory, 'get_state_path'),
    os.path.join(_utilities_directory, 'filter_first_time_denials'),
    os.path.join(_utilities_directory, 'get_display_path'),
    os.path.join(_utilities_directory, 'read_stored_file_content_for_write_comparison'),
    os.path.join(_utilities_directory, 'get_stripped_lines'),
]
import read_hook_input as _hook_input
import write_hook_output as _hook_output
from truncate_text import truncate_text as _truncate_text
from get_sha256_digest import get_path_sha256_digest as _get_path_sha256_digest
import read_definitions as _definitions
from get_state_path import get_state_path as _get_state_path
from filter_first_time_denials import filter_first_time_denials as _filter_first_time_denials
from get_display_path import get_display_path as _get_display_path
from read_stored_file_content_for_write_comparison import read_stored_file_content_for_write_comparison as _read_stored_file_content_for_write_comparison
from get_stripped_lines import get_stripped_lines as _get_stripped_lines


_EXCLUDED_FILE_PATTERNS_KEY = 'excludedFilePatterns'
_MOJIBAKE_PATTERNS_KEY = 'mojibakePatterns'
_MAX_SNIPPETS = 3
_MAX_SNIPPET_LENGTH = 80
_FINDING = '{file} would get {kinds} ({snippets})'
_DENY_REASON = (
    'Edit denied: {findings}. This text looks like mojibake: UTF-8 text decoded with the wrong encoding '
    '(for example, output piped through Windows PowerShell 5.1 or a console using the ANSI code page, or a file read '
    'as Shift_JIS or Latin-1), or characters already lost and replaced with U+FFFD. Writing it would corrupt the file. '
    'Re-read the source as UTF-8 (for example, with the view tool, or Get-Content -Encoding utf8) and write the '
    'original characters instead. If the garbled text is intended, for example test data for encoding handling, '
    'generate it in code, such as text.encode("utf-8").decode("cp932") in Python, or write it by its code points. '
    'Retry this edit unchanged only if neither is appropriate; this check denies it only once per file.'
)


def _load_definitions(path):
    label = 'mojibake definitions'
    definitions = _definitions.read_definitions(path, label)
    return (
        _definitions.get_regex_matchers(definitions, _EXCLUDED_FILE_PATTERNS_KEY, label, allow_empty_list=True),
        _definitions.get_named_regex_matchers(definitions, _MOJIBAKE_PATTERNS_KEY, label),
    )


def _find_mojibake(mojibake_patterns, written, existing):
    # 戻り値は (種類の名前のリスト, 該当行のリスト)。既存の内容に同じ行があるものは、元からあるものとみなす。
    existing_lines = _get_stripped_lines(existing)
    kinds = []
    lines = []
    for line in written.splitlines():
        matched = [name for name, pattern in mojibake_patterns if pattern.search(line)]
        trimmed = line.strip()
        if not matched or trimmed in existing_lines:
            continue
        if trimmed not in lines:
            lines.append(trimmed)
        kinds.extend(name for name in matched if name not in kinds)
    return kinds, lines


def _format_snippet(line):
    # 文字化けにはC1制御文字などの見えない文字が含まれるため、`<U+XXXX>` に置き換えて示す。
    snippet = ''.join(
        f'<U+{ord(character):04X}>' if unicodedata.category(character) in ('Cc', 'Cf') and character != '\t'
        else character
        for character in line
    )
    return f'"{_truncate_text(snippet, _MAX_SNIPPET_LENGTH)}"'


def main(arguments=None):
    _hook_output.configure_output_streams()

    # テスト用に定義ファイルを差し替えられるようにする。
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--definitions-path',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mojibake_definitions.json'),
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        excluded_file_patterns, mojibake_patterns = _load_definitions(parsed_arguments.definitions_path)

        hook_input = _hook_input.read_hook_input()
        state_path = _get_state_path('block_mojibake_writes', hook_input.get('sessionId'))
        cwd = _hook_input.get_cwd(hook_input)
        tool_name, tool_args = _hook_input.get_tool_call(hook_input)

        real_cwd = os.path.realpath(cwd)
        candidates = []
        # 編集ツールの引数の解析は、共通utilityで行う。
        sys.path.insert(0, os.path.join(_utilities_directory, 'edit_tool_changes'))
        from edit_tool_changes import extract_edit_tool_changes
        for change in extract_edit_tool_changes(tool_name, tool_args):
            path = os.path.realpath(os.path.join(cwd, change['path']))
            display_path = _get_display_path(path, real_cwd)
            # パターンは、get_display_pathがOSによらず `/` 区切りで返すパスに対して照合する。
            if any(pattern.search(display_path) for pattern in excluded_file_patterns):
                continue
            current = (_read_stored_file_content_for_write_comparison(path) if os.path.isfile(path) else None) or ''
            kinds, lines = _find_mojibake(mojibake_patterns, change['written'], '\n'.join([change['removed'], current]))
            if lines:
                # 状態にはパスを保存せず、照合用のSHA-256だけを保存する。
                file_key = _get_path_sha256_digest(path)
                candidates.append((file_key, _FINDING.format(
                    file=display_path, kinds=', '.join(kinds),
                    snippets=', '.join(_format_snippet(line) for line in lines[:_MAX_SNIPPETS]),
                )))

        reasons = []
        if candidates:
            reasons = _filter_first_time_denials(state_path, candidates)
    except (OSError, UnicodeError, ValueError, ImportError, sqlite3.Error) as ex:
        print(f'Failed to check mojibake: {ex}', file=sys.stderr)
        return 2

    if reasons:
        reason = _DENY_REASON.format(findings='; '.join(reasons))
        print(reason, file=sys.stderr)
        # 正常な拒否なので終了コードは0。
        _hook_output.write_deny_decision(reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
