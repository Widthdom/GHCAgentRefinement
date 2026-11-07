# detect-unread-speculation hook README

## 発動条件
agentStop（エージェントの応答終了時）

## 処理概要
最終応答で推測の表現とともに挙げたファイルのうち、このセッションで読んでいないものを検出し、読んで訂正するか未確認である旨を明記するよう差し戻す。

## 処理詳細
1. [unread-speculation-definitions.json](./unread-speculation-definitions.json) から、推測とみなす表現の正規表現（`speculationPatterns`）を読み込む。ファイルがない場合、形式に違反する場合、不正な正規表現を含む場合は標準エラーに原因を出力して異常終了する。
2. トランスクリプトに本hook自身の `hook.start` が書き込まれるまで最大5秒待つ（Copilotはトランスクリプトへ非同期に追記するため、発動直後は最終応答が未書き込みのことがある）。
3. ユーザーの最新の依頼以降の最終応答を文に分け、推測の表現（「おそらく」「〜のはず」「と思われる」「probably」など）を含む文から、コードスパン・Markdownリンク・地の文のパスを抽出する（コードブロックは除く）。末尾の `:12`・`#L12-L34` は行番号として除く。
4. 各パスをcwdとリポジトリルートを基準に解決し、実在するファイルのうち、このセッションで読んでいないものを探す。次のいずれかに当たるファイルは読んだとみなす。
   - [record-seen-files](../../postToolUse/record-seen-files/README.md) hookが閲覧・編集を記録している（`denied` の行は読んでいないとみなす）。
   - `bash`・`powershell`・`rg`・`grep` ツールの引数に、書かれた表記・cwd相対・絶対パスのいずれかが含まれる。
5. 読んでいないファイルを最大10件まで列挙し、`{"decision":"block","reason":"..."}` を標準出力して差し戻す。ファイルを読んで記述を訂正するか、未確認である旨を明記するよう求める。

## 前提条件
- [record-seen-files](../../postToolUse/record-seen-files/README.md) hookの記録を使う。状態は [known-files-state.py](../../../utilities/known-files-state/known-files-state.py) でrecord-seen-files hook・[require-read-before-edit](../../preToolUse/require-read-before-edit/README.md) hookと共有し、読み取りだけを行う。

## 例外
- 推測の表現と同じ文にあるパスだけを対象にする。断定で語った場合は、正当な言及（存在や操作の報告など）と区別できないため対象外とする。
- 推測の判定にはコードスパンの中身を使わない（`probably_ok` のような識別子を推測とみなさないため）。
- 地の文のパスは拡張子付きに限る。実在しないパスは対象外とする（誤参照は detect-phantom-references が扱う）。
- hookの差し戻しやユーザーの割り込みは `delivery: "queued"` のuser.messageとして記録されるため、依頼の区切りとしない。

## 制約と限界
- 同じ依頼で差し戻すのは1回だけ。読めない場合でも停止できるよう、2回目以降の応答は検査しない。
- サブエージェントの閲覧も親と同じ記録に含まれるため、サブエージェントが読んだファイルは親が読んだとみなす（[record-seen-files](../../postToolUse/record-seen-files/README.md) の制約と限界を参照）。

## 背景
記憶や思い込みで、読んでいないファイルの中身を推測で語るのを防ぐ。[detect-phantom-references](../detect-phantom-references/README.md) は実在しないパスを扱い、本hookは実在するが読んでいないパスを扱う。

## 備考
- 正規表現はPythonの `re` 構文で書き、大文字小文字を区別しない場合は `(?i)` を先頭に付ける。テスト用に `--definitions-path` で定義ファイルを差し替えられる。
