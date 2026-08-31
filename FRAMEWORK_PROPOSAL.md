# Zero Button Game Framework Proposal

> **文書の位置づけ:** 本書は、Zero Button Game向けフレームワークの将来構想・改良案をまとめた提案文書であり、現行仕様ではない。以下の要件・インターフェース・評価方法は、実装済みであることや実装予定を保証しない。現行動作の正本はREADME、コード、および `presets/current/` とする。

本書では、Zero Button Game向けの「問題生成・自動評価・難易度選別フレームワーク」を設計・改良する際の構想を示す。

Zero Button Gameとは、ユーザーが一切入力を行わず、

1. 問題を見る
2. 頭の中で解答・結果を予測する
3. 解法またはシミュレーションが自動実行される
4. その結果によって答え合わせを行う

というゲーム形式である。

対象は、迷路・回転パイプ・箱入り娘などの論理パズルだけでなく、重力・衝突・構造・流体・タイミングなどの物理／シミュレーション系ゲームも含む。

目的は、個別ゲームを一つ作ることではない。

「実行前には考えたくなり、実行後には答えに納得できる問題」を自動生成・評価・選別する汎用システムを構築することである。

# 1. 基本思想

良いZero Button Game問題は、

「Beforeでは不確実、Afterでは明快」

である。

理想的な体験は、

Before:
「どちらだろう」
「こうすれば解けそうだ」
「AかBのどちらかだと思う」

After:
「なるほど」
「確かにそうなる」
「そこがポイントだったのか」

である。

以下は区別すること。

## 良い難しさ

- 複数のもっともらしい候補が存在する
- 観察や推論によって答えに近づける
- 誤答にも理由がある
- 正解を見ると理解できる
- 解法や結果に因果関係がある

## 悪い難しさ

- 情報不足
- 隠れたルール
- ランダム性
- 過度なカオス
- 数値誤差への依存
- 不必要な複雑さ
- 正解を見ても理由が分からない

システムは「難しい」と「理解不能」を明確に区別しなければならない。

# 2. Game Genre Abstraction

各ゲームジャンルを共通インターフェースで扱う。

例：

Puzzle:

- Maze
- Pipe Rotation
- Klotski
- Sliding Puzzle
- Parking Escape
- One-Stroke Puzzle

Simulation:

- Ball Drop
- Gravity Maze
- Collision
- Bridge / Balance
- Domino Chain
- Pendulum
- Magnet
- Timing Gate
- Fluid Routing

各Genreは最低限以下を定義する。

- state schema
- configurable parameters
- generation constraints
- public rules
- observable information
- answer type
- terminal condition
- evaluator interface
- renderer
- replay / solution representation

新ジャンルをプラグイン形式で追加可能にする。

# 3. Truth Engine

正解を決定する機構をLLMから分離する。

論理パズルではSolverを用いる。

例：

Maze:
最短経路探索

Klotski:
状態空間探索

Pipe:
接続判定および最短操作探索

シミュレーション系ではSimulatorを用いる。

例：

Ball Drop:
物理シミュレーション

Bridge:
構造または簡略化された力学計算

Timing:
決定論的時間発展

共通インターフェースとして、

TruthEngine.evaluate(problem)

が、

- true answer
- solution / trajectory
- terminal state
- important events
- diagnostics

を返す構造にする。

真の答えはLLMに決めさせない。

# 4. Candidate Generator

各Genreについて大量の問題候補を生成する。

手法は交換可能にする。

- procedural generation
- constraint solving
- mutation
- evolutionary search
- parameter sweep
- random generation with constraints
- search guided by evaluation score

生成時点で「面白い問題」を直接作ろうとしすぎない。

基本構造は、

Generate
→ Validate
→ Solve / Simulate
→ Evaluate
→ Rank
→ Select

とする。

# 5. Validity Evaluation

最初に問題として成立しているかを機械判定する。

パズルなら、

- solvable
- terminal state reachable
- invalid shortcut absence
- required rules satisfied

などを見る。

シミュレーションなら、

- simulation terminates
- no numerical explosion
- answer is classifiable
- result stays in observable area
- no hidden randomness

などを見る。

Validityを満たさない候補は即時除外する。

# 6. Blind Prediction Evaluation

解答やシミュレーション結果を隠した状態で、複数の独立評価者に問題を解かせる。

評価者に与えてよいものは、

- initial state
- public rules
- visible problem image
- answer choices if applicable

のみとする。

評価者は、

- predicted answer
- confidence
- alternative probabilities
- short reasoning

を返す。

この評価から「事前の読みにくさ」を測定する。

ただし、正答率が低いほど高評価にはしない。

例えば三択で、

A: 46%
B: 41%
C: 13%

なら有望なHARD候補である。

一方、

A: 34%
B: 33%
C: 33%

なら、単なる情報不足や理解不能である可能性がある。

# 7. Solution Difficulty

論理パズルでは追加で以下を測定する。

- shortest solution length
- branching factor
- number of misleading branches
- dead-end depth
- required lookahead
- forced move ratio
- state-space size
- number of near-optimal wrong solutions

単純な最短手数だけを難易度としない。

特に、

「もっともらしいが失敗する経路」

の存在を重視する。

# 8. Near-Miss Richness

良い問題には「惜しい誤答」が存在する。

例えば、

Maze:
ゴールに近づくが途中で行き止まりになる経路

Pipe:
ほぼつながるが一箇所だけ不整合

Klotski:
数手進むと良さそうだが後で詰む手順

Ball Drop:
AにもBにも入りそうに見える配置

Bridge:
耐えそうに見えるが一箇所の荷重集中で崩れる構造

Near-Missを、

- number of plausible wrong answers
- distance from correct answer
- evaluator selection frequency
- visual plausibility

などで定量化する。

# 9. Result Clarity

解法やシミュレーションを実行した後、

「何が正解だったか」

が明確でなければならない。

複数のObserverに実行結果だけを見せ、

- final answer
- success / fail
- destination
- solved state

などを判定させる。

Observer間一致率をResult Clarityとして利用する。

Afterフェーズでは高い一致率を要求する。

# 10. Causal Legibility

正解を知った後、

「なぜそうなったか」

を短く説明できるかを評価する。

良い説明は、

- 少数の重要イベントで構成できる
- 見えている情報だけで説明できる
- 隠れた内部状態に依存しない
- 過度な数値計算を必要としない

ものとする。

例：

Maze:
「右側は途中で閉じているので左経路しか残らない」

Klotski:
「先に下段を空けないと主ブロックを下へ移動できない」

Ball Drop:
「最初の板で右へ反射し、その後中央ブロックに当たったためBへ入る」

Bridge:
「中央支点に荷重が集中し、そこで破断する」

説明の簡潔さと因果整合性をスコア化する。

# 11. Counterfactual Evaluation

問題の構造理解を検証するため、反実仮想テストを行う。

評価者に、

「結果を変えるための最小変更」

を提案させる。

パズルなら、

- 壁を1つ変える
- ピース位置を1つ変える
- パイプ1個を回転させる

シミュレーションなら、

- 板を5度回す
- ボール位置を少し動かす
- 重量を変更する
- 障害物を1つ除去する

その変更をTruth Engineで再評価する。

予測した因果関係が実際に成立すれば、

- causal understanding
- controllability
- structural interpretability

を高く評価する。

# 12. Robustness

問題が偶然の一点に依存していないかを見る。

シミュレーション系では、

- position ±1%
- angle ±1°
- velocity ±1%
- timing ±1%

などを摂動する。

論理パズルでは、

- 壁1個の変更
- 1マスの位置変更
- 小さなルール変更
- 不要要素の除去

などを試す。

目的は「何を変えても同じであること」ではない。

問題の核心が理解可能な構造として存在しているかを見ることである。

# 13. Minimality

特に論理パズルでは、

「不要な要素がどれだけ少ないか」

を評価する。

各要素を一つずつ除去または簡略化し、

- 答えが変わるか
- 難易度が大きく下がるか
- 問題としての特徴が失われるか

を確認する。

何を除いても同じ問題になる場合、その要素はノイズである。

可能な限り、問題の面白さに寄与する要素密度を高くする。

# 14. Visual Readability

短時間で、

- 何を見ればよいか
- 何が動くか
- ゴールは何か
- 何を予測すべきか

が理解できる必要がある。

評価項目：

- object count
- visual clutter
- overlap
- occlusion
- target visibility
- critical object saliency
- unnecessary decorations
- screen-space usage

複雑さと視認性を別々に扱う。

# 15. Surprise and Inevitability

Zero Button Gameの中心指標として、

Surprising but Inevitable

を定義する。

Surprise:
実行前に正解が自明ではない。

Inevitability:
実行後には結果が必然に見える。

この二軸を別々に評価する。

理想：

High Surprise
High Inevitability

除外候補：

Low Surprise
High Inevitability
→ 自明すぎる

High Surprise
Low Inevitability
→ 理解不能

Low Surprise
Low Inevitability
→ 面白くない

# 16. Difficulty Assignment

EASY / MEDIUM / HARDは、盤面サイズや物体数ではなく、

「人間が結果を予測する難しさ」

として定義する。

EASY:

- 答えに気づきやすい
- 正答率が高い
- ただし完全に自明ではない
- 解法を見ると気持ちよく確認できる

MEDIUM:

- 有力な誤答候補が存在する
- 一定の観察や先読みを必要とする
- 正答率が中程度

HARD:

- 複数の強い候補に予測が割れる
- 十分な思考を必要とする
- ただしAfterの納得感は高い

重要：

HARDだからといって、

- Result Clarity
- Causal Legibility
- Validity

を下げてはならない。

# 17. Evaluation Model

各候補に以下の指標を持たせる。

- Validity
- Prediction Difficulty
- Near-Miss Richness
- Result Clarity
- Causal Legibility
- Robustness
- Minimality
- Visual Readability
- Surprise
- Inevitability

パズル専用：

- Solvability
- Solution Uniqueness
- Solution Length
- Search Complexity

必要ならジャンル固有指標も追加する。

単一の総合点だけに依存しない。

以下を比較可能にする。

- weighted sum
- geometric mean
- hard thresholds
- Pareto frontier
- learned ranking model

Result Clarity、Causal Legibility、Validityには最低閾値を設ける。

# 18. Evaluator Separation

一つのLLMに、

「これは面白いか」

と聞いて終わらせない。

役割を分離する。

Generator
→ 問題生成

Truth Engine
→ 正解決定

Blind Predictors
→ 実行前予測

Solver Metrics
→ パズル構造解析

Observers
→ 実行後の結果判定

Causal Critics
→ 因果説明

Counterfactual Testers
→ 最小変更検証

Selector
→ 最終ランキング

評価者同士に答えを漏らさない。

特にBlind PredictorはTruth Engineの結果へアクセスしてはならない。

# 19. Anti-Patterns

以下を検出・除外する。

- 解けない
- 正解が定義できない
- 複数解が意図せず存在する
- 正解が一目で分かる
- 正解が見ても分からない
- 無意味な複雑化
- ランダム性頼み
- 微小誤差依存
- ルール外の知識が必要
- 隠れた情報が必要
- 動いているだけ
- 解法が長いだけ
- 物体数が多いだけ
- 誤答候補が存在しない
- 問題文より説明文の方が重要
- EASY / MEDIUM / HARDが単なるサイズ違い

# 20. Dataset

すべての候補について以下を保存する。

- genre
- seed
- parameters
- initial state
- rendered problem
- true answer
- solution / trajectory
- solver metrics
- predictor responses
- predictor confidence
- prediction distribution
- prediction entropy
- near-miss candidates
- result clarity
- causal explanations
- counterfactual results
- robustness results
- visual metrics
- final scores
- assigned difficulty
- rejection reasons

JSONLまたはSQLiteなど、後から分析可能な形式を使う。

# 21. Human Calibration

自動評価を絶対視しない。

人間評価を少量収集できるようにする。

最低限、

- fun
- difficulty
- clarity
- satisfaction
- surprise
- fairness
- “aha” feeling
- share willingness

を1〜5で評価可能にする。

さらに可能なら、

- predicted answer
- solving time
- confidence
- actual correctness

も保存する。

自動評価と人間評価の相関を測定し、評価関数を継続的に改善する。

# 22. Diversity Evaluation

高スコア問題だけを選ぶと、似た問題へ収束する可能性がある。

以下の多様性を測定する。

- structural diversity
- visual diversity
- solution-pattern diversity
- outcome diversity
- difficulty diversity

採用時には、

scoreだけでなくnoveltyも考慮する。

同一解法パターンの問題を連続採用しない仕組みを用意する。

# 23. Development Strategy

最初から全ジャンルを実装しない。

まず既存の論理パズル1種で評価基盤を検証する。

推奨：

Maze

理由：

- Truth Engineを正確に作れる
- 難易度指標を計算しやすい
- Blind Predictionとの比較が容易
- Near-Missを定義しやすい
- 人間評価との比較もしやすい

次に、

Ball Drop

を追加する。

これにより、

Solver-based Genre

と

Simulation-based Genre

の両方が、同じEvaluation Framework上で動くことを確認する。

# 24. Coding Requirements

- Genre固有コードと評価コードを分離する
- Truth EngineとRendererを分離する
- seedによる完全再現
- headless execution対応
- batch generation対応
- batch evaluation対応
- CLI提供
- unit tests
- integration tests
- evaluation report生成
- HTMLまたは簡単なdashboard
- LLM/VLM evaluatorはadapter化
- 特定APIへ依存しない
- APIキーなしでもTruth Engineとrule-based metricsは動作する

# 25. CLI Example

generate maze --count 10000

generate ball-drop --count 10000

solve <candidate-id>

simulate <candidate-id>

evaluate <candidate-id>

rank --genre maze

select --difficulty easy --count 3

select --difficulty medium --count 3

select --difficulty hard --count 3

render <candidate-id>

report <candidate-id>

# 26. 最初に行うこと

いきなり全コードを書かない。

最初に、

1. 要件整理
2. 共通アーキテクチャ設計
3. Genre interface設計
4. Truth Engine interface設計
5. Evaluation pipeline設計
6. データモデル設計
7. 各評価指標の数式または疑似コード定義
8. Maze MVP設計
9. Ball Drop追加時に変更不要となる境界設計
10. テスト戦略

を提示する。

その後、実装を開始する。

常に以下を問うこと。

「これは本当に難しいのか、それとも分かりにくいだけか」

「正解を見ることで理解が増えるか」

「この要素は問題の面白さに寄与しているか」

「実行前の迷いと、実行後の納得が両立しているか」

最終目的は、パズルを大量生成することでも、物理シミュレーションを大量生成することでもない。

最終目的は、

「見るだけで思考が始まり、答えを見ることで納得が生まれるZero Button Game問題」

を自動的に発見・選別できるシステムを作ることである。
