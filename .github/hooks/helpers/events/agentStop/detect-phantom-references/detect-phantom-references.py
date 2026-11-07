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
_git = runpy.run_path(os.path.join(_utilities_directory, 'run-git', 'run-git.py'))


_FENCED_CODE_PATTERN = re.compile(r'^(```|~~~).*?^\1', re.MULTILINE | re.DOTALL)
_INLINE_CODE_PATTERN = re.compile(r'`([^`\n]+)`')
_MARKDOWN_LINK_PATTERN = re.compile(r'\]\(([^()\s]+)\)')
# コードスパン外のパス。日本語の文中に続けて書かれても切り出せるよう、ASCIIの文字だけで構成する。
_BARE_PATH_PATTERN = re.compile(
    r'(?<![A-Za-z0-9_./\\~-])((?:\.{1,2}/|/|~/)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+/?(?::\d+(?:-\d+)?)?)'
)
# 末尾の `:12`・`:12-34`・`#L12`・`#L12-L34` を行番号として扱う。
_LINE_SUFFIX_PATTERN = re.compile(r'(?::(\d+)(?:-(\d+))?|#L(\d+)(?:-L?(\d+))?)$')
# プレースホルダ・glob・コマンドなど、実在するファイルを指さない表記に含まれる文字。
_NON_PATH_CHARACTERS = re.compile(r'[\s<>{}*?\[\]|$%=@,;"\'()!^&+]')
_DOMAIN_PATTERN = re.compile(r'^[a-z0-9-]+(?:\.[a-z0-9-]+)+$', re.IGNORECASE)
_EXTENSION_PATTERN = re.compile(r'\.[A-Za-z0-9]+$')
# パスの直後に付ける未作成の目印。閉じたコードスパンやMarkdownリンクの直後と、間の空白も許容する。
# 正式な目印は共通指示（copilot-instructions.md）で定める `(planned)` で、表記揺れも受け付ける。
_PLANNED_MARKER_PATTERN = r'`?\)?\s*\(\s*(?:planned|new|not yet created)\s*\)'
_MAX_REPORTED_REFERENCES = 10
_GIT_TIMEOUT_SECONDS = 5
_REASON_PREFIX = 'The final response references paths'


def _get_candidates(response):
    # (候補, 地の文から抽出したか) のリストを返す。コードブロック内の出力例やコマンドは参照とみなさない。
    text = _FENCED_CODE_PATTERN.sub('', response)
    candidates = [(candidate, False) for candidate in _INLINE_CODE_PATTERN.findall(text) + _MARKDOWN_LINK_PATTERN.findall(text)]
    # コードスパンを除いた地の文から、区切り文字を含むパスを集める。文末のピリオドはパスに含めない。
    candidates += [
        (re.sub(r'(?<!/)\.+$', '', match), True)
        for match in _BARE_PATH_PATTERN.findall(_INLINE_CODE_PATTERN.sub(' ', text))
    ]
    # Markdownリンクの参照先は地の文からも抽出されるため、同じ表記は最初の抽出結果だけを使う。
    unique = {}
    for candidate, is_bare in candidates:
        unique.setdefault(candidate.strip(), is_bare)
    return list(unique.items())


def _parse_reference(candidate, is_bare=False):
    # パスとして検証する参照なら (パス, 行番号) を返し、対象外ならNoneを返す。行番号は範囲の終端を使う。
    # 地の文のパスは空白で途切れたり一般的な語の組み合わせだったりするため、拡張子付きのファイルに限る。
    if '://' in candidate or candidate.startswith(('#', 'mailto:')) or _NON_PATH_CHARACTERS.search(candidate):
        return None
    line = None
    match = _LINE_SUFFIX_PATTERN.search(candidate)
    if match:
        numbers = [int(number) for number in match.groups() if number]
        line = max(numbers)
        candidate = candidate[:match.start()]
    # バックスラッシュ区切りはWindowsでだけパスとして扱う。他のOSではエスケープなどと区別できないため。
    if '\\' in candidate and os.sep != '\\':
        return None
    segments = [segment for segment in re.split(r'[/\\]', candidate) if segment]
    # `/update-hook` のようなスラッシュコマンドも除くため、区切りで分けた要素が2つ以上のものに限る。
    if len(segments) < 2:
        return None
    # `github.com/owner/repo` のようなスキームなしのURLは対象外。
    if _DOMAIN_PATTERN.match(segments[0]) and not segments[0].startswith('.'):
        return None
    has_extension = bool(_EXTENSION_PATTERN.search(segments[-1]))
    if is_bare and not has_extension:
        return None
    explicit = candidate.startswith(('./', '../', '~/', '.\\', '..\\')) or os.path.isabs(candidate)
    # 明示的なパス表記でなければ、拡張子付きのファイルか末尾区切りのディレクトリに限る。`origin/main` などを除くため。
    if not explicit and not has_extension and not candidate.endswith(('/', '\\')):
        return None
    return candidate, line


def _count_lines(path):
    with open(path, 'rb') as file:
        content = file.read()
    return content.count(b'\n') + (1 if content and not content.endswith(b'\n') else 0)


def _add_path_suffixes(index, path):
    # `a/b/c.py` から `c.py`・`b/c.py`・`a/b/c.py` と、親ディレクトリの同様の末尾部分を登録する。
    segments = path.split('/')
    for end in range(1, len(segments) + 1):
        for start in range(end):
            index.setdefault('/'.join(segments[start:end]), set()).add('/'.join(segments[:end]))


def _get_known_paths(repository_root):
    # リポジトリルート相対の末尾部分から、それを末尾に持つ現存パスの集合と、過去に存在したかを引ける索引を返す。
    # 応答では `create-hook/SKILL.md` のように途中を省いたパスや、削除・リネーム前の旧パスも使われるため。
    current = {}
    historical = {}
    if repository_root is None:
        return current, historical
    # gitの出力を得られない場合は、履歴で判断できないだけなので異常終了にはしない。
    listed = _git['get_git_output'](
        repository_root, ['ls-files', '--cached', '--others', '--exclude-standard', '-z'], _GIT_TIMEOUT_SECONDS,
    )
    for path in (listed or '').split('\0'):
        if path:
            _add_path_suffixes(current, path)
    logged = _git['get_git_output'](repository_root, ['log', '--all', '--name-only', '--format=', '-z'], _GIT_TIMEOUT_SECONDS)
    for path in re.split(r'[\0\n]', logged or ''):
        if path:
            _add_path_suffixes(historical, path)
    return current, historical


def _resolve_reference(path, cwd, repository_root, get_index):
    # (参照先の絶対パス, 見つからないか) を返す。
    #   (full_path, False)：実在する。full_pathがNoneなら、実在するが1つのファイルに定まらないか旧パス。
    #   (None, True)：見つからない。
    expanded = os.path.expanduser(path)
    # 応答ではリポジトリルート基準のパスも使うため、cwdとリポジトリルートの両方を基準に解決する。
    for base in dict.fromkeys(filter(None, (cwd, repository_root))):
        full_path = os.path.join(base, expanded)
        if os.path.exists(full_path):
            return full_path, False
    if repository_root is None:
        return None, True
    if os.path.isabs(expanded) or path.startswith(('./', '../', '.\\', '..\\')):
        # 明示的なパスはリポジトリ内に限り、削除・リネーム前の旧パスかを確かめる。
        relative_path = os.path.relpath(os.path.join(cwd, expanded), repository_root)
        if relative_path.startswith('..'):
            return None, True
        key = relative_path.replace(os.sep, '/')
        _, historical = get_index()
        return None, key not in historical
    # 応答では `create-hook/SKILL.md` のように途中を省いたパスも使うため、末尾一致で照合する。
    key = path.replace('\\', '/').strip('/')
    current, historical = get_index()
    matches = current.get(key, set())
    if not matches:
        # 削除やリネームを報告した旧パスを誤検知しないよう、gitの履歴に存在したパスは対象外とする。
        return None, key not in historical
    # 省略されたパスが1つのファイルに定まる場合だけ、行番号を検証できるようにする。
    if len(matches) == 1:
        return os.path.join(repository_root, *next(iter(matches)).split('/')), False
    return None, False


def _is_marked_as_planned(response, candidate):
    # 提案や計画で示す未作成のパスは、実在しないことが正しいため検証しない。
    # 同じパスが複数回現れる場合は、いずれかに目印があれば未作成とみなす。
    return re.search(re.escape(candidate) + _PLANNED_MARKER_PATTERN, response, re.IGNORECASE) is not None


def _get_phantom_references(response, user_request, cwd):
    repository_root = _git['get_repository_root'](cwd, _GIT_TIMEOUT_SECONDS)
    known_paths = []

    def get_index():
        # gitの索引は必要になったときに1回だけ作る。
        if not known_paths:
            known_paths.append(_get_known_paths(repository_root))
        return known_paths[0]

    phantoms = []
    for candidate, is_bare in _get_candidates(response):
        # 依頼文にあるパスは、存在しないことの指摘など、ユーザーの表記をそのまま引用している場合がある。
        if candidate in user_request or _is_marked_as_planned(response, candidate):
            continue
        reference = _parse_reference(candidate, is_bare)
        if reference is None:
            continue
        path, line = reference
        full_path, is_missing = _resolve_reference(path, cwd, repository_root, get_index)
        if is_missing:
            phantoms.append(f'{candidate} (not found)')
        elif full_path is not None and line is not None and os.path.isfile(full_path):
            line_count = _count_lines(full_path)
            if line > line_count:
                phantoms.append(f'{candidate} (the file has only {line_count} lines)')
        if len(phantoms) >= _MAX_REPORTED_REFERENCES:
            break
    return phantoms


def main():
    # 型定義上の `sys.stdout` はTextIOでreconfigureを持たないため、実体のTextIOWrapperとして扱う。
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding='utf-8', errors='replace', newline='\n')
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding='utf-8', errors='replace', newline='\n')

    try:
        hook_input = _hook_input['read_hook_input']()
        # サブエージェントの停止は対象外。最終応答として報告されるのは親の応答のため。
        if hook_input.get('agentId'):
            return 0
        transcript_path = _hook_input['get_transcript_path'](hook_input)
        cwd = _hook_input['get_cwd'](hook_input)
        if not os.path.isdir(cwd):
            raise ValueError('cwd must be an existing directory.')

        # 自身のhook.startが書き込まれるまで最大5秒待ち、最終応答を書き込み済みにする。
        runpy.run_path(
            os.path.join(_utilities_directory, 'wait-for-agent-stop-event', 'wait-for-agent-stop-event.py')
        )['wait_for_agent_stop_event'](transcript_path, hook_input.get('timestamp'))

        # ユーザーの最新の依頼文、それ以降の最終応答、本hookによる差し戻し済みかを集める。
        events, already_requested = _turn_events['get_latest_turn'](transcript_path, _REASON_PREFIX)
        last_assistant_msg = _turn_events['get_last_assistant_message'](events)
        # 同じ依頼で差し戻すのは1回だけにし、参照を直せない場合に停止できなくなるのを防ぐ。
        if last_assistant_msg is None or already_requested:
            return 0
        phantoms = _get_phantom_references(last_assistant_msg, _turn_events['get_request'](events) or '', cwd)
    except (OSError, UnicodeError, ValueError) as ex:
        print(f'Failed to inspect path references: {ex}', file=sys.stderr)
        return 2

    if phantoms:
        reason = (
            f'{_REASON_PREFIX} that do not exist relative to {cwd}:\n'
            + '\n'.join(f'- {phantom}' for phantom in phantoms)
            + '\nCheck each path and line number with a tool, then correct or remove the wrong references. '
            'If a path intentionally refers to a file that does not exist yet, such as a proposal or plan, '
            'append the marker (planned) right after the path. Keep the rest of the response unchanged.'
        )
        print(json.dumps({'decision': 'block', 'reason': reason}, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
