# PowerShellの自動設定変数ErrorActionPreferenceにStopを設定すると、
# 以降のコマンドにおけるエラー処理が（既定処理に上書きで）「停止」になる。
$ErrorActionPreference = 'Stop'

# 標準入力をUTF-8として復号化し、JSON内の日本語などを正しく読み取る。
#   左辺はSystem.Consoleクラスに属する静的メンバInputEncodingにアクセスしていることを意味する。
[System.Console]::InputEncoding = [System.Text.UTF8Encoding]::new()

# 標準出力をUTF-8に統一することで、先頭にBOMが含まれJSON解析を妨げることのないようにする。
#   右辺はSystem.Text.UTF8Encodingクラスのインスタンス化。コンストラクタの引数にfalseを渡すとエンコーダによるBOMの出力を無効にする。
[System.Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

# userPromptSubmittedのタイミングで本スクリプトの呼び出し元であるCopilotがPowerShellプロセスを起動し
# 標準入力パイプにツール名や引数を含むイベント情報をJSON形式で書き込むので、それを読み込む。
# このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
$inputJson = [System.Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($inputJson)) {
    [System.Console]::Error.WriteLine('userPromptSubmitted hook input is empty.')
    exit 2
}

# 読み取ったJSONをPowerShellのオブジェクトに変換する。
#   -NoEnumerate を指定することで最外側の配列を展開せず、そのまま保持する。
#     最外側が[]のものは拒否したいが、-NoEnumerate なしだと展開され拒否条件を満たさなくなってしまう。
try {
    $hookInput = $inputJson | ConvertFrom-Json -NoEnumerate
} catch {
    [System.Console]::Error.WriteLine("Failed to parse userPromptSubmitted hook input: $($_.Exception.Message)")
    exit 2
}

# 変換結果がPSCustomObjectでなければ異常終了。
#   文字列・数値・null・（最外側が[]である）配列などはここを通過させない。
if ($hookInput -isnot [System.Management.Automation.PSCustomObject]) {
    [System.Console]::Error.WriteLine('userPromptSubmitted hook input must be a JSON object.')
    exit 2
}

$prompt = [string]$hookInput.prompt
if ([string]::IsNullOrWhiteSpace($prompt)) {
    [System.Console]::Error.WriteLine('userPromptSubmitted hook input does not contain a prompt.')
    exit 2
}

# System.Guidクラスには静的メンバEmptyが存在し、値は全ビット0のGUIDである。
$parsedSessionId = [System.Guid]::Empty
$sessionId = $hookInput.sessionId
if (-not [System.Guid]::TryParse($sessionId, [ref]$parsedSessionId)) {
    [System.Console]::Error.WriteLine("Invalid session ID: $sessionId")
    exit 2
}

# パス区切りはOS依存のため、Join-Pathで階層ごとに連結する。
$stateDirectory = Join-Path (Join-Path ([System.IO.Path]::GetTempPath()) 'ghc-agent-refinement') 'debug-mode'
# 状態ファイルを格納するディレクトリを作成する。存在しない場合は新規作成。
[System.IO.Directory]::CreateDirectory($stateDirectory) | Out-Null
# 状態ファイルのパスを作成する。ファイル名にはセッションIDを使用する。
#   ToString('D') により、ハイフン区切りの標準的なGUID文字列形式になる。
$statePath = Join-Path $stateDirectory "$($parsedSessionId.ToString('D')).json"
# @{}はハッシュテーブルを作る構文。項目名と値を組にして保持する。
#   デバッグモードが有効か否か、最終更新日時をハッシュテーブルにまとめ、JSON形式に変換して返却する。
#     -Compress はJSONの余分な空白や改行を省き、1行のJSONにする指定
# デバッグモードが有効となるための条件は、プロンプトが'??'で始まることである。
$state = @{
    enabled = $prompt.StartsWith('??', [System.StringComparison]::Ordinal)
    updatedAt = [DateTimeOffset]::UtcNow.ToString('O')
} | ConvertTo-Json -Compress

# 状態ファイルにデバッグモードの情報を書き込む。
[System.IO.File]::WriteAllText(
    $statePath,
    $state,
    [System.Text.UTF8Encoding]::new($false)
)
