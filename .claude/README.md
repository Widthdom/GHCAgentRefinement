# .claude README

Claude Code向けの設定を置く。

|項目|役割|
|--|--|
|[skills](./skills)|[.github/skills](../.github/skills) のskillをClaude Codeが発見するためのラッパー。管理方法は [skillの共通規約](../.github/skills/create-skill/references/skill-conventions.md) を参照。|
|[settings.json](./settings.json)|[.github/hooks](../.github/hooks) のhookをClaude Codeで実行するための設定。以下で説明する。|

## hookの実行

### 背景

このリポジトリのhookは、Copilot SDKの形式（イベント名、入力、出力、トランスクリプト）で実装し、検証している。Claude Codeにもhookはあるが、形式が異なるため、同じスクリプトをそのまま登録しても動かない。

hookごとに互換処理を入れると、すべてのhookが2つの形式を意識することになる。そこで、hookの外側に置いた1本のアダプターがClaude Codeの形式をCopilot SDKの形式に変換し、既存のhookを変更せずに実行する。

hookごとにラッパーを作らず、アダプターを1本にした理由は次のとおり。

- Claude Codeは同じイベントのhookを並列に実行する。アダプターを1本にすれば、状態を共有するhookを定義順に1つずつ実行でき、拒否や差し戻しも1つにまとめて返せる。
- hookの一覧を [.github/hooks](../.github/hooks) のhook定義（`<event-name>.json`）と `settings.json` に二重に書かずに済む。hookを追加・削除しても `settings.json` は変わらない。

### 構成

|ファイル|役割|
|--|--|
|[settings.json](./settings.json)|対応するイベントごとに、起動スクリプトを1つだけ登録する。|
|[run-claude-hook.sh](../.github/hooks/helpers/runner/run-claude-hook.sh)|起動スクリプト。OSに合わせてPythonのコマンド名（Windowsでは `python`、macOS/Linuxでは `python3`）を選び、アダプターを起動する。Claude Codeのhook設定はOS別のコマンドを持てないため。|
|[run-claude-hook.py](../.github/hooks/helpers/runner/run-claude-hook.py)|アダプター。入力の `hook_event_name` から、実行するhookを決める。|
|[test-run-claude-hook.py](../.github/hooks/helpers/runner/test-run-claude-hook.py)|アダプター、起動スクリプト、`settings.json` のテスト。|

### 処理の流れ

1. Claude Codeの入力を、Copilot SDKの入力に変換する（[入力の変換](#入力の変換)）。
2. Stopでは、Claude CodeのトランスクリプトをCopilotのイベント履歴（`events.jsonl`）の形式に変換して一時フォルダーに書き、そのパスを `transcriptPath` として渡す（[トランスクリプトの変換](#トランスクリプトの変換)）。一時フォルダーはhookの実行後に削除する。
3. 対応するイベントのhook定義を、このリポジトリ専用の定義（`-repo-<event-name>.json`）、共通の定義（`<event-name>.json`）の順に読む。`matcher` があるhookは、変換後のツール名の全体に一致するものだけを対象にする。
4. 対象のhookを定義順に1つずつ実行する。拒否や差し戻しを出力したhookがあっても、残りのhookを実行する。
   - [hookランナー](../.github/hooks/helpers/runner/README.md) を通すhookは、アダプターのプロセス内でランナーを呼び出して実行する。hookごとにPythonを起動しないため。起動と、機能したかどうかは、Copilotでの実行と同じく記録される。
   - ランナーを通さないhook（`check-python`・`block-commands`）は、hook定義のコマンド（Windowsでは `powershell`、それ以外では `bash`）のまま、リポジトリルートで子プロセスとして実行する。
5. 各hookの出力を、Claude Codeの形式の1つの出力にまとめる（[出力の変換](#出力の変換)）。

### イベントの対応

|Claude Code|Copilot SDK|備考|
|--|--|--|
|`SessionStart`|`sessionStart`||
|`UserPromptSubmit`|`userPromptSubmitted`||
|`SubagentStart`|`userPromptSubmitted`|Copilotでは、サブエージェントへの依頼が `agentId` 付きのuserPromptSubmittedとして発火するため。依頼文（`prompt`）は空文字列になる。|
|`PreToolUse`|`preToolUse`||
|`PostToolUse`|`postToolUse`||
|`PostToolUseFailure`|`postToolUse` または `postToolUseFailure`|シェルの非ゼロ終了（エラーが `Exit code <終了コード>` で始まり、中断ではないもの）は `postToolUse`、それ以外は `postToolUseFailure`。Copilotでは、シェルの非ゼロ終了はツールの成功として扱われるため。|
|`Stop`|`agentStop`||

### 入力の変換

|Copilot SDK|Claude Code|
|--|--|
|`sessionId`|`session_id`|
|`timestamp`|アダプターの起動時刻（Unixミリ秒）|
|`cwd`|`cwd`|
|`agentId`|`agent_id`（サブエージェント内で発火したときだけある）|
|`source`|`source`|
|`prompt`|`prompt`|
|`toolName`|`tool_name` を下の表で変換した名前|
|`toolArgs`|`tool_input`（変換しない）|
|`toolResult.textResultForLlm`|`tool_response` に含まれる文字列を出現順に改行でつないだもの。シェルの非ゼロ終了では、エラーから `Exit code` の行を除いたもの。|
|`error`|`error`|
|`transcriptPath`|変換したトランスクリプトのパス（Stopだけ）|

`hook_event_name` は渡さない。[check-target-session](../.github/hooks/helpers/events/sessionStart/check-target-session/README.md) がこの項目でVS CodeのLocal harnessを検知するため。

|Claude Codeのツール|Copilotのツール|
|--|--|
|`Bash`|`bash`|
|`PowerShell`|`powershell`|
|`Edit`|`edit`|
|`Write`|`create`|
|`Read`|`view`|
|`Grep`|`grep`|
|`Glob`|`glob`|
|`WebFetch`|`web_fetch`|
|`Skill`|`skill`|

表にないツール（MCPのツール、`NotebookEdit` など）は名前を変えない。引数は変換しない。編集ツールの引数名の違い（`file_path`・`old_string`・`new_string`・`content`）は、[edit-tool-changes](../.github/hooks/helpers/utilities/edit-tool-changes/README.md) が元から受け付ける。

### 出力の変換

|Copilot SDKの出力|Claude Codeの出力|
|--|--|
|`permissionDecision: "deny"`（preToolUse）|`hookSpecificOutput.permissionDecision: "deny"`。複数のhookの理由は空行でつなぐ。|
|`decision: "block"`（agentStop）|`decision: "block"`。複数のhookの理由は `---` の行でつなぐ。|
|`additionalContext`|`hookSpecificOutput.additionalContext`。複数のhookの助言は空行でつなぐ。Stopでは渡さない（Claude Codeでは応答を続行させるため）。|
|`systemMessage`|`systemMessage`。複数のhookの文は空行でつなぐ。|

終了コードは次のとおり。

- 出力があれば、出力して0で終える。
- 出力がなく、異常終了したhookがあれば、1で終える。Claude Codeでは終了コード2が「ブロック」を意味し、hookの異常終了（入力の不正など）でツールの実行や応答の終了が止まるため、非ブロックのエラーである1にする。原因はhookが標準エラーに出力する。
- 異常終了したhookでも、判定のJSONを出力していれば判定を採用する。Pythonが古いときに [block-commands](../.github/hooks/helpers/events/preToolUse/block-commands/README.md) がシェルの実行を拒否できるようにするため。

### トランスクリプトの変換

agentStopのhookは [read-transcript-events](../.github/hooks/helpers/utilities/read-transcript-events/read-transcript-events.py) でCopilotのイベント履歴を読む。Claude Codeのトランスクリプトから、hookが読むイベントを次のように組み立てる。サブエージェントのやり取り（`isSidechain`）は含めない。

|Copilotのイベント|Claude Codeの記録|
|--|--|
|`user.message`|ユーザーの文。hookの差し戻しなどの `isMeta` と、圧縮後の要約（`isCompactSummary`）は除く。|
|`assistant.message`|アシスタントの文（`text`）。入力の `last_assistant_message` が最後の文と異なる場合は、末尾に加える。Stopの時点で、最終応答がまだトランスクリプトに書き込まれていない場合があるため。|
|`tool.execution_start`|ツール呼び出し（`tool_use`）。ツール名は上の表で変換する。|
|`tool.execution_complete`|ツール結果（`tool_result`）。`success` はエラーでないこと。シェルは、成功なら終了コード0、エラーが `Exit code <終了コード>` で始まるなら成功扱いでその終了コードとする。|
|`hook.end` と `queued` の `user.message`|Stop hookの差し戻し（`hook_blocking_error`）。`---` の行で分け、hookごとの差し戻しに戻す。これで、各hookが「自分は差し戻し済みか」を判定できる。|
|`hook.start`|やり取りから補う（依頼の前に `userPromptSubmitted`、ツール呼び出しの前に `preToolUse`、結果の後に `postToolUse` または `postToolUseFailure`）。Claude Codeは出力のないhookの起動を記録しないため。末尾には今回の `agentStop` を加え、hookがトランスクリプトの追記を待たないようにする。|

### Copilotとの違いと制約

- Copilotは同じイベントのhookを並列に実行するが、アダプターは定義順に1つずつ実行する。
- hook定義の `timeoutSec` は、ランナーを通さないhookにだけ適用する。プロセス内で実行するhookは個別に止められず、全体がClaude Codeのhookのタイムアウト（既定は600秒、UserPromptSubmitは30秒）に従う。タイムアウトすると、そのイベントのすべてのhookの出力が失われる。
- Stop hookの差し戻しは、ツール呼び出しを挟まずに8回続くとClaude Codeが打ち切る（`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` で変更できる）。
- `additionalContext` は、Claude Codeでは10,000文字が上限。
- Pythonがない環境ではアダプター自体が起動できず、非ブロックのエラーになるだけで、[check-python](../.github/hooks/helpers/events/sessionStart/check-python/README.md) の案内も `block-commands` の拒否も働かない。Pythonが3.14より古い環境では、どちらも働く。
- `Edit` の `replace_all` は、編集後の内容の再現（`simulate_edit_tool_changes`）では最初の1か所だけの置換として扱われる。
- バックグラウンドで実行したシェル（`run_in_background`）は、終了コードを不明として扱う。
- 応答の途中でユーザーが送った文と、ローカルコマンド（`/compact` など）の記録は、新しい依頼として扱う。Copilotの `queued` のような区別を、Claude Codeの記録から確認できていないため。
- 拒否や助言の文言には、Copilotのツール名（`view`・`create` など）がそのまま出る。
- [debug-add-response-footer](../.github/hooks/helpers/events/agentStop/debug-add-response-footer/README.md) は、フッターの後の空行まで照合する。Claude Codeの最終応答は末尾の空行を持たないため、フッターが正しく付いても、上限の回数まで差し戻しが繰り返される。
- [load-copilot-instructions](../.github/hooks/helpers/events/sessionStart/load-copilot-instructions/README.md) が渡す指示は、[CLAUDE.md](../CLAUDE.md) が読むよう指示するものと同じ内容で、重複する。

### 対象外

- `userPromptTransformed`：Claude Codeに相当するイベントがない。
- `SubagentStop`：Copilotでも、agentStopのhookはサブエージェントの停止を対象外としている。

### 前提条件

- [.github/README.md](../.github/README.md) の前提条件と同じく、Gitのインストールを前提とし、Python 3.14以降が必要。
- 起動スクリプト（[run-claude-hook.sh](../.github/hooks/helpers/runner/run-claude-hook.sh)）の実行に `sh` が必要。Windowsでは、Git for Windowsに含まれるGit Bashの `sh` を使う。Claude Codeは、Git Bashがあればhookのコマンドをそこで実行する。
- クラウドのセッションでは、環境にPython 3.14以降を `python3` として用意する。3.14より古いと、`block-commands` がすべてのシェルの実行を拒否する。

### Windows用の `.cmd` を置かない理由

Copilotのhook定義は、1つのhookに `bash` と `powershell` のコマンドを併記できる。Claude Codeのhook設定は、1つのhookにコマンドを1つしか持てず、OSで出し分ける項目もない。

`.cmd` を呼ぶには `"shell": "powershell"` のhookを別に登録することになるが、PowerShellのないmacOS/Linuxでは、そのhookがイベントのたびにエラーになる（Claude Code 2.1.325で確認）。Git BashのないWindowsには対応しない。このリポジトリのhookはGitを必要とし、Git for WindowsにはGit Bashが含まれるため。

### 動作確認

Claude Code 2.1.325（Linux）で、実際のセッションを使って次を確認した。

- SessionStartで共通指示が渡り、UserPromptSubmitでデバッグモードが切り替わる。
- PreToolUseで、git管理下のファイルを書き換えるシェルコマンドが拒否され、理由がエージェントに届く。
- シェルの非ゼロ終了で、postToolUseのhookが実行される。
- SubagentStartで、サブエージェントに共通指示が渡る。
- Stopで差し戻しが届き、次のStopでは変換したトランスクリプトから差し戻し済みと判定される。

Windows、macOS、VS CodeのSession Target `Claude` では確認していない。

### 変更するとき

- hookの追加・削除・変更では、ここは変更しない。手順は [create-hook](../.github/skills/create-hook/SKILL.md)・[update-hook](../.github/skills/update-hook/SKILL.md) のとおり。
- 新しいイベントやツールに対応するときは、アダプターの対応表（`_EVENT_NAMES`・`_TOOL_NAMES`）、`settings.json`、テスト、このREADMEを更新する。
