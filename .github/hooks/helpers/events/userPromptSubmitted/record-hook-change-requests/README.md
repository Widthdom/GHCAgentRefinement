# record-hook-change-requests hook README

## 発動条件
userPromptSubmitted（メインエージェントへの依頼時）

## 処理概要
ユーザーの依頼がhookに触れていれば、このセッションでhookの変更を認めた旨を記録する。[block-hook-tampering](../../preToolUse/block-hook-tampering/README.md) hookが使う記録で、単独では何も出力しない。

## 処理詳細
1. 入力に `agentId` があれば何もしない。サブエージェントへの依頼は親のエージェントが書いたもので、ユーザーの依頼ではないため。
2. プロンプトが次のいずれかを含めば、このセッションでhookの変更を認めた旨を状態ファイルに記録する。大文字小文字は区別しない。
   - 語：`hook`・`hooks`・`フック`・`.github/hooks`、イベント名（`preToolUse`・`postToolUse`・`postToolUseFailure`・`agentStop`・`sessionStart`・`userPromptSubmitted`・`userPromptTransformed`、`pre-tool-use` などの表記も含む）
     - `hook`・`hooks` は英字の途中にあるもの（`webhook`・`useFooHook` など）を除く。`フック` は直前がカタカナ・英字のもの（`カスタムフック`・`ウェブフック`・`Reactフック` など）を除く。
   - hookの名前：cwdから親へたどって最初に見つかった `.github/hooks` の、`helpers/events/<event>/<hook>/`・`helpers/-repo-events/<event>/<hook>/` のフォルダー名（`block-commands` など）
3. 含まなければ何もしない。記録済みの状態は消さない。

## 例外
- サブエージェントへの依頼を記録すると、エージェントがサブエージェントへの依頼文にhookの語を書くだけで、変更を自ら許可できてしまう。そのため `agentId` 付きの入力は記録しない。

## 制約と限界
- 一度hookに触れた依頼があれば、セッションの間は変更を認めたままにする。「続けて」「1,2の順に対応して」など、hookに触れない後続の依頼で作業を止めないため。
- 語は前後の文字だけで判定するため、`Reactのフック`・`git hook` など、このリポジトリのhookを指さない依頼でも記録する。

## 備考
- [block-hook-tampering](../../preToolUse/block-hook-tampering/README.md) hookが使う記録。単独では何も出力しない。preToolUseの入力にはユーザーの依頼もトランスクリプトのパスも含まれないため、依頼の時点で記録しておく。
- 状態は [hook-change-request-state.py](../../../utilities/hook-change-request-state/hook-change-request-state.py) でblock-hook-tampering hookと共有する。プロンプトの内容は保存せず、`{"updatedAt":"...","permitted":true}` だけを `<一時フォルダー>/ghc-agent-refinement/hook-states/hook-change-requests/<セッションID>.json` に保存する。hookに触れない依頼では状態ファイルを作らない。保存は [write-json-atomically](../../../utilities/write-json-atomically/README.md) で一時ファイルに書いてから置き換え、block-hook-tampering hookに書き込み途中の内容を読ませない。
