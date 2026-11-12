import subprocess
import time

# gitを呼び出す。gitの状態や履歴を検査するhookが共有する。
# gitがない、タイムアウトした場合も、検査できないだけなので例外にせずNoneを返し、呼び出し元が扱いを決める。
# gitを複数回呼ぶhookは、create_deadlineで作った期限を各呼び出しに渡し、合計の時間をhookの `timeoutSec` 未満に抑える。


def create_deadline(seconds):
    # 今からseconds秒後の期限（time.monotonic()の値）を返す。
    return time.monotonic() + seconds


def run_git(cwd, arguments, timeout, deadline=None):
    # 戻り値は標準出力をバイト列で受け取ったCompletedProcess。終了コードの判定と出力の復号は呼び出し元で行う。
    # deadlineを指定した場合は、1回の上限（timeout）と期限までの残り時間の短い方で打ち切り、期限を過ぎていれば実行しない。
    # 標準入力は閉じ、認証の入力待ちなどで止まらないようにする。標準エラーは捨てる。
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            return None
    try:
        return subprocess.run(
            ['git', *arguments], cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def get_git_output(cwd, arguments, timeout, deadline=None):
    # 成功した（終了コード0の）場合の標準出力を、UTF-8で復号して返す。それ以外はNone。
    result = run_git(cwd, arguments, timeout, deadline)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.decode('utf-8', errors='replace')


def get_repository_root(cwd, timeout, deadline=None):
    # cwdを含むリポジトリのルートを返す。gitがない、リポジトリ外、またはタイムアウトした場合はNone。
    return (get_git_output(cwd, ['rev-parse', '--show-toplevel'], timeout, deadline) or '').strip() or None
