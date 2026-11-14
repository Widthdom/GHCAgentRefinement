import ast
import io
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
_hook_output = runpy.run_path(os.path.join(_utilities_directory, 'write-hook-output', 'write-hook-output.py'))
_get_display_path = runpy.run_path(os.path.join(_utilities_directory, 'get-display-path', 'get-display-path.py'))['get_display_path']


_PYTHON_EXTENSIONS = ('.py', '.pyi')
_CSHARP_EXTENSIONS = ('.cs',)
# 引数の記述として読む、Pythonのdocstringの形式。
#   Sphinx：`:param name:`・`:param int name:`
#   Google：`Args:`・`Arguments:`・`Parameters:`・`Params:`・`Keyword Args:`・`Keyword Arguments:`・`引数:` の見出しの下に、1段深い字下げで `name (int): ...`
#   NumPy：`Parameters`・`Other Parameters` の見出しと `----------` の下に、同じ字下げで `name : int`
_SPHINX_PARAM_PATTERN = re.compile(r'^\s*:param\s+(?:[^:\n]*\s)?\*{0,2}(\w+)\s*:', re.MULTILINE)
_GOOGLE_HEADER_PATTERN = re.compile(r'^(\s*)(?:Args|Arguments|Parameters|Params|Keyword Args|Keyword Arguments|引数)\s*[:：]\s*$')
_GOOGLE_ENTRY_PATTERN = re.compile(r'^\s*\*{0,2}(\w+)\s*(?:\([^)]*\))?\s*[:：]')
_NUMPY_HEADER_PATTERN = re.compile(r'^(\s*)(?:Parameters|Other Parameters)\s*$')
_NUMPY_UNDERLINE_PATTERN = re.compile(r'^\s*-{3,}\s*$')
_NUMPY_ENTRY_PATTERN = re.compile(r'^\s*(\*{0,2}\w+(?:\s*,\s*\*{0,2}\w+)*)\s*(?::|$)')
# Pythonのメソッドの最初の引数。呼び出し側が渡さないため、記述がなくてもよい。
_IMPLICIT_PYTHON_PARAMETERS = {'self', 'cls'}
_CSHARP_DOC_PATTERN = re.compile(r'^\s*///')
_CSHARP_PARAM_PATTERN = re.compile(r'<param\s+name\s*=\s*"([^"]+)"')
_CSHARP_ATTRIBUTE_PATTERN = re.compile(r'^\s*\[.*\]\s*$')
_CSHARP_MODIFIERS = {'this', 'ref', 'out', 'in', 'params', 'scoped', 'readonly'}
_MAX_DECLARATION_LINES = 30
_MAX_REPORTS = 10
_STALE_CONTEXT = (
    'The parameter documentation no longer matches the signature of {count} you just edited:\n{reports}\n'
    'Update the docstring or XML documentation comments so that they describe the current parameters, '
    'or change the signature if the documentation is what you intended.'
)


def _get_written_lines(changes):
    # 書き込んだ内容の行（前後の空白を除く）。英数字を2文字以上含まない行（`"""`・`)` など）は手掛かりにならないため除く。
    return {
        line.strip()
        for change in changes
        for line in (change['full_content'] or change['written'] or '').splitlines()
        if re.search(r'\w{2}', line)
    }


def _is_touched(lines, start, end, written_lines, line_counts):
    # 定義の範囲（0始まり、endを含まない）に、書き込んだ行のうちファイル内で1回だけ現れるものがあるか。
    # `Args:` のようにどの定義にも現れる行は、どの定義を変えたかの手掛かりにならないため使わない。
    return any(
        stripped in written_lines and line_counts.get(stripped) == 1
        for stripped in (line.strip() for line in lines[start:end])
    )


def _get_python_documented_parameters(docstring):
    # docstringに書かれた引数名を返す。引数の記述がないか、見出しの下の項目を読めない形式（`name -- 説明` など）ならNone。
    documented = set(_SPHINX_PARAM_PATTERN.findall(docstring))
    lines = docstring.splitlines()
    index = 0
    while index < len(lines):
        google = _GOOGLE_HEADER_PATTERN.match(lines[index])
        numpy = _NUMPY_HEADER_PATTERN.match(lines[index])
        if google:
            # 1行目の見出し（`"""Args:`）は引用符の後にあり字下げを持たないため、続く行をすべて項目の候補とする。
            header_indent = len(google.group(1)) if index > 0 else -1
            entry_indent = None
            index += 1
            while index < len(lines):
                line = lines[index]
                if line.strip():
                    indent = len(line) - len(line.lstrip())
                    if indent <= header_indent:
                        break
                    if entry_indent is None:
                        entry_indent = indent
                    if indent == entry_indent:
                        entry = _GOOGLE_ENTRY_PATTERN.match(line)
                        if entry:
                            documented.add(entry.group(1))
                index += 1
            continue
        if numpy and index + 1 < len(lines) and _NUMPY_UNDERLINE_PATTERN.match(lines[index + 1]):
            # 1行目の見出しは字下げを持たないため、見出しと同じ字下げで書く下線の字下げを使う。
            underline = lines[index + 1]
            header_indent = len(underline) - len(underline.lstrip())
            index += 2
            while index < len(lines):
                line = lines[index]
                # 次の見出し（下線付きの行）で終える。
                if index + 1 < len(lines) and line.strip() and _NUMPY_UNDERLINE_PATTERN.match(lines[index + 1]):
                    break
                if line.strip() and len(line) - len(line.lstrip()) == header_indent:
                    entry = _NUMPY_ENTRY_PATTERN.match(line)
                    if entry:
                        documented.update(name.strip().lstrip('*') for name in entry.group(1).split(','))
                index += 1
            continue
        index += 1
    return documented or None


def _find_python_mismatches(source, written_lines):
    # 戻り値は (行番号, 関数名, 記述だけにある引数, 記述にない引数) のリスト。
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    lines = source.splitlines()
    line_counts = {}
    for line in lines:
        line_counts[line.strip()] = line_counts.get(line.strip(), 0) + 1
    mismatches = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # 字下げを整えると、1行目に書いた見出し（`"""Args:`）と項目の字下げが揃い、項目を読めなくなるため整えない。
        docstring = ast.get_docstring(node, clean=False)
        if not docstring or not node.body:
            continue
        documented = _get_python_documented_parameters(docstring)
        if documented is None:
            continue
        doc_node = node.body[0]
        if not _is_touched(lines, node.lineno - 1, doc_node.end_lineno or doc_node.lineno, written_lines, line_counts):
            continue
        arguments = node.args
        named = [
            argument.arg for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
            if argument.arg not in _IMPLICIT_PYTHON_PARAMETERS
        ]
        variadic = [argument.arg for argument in (arguments.vararg, arguments.kwarg) if argument is not None]
        # `**kwargs` を持つ関数では、記述だけにある名前もキーワード引数として受け取りうるため、古い記述とみなさない。
        stale = [] if arguments.kwarg else sorted(documented - {*named, *variadic, *_IMPLICIT_PYTHON_PARAMETERS})
        # `*args`・`**kwargs` は記述を省くことが多いため、記述がなくても対象外とする。
        missing = [name for name in named if name not in documented]
        if stale or missing:
            mismatches.append((node.lineno, node.name, stale, missing))
    return sorted(mismatches)


def _split_top_level(text):
    # `,` で区切る。`<>`・`()`・`[]`・`{}` と文字列の中の `,` では区切らない。
    parts = []
    depth = 0
    quote = None
    current = []
    for character in text:
        if quote:
            current.append(character)
            if character == quote:
                quote = None
            continue
        if character in '"\'':
            quote = character
        elif character in '<([{':
            depth += 1
        elif character in '>)]}':
            depth -= 1
        elif character == ',' and depth == 0:
            parts.append(''.join(current))
            current = []
            continue
        current.append(character)
    parts.append(''.join(current))
    return [part.strip() for part in parts if part.strip()]


def _get_csharp_parameter_name(parameter):
    # 属性・既定値・修飾子・型を除いた引数名を返す。読めなければNone。
    parameter = re.sub(r'^\s*(?:\[[^\]]*\]\s*)+', '', parameter)
    parameter = _split_top_level(parameter.replace('=', ',', 1))[0] if '=' in parameter else parameter
    tokens = [token for token in parameter.split() if token not in _CSHARP_MODIFIERS]
    if len(tokens) < 2:
        return None
    name = tokens[-1].lstrip('@')
    return name if re.fullmatch(r'[A-Za-z_]\w*', name) else None


def _get_csharp_parameters(lines, start):
    # 宣言の引数名のリストと、宣言の終わりの行（0始まり、含まない）を返す。引数の括弧がない宣言ではNone。
    text = ''
    for end in range(start, min(start + _MAX_DECLARATION_LINES, len(lines))):
        text += lines[end] + '\n'
        open_index = text.find('(')
        terminator = re.search(r'[{;]|=>', text)
        if open_index < 0:
            if terminator:
                return None
            continue
        if terminator and terminator.start() < open_index:
            return None
        depth = 0
        for index in range(open_index, len(text)):
            if text[index] == '(':
                depth += 1
            elif text[index] == ')':
                depth -= 1
                if depth == 0:
                    names = [_get_csharp_parameter_name(part) for part in _split_top_level(text[open_index + 1:index])]
                    if any(name is None for name in names):
                        return None
                    return names, end + 1
    return None


def _find_csharp_mismatches(source, written_lines):
    # 戻り値は (行番号, 宣言の先頭, 記述だけにある引数, 記述にない引数) のリスト。
    lines = source.splitlines()
    line_counts = {}
    for line in lines:
        line_counts[line.strip()] = line_counts.get(line.strip(), 0) + 1
    mismatches = []
    index = 0
    while index < len(lines):
        if not _CSHARP_DOC_PATTERN.match(lines[index]):
            index += 1
            continue
        doc_start = index
        while index < len(lines) and _CSHARP_DOC_PATTERN.match(lines[index]):
            index += 1
        documented = _CSHARP_PARAM_PATTERN.findall('\n'.join(lines[doc_start:index]))
        declaration = index
        while declaration < len(lines) and (not lines[declaration].strip() or _CSHARP_ATTRIBUTE_PATTERN.match(lines[declaration])):
            declaration += 1
        if not documented or declaration >= len(lines):
            continue
        parsed = _get_csharp_parameters(lines, declaration)
        if parsed is None:
            continue
        actual, end = parsed
        if not _is_touched(lines, doc_start, end, written_lines, line_counts):
            continue
        stale = [name for name in dict.fromkeys(documented) if name not in actual]
        missing = [name for name in actual if name not in documented]
        if stale or missing:
            name = re.search(r'([A-Za-z_]\w*)\s*(?:<[^()]*>)?\s*\(', lines[declaration])
            mismatches.append((declaration + 1, name.group(1) if name else lines[declaration].strip(), stale, missing))
    return mismatches


def _format_report(display_path, line_number, name, stale, missing):
    details = []
    if stale:
        details.append('documented but not in the signature: ' + ', '.join(f'`{item}`' for item in stale))
    if missing:
        details.append('in the signature but not documented: ' + ', '.join(f'`{item}`' for item in missing))
    return f'- {display_path}:{line_number} `{name}`: ' + '; '.join(details)


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()
        cwd = _hook_input['get_cwd'](hook_input)
        tool_name, tool_args = _hook_input['get_tool_call'](hook_input)
        extract_edit_tool_changes = runpy.run_path(
            os.path.join(_utilities_directory, 'edit-tool-changes', 'edit-tool-changes.py')
        )['extract_edit_tool_changes']
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to detect stale parameter docs: {ex}', file=sys.stderr)
        return 2
    cwd = os.path.normpath(cwd)

    # 編集後のファイルを読むため、同じファイルへの変更をまとめる。
    changes_by_path = {}
    for change in extract_edit_tool_changes(tool_name, tool_args):
        path = os.path.normpath(os.path.join(cwd, change['path']))
        changes_by_path.setdefault(path, []).append(change)

    reports = []
    for path, changes in changes_by_path.items():
        lower_path = path.lower()
        if lower_path.endswith(_PYTHON_EXTENSIONS):
            find_mismatches = _find_python_mismatches
        elif lower_path.endswith(_CSHARP_EXTENSIONS):
            find_mismatches = _find_csharp_mismatches
        else:
            continue
        try:
            with open(path, encoding='utf-8-sig') as file:
                source = file.read()
        except (OSError, UnicodeError):
            # 削除したファイルや読めないファイルは確かめられないため対象外とする。
            continue
        display_path = _get_display_path(path, cwd)
        reports.extend(
            _format_report(display_path, *mismatch) for mismatch in find_mismatches(source, _get_written_lines(changes))
        )

    if not reports:
        return 0
    shown = reports[:_MAX_REPORTS]
    if len(reports) > _MAX_REPORTS:
        shown.append(f'- ... and {len(reports) - _MAX_REPORTS} more')
    count = 'a function' if len(reports) == 1 else f'{len(reports)} functions'
    # SDK形式のトップレベルadditionalContext。正常な介入なので終了コードは0。
    _hook_output['write_additional_context'](_STALE_CONTEXT.format(count=count, reports='\n'.join(shown)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
