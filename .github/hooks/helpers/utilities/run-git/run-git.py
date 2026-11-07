import subprocess

# gitを呼び出す。gitの状態や履歴を検査するhookが共有する。
# gitがない、タイムアウトした場合も、検査できないだけなので例外にせずNoneを返し、呼び出し元が扱いを決める。


def run_git(cwd, arguments, timeout):
    # 戻り値は標準出力をバイト列で受け取ったCompletedProcess。終了コードの判定と出力の復号は呼び出し元で行う。
    # 標準入力は閉じ、認証の入力待ちなどで止まらないようにする。標準エラーは捨てる。
    try:
        return subprocess.run(
            ['git', *arguments], cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def get_git_output(cwd, arguments, timeout):
    # 成功した（終了コード0の）場合の標準出力を、UTF-8で復号して返す。それ以外はNone。
    result = run_git(cwd, arguments, timeout)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.decode('utf-8', errors='replace')


def get_repository_root(cwd, timeout):
    # cwdを含むリポジトリのルートを返す。gitがない、リポジトリ外、またはタイムアウトした場合はNone。
    return (get_git_output(cwd, ['rev-parse', '--show-toplevel'], timeout) or '').strip() or None
