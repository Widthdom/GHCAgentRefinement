# PowerShellの自動設定変数ErrorActionPreferenceにStopを設定すると、
# 以降のコマンドにおけるエラー処理が（既定処理に上書きで）「停止」になる。
$ErrorActionPreference = 'Stop'

# 標準入力をUTF-8として復号化し、JSON内の日本語などを正しく読み取る。
#   左辺はSystem.Consoleクラスに属する静的メンバInputEncodingにアクセスしていることを意味する。
[System.Console]::InputEncoding = [System.Text.UTF8Encoding]::new()

# 標準出力をUTF-8に統一することで、先頭にBOMが含まれJSON解析を妨げることのないようにする。
#   右辺はSystem.Text.UTF8Encodingクラスのインスタンス化。コンストラクタの引数にfalseを渡すとエンコーダによるBOMの出力を無効にする。
[System.Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

# agentStopのタイミングで本スクリプトの呼び出し元であるCopilotがPowerShellプロセスを起動し
# 標準入力パイプにツール名や引数を含むイベント情報をJSON形式で書き込むので、それを読み込む。
# このように呼び出し元が渡してくるデータのことをpayloadと呼ぶ。
$inputJson = [System.Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($inputJson)) {
    [System.Console]::Error.WriteLine('agentStop hook input is empty.')
    exit 2
}

# 読み取ったJSONをPowerShellのオブジェクトに変換する。
#   -NoEnumerate を指定することで最外側の配列を展開せず、そのまま保持する。
#     最外側が[]のものは拒否したいが、-NoEnumerate なしだと展開され拒否条件を満たさなくなってしまう。
try {
    $hookInput = $inputJson | ConvertFrom-Json -NoEnumerate
} catch {
    [System.Console]::Error.WriteLine("Failed to parse agentStop hook input: $($_.Exception.Message)")
    exit 2
}

# 変換結果がPSCustomObjectでなければ異常終了。
#   文字列・数値・null・（最外側が[]である）配列などはここを通過させない。
if ($hookInput -isnot [System.Management.Automation.PSCustomObject]) {
    [System.Console]::Error.WriteLine('agentStop hook input must be a JSON object.')
    exit 2
}

# トランスクリプトファイル（セッション中の会話やイベントを記録したログファイル）の内容を取得する。
#   agentStop の入力内容そのままでは情報が不足している。
#   入力内容にはトランスクリプトファイルのパスが含まれているので、それを利用して追加情報を取得する。
$transcriptPath = $hookInput.transcriptPath

# トランスクリプトファイルの存在確認
#   -LiteralPath を指定することで引数をワイルドカードとして解釈せず、文字どおりのパスとして扱う。
#   -PathType Leaf を指定することで、ファイルとして存在するかどうかを確認する。
if ([string]::IsNullOrWhiteSpace($transcriptPath) -or
    -not (Test-Path -LiteralPath $transcriptPath -PathType Leaf)) {
    [System.Console]::Error.WriteLine("Transcript file not found: $transcriptPath")
    exit 2
}

# System.Guidクラスには静的メンバEmptyが存在し、値は全ビット0のGUIDである。
$parsedSessionId = [System.guid]::Empty
$sessionId = $hookInput.sessionId
if (-not [guid]::TryParse($sessionId, [ref]$parsedSessionId)) {
    [System.Console]::Error.WriteLine("Invalid session ID: $sessionId")
    exit 2
}

# パス区切りはOS依存のため、Join-Pathで階層ごとに連結する。
$debugStateFilePath = Join-Path (Join-Path (Join-Path `
    ([System.IO.Path]::GetTempPath()) 'ghc-agent-refinement') 'debug-mode') `
    "$($parsedSessionId.ToString('D')).json"

# デバッグモード用の状態ファイルが存在しない場合はデバッグモードでないものとみなし、処理を終了する。
if (-not (Test-Path -LiteralPath $debugStateFilePath -PathType Leaf)) {
    exit 0
}

try {
    # -NoEnumerate を指定しないので、最外側が[]である配列であったとしても展開されJSON形式となる。
    $debugState = [System.IO.File]::ReadAllText($debugStateFilePath) | ConvertFrom-Json
} catch {
    [System.Console]::Error.WriteLine("Failed to parse footer debug-mode state: $($_.Exception.Message)")
    exit 2
}

# enabled が真偽値（boolean）でない場合は想定外の値とみなし、通常対話を妨げないよう安全側に倒して処理を終了する。
if ($debugState.enabled -isnot [bool]) {
    exit 0
}

# デバッグモードが有効でなければ、処理を終了
if ($debugState.enabled -ne $true) {
    exit 0
}

$lastAssistantMessage = $null

# トランスクリプトファイルに記録されているエージェントの発話（assistant.message）のうち、最後のものを取得する。
foreach ($line in [System.IO.File]::ReadLines($transcriptPath)) {
    if ([string]::IsNullOrWhiteSpace($line)) {
        continue
    }

    try {
        # -NoEnumerate を指定しないので、最外側が[]である配列であったとしても展開されJSON形式となる。
        $event = $line | ConvertFrom-Json
    } catch {
        [System.Console]::Error.WriteLine("Failed to parse transcript event: $($_.Exception.Message)")
        exit 2
    }

    if ($event.type -eq 'assistant.message' -and
        $event.data.content -is [string] -and
        -not [string]::IsNullOrWhiteSpace($event.data.content)) {
        $lastAssistantMessage = $event.data.content
    }
}

# この時点で $lastAssistantMessage に何も記録されていないということは、
# トランスクリプトにアシスタントの応答が存在しなかったことを意味するが、
# これは異常扱いとする。
#   1. なぜ異常事態と言えるのか
#     発動タイミング:
#       このhookは agentStop で実行される。通常の会話ターンであれば、
#       エージェントは何かしらの発話（assistant.message）を行って停止するため、
#       ログにアシスタントメッセージが1件も存在しない状態は本来あり得ない.
#     hookの目的:
#       このhookの責務は「アシスタントの最終応答を取得し、
#       デバッグフッタが正しく付与されているかを検証・強制すること」にある。
#       対象となる応答がそもそも存在しない以上、検査自体が成立しないため
#       exit 2（処理不能な異常）として扱うのが適切である。
#   2. 例外的に起こり得るエッジケース
#     完全にあり得ないわけではなく、以下のような特殊なケースでは発生し得る。
#       - ユーザーがプロンプトを送信した直後にキャンセル操作を行った場合
#       - エージェントがメッセージを1文字も出力する前に, 内部エラーや致命的例外で中断した場合
#     ただし、これらはいずれも「正常に対話・応答が完了した状態」ではないため、
#     hook側としては例外・異常事態として exit 2 で終了させる設計になっている.
if ([string]::IsNullOrWhiteSpace($lastAssistantMessage)) {
    [System.Console]::Error.WriteLine('No assistant response was found in the transcript.')
    exit 2
}

# 現在のJSTを取得
# & は呼び出し演算子で、変数や文字列に入っているパスやコマンド名を、コマンドとして実行する。
# 冒頭にドットがないので、スクリプトは新しいスコープで実行される。
$currentJst = & (Join-Path $PSScriptRoot 'get-current-jst.ps1')

# エージェントが使用したhookを取得（長さ0の場合は 'none' に置き換える）
$usedHooks = @(& (Join-Path $PSScriptRoot 'get-used-hooks.ps1') `
    -TranscriptPath $transcriptPath `
    -HooksPath (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
$resolvedUsedHooks = if ($usedHooks.Count -eq 0) {
    @('none')
} else {
    $usedHooks
}

# エージェントが使用したskillを取得（長さ0の場合は 'none' に置き換える）
$usedSkills = @(& (Join-Path $PSScriptRoot 'get-used-skills.ps1') `
    -TranscriptPath $transcriptPath)
$resolvedUsedSkills = if ($usedSkills.Count -eq 0) {
    @('none')
} else {
    $usedSkills
}

# フッタを整形して取得する。
$formattedFooter = & (Join-Path $PSScriptRoot 'format-response-footer.ps1') `
    -Hooks $resolvedUsedHooks `
    -Skills $resolvedUsedSkills `
    -CurrentJst $currentJst

# フッタが正しいかどうかを検証する。
$isFooterValid = & (Join-Path $PSScriptRoot 'test-response-footer.ps1') `
    -Response $lastAssistantMessage `
    -ExpectedHooks $resolvedUsedHooks `
    -ExpectedSkills $resolvedUsedSkills `
    -CurrentJst $currentJst

if (-not $isFooterValid) {
    # @" と "@ は、ヒアストリング(here-string)の開始と終了を表す。
    # 一見インデントミスに見えるが、ヒアストリングの開始と終了は行頭に置く必要があり、
    # 途中行もインデントがそのまま維持されてしまうため、この形が正しい。
    $output = @{
        decision = 'block'
        reason = @"
The final response footer is missing or incorrect. Preserve the response body. Replace any existing footer blocks with the exact block below; if no footer exists, append it to the end. The corrected response must contain exactly one footer block:

$formattedFooter
"@
    } | ConvertTo-Json -Compress

    # パイプライン出力（式の評価結果をそのまま流す形式）でも Copilot に返却可能だが、
    # [System.Console]::Out.WriteLine で明示的に出力することで、
    # 不要な出力の混入やフォーマット乱れを防ぎ、純粋な JSON 文字列を確実に渡すことができる。
    [System.Console]::Out.WriteLine($output)
}
