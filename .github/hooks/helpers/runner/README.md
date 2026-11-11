# hook runner README

## 役割
hook定義JSONから各hookの実行スクリプトを起動し、hookが実際に起動したかどうかと、機能したかどうかを記録する。

Copilotのトランスクリプトには、`hook.start` と `hook.end` がイベント種別（hookType）ごとに1組しか記録されない。どのhookが起動したか、どのhookが出力したかは区別されないため、本ランナーで記録する。

## 起動方法
JSONの `bash` には `python3 .github/hooks/helpers/runner/run-hook.py <実行スクリプト>`、`powershell` には `python .github/hooks/helpers/runner/run-hook.py <実行スクリプト>` を記述する。実行スクリプトは、リポジトリルートからの相対パスで `.github/hooks/helpers/events/<イベント名>/<hook名>/<hook名>.py` を指定する。このリポジトリ専用のhookは `.github/hooks/helpers/-repo-events/<イベント名>/<hook名>/<hook名>.py` を指定する。

## 処理詳細
1. 引数の実行スクリプトが `helpers/events/<イベント名>/<hook名>/<hook名>.py` または `helpers/-repo-events/<イベント名>/<hook名>/<hook名>.py` の形で存在するか確認する。違う場合は原因を標準エラーに出力し、終了コード2で終了する。
2. 実行スクリプトを、子プロセスにせずランナーのプロセス内で `runpy.run_path(..., run_name='__main__')` により実行する（[実行方式](#実行方式)を参照）。標準入力はバイト列のまま渡し、標準出力・標準エラーは差し替えたバッファで受け取る。
3. 受け取った標準出力・標準エラーをバイト列のまま中継し、hookの終了コードをそのまま返す。
4. 入力の `sessionId` がUUIDとして読める場合、起動時刻・イベント名・hook名・機能したかどうかを `<一時フォルダー>/ghc-agent-refinement/hook-states/hook-activity/<セッションID>.sqlite3` に記録する。記録は [hook-activity-state.py](../utilities/hook-activity-state/hook-activity-state.py) で読み書きする。

## 実行方式
### プロセス内で実行する理由
Copilotは `timeoutSec` を超えたhookのプロセスを終了させる。hookを子プロセスとして起動すると、終了させられるのはランナーだけで、子のhookは取り残されて最後まで動くことがある。このとき、Copilotはタイムアウトとして扱って出力を捨てるのに、hookの副作用（状態ファイルへの書き込みなど）だけが後から残る。たとえば `block-*` 系のhookは「同じファイルの拒否は1回だけ」という状態を記録するため、拒否がエージェントに届かないまま記録だけが残り、次の同じ編集が素通りする。

プロセス内で実行すれば、ランナーの終了とともにhookも止まり、ランナー導入前と同じタイムアウトの振る舞いになる。子プロセスを起動しないので、hookごとのPythonの起動も1回で済む。

### 直接起動との違いを埋める処理
直接 `python <実行スクリプト>` で起動した場合と同じに振る舞うよう、次のように再現する。
- `sys.stdin`・`sys.stdout`・`sys.stderr` を、元のストリームと同じエンコーディング・エラー処理・改行コード変換（Windowsでは変換し、それ以外では変換しない）のストリームに差し替える。hookが `reconfigure` してもよい。この設定は、hookが `reconfigure` する前の出力（読み込み時のエラーメッセージなど）に使われる。
- `sys.argv` を `[<実行スクリプト>]`、`sys.path[0]` を実行スクリプトのディレクトリにする。
- `sys.exit` の引数は、インタープリターと同じ規則で終了コードにする（`None` は0、整数はその値、それ以外は標準エラーに出力して1）。
- 捕捉されなかった例外は、トレースバックを標準エラーに出力して終了コード1にする。
- 実行後は差し替えたものを元に戻す。

### 採用しなかった案
|案|採用しなかった理由|
|--|--|
|子プロセスとして起動し、ランナー側で `subprocess.run(timeout=...)` により `timeoutSec` より先に止める|JSONの `timeoutSec` と同じ値をランナーの引数にも書く二重管理になる。`timeoutSec` を省略したhookでは、Copilotの既定のタイムアウトも知る必要がある。|
|子プロセスとして起動し、OSの機能で親の終了時に子も止める（WindowsのJob Object、LinuxのPR_SET_PDEATHSIG）|macOSに同等の仕組みがなく、OS固有の処理（ctypesなど）が増える。[共通規約](../../../skills/create-hook/references/hook-conventions.md)の「Windows、macOS、Linuxのすべてを対象にする」に合わない。|

## 機能したとみなす条件
- 終了コードが0以外。
- 標準出力が空・空白だけ・空のJSONオブジェクト（`{}`）のいずれでもない。

## 例外
- `sessionId` を読めない入力（UUIDとして解析できない値を含む）のときは、記録だけを行わない。入力の検証は起動したhook自身が行う。
- [check-python](../events/sessionStart/check-python/README.md) と [block-commands](../events/preToolUse/block-commands/README.md) は本ランナーを通さない。Pythonが無い環境や古い環境でも、Pythonの不足を報告したりコマンドを拒否したりする必要があるため。この2つは起動も機能も記録されない。

## 制約と限界
### プロセス内で実行する場合の制約
- hookが `os._exit` を呼ぶと、出力の中継と記録を行わずにランナーごと終了する。
- hookが起動した子プロセスが、出力をパイプで受け取らずに標準出力へ直接書くと、記録の判定から漏れる。今のhookのgit呼び出しは、どれも出力をパイプで受け取るか捨てている。
- タイムアウトで終了させられたhookは記録されない。

### 未確認の事項
Copilotはhookのコマンドを `bash`/`powershell` 経由で起動する。タイムアウト時にCopilotが終了させるのがシェルだけの場合、その先の `python` はランナーの方式によらず取り残される可能性がある。実際に `timeoutSec` を超えるhookでの確認は未実施。

## 備考
- 記録に失敗しても、hookの出力と終了コードは変えない。失敗の原因は標準エラーに出力する。
- 記録を読むのは [debug-add-response-footer](../events/agentStop/debug-add-response-footer/README.md) のみ。
