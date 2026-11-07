# detect-orphaned-additions hook README

## 発動条件
agentStop（エージェントの応答終了時）

## 処理概要
今回の依頼で追加した関数・型などの定義のうち、どこからも参照されていないものや、テストからしか参照されていないものを検出し、呼び出し元につなぐか削除するよう差し戻す。

## 処理詳細
1. [orphaned-addition-definitions.json](./orphaned-addition-definitions.json) から、参照に数えないファイルのパス（`ignoredReferenceFilePatterns`）、対象外の名前（`excludedNamePatterns`）、言語ごとの定義（`languages`）を読み込む。また、[block-test-weakening](../../preToolUse/block-test-weakening/README.md) hookの [test-weakening-definitions.json](../../preToolUse/block-test-weakening/test-weakening-definitions.json) から、テストファイルのパス（`testFilePatterns`）を読み込む。いずれかのファイルがない、形式が不正、正規表現が不正な場合は異常終了する。
2. トランスクリプトに本hook自身の `hook.start` が書き込まれるまで最大5秒待つ（[wait-for-agent-stop-event](../../../utilities/wait-for-agent-stop-event/README.md)）。
3. ユーザーの最新の依頼以降について、編集系ツール（`edit`・`create`・`insert`・`str_replace_editor`・`apply_patch`）が書き込む内容と、置換前の内容を集める。
   - 各ツールの引数は [extract-edit-tool-changes](../../../utilities/extract-edit-tool-changes/README.md) の規則で解析する。
4. `git rev-parse --show-toplevel` でリポジトリルートを特定する。gitがない、またはリポジトリ外の場合は何もせず正常終了する。
5. リポジトリ内のファイルのうち、リポジトリルートからの相対パス（`/` 区切り）が `languages` の `filePattern` に一致し、テストファイルでないものについて、書き込む内容から `definitionPatterns` に一致する定義を探し、名前付きグループ `name` を定義名とする。次の定義は除く。
   - 定義名が `excludedNamePatterns` に一致するもの
   - 前後の空白を除いて同じ行が置換前の内容にあるもの（元からあるもの）
   - ファイルの現在の内容にないもの（後の編集で消えたもの）
   - 直前の空行でない行が `annotationPattern`（属性・デコレーター）に一致するもの
6. 定義名をまとめて `git grep --untracked -I -n -w -F` でリポジトリ全体から検索し、定義した行そのものと、`ignoredReferenceFilePatterns` に一致するファイルの行を除いて参照を数える。
7. 参照がない定義（`no references`）と、テストファイルからしか参照されていない定義（`referenced only from tests`）を、`<相対パス>:<行番号>: <定義名>` の形式で最大10件列挙する。呼び出し元につなぐか削除するよう求め、リポジトリ外から使う公開APIやフレームワークが名前で呼ぶ入口なら応答でその旨を示すよう求める英語の理由とともに `{"decision":"block","reason":"..."}` を標準出力して差し戻す。

## 検出する内容

|言語|対象のファイル|検出する定義|注釈|
|--|--|--|--|
|C#|`*.cs`|型（`class`・`struct`・`interface`・`enum`・`record`）、アクセス修飾子付きのメソッド（`override` を除く）|`[...]`|
|Java|`*.java`|型（`class`・`interface`・`enum`・`record`・`@interface`）、アクセス修飾子付きのメソッド|`@...`|
|Python|`*.py`・`*.pyw`|トップレベルの `def`・`async def`・`class`|`@...`|
|JavaScript/TypeScript|`*.js`・`*.jsx`・`*.ts`・`*.tsx`・`*.mjs`・`*.cjs`・`*.mts`・`*.cts`|`function`、`class`・`interface`・`enum`・`type`、トップレベルの関数を代入する `const`・`let`・`var`|`@...`|
|Go|`*.go`|トップレベルの `func`（メソッドを含む）・`type`|なし|

|除外する名前|例|
|--|--|
|エントリーポイント|`main`・`Main`・`init`|
|Pythonの特殊メソッド|`__init__` など `__*__`|
|.NETの規約上のメソッド|`Dispose`・`DisposeAsync`・`GetEnumerator`・`Equals`・`GetHashCode`・`ToString`・`CompareTo`・`Deconstruct`|
|Goの標準インターフェースのメソッド|`String`・`Error`・`ServeHTTP`・`Len`・`Less`・`Swap`・`MarshalJSON`・`UnmarshalJSON`|

## 前提条件
- gitを使う。gitがない、またはリポジトリ外の場合は何もしない。

## 例外
- 書き込む時点では拒否せず、応答終了時に最終的なファイルに残っている定義だけを検査する。呼び出し側を後から書く途中の状態では差し戻さない。
- ドキュメント（`*.md`・`*.markdown`・`*.rst`・`*.txt`・`*.adoc`）の言及は参照に数えない。
- テストファイル内の定義はテストフレームワークが呼ぶため対象外とする。テストファイルからの参照しかない定義は、実装が本体から呼ばれていないものとして差し戻す。
- `override` や属性・デコレーター付きの定義、Pythonのメソッド、JavaScript/TypeScriptのクラスのメソッドは、基底クラスやフレームワークから名前で呼ばれることが多いため対象外とする。
- サブエージェントの停止は対象外。
- 依頼の区切りは [get-turn-events](../../../utilities/get-turn-events/README.md) の規則に従う。
- 最新の依頼の編集だけを対象とする。それより前の依頼の編集やユーザーの変更で入った定義は検出しない。
- シェルによる書き込みは対象外とする。git管理下のファイルへの書き込みは [block-shell-file-edits](../../preToolUse/block-shell-file-edits/README.md) hookが拒否する。
- gitの検索に失敗した場合（タイムアウトの10秒を超えた場合を含む）は、参照を数えられないため何もせず正常終了する。

## 制約と限界
- 参照は名前の単語一致で数える。同名の別の定義、コメント・文字列内の一致、同じファイルの別の行（コンストラクターの宣言など）も参照に数えるため、それらがあると見逃す。
- 参照はgitが管理するファイルと、無視設定（`.gitignore` など）に一致しない未追跡のファイルから探す。
- リポジトリ外のインターフェースを実装したメソッドは、除外する名前にないものを誤検知する。
- リポジトリ外から使う公開APIでも一度は差し戻す。応答でその旨を示せば、同じ依頼では再び差し戻さない。
- 同じ依頼で差し戻すのは1回だけ。2回目以降の応答は検査しない。
- 正規表現で検出するため、定義にない形式（C#のプロパティ、ローカル関数など）は見逃す。

## 備考
- 「念のため」作ったヘルパーや、実装とテストは書いたが呼び出し元へつなぎ忘れた機能を、作業終了時に検出するためのhook。使われない定義は読む・保守する負担だけを残す。
