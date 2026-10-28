import json
import os
import re
import runpy
import sqlite3
import sys


# 成功すればファイルの内容を把握したとみなせる組み込みツール。CLI形式の `functions.` 接頭辞も許容する。
#   view・str_replace_editorのview：内容を読んだ。
#   編集系：置換やパッチの適用には現在の内容との一致が必要で、作成したファイルは内容を自分で書いた。
READ_TOOL_NAMES = {'view', 'edit', 'create', 'insert', 'str_replace_editor', 'apply_patch'}
# パッチの適用後に存在するファイル。削除したファイルと移動元は記録しない。
PATCH_FILE_PATTERN = re.compile(r'^\*\*\* (?:(?:Add|Update) File|Move to): (.+?)\s*$', re.MULTILINE)


def get_read_paths(tool_name, tool_args, state):
    if tool_name == 'apply_patch':
        return [
            match for text in state['collect_strings'](tool_args) for match in PATCH_FILE_PATTERN.findall(text)
        ]
    path = tool_args.get('path') if isinstance(tool_args, dict) else None
    return [path] if isinstance(path, str) and path.strip() else []


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    try:
        # 拒否側のpreToolUse hookと状態の形式・保存先を共有する。
        state = runpy.run_path(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'utilities', 'known-files-state.py',
        ))

        hook_input_json = sys.stdin.buffer.read().decode('utf-8')
        if not hook_input_json.strip():
            raise ValueError('hook input is empty.')
        hook_input = json.loads(hook_input_json)
        if not isinstance(hook_input, dict):
            raise ValueError('hook input must be a JSON object.')

        state_path = state['get_state_path'](hook_input.get('sessionId'))
        for field in ('cwd', 'toolName'):
            value = hook_input.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{field} must be a non-empty string.')
        if not os.path.isabs(hook_input['cwd']):
            raise ValueError('cwd must be an absolute path.')
        if 'toolArgs' not in hook_input:
            raise ValueError('toolArgs is required.')

        tool_name = hook_input['toolName'].removeprefix('functions.')
        if tool_name not in READ_TOOL_NAMES:
            return 0
        tool_args = state['get_tool_args'](hook_input['toolArgs'])
        paths = {
            state['normalize_path'](hook_input['cwd'], path) for path in get_read_paths(tool_name, tool_args, state)
        }
        # ディレクトリの一覧表示や、消えたファイルは記録しない。
        keys = sorted(state['get_file_key'](path) for path in paths if os.path.isfile(path))
        if not keys:
            return 0

        with state['open_state'](state_path) as connection:
            connection.executemany(
                'INSERT INTO files (file, state) VALUES (?, ?) ON CONFLICT(file) DO UPDATE SET state = excluded.state',
                [(key, state['SEEN']) for key in keys],
            )
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as ex:
        print(f'Failed to record seen files: {ex}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
