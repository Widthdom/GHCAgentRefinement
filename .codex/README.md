# .codex README

Codexで共通hookを実行するための設定を置く。共通指示は [AGENTS.md](../AGENTS.md)、skillは [.agents/skills](../.agents/skills) から読み込む。

## 前提条件
- ローカルのCodexで、プロジェクトの `.codex/hooks.json`、`commandWindows`、下表のイベントをサポートする版を使う。調査環境のCLIは `0.169.0`。公式ガイドは2026-11-15に確認した。古い版やVS Codeの別のSession Targetで同じ動作をするとは限らない。
- GitとPython 3.14以降をPATHから実行できること。macOS/Linuxは `python3`、Windowsは `python` とWindows PowerShellを使う。Pythonを新規導入する場合は3.14系の最新パッチ版を使う。
- プロジェクトとhookをCodexで信頼済みにする（[hookの信頼](#hookの信頼)）。
- hook機能は公式の既定では有効。ユーザーや管理者の設定で無効になっている場合は実行されない。

## clone後の初回セットアップ
Codex CLIの `codex`、Git、Python 3.14以降をPATHから実行できる状態で、[hooks.json](./hooks.json) と起動先のアダプター・共通hookを確認し、このcloneを信頼する場合に実行する。セットアップには `hooks/list` と `config/batchWrite` に対応するCodex CLIが必要。検証した版は `0.169.0`。

リポジトリルートで、macOS/Linuxは次を実行する。

```sh
python3 .codex/setup_codex_hooks.py
```

Windowsは次を実行する。

```powershell
python .codex/setup_codex_hooks.py
```

`OK: 6 hooks are enabled and trusted ...` が表示されたら、新しいCodexセッションを開始する。セットアップは実行したスクリプトの場所からclone先を求めるため、ユーザー名や絶対パスの編集は不要。別の場所からスクリプトのパスを指定して実行してもよい。

設定を変更せず、保存済みの信頼状態だけを確認するには次を使う。Windowsでは `python3` を `python` に置き換える。

```sh
python3 .codex/setup_codex_hooks.py --check
```

### 保存する内容と再実行
- 使用中のCodexホームのユーザー設定（既定は `~/.codex/config.toml`、`CODEX_HOME` 指定時はその配下）に、このcloneのプロジェクト信頼と `.codex/hooks.json` の各hookの現在のハッシュを保存する。
- Codex公式APIで対象フィールドをマージするため、他のプロジェクト・hookの信頼、モデル、承認、サンドボックスなどの設定は保持する。無効化済みのhookは自動で有効にせず、`/hooks` での確認を求めて終了する。
- プロジェクト信頼を保存してからhook一覧を取得し、clone内の定義と一致することを確認してhookの信頼を保存する。最後に別のapp-serverプロセスで保存結果を確認する。スレッドやモデル、hookのコマンドは起動しない。
- 同じ内容で再実行できる。別端末・別のCodexホーム（CLIと実際に使うアプリで異なる場合を含む）・別のclone先では初回に実行する。hook定義を変更した場合も、変更内容を確認してから再実行する。同じ環境・定義ならセッションごとの実行は不要。

### 失敗した場合
終了コードは成功時0、失敗時2。APIエラー、応答待ちのタイムアウト（各要求30秒）、定義の欠落・不一致、無効化されたhook、保存後も未信頼のhookはエラーとして報告する。

プロジェクト信頼は先に保存するため、後続のhook検証・保存に失敗しても、そのプロジェクト信頼は残る。表示された原因を解消して再実行する。`--check` が未信頼を報告した場合は、`--check` を外してセットアップするか `/hooks` で確認する。

管理ポリシーやhook機能の無効化は変更しない。linked worktreeで本体のhookが読み込まれる場合は定義の不一致として停止するため、本体のcloneで実行する。Windows/Linuxの実CLI検証は未実施。

## hookの信頼
プロジェクトの `trust_level = "trusted"` と、個別hookの信頼は別の設定。プロジェクトだけを信頼しても、新規・変更された未信頼のhookは全イベントでスキップされ、アダプターには入力が届かない。

hookの信頼は、上の初回セットアップの代わりにCLIの `/hooks` で6イベントを信頼して保存してもよい。どちらも同じ経路（`config/batchWrite` による `hooks.state` への `upsert`）で、使用中のCodexホームの `config.toml` に `hooks.state.<hookのキー>.trusted_hash` を保存する。リポジトリの移動やhook定義の変更でキー・ハッシュが変わった場合は、改めて信頼する。

全イベントが動かない場合は、`/hooks` で各イベントの有効状態と信頼状態を確認する。APIで調べる場合はCodex app-serverの `hooks/list` に `cwds` を渡すと、`sourcePath`、`enabled`、`trustStatus`、`currentHash`、警告・エラーを確認できる。ハッシュを自前で算出したり、全hookの信頼確認を無効化したりしない。

アダプターは信頼設定を自動更新しない。既にスキップされた `SessionStart` などは、信頼を保存した後も遡って実行されない。

## 構成
|ファイル|役割|
|--|--|
|[hooks.json](./hooks.json)|イベントごとにアダプターを1つ登録する。|
|[setup_codex_hooks.py](./setup_codex_hooks.py)|clone先のプロジェクトとhookの信頼をユーザー設定に保存し、別プロセスで確認する。|
|[test_setup_codex_hooks.py](../.github/hooks/helpers/runner/test_setup_codex_hooks.py)|セットアップの設定保持、再実行、読み取り確認、異常系を検証する。|
|[run_codex_hook.py](../.github/hooks/helpers/runner/run_codex_hook.py)|Codexの入力と履歴をCopilot SDK形式に変換する。|
|[adapted_hook_runtime.py](../.github/hooks/helpers/runner/adapted_hook_runtime.py)|Claude Codeと共通の定義読込・順次実行・出力集約・履歴の末尾の補完と書き出し。|
|[test_run_codex_hook.py](../.github/hooks/helpers/runner/test_run_codex_hook.py)|形式変換、拒否、停止、起動設定のテスト。Windowsの起動テストはCodexと同じく、内部の引用符を保持したコマンド文字列をCOMSPECの `/c` に渡す。|

イベントごとにアダプターを1つだけ登録する理由と、Claude Codeのアダプターと共通の実行・出力の集約は [hookランナーのREADME](../.github/hooks/helpers/runner/README.md#アダプターからの起動) を参照。ここではCodex固有の事項を記載する。

起動パスは `git rev-parse --show-toplevel` で解決するため、サブディレクトリからCodexを開始しても使える。Windowsは `commandWindows` でPowerShellからPythonを起動する。Claude Code用のGit Bash起動スクリプトには依存しない。

## イベントの対応
|Codex|Copilot SDK|処理|
|--|--|--|
|`SessionStart`|`sessionStart`|起動元の `source` を渡す。|
|`UserPromptSubmit`|`userPromptSubmitted`|ユーザーの `prompt` を渡す。|
|`SubagentStart`|`userPromptSubmitted`|`agentId` を渡す。依頼文は入力にないため空文字列。|
|`PreToolUse`|`preToolUse`|ツール名と引数を変換して拒否を集約する。|
|`PostToolUse`|`postToolUse`|非ゼロ終了のシェルも含む。出力の文字列を `toolResult.textResultForLlm` に渡す。|
|`Stop`|`agentStop`|親の履歴と最終応答を検査する。|

`SubagentStop` は対象外とし、登録・変換しない。共通の `agentStop` hookはサブエージェントの停止を対象外としているため、停止時の検査は親の `Stop` だけで実行する。

`PostToolUseFailure` と `userPromptTransformed` に対応するイベントは登録しない。このため、失敗の繰り返し検出と変換後プロンプトの記録はCodexでは働かない。承認を代行する `PermissionRequest` も登録しない。

## 入力と実行
- `session_id`、`cwd`、現在時刻を `sessionId`、`cwd`、`timestamp` に変換する。UUIDでないセッションIDは名前ベースUUIDに変換する。
- Codexのサブエージェントhookは親のセッションIDを持つため、`agent_id` があれば親と子のIDの組から名前ベースUUIDを作り、状態の衝突を防ぐ。`agentId` も渡す。
- Codexの正規化済みツール名 `Bash` は `bash` に変換する。履歴中の `exec_command`、`shell_command`、`shell` も同じ形式にし、`cmd` やコマンド配列を `command` に変換する。
- `apply_patch` はそのまま渡す。パッチ本文は `tool_input.command` に入るため、既存の編集用utilityが読める。履歴中の自由形式パッチは `command` に包む。
- MCPなどのその他のツール名・引数は保持する。共通hookに対応する判定がなければ処理対象にならない。
- `functions.exec` のコードモードでも、対応版のCodexは内部の個々のツールに `PreToolUse` / `PostToolUse` を通知する。シェルなら `Bash` とコマンド、編集なら `apply_patch` とパッチを上と同じ経路で処理する。アダプターで外側のJavaScriptをシェルとみなしたり、文字列解析して内部呼び出しを推定したりしない。
- hook定義の読み込みと実行は、Claude Codeのアダプターと共通（[共通の実行](../.github/hooks/helpers/runner/README.md#共通の実行)）。

## 停止時の履歴
`transcript_path` は公式にも非安定インターフェースとされている。アダプターは公開ソースのrollout JSONL（`type`、`payload`）を対象に、次の情報を一時的なCopilot形式の履歴に変換する。

- 旧形式の `event_msg.user_message` / `agent_message`、ページ分割形式の `item_completed` の `UserMessage` / `AgentMessage`。
- `response_item` の `function_call` / `custom_tool_call` とその出力。編集ツールの引数も保持する。
- `CommandExecution.exit_code`、構造化されたシェル出力の `metadata.exit_code` / `exit_code`、シェル出力の終了行から確定できる終了コード。起動中・拒否・終了コード不明のシェルを成功扱いしない。
- コードモード内部のシェルに独立した `response_item` がない場合も、ページ分割形式の `CommandExecution` の `command` と `id` から呼び出しを復元する。対象は `source` が `agent` / `unified_exec_startup` の実行。ユーザー自身の実行や `write_stdin` の通信を新しい実行にはしない。同じIDの直接呼び出し・完了は重複させず、整数の終了コードがある場合だけ完了を記録する。
- ページ分割形式の `HookPrompt` を差し戻し履歴として渡す。共通hookの差し戻し文は個別に復元する。
- 入力の `last_assistant_message` が末尾の応答と異なる場合は補う。自身の `agentStop` 開始も末尾に追加し、共通hookの履歴待機を終わらせる。

指示や圧縮要約を、ユーザーが入力した依頼として補完しない。一時的な履歴は実行終了時に削除する。読めない・空・未知の履歴は標準エラーに報告し、その回の停止検査を実行しない。

## 出力
- 出力の集約と終了コードは、Claude Codeのアダプターと共通（[出力の集約](../.github/hooks/helpers/runner/README.md#出力の集約)）。
- Codexが続行したあとの `stop_hook_active: true` では、停止時の差し戻しを外して再ブロックしない。差し戻しは1ターンにつき最大1回となる。
- アダプター自体の異常（入力や履歴を読めないなど）も、終了コード1で報告する。

## 制約と限界
- 共通hookをPythonなしで起動できない（[共通の制約](../.github/hooks/helpers/runner/README.md#共通の制約)）。
- Codexがhookに渡さない呼び出し（ホスト側Web検索など）は検査できない。`write_stdin` は既存プロセスの通信であり、再度の実行前検査はない。ホストがイベントを通知しない場合、アダプターだけでは実行前・実行後の検査を追加できない。
- コードモード内部のツールに独立した `response_item` がない場合、停止時に復元するのは上記の `CommandExecution`。編集やMCPなど、他の完了項目から元の引数は復元しない。それらに依存する停止検査には履歴の制約が残る。
- 共通hookの検出対象がCopilotのツールに限られる場合、別名のMCPツールなどには同じ検出が働くとは限らない。
- 古い履歴で後続の `write_stdin` にだけ終了コードがある場合、元のコマンドの終了コードを推定しない。ページ分割形式の `CommandExecution` があれば元の呼び出しIDで補う。
- 履歴のロールバックや圧縮による置換・欠落、旧形式の割り込みの依頼区切りは完全には再現しない。履歴依存の停止検査は取得できた範囲に限る。
- プロセス内で実行するhookは、全体がCodexのタイムアウト（設定省略時600秒）に従う（[共通の制約](../.github/hooks/helpers/runner/README.md#共通の制約)）。
- テストはアダプターを子プロセスで実行する方式。Codexによる信頼済みhookの実発火とWindows/Linux上の実行は、この変更時には確認していない。

## 変更するとき
1. 公式ガイドでイベント・入出力・起動方法を確認し、履歴を扱う場合は公開ソースの保存形式も確認する。
2. Codex固有の変換はアダプターに加える。共通hookにはCodex専用の分岐を加えない。
3. イベントを増減した場合は `hooks.json` と上表を合わせる。
4. `python3 .github/hooks/helpers/runner/test_run_codex_hook.py`、`python3 .github/hooks/helpers/runner/test_setup_codex_hooks.py`、`python3 .github/hooks/tests/test_all_hooks.py`、`python3 .github/hooks/tests/test_hook_configurations.py` を実行する。Windowsでは `python` を使う。

## 確認した公式情報
- [Hooksガイド](https://learn.chatgpt.com/docs/hooks)：イベント、設定探索、信頼、OS別コマンド、入出力、履歴の非安定性。
- [CLIのhook信頼保存](https://github.com/openai/codex/blob/ee344ca9458351a6ea102795fa73110ad3ede5dc/codex-rs/tui/src/hooks_rpc.rs)：`hooks/list` と `config/batchWrite` による `trusted_hash` の永続化。
- [プロジェクト信頼の照合](https://github.com/openai/codex/blob/ee344ca9458351a6ea102795fa73110ad3ede5dc/codex-rs/config/src/loader/mod.rs)：cloneの信頼判定とWindowsの設定キーの正規化。
- [コードモードのhookテスト](https://github.com/openai/codex/blob/ee344ca9458351a6ea102795fa73110ad3ede5dc/codex-rs/core/tests/suite/hooks.rs) と [CommandExecutionItem](https://github.com/openai/codex/blob/ee344ca9458351a6ea102795fa73110ad3ede5dc/codex-rs/protocol/src/items.rs)：内部ツールへのhook適用と、停止時に復元する実行項目。
- [hook設定型](https://github.com/openai/codex/blob/81b995a6c9a4c978f399775cfa5ce50702368749/codex-rs/protocol/src/hooks.rs) と [command runner](https://github.com/openai/codex/blob/81b995a6c9a4c978f399775cfa5ce50702368749/codex-rs/hooks/src/engine/command_runner.rs)：`commandWindows` とWindowsでのCOMSPEC経由の実行。
- [rolloutのwire形式](https://github.com/openai/codex/blob/81b995a6c9a4c978f399775cfa5ce50702368749/codex-rs/history/src/rollout_payload.rs)、[保存対象の規則](https://github.com/openai/codex/blob/81b995a6c9a4c978f399775cfa5ce50702368749/codex-rs/rollout/src/policy.rs)、[TurnItem型](https://github.com/openai/codex/blob/81b995a6c9a4c978f399775cfa5ce50702368749/codex-rs/protocol/src/items.rs)：旧形式・ページ分割形式の変換根拠。

公開ソースは調査時のmainをコミットで固定した参照であり、すべてのリリース版で同じ形式になる保証ではない。
