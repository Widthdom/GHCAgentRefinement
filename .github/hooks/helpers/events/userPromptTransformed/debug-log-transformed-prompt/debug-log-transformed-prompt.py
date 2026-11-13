import difflib
import io
import json
import os
import re
import runpy
import sys
import tempfile
from typing import cast


# 複数のhookで共有する処理は、utilitiesのスクリプトから読み込む。
# hookのフォルダー → イベントのフォルダー → events → helpers の直下にutilitiesがある。
_utilities_directory = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'utilities',
)
_hook_input = runpy.run_path(os.path.join(_utilities_directory, 'read-hook-input', 'read-hook-input.py'))
_parse_session_id = runpy.run_path(os.path.join(_utilities_directory, 'get-state-path', 'get-state-path.py'))['parse_session_id']
_get_debug_log_directory = runpy.run_path(os.path.join(
    _utilities_directory, 'get-debug-log-directory', 'get-debug-log-directory.py',
))['get_debug_log_directory']


def _code_block(content, language):
    # プロンプト中の```などでログのコードブロックが閉じない長さにする。
    fence_length = max([3] + [len(match[0]) + 1 for match in re.finditer(r'`+', content)])
    fence = '`' * fence_length
    ending = '' if content.endswith('\n') else '\n'
    return f'{fence}{language}\n{content}{ending}{fence}\n'


def _format_log(hook_input, session_id):
    prompt = hook_input['prompt']
    transformed_prompt = hook_input['transformedPrompt']
    difference = []
    # keepends=Trueで改行の有無も比較する。改行のない行には明示的な印を付け、
    # 削除行と追加行が同じ行につながるのを防ぐ。
    for line in difflib.unified_diff(
        prompt.splitlines(keepends=True), transformed_prompt.splitlines(keepends=True),
        fromfile='prompt', tofile='transformedPrompt', lineterm='\n',
    ):
        difference.append(line)
        if not line.endswith(('\n', '\r')):
            difference.append('\n\\ No newline at end of file\n')

    metadata = json.dumps(
        {'sessionId': str(session_id), 'timestamp': hook_input['timestamp'], 'cwd': hook_input['cwd']},
        ensure_ascii=False, indent=2,
    )
    diff_section = _code_block(''.join(difference), 'diff') if difference else 'No differences.\n'
    return (
        '# Prompt transformation\n\n'
        '## Difference (`prompt` → `transformedPrompt`)\n\n' + diff_section + '\n'
        '## User prompt (`prompt`)\n\n' + _code_block(prompt, 'text') + '\n'
        '## Model-facing prompt (`transformedPrompt`)\n\n'
        + _code_block(transformed_prompt, 'text') + '\n'
        '## Event\n\n' + _code_block(metadata, 'json')
    )


def _save_log(content, session_id, timestamp):
    log_directory = _get_debug_log_directory('prompt-transformations', str(session_id))
    os.makedirs(log_directory, exist_ok=True)
    temporary_path = None
    try:
        # 同じtimestampのイベントも上書きしないよう一意なファイル名にする。
        # Windowsでも置換できるよう、一度閉じてから完成した.mdとして公開する。
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', newline='\n', dir=log_directory,
            prefix=f'{timestamp}-', suffix='.tmp', delete=False,
        ) as file:
            temporary_path = file.name
            file.write(content)
        log_path = os.path.splitext(temporary_path)[0] + '.md'
        os.replace(temporary_path, log_path)
        temporary_path = None
        return log_path
    finally:
        if temporary_path is not None:
            os.unlink(temporary_path)


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()

        parsed_session_id = _parse_session_id(hook_input.get('sessionId'))

        if hook_input.get('agentId'):
            return 0

        get_debug_mode = runpy.run_path(
            os.path.join(_utilities_directory, 'get-debug-mode', 'get-debug-mode.py'),
        )['get_debug_mode']
        if not get_debug_mode(parsed_session_id):
            return 0

        # モデルへ渡る文字列を観察するため、stripなどで本文を変えない。
        for field in ('prompt', 'transformedPrompt'):
            if not isinstance(hook_input.get(field), str):
                raise ValueError(f'{field} must be a string.')
        # cwdはログに記録するため、形式だけを確かめる。
        _hook_input['get_cwd'](hook_input)
        timestamp = hook_input.get('timestamp')
        if type(timestamp) is not int or timestamp < 0:
            raise ValueError('timestamp must be a non-negative Unix millisecond integer.')

        _save_log(_format_log(hook_input, parsed_session_id), parsed_session_id, timestamp)
    except (OSError, UnicodeError, ValueError, SyntaxError, KeyError) as ex:
        print(f'Failed to log transformed prompt: {ex}', file=sys.stderr)
        return 2

    # {}は「プロンプトの変更なし」を表す。保存先の表示はagentStopで行う。
    # 仕様：https://docs.github.com/en/copilot/reference/hooks-reference
    print('{}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
