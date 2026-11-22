# read_definitions utility README

## 役割
hookの定義ファイル（JSON）を読み、値を検証して使える形（文字列のリスト、コンパイル済みの正規表現）にする。定義ファイルを読むhookとutilityが共有し、読み方・検証の基準・エラーの書き方を1か所で管理する。

|関数|役割|
|--|--|
|`read_definitions`|定義ファイルを読み、最上位がオブジェクト（または空でない配列）か確かめる|
|`is_non_blank_string_list`|値が、空白以外を含む文字列だけのリストかを判定する|
|`build_regex_matcher`|正規表現の文字列を1つコンパイルする|
|`build_regex_matchers`|正規表現の文字列のリストをコンパイルする|
|`get_string_list`|キーの値を、空白以外を含む文字列のリストとして取り出す|
|`get_regex_matchers`|キーの値を、正規表現のリストとして取り出す|
|`get_regex_matcher`|キーの値を、1つの正規表現として取り出す|
|`get_named_regex_matchers`・`build_named_regex_matchers`|`name` と `pattern` を持つオブジェクトのリストを、(名前, 正規表現) のリストとして取り出す|

## 読み方と検証の規則
- 定義ファイルはUTF-8のJSONとして読む。UTF-8 BOMは許容する。
- ファイルでないパスは `OSError`、JSONとして解析できない内容・最上位の型の違い・不正な値・不正な正規表現は `ValueError` にする。hookはこれを定義の読み込みエラーとして異常終了する。
- 文字列のリストは、空白だけの要素を認めない。空のリストは、呼び出し元が `allow_empty_list` を指定したときだけ認める（除外パターンなど、なくてもよい定義）。
- エラーには、定義の名前（`label`）とキーを `<キー> in <定義の名前>` の形で示す。不正な正規表現は `invalid regular expression in <場所>: <理由> (pattern: <パターン>)` と示し、場所には、文字列のリストではリストでの添字（`(index 0)` など、0始まり）を、名前付きの定義では名前を添える。
- 正規表現のフラグ（`re.MULTILINE` など）は、照合のしかたを決める呼び出し元が指定する。
