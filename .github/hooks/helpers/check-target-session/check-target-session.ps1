# PowerShellの自動設定変数ErrorActionPreferenceにStopを設定すると、
# 以降のコマンドにおけるエラー処理が（既定処理に上書きで）「停止」になる。
$ErrorActionPreference = 'Stop'

# 標準入力をUTF-8として復号化し、JSON内の日本語などを正しく読み取る。
#   左辺はSystem.Consoleクラスに属する静的メンバInputEncodingにアクセスしていることを意味する。
[System.Console]::InputEncoding = [System.Text.UTF8Encoding]::new()

# 標準出力をUTF-8に統一することで、先頭にBOMが含まれJSON解析を妨げることのないようにする。
#   右辺はSystem.Text.UTF8Encodingクラスのインスタンス化。コンストラクタの引数にfalseを渡すとエンコーダによるBOMの出力を無効にする。
[System.Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

# sessionStartのタイミングで本スクリプトの呼び出し元であるCopilotがPowerShellプロセスを起動し
# 標準入力パイプにツール名や引数を含むイベント情報をJSON形式で書き込むので、それを読み込む。
# このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
$inputJson = [System.Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($inputJson)) {
    [System.Console]::Error.WriteLine('sessionStart hook input is empty.')
    exit 2
}

# 読み取ったJSONをPowerShellのオブジェクトに変換する。
#   -NoEnumerate を指定することで最外側の配列を展開せず、そのまま保持する。
#     最外側が[]のものは拒否したいが、-NoEnumerate なしだと展開され拒否条件を満たさなくなってしまう。
try {
    $hookInput = $inputJson | ConvertFrom-Json -NoEnumerate
} catch {
    [System.Console]::Error.WriteLine("Failed to parse sessionStart hook input: $($_.Exception.Message)")
    exit 2
}

# 変換結果がPSCustomObjectでなければ異常終了。
#   文字列・数値・null・（最外側が[]である）配列などはここを通過させない。
if ($hookInput -isnot [System.Management.Automation.PSCustomObject]) {
    [System.Console]::Error.WriteLine('sessionStart hook input must be a JSON object.')
    exit 2
}

# VS Code Local harness はフック実行時の payload に hook_event_name: 'SessionStart' を含めて渡してくる。
# 一方、Copilot CLI や Session Target 'Copilot'（Copilot SDK形式）ではこのプロパティが含まれない。
# そのため、hook_event_name が大文字小文字を区別して 'SessionStart' に等しい場合を除き、
# Copilot target 等と判断して何も出力せず終了する（exit 0）。
# -cneのcは"case-sensitive"を意味し、比較時に大文字小文字を区別してすることを意味する。
#   -ceqは、大文字小文字を区別して比較した結果、等しい場合に真となる。
#   -cneは、大文字小文字を区別して比較した結果、等しくない場合に真となる。
if ($hookInput.hook_event_name -cne 'SessionStart') {
    exit 0
}

# ここに到達したということはhook_event_name が大文字小文字を区別して 'SessionStart' に等しいということなので、
# VS Code Local harness と判定できるので Session Target を Copilot に切り替えて新しいセッションで作業するよう案内する。
@{
    systemMessage = 'This session is using the VS Code Local harness. Open the Agents window, create a new session, and select Copilot as the Session Target to use this repository''s Copilot CLI-compatible hooks. Continue this task in that new session.'
} | ConvertTo-Json -Compress
