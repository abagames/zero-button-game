# Visual Design: Zero Button Game

**文書の役割。** 本書は7種の**現行視覚仕様の正本**です。配色、layout、safe area、可読下限、frame 0、feedback、種別の受入意図を扱います。

| 節  | 種      | 状態                                             |
| --- | ------- | ------------------------------------------------ |
| §1  | pipes   | 記載済み（旧 `Visual Design: Pipes GIF` の全文） |
| §2  | lights  | 記載済み（2026-08-24 追加）                      |
| §3  | fold    | 記載済み（2026-08-24 追加）                      |
| §4  | parking | 記載済み（2026-08-25 追加）                      |
| §5  | packing | 記載済み（2026-08-25 追加）                      |
| §6  | maze    | 記載済み（2026-08-25 追加）                      |
| §7  | mosaic  | 記載済み（2026-09-01 追加）                      |

共通契約（全種）: canvas 720×720、safe area (36, 36, 684, 684)、procedural RGB24 描画のみで外部 bitmap asset を使わない、frame 0 では盤面を描かない（fade `min(1.0, frame / appearance)`）、題字は fade 非適用。

**仕様と実行値。** 視覚意図と採用仕様の文書正本は本書です。実際に機械検査される寸法・色・可読下限は各 `src/zero_button_game/*_render.py`（mazeは `render.py`）と `registry.py` の `visual_contract` / `render_contract_checks` に実装されています。変更時はコード・検査・本書を同じ変更で同期し、本書だけを書き換えないでください。

## 1. pipes

**Concept-Derived Visual Tags**: `render-crisp-modular-plumbing`, `motionviz-mechanical-quarter-turn`, `motionviz-traveling-pressure-pulse`

### 1.1 Visual Concept

「静かな配管図が、機械的な四分回転を経て一本の発光回路として起動する」。装飾よりconnector幾何を優先し、問題、操作、接続成功を文字なしでも区別できるようにする。

### 1.2 Color Palette

| Role                  | Color           | Hex       | Usage                              |
| :-------------------- | :-------------- | :-------- | :--------------------------------- |
| Background            | near-black navy | `#10151D` | 外周と最大面積                     |
| Pipe structure        | pale steel      | `#D8E2EA` | 未接続pipeの太線                   |
| Source / flow outer   | aqua            | `#43D9C2` | START marker、flow外周、front ring |
| Goal / rotation focus | amber           | `#FFD166` | GOAL diamond、回転対象ring/tick    |
| Flow core             | white           | `#F5F8FA` | flow二重線の芯、front dot          |

palette roleは `pipes_render.PIPE_VISUAL_ROLES` を正本とし、rendererと検査が同じ値を参照する。

### 1.3 Object Rendering Specifications

- 各cellは3×3で160px、4×4で120px。strict下限96px。
- pipe connectorは16px steel strokeと中心hubで描き、elbow/straight/junctionを輪郭だけで識別可能にする。
- STARTはaqua circle＋白い進行arrow、GOALはamber diamond＋白い中心dot。色を失っても形が異なる。
- 回転中pieceはconnector自体を連続角で回し、amber ringと外向きtickを重ねる。ringだけで「対象」、斜めconnectorで「途中角」を示す。
- flowはaqua 12px外周＋white 5px芯の二重線、先端にwhite dot＋aqua ringを置く。未接続steel pipeとの差を色、線構造、動く先端の三重で示す。

### 1.4 Background & Environment

720×720固定。中央の552×578 panel内に480px boardを置き、背景textureは使わない。grid境界は2px muted線に抑え、connector中心の視認を妨げない。title、phase、START/GOAL labelは盤外へ置く。

frame 0では盤面を描かない。fadeは `min(1.0, frame / appearance)` で、frame 0のpanel、cell、grid線、connectorは背景と同色になる。title、START/GOAL marker、START/GOAL label、THINKバーのtrack、phase文字はfade非適用なのでframe 0でも見える。静止サムネとしてframe 0が無制限に表示される配信面で、制限時間に依存する難易度が無効化されるのを防ぐための措置であり、appearance終了後の見た目は従来どおりである。

### 1.5 Feedback Effects

| Event            | Visual Response                                                   | Tag Reference            |
| :--------------- | :---------------------------------------------------------------- | :----------------------- |
| Frame 0          | 盤面非表示。title / markers / labels / THINK track / phase のみ   | crisp modular plumbing   |
| Problem ready    | scrambled steel connectors only; solution-dependent highlightなし | crisp modular plumbing   |
| Piece rotation   | connector continuous rotation＋ring＋direction tick               | mechanical quarter-turn  |
| Final connection | rotation ring消失、STARTにflow front出現                          | traveling pressure pulse |
| Route solved     | double-stroke flowがSTART→GOALの成立経路だけを進む                | traveling pressure pulse |
| Clear            | GOALへ到達した経路flowを保持し、GOAL diamondとCLEARを併用         | crisp modular plumbing   |

### 1.6 Relationship with Visual Tags

`crisp modular plumbing`は均一strokeとgrid構成、`mechanical quarter-turn`は連続回転とring/tick、`traveling pressure pulse`はstateを変えないflow frontへ変換した。全効果はprocedural RGB24描画で、外部bitmap assetは使用しない。

### 1.7 AI-Generated Look Suppression Rules

#### 1.7.1 Visual Hierarchy Rules

- Protagonist: STARTから進むwhite-core aqua flow front。
- Threat: 誤方向を向いたsteel connectorと境界leakの可能性。
- Reward: START→GOALの正解経路を覆いdiamondへ入る二重線flow。
- 2-second recognition check: START circle/arrow、GOAL diamond、pipe connector、回転ringが3×3/4×4 contact sheetで識別できること。

#### 1.7.2 Limits on Familiar Template Symbols

- Adopted familiar elements (max 2): START arrow、GOAL diamond。
- Replaced unique element: 一般的なparticle burstを使わず、配管網そのものを満たすpressure frontを成功現象にする。

#### 1.7.3 UI-Independent Feedback

| Event            | Non-UI visual response                       | Intensity (Low/Med/High) |
| :--------------- | :------------------------------------------- | :----------------------- |
| Piece rotation   | connector geometry rotation＋focus ring/tick | Med                      |
| Final connection | focus ring消失＋source front発生             | High                     |
| Goal reached     | double-stroke flowが全網とdiamondへ到達      | High                     |

#### 1.7.4 Composition and Gaze Guidance

- Initial focal point: 左上START circle/arrow。
- Visual flow: GOAL側pieceの回転からSTART側の最終接続へ戻り、STARTから成立経路だけをたどってGOALへ進む。
- Anti-center-clutter implementation: textureとparticleを排除し、grid線をmuted、状態効果を現在pieceまたはflow済みpipeだけへ限定する。

### 1.8 Asset Handoff and Acceptance

- Proceduralのまま維持: grid、connector、markers、ring/tick、flow、bitmap font。
- 将来asset化する場合もconnector中心線とcanonical maskをRulesと共有し、画像側の見た目だけで接続方向を決めない。
- 受入条件: `reveal_start` より前の全frameでalternate-solution pixel hash一致（かつ `reveal_start` では不一致）、cell≥96px、connector≥16px、flow core≥5px、回転Action semantic snapshot一致、flowはsolve後のみ開始しresult末尾でcanonical START→GOAL pathと一致。未使用distractor connectorにはflowを描かない。

## 2. lights

**正本**: `src/zero_button_game/lights_render.py`（定数・描画）と `registry.LightsPlugin.visual_contract` / `render_contract_checks`（契約と検査）。以下の数値はすべてそこから取っている。

### 2.1 Visual Concept

「暗い格子上の押下点へフォーカスが閉じ、そこから十字が広がり、押した順番を盤上へ残したまま最後に盤全体が灯る」。灯は2状態しか持たないので、**押下という行為と順序の形はフォーカス括弧・順序番号バッジ・十字パルスが担い、盤面の色は結果だけを表す**。

### 2.2 Layout（採用案 C）

| 要素         | 定数                                                                       | 数値                                                                    |
| ------------ | -------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| canvas       | `LightsRenderer.width/height`                                              | 720×720                                                                 |
| panel        | `PANEL_BOX`                                                                | (44, 70, 676, 660) ＝ `semantic_bounds`                                 |
| セル         | `CELL_PX`                                                                  | 96px（gap 5、edge 4）。可読下限96と同値                                 |
| 盤           | `BOARD_ORIGIN` (120, 96)、5×4                                              | 480×384px、範囲 x 120–600 / y 96–480                                    |
| ゴールタイル | `GOAL_TILE_ORIGIN` (96, 508)、3×3×`MINI_CELL_PX` 32                        | 96px角（x 96–192）、panel は pad 14 で (82, 494, 206, 622)              |
| ルールタイル | `RULE_TILE_ORIGIN` (300, 508)、3×3 タイル2枚＋矢印 `RULE_TILE_ARROW_PX` 52 | 幅 32×3 + 52 + 32×3 = 244px（x 300–544）、panel は (286, 494, 558, 622) |
| 進行バー     | `PROGRESS_BAR`                                                             | (120, 626, 600, 636)、盤幅と一致                                        |
| 題字         | `TITLE_ORIGIN`                                                             | (84, 30)、scale 4                                                       |
| phase 文字   | —                                                                          | y 674、中央寄せ                                                         |

safe area (36, 36, 684, 684) に対する余裕は、盤が左84 / 上60 / 右84 / 下（凡例帯が続く）、凡例 panel が左46 / 右126 / 下62 px である。`render_contract_checks` は盤と凡例2 panel が safe area 内にあること、かつ**凡例 panel が盤の下端より下にある**ことを実測で検査する（`board_and_legend_within_safe_area`）。`MINI_CELL_PX` は 28px 以上であることを検査する（`minimum_legend_cell_size`）。

**却下案（一次記録が残っていないため、現行定数からの再導出である）。**

- **却下: 凡例を盤の横へ置く A 案。** 幅は safe area の 648px しかない。ルールタイル帯だけで 244＋pad 28 = 272px を占めるので盤に残るのは 376px、5列では 1セル 75px となり **96px の可読下限を割る**。凡例を横に置く限り 5×4 盤は成立しない。この却下は数値で確定する。
- **却下: 凡例を盤の上（題字と盤の間）へ置く B 案。** 縦方向には収まるが、視線が「題字 → ルール → 盤」の順に凡例を必ず通り、盤の初見までの距離が伸びる。凡例は**必要なときに参照する補助**であって導線の主役ではない、という判断で下へ移した。**これは判断であり、A 案のような数値上の不可能性ではない。**
- **採用: C 案（盤を上、凡例帯を下）。** 盤を上端寄り（y 96）に置いて 96px セルを確保し、凡例帯を y 508 起点の一列に並べる。進行バー（y 626）と phase 文字（y 674）まで含めて safe area 内に収まる。

### 2.3 凡例タイル（ゴールタイルとルールタイル）

凡例は**解に依存せず問題にのみ依存する**。`legend_geometry()` は `scene.puzzle` しか読まず、`_draw_legend()` は押下集合を一切参照しない。

- **ゴールタイル**: 3×3 の全灯ミニ盤に amber のコーナー枠を付け、「全部点いた状態が目標」を示す。
- **ルールタイル**: 全消灯のミニ盤に cursor 色のリングを1つ（中央 (1,1) を押す）→ 白い矢印 → plus 5セルが灯ったミニ盤、で「押すと十字が反転する」を示す（`LEGEND_PLUS`）。

**中立性オラクルとの関係**: `alternate_lights_scene`（押下順の巡回シフト）に対して `legend_geometry()` の戻り値が完全一致することを `render_contract_checks` が検査し（`legend_solution_independent`）、同時に reveal 前の全 frame で `semantic_snapshot` が一致することも検査する。`visual_contract` は `legend_solution_dependent: False` を宣言する。つまり凡例は pre-reveal 中立性の例外ではなく、**中立性検査を通ったうえでルールを伝える経路**である。

### 2.4 文字

- 題字 `ALL LIGHTS ON`。当初の `LIGHTS OUT` は目標（全点灯）と矛盾し、隔離エージェントによる意図可読性測定で「全消灯が目標」と誤読された。変更後の再測定で目標・ルール・リスクがすべて復元された（計画書 §21.10）。
- phase 文字 `THINK / PRESS / LIT / CLEAR`。`LIT` と `CLEAR` は amber（`lit`）、他は白。
- 同梱 bitmap font（`render.FONT`）は**大文字・数字・`- . :` のみで、`J` と `Q` を持たない**。文字列は `_text()` が `upper()` する。題字と phase 文字はこの制約内に収まっている。

### 2.5 Color Palette

| Role          | 定数                             | RGB                              | Usage                                               |
| :------------ | :------------------------------- | :------------------------------- | :-------------------------------------------------- |
| Background    | `background`                     | `#10151D`（`render.BACKGROUND`） | 外周                                                |
| Panel         | `panel`                          | `render.PANEL`                   | 盤と凡例を載せる面                                  |
| Legend panel  | `legend_panel`                   | (25, 34, 45)                     | 凡例タイルの下敷き。panel よりわずかに暗い          |
| Light off     | `unlit`                          | (34, 46, 59)                     | 消灯セル                                            |
| Light on      | `lit`                            | (255, 209, 102) amber            | 点灯セル、ゴール枠、LIT/CLEAR 文字、result のリング |
| Cell edge     | `cell_edge`                      | (150, 172, 192)                  | セル輪郭（4px）                                     |
| Cursor        | `cursor`                         | (67, 217, 194) aqua              | ルール凡例の押下リング、THINK バー                  |
| Focus / badge | `focus` / `badge` / `badge_done` | aqua / dim aqua                  | 押下点固定の括弧、現在・押下済みの順序番号          |
| Pulse         | `pulse`                          | (67, 217, 194) aqua              | 押下セルから四方へ伸びる十字（太さ7）               |

役割の正本は `lights_render.LIGHTS_VISUAL_ROLES` で、renderer と契約が同じ値を参照する。灯の on/off は amber と暗い青灰で、**明度差も色相差もある**（色覚に依存しにくい）が、形は変わらない。

### 2.6 state_change_not_color_only: False

`visual_contract` は `state_change_not_color_only: False` を**明示的に宣言する**。灯は本質的に2状態で、状態変化に形を伴わせようがないためであり、parking / packing が掲げた「状態変化を色だけに依存させない」原則の唯一の例外である。原則を黙って破らず宣言として成果物へ残す扱いにした。形状情報は次が担う。

- **フォーカス括弧**: 押下セル中心に固定され、半幅78pxから44pxへ閉じる四隅の aqua 括弧＋中心リング。「いまどのセルを押しているか」。セル間は移動しない。
- **順序番号バッジ**: 各押下セル左上の番号。現在の押下は明るい aqua、押下後は dim aqua で残り、`LIT` / `CLEAR` の全区間でも消えない。全バッジを番号順に読むと押下順を復元できる。
- **十字パルス**: 押下セル中心から四方へ、`progress` に比例して `inner = max(40, radius*0.45)` から `radius = progress * 96` まで伸びる線。「plus 5セルが反転した」という**作用の形**。
- **灯のクロスフェード**: 反転する plus 5セルだけが `progress` で色補間される（`_mix`）。どのセルが変わったかが動きで分かる。

### 2.7 Feedback Effects と frame 0

| Frame 区間                   | 表示                                                                                                                                                                                                          |
| :--------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| frame 0                      | fade = `min(1.0, frame / appearance)` = 0。panel・盤・凡例は背景と同色になり見えない。**題字 `ALL LIGHTS ON` だけが fade 非適用**で読める。THINK バーの track（`MUTED`）と phase 文字も fade を通さず描かれる |
| appearance〜reveal 前        | 初期盤・凡例が現れる。`semantic_snapshot` は押下集合を参照せず `puzzle.initial` だけを返す。THINK バーが伸びる                                                                                                |
| PRESS（reveal〜solve_end）   | 押下点固定のフォーカス括弧が閉じ、十字パルスが伸び、plus 5セルがクロスフェード。完了した押下の番号バッジは残る                                                                                                |
| LIT（solve_end〜result_end） | 盤外周に amber のリングが `sin` で 10〜16px 呼吸する。盤は全灯で、全順序バッジを保持                                                                                                                          |
| CLEAR（result_end〜）        | 全灯・全順序バッジを保ったまま phase 文字が `CLEAR`                                                                                                                                                           |

frame 0 で盤を描かないのは、静止サムネとして frame 0 が無制限に表示される配信面で制限時間依存の難易度が無効化されるのを防ぐためである（計画書 §21.6、pipes と同じ方針）。

### 2.8 solve 動き検査の通し方

`validation.py` は solve 区間で **4 frame ごとに frame が変化すること**を要求する（静止200ms超の禁止）。トグル系は押下の瞬間以外は盤面が動かないため、2.6 のフォーカス括弧・十字パルス・灯のクロスフェードで連続変化を作る。`render_contract_checks` はさらに厳しく、押下点、括弧の半幅、パルス半径、完了バッジ数を量子化した marker signature が solve 区間の**連続する全 frame**で変わることを検査する（`press_marker_animates_every_frame`）。順序バッジの残置は `tests/test_lights_render.py` が全bandの `LIT` / `CLEAR` 開始・末尾で検査する。

### 2.9 Acceptance

- reveal 前の全 frame で alternate scene（押下順の巡回シフト）と pixel hash 一致、`reveal_start` では不一致。
- セル 96px 以上、凡例ミニセル 28px 以上、盤と凡例2 panel が safe area 内、凡例が盤の下。
- 凡例 geometry が alternate scene で完全一致（`legend_solution_independent`）。
- 描画された押下が `toggle_cell` Action と一致（`toggle_action_rendering`）。
- 全bandの `LIT` / `CLEAR` 開始・末尾で、全順序バッジから押下順を復元できる。
- 最後の押下の**後に**初めて全灯になる（`lit_after_last_press`）。
- 押下集合が GF(2) で一意（`unique_press_set`、nullity 0）。
- `board_lit` と `rule_legend` の visual cue が `state_mutation: False`（凡例はさらに `solution_dependent: False`）を宣言している。

## 3. fold

**正本**: ruleset は `fold.FOLD_RULESET`（`fold-to-target-exact-v1`）、生成・goal述語は `src/zero_button_game/fold.py`、描画は `src/zero_button_game/fold_render.py`、視覚契約は `registry.FoldPlugin.visual_contract` / `render_contract_checks`。以下のレイアウト数値は描画実装から取っている。

### 3.1 Visual Concept

「方眼紙の一部が着色されていて、破線の箱が描いてある。紙を折るたびに外形が縮み、重なった層が入れ子の輪郭として見え、最後に紙の形が破線の箱と一致して、各セルが着色セルでちょうど1回ずつ埋まる」。着色セル同士の重なりは禁止で、色数は目標面積と同数である。**状態変化を運ぶのは色ではなく形**である（外形の狭義単調減少、層深さの入れ子輪郭、フラップの実角度回転）。

### 3.2 Layout（採用案 C）

| 要素                | 定数                                                                     | 数値                                                                          |
| ------------------- | ------------------------------------------------------------------------ | ----------------------------------------------------------------------------- |
| canvas              | `FoldRenderer.width/height`                                              | 720×720                                                                       |
| panel               | `PANEL_BOX`                                                              | (118, 56, 600, 656) ＝ `semantic_bounds`                                      |
| セル                | `CELL_PX`                                                                | 68px（gap 3、層の内側 inset 5）。可読下限 `MIN_CELL_PX` 64                    |
| 盤（紙）            | `BOARD_ORIGIN` (156, 74)、6×6                                            | 408×408px、範囲 x 156–564 / y 74–482                                          |
| 目標の破線枠        | `target_box()`                                                           | 盤座標そのまま（1格子 = 68px）。問題のみに依存                                |
| ゴール凡例          | `GOAL_TILE_ORIGIN` (148, 552)、3×2×`MINI_CELL_PX` 38                     | タイル 114×76（x 148–262）、panel は (134, 508, 276, 642)                     |
| ルール凡例（3段図） | `RULE_TILE_ORIGIN` (340, 552)、`RULE_ARROW_PX` 30、`STANDING_FLAP_PX` 18 | 幅 76 + 30 + 56 + 30 + 38 = 230px（x 340–570）、panel は (326, 508, 584, 642) |
| 進行バー            | `PROGRESS_BAR`                                                           | (156, 58, 564, 68)、盤幅と一致                                                |
| 題字                | `TITLE_ORIGIN`                                                           | (120, 22)、scale 4、`FOLD TO FILL THE BOX`                                    |
| phase 文字          | `PHASE_BASELINE`                                                         | y 672、中央寄せ                                                               |

safe area (36, 36, 684, 684) に対して、盤・凡例2 panel・`PANEL_BOX` がすべて内側にあることを `tests/test_fold_render.py` の `test_layout_stays_inside_the_safe_area` が実測で検査する。`CELL_PX >= MIN_CELL_PX`（68 ≥ 64）と `MINI_CELL_PX >= MIN_MINI_CELL_PX`（38 ≥ 32）も同じテストが見る。

**却下案（一次記録が `studies/` に残っていないため、現行定数からの再導出である）。**

- **却下: 凡例を盤の横へ置く A 案。** safe area の幅は 648px。ルール凡例の panel だけで 258px を占め、ゴール凡例との間隔（現行 50px）を足すと盤に残るのは 648 − 258 − 50 = 340px となり、6列では 1セル 56.7px で **可読下限 64px を割る**。逆に 68px セル（408px）を保つと必要幅は 408 + 50 + 258 = 716px で safe area を 68px 超える。**3段のルール凡例を横へ置く余地はゼロである。**この却下は数値で確定する。
- **却下: 凡例帯を題字と盤の間へ置く B 案。** 実装時の判断は「最大盤面で safe area を約10px 侵犯した」というものだったが、**現行定数からの再導出ではその数値を再現できない**（凡例帯の高さ 134px と現行の余白を保って積むと盤は y 234–642 となり、safe area 下端 684 の内側に収まる。ただし `PANEL_BOX` 下端 656 までの余裕は 14px しか残らず、進行バーと phase 文字の帯を含めると余裕が消える）。**したがってここは「余白が消えるため却下」という判断として記録し、10px という数値は採らない。**
- **採用: C 案（紙を上、凡例帯を下）。** 紙を y 74 に置いて 68px セルの 6×6 を確保し、凡例帯を y 508 起点の一列（ゴール凡例 → ルール凡例）に置く。進行バー（y 58）と phase 文字（y 672）まで含めて成立する。

### 3.3 凡例タイル（ゴール凡例とルール凡例）

凡例は**解に依存せず問題にのみ依存する**。`legend_geometry()` は `scene.puzzle` しか読まず、`_draw_legend()` は fold class を一切参照しない。

- **ゴール凡例**: 3×2 の着色ミニ盤を amber の破線枠で囲み、`GOAL` のラベルを付ける。「破線の箱が中身まで埋まった状態が目標」を示す。
- **ルール凡例（3段図）**: `FOLD` のラベルの下に、**折る前**（左半分が白紙・右半分が着色、折り線に白い縦線と半回転の円弧矢印）→ **折り線に立ったフラップ**（`_quad` で描く台形）→ **折った後**（2層になったミニ盤）を白い矢印でつなぐ。

**中立性オラクルとの関係**: `alternate_fold_scene`（軸グループの順序交換）に対して `legend_geometry()` と `target_box()` の戻り値が完全一致することを `render_contract_checks` が検査し（`legend_solution_independent`）、reveal 前の全 frame で `semantic_snapshot` が一致することも検査する。presentation 側も `rule_legend` / `target_outline` の visual cue に `state_mutation: False` / `solution_dependent: False` を宣言し、それを `legend_cue_neutral` が検査する。

### 3.4 可読性測定（2ラウンド）

`gating-intent-legibility` スキルは**この環境に存在しない**ため、同等のプロトコル（設計・ソースを見せず画面だけを見せ、目標・操作・リスクを言わせる）を手動で実行した。**各条件1名のみ**である。

- **Round 1**: ルール凡例が「タイル → 円弧矢印 → タイル」の2段だったため、円弧が**回転（rotation）と誤読された**。折り紙ではなくタイル回転パズルに見える、という読みである。
- **改修**: 3段図（折る前 / 折り線に立ったフラップ / 折った後）へ変更し、`GOAL` と `FOLD` のラベルを追加した。中段のフラップを描くために `render.py` へ `_quad`（凸四辺形のスキャンライン塗り）を追加した。
- **Round 2**: 題字を隠した条件でも目標と操作が復元され、rotation との誤読は消滅した。
- **適用範囲**: 各条件1名の測定であり、人間の母集団へ一般化しない。一次記録ファイルは `studies/` に無い。

### 3.5 Color Palette

| Role                | 定数                          | RGB                            | Usage                                                                |
| :------------------ | :---------------------------- | :----------------------------- | :------------------------------------------------------------------- |
| Background          | `background`                  | `render.BACKGROUND`            | 外周                                                                 |
| Panel               | `panel`                       | `render.PANEL`                 | 盤と凡例を載せる面                                                   |
| Legend panel        | `legend_panel`                | (25, 34, 45)                   | 凡例の下敷き                                                         |
| Board               | `board`                       | (20, 27, 36)                   | 紙が載る盤面                                                         |
| Paper               | `paper`                       | (78, 96, 116)                  | 白紙セル                                                             |
| Paper edge          | `paper_edge`                  | (168, 188, 206)                | 紙の輪郭・フラップの縁                                               |
| Colour              | `colour`                      | (67, 217, 194) aqua            | 着色セル、立っているフラップ                                         |
| Stack shadow / edge | `stack_shadow` / `stack_edge` | (12, 17, 24) / (110, 132, 152) | 層の入れ子輪郭（深さ表現、最大 `MAX_DRAWN_STACK_STEPS` 3段）         |
| Crease              | `crease`                      | (245, 248, 250)                | 折り線                                                               |
| Target              | `target`                      | (255, 209, 102) amber          | 目標の破線枠、`GOAL` ラベル、FULL のリング、FULL/CLEAR の phase 文字 |
| Progress            | `progress`                    | (67, 217, 194)                 | THINK バー                                                           |

役割の正本は `fold_render.FOLD_VISUAL_ROLES` で、renderer と契約が同じ値を参照する。

### 3.6 state_change_not_color_only: True

lights（§2.6）が `False` を宣言する唯一の種であるのに対し、fold は `True` を主張できる。根拠は3つである。

- **外形の狭義単調減少**: 折るたびに紙の外形が必ず小さくなる（`fold_result_extent` が「動く側 ≤ 静止側」に限定するため、紙は長方形のまま縮む）。`render_contract_checks` は面積が毎回狭義に減ること、かつ最終層深さが2以上であることを合わせて検査する（`outline_and_depth_carry_state`）。
- **入れ子アウトライン**: 層の深さを内側へ inset 5px の輪郭を重ねて描く（`_draw_stacked_cell`）。層数が色ではなく線の本数で読める。
- **実角度回転**: フラップは折り進行に応じた実角度で立ち上がり倒れる（`_flap_point`、`LIFT_GAIN` 0.09）。連続する全 frame で角度が単調に進むことを `fold_angle_advances_every_frame` が検査する。

なお**層の順序そのものは描画から見えない**。goal は層順ではなく各セルへの着色射影数を読み、正当な解では必ず1であるため、描画は層内の着色有無の和集合にしている（`visible_value`）。層順は `FoldState` の hash（Action の `precondition.state_hash` 連鎖）に効くため、`tests/test_fold_logic.py` の `FoldLayerOrderTests` が非対称な 4×1 短冊の層タプルを直接 assert して検証する。

**実装中に見つかった欠陥**: 当初は「最上層の色」を描いており、ゴールフレームで 6セル中 2セルしか着色されず、各目標セルをちょうど1回覆う成功条件と成功画像が矛盾していた。描画を層の和集合に変えて解決した（計画書 §21.11）。

### 3.7 Feedback Effects と frame 0

標準の THINK 区間（frame 0 から `reveal_start`）は easy / medium / target で 4.0 / 6.0 / 6.0秒。round 5 に基づく**単独評価者の現行個人内標準**として正式採用しているが、一般利用者向けに最適化済みとは主張しない。追加の複数seed掃引は追跡課題であり採用前の必須条件ではない。複数seedで target の時間不足または medium の余裕過多が繰り返される、medium / target の識別性不足が実利用で問題になる、生成条件・折り数・提示方式を変更する、または一般利用者向け適正を主張する場合に再検討する。比較・個別調整用の `--thinking-time` は維持する。

| Frame 区間                     | 表示                                                                                                                     |
| :----------------------------- | :----------------------------------------------------------------------------------------------------------------------- |
| frame 0                        | fade = 0。panel・盤・紙・凡例は背景と同色で見えない。**題字 `FOLD TO FILL THE BOX` だけが fade 非適用**で読める          |
| appearance〜reveal 前（THINK） | 平らな紙・破線の目標枠・凡例が現れる。`semantic_snapshot` は fold class を参照せず初期状態だけを返す。THINK バーが伸びる |
| FOLD（reveal〜solve_end）      | 動く側を消して代わりにフラップを立て、角度を進めながら倒す。外形が縮み層が増える                                         |
| FULL（solve_end〜result_end）  | 目標枠の外側に amber のリングが `sin` で 10〜16px 呼吸する                                                               |
| CLEAR（result_end〜）          | phase 文字が `CLEAR`                                                                                                     |

frame 0 で盤を描かないのは、静止サムネとして frame 0 が無制限に表示される配信面で制限時間依存の難易度が無効化されるのを防ぐためである（計画書 §21.6、pipes / lights と同じ方針）。

### 3.8 Acceptance

- reveal 前の全 frame で alternate scene（軸グループの順序交換）と pixel hash 一致、`reveal_start` では不一致。
- セル 68px 以上（下限64）、凡例ミニセル 38px 以上（下限32）、盤・凡例2 panel・`PANEL_BOX` が safe area 内。
- 凡例 geometry と目標枠が alternate scene で完全一致（`legend_solution_independent`）。
- 描画された折りが `fold_along` Action と一致（`fold_action_rendering`）、フラップ角度が全 frame で進む（`fold_angle_advances_every_frame`）。
- **最後の折りの後に**初めて目標が埋まる（`filled_after_last_fold`）。
- fold class が完全列挙で一意（`unique_fold_class`、`proof: complete-fold-class-enumeration`）。
- `rule_legend` と `target_outline` の visual cue が `state_mutation: False` / `solution_dependent: False` を宣言している（`legend_cue_neutral`）。

## 4. parking

**正本**: `src/zero_button_game/parking_render.py` と `registry.ParkingPlugin.visual_contract` / `render_contract_checks`。以下は計画書 §21.7 の設計記録を現行コードで照合した要約である。

### 4.1 Visual Concept と Layout

「混雑した駐車枠で車を軸方向に滑らせ、aqua の対象車を東の amber の切れ目から外へ出す」。盤は (120, 126) 起点の480px角で、5×5ならセル96px、6×6なら80px。panel / `semantic_bounds` は (84, 88, 636, 666)、safe area (36, 36, 684, 684) に対する余裕は左右48px、上52px、下18pxである。セル余白 `CELL_MARGIN = 9` により車体短辺は78px / 62pxとなり、契約下限48pxを満たす。東辺は出口行だけ物理的に切り、開口の実効値 `cell - 8` は88px / 72pxで下限24pxを満たす。

これは**実装事実**であり、5×5/6×6を同じ480px盤に載せること、および可読下限は自動検査される。一方、「中央盤を優先し、出口まで視線を水平に流す」は計画書 §9 に基づく**設計判断**である。

### 4.2 色・形・動き

- 対象車は aqua、障害車は青灰、出口と現在移動中の focus 枠は amber。背景は `#10151D`、通常構造は `#D8E2EA`。
- 各車は角を落とした矩形に長軸方向の白線を入れる。移動可能軸は色ではなく車体形状と線方向でも読めるため、契約は `state_change_not_color_only: True`。
- solve 中は `move_piece` の移動量に沿って連続補間し、最後の slide 後だけ `released: True` になる。result は出口中心の amber ring、CLEAR 文字は補助である。
- frame 0 は fade 0 で panel・盤・車が見えず、題字 `GET THE CAR OUT`、THINK track、phase 文字は残る。出口ラベルも盤と独立して描かれるが、成功方向を変える solution 依存 cue ではない。

### 4.3 Acceptance

- セル≥72px、車体短辺≥48px、出口開口≥24px。
- 描画上の連続移動が各 `move_piece` Action と一致し、対象車の release は最後の slide 後にのみ成立する。
- reveal 前の全 frame は move 順を巡回シフトした alternate scene と同一で、reveal frame から異なる。
- target / blocker / exit は色だけでなく、車体形状・軸線・東辺の切れ目でも区別できる。

## 5. packing

**正本**: `src/zero_button_game/packing_render.py`、`packing.MAX_TRAY_WIDTH_CELLS`、`registry.PackingPlugin.visual_contract` / `render_contract_checks`。計画書 §21.8 とREADME「packing」を現行コードで照合した。

### 5.1 Visual Concept と採用 Layout A

「上の暗い穴へ、下のトレイに並ぶ固定向きの片を運び、隙間なく一枚に封じる」。穴は `HOLE_BAND = (90, 474)` 内で中央寄せし、セル96px、最大4×4で384px角（x 168–552 / y 90–474）。トレイは y=508 起点、セル60px、片間gap 20px。片の bbox 幅合計はコード側で9セル以下に制約され、4片なら最大幅は `60 × 9 + 20 × 3 = 600px`、x 60–660に収まる。panel / `semantic_bounds` は (60, 78, 660, 660) で、safe area に対する余裕は左24 / 上42 / 右24 / 下24pxである。

穴セルの片本体は `96 - 2×5 = 86px`、トレイでは `60 - 2×4 = 52px`。契約下限は穴セル96px、トレイセル54px、片本体48pxである。実際の収まりは想定値ではなく `tray_extent()` と穴の実寸を safe area に突き合わせて検査する。

**却下案（当時のPNG比較記録に基づく判断）。** 同一84pxセル案は幅約8セルとgapで696pxとなり safe area を越え、幅7セルでは4片を置けない。穴を左・トレイを右へ縦積みする案は4片で高さ520pxを越え、穴が中心から外れる。このため、穴を上・トレイを下に置き、セル寸法を分ける A 案を採用した。数値は履歴記録であり、将来の判定は現行コードを正本とする。

### 5.2 色・形・動き

- 空の穴は暗い socket と明るい輪郭、未配置片は青灰、着座済み片は aqua、移動 focus と完成 ring は amber。
- 片はセル集合の外周だけを3px線で縁取り、同一片の内部境界を消す。形状は色に依存せず読める。なおトレイ上の隣接片の区切りの十分性は人手未評価である。
- solve 中は各片がトレイ位置から穴の anchor へ移動しながら60pxセルから96pxセルへ拡大する。回転・反転はしない。最後の配置後のみ exact cover が完成し、穴外周に呼吸する amber ring を出す。
- frame 0 は fade 0 で panel・穴・片が見えず、題字 `FILL THE HOLE`、THINK track、phase 文字だけが残る。

### 5.3 Acceptance

- 穴セル≥96px、トレイセル≥54px、トレイ片本体≥48px。穴と `tray_extent()` が safe area 内で、穴がトレイより上にある。
- 描画された配置順・piece・anchor が `move_piece` Action と一致し、最後の配置後にのみ exact cover が完成する。
- reveal 前の全 frame は配置順を巡回シフトした alternate scene と同一で、reveal frame から異なる。
- 状態変化は位置、外形、セル寸法の連続変化で読めるため `state_change_not_color_only: True`。

## 6. maze

**正本**: `src/zero_button_game/render.py` と `registry.MazePlugin.visual_contract` / `render_contract_checks`。計画書 §9 の共通視覚方針を、現行 maze renderer の具体値で記録する。

### 6.1 Visual Concept と Layout

「静かな壁迷路に、aqua の始点から amber の菱形ゴールまで軌跡が伸びる」。盤は (90, 108) 起点の540px角、panel は (72, 90, 648, 666)、`semantic_bounds` は (78, 96, 642, 660)。safe area に対する semantic bounds の余裕は左42 / 上60 / 右42 / 下24pxである。grid は5×5〜9×9なのでセルは108〜60pxとなり、契約下限54pxを満たす。壁は5px、解答pathの契約下限は7pxである。

### 6.2 色・形・動き

- 壁は pale steel、始点は aqua の二重円、goal は amber の菱形。色を失っても円と菱形で役割を区別する。
- reveal 前は壁・始点・goalだけを描き、pathを参照しない。reveal 後は canonical path の先頭から aqua の10px線と白い先端dotを連続的に伸ばし、solve 後は amber の7px線へ切り替える。解答の因果を盤面そのものに載せ、CLEAR文字だけに頼らない。
- frame 0 は fade 0 で panel・壁・始点・goalが見えず、題字 `SOLVE THE MAZE`、THINK track、phase 文字だけが残る。

「中央の盤を最大化し、start→goal→伸びるpathへ視線を導く」「装飾motionを足さない」は計画書 §9 に由来する**設計判断**である。上記の座標、線幅、fade、形状は**実装事実**である。

### 6.3 Acceptance

- セル≥54px（現行範囲60〜108px）。壁5px、pathは進行中10px / 完成時7px（契約下限7px）。
- reveal 前の全 frame は path を反転した alternate scene と pixel hash が一致し、reveal frame から異なる。
- start / goal は二重円 / 菱形で識別でき、成功は amber のpathが両者を結ぶ現象として文字なしでも示される。

## 7. mosaic（MOSAIC SHIFT）

**正本**: `src/zero_button_game/mosaic_render.py` と `registry.MosaicPlugin.visual_contract` / `render_contract_checks`。

### 7.1 Visual Concept と Layout

「分断されたemblemを、3×3盤のrowまたはcolumn全体を循環shiftして復元する」。盤は (120, 112) 起点の480px角、1cell 160px、panel / `semantic_bounds` は (72, 78, 648, 656) である。safe area (36, 36, 684, 684) に対して左右36px、上42px、下28pxの余裕を持つ。品質管理されたprocedural vocabularyは `halo-diamond` / `four-petal-star` / `shield-knot` の3種で、外部bitmap assetを使わない。

### 7.2 色・形・動き

- tile面は青灰、emblem主線はaqua、副線と操作focusはamber。各tileは3px seamを持つ。
- emblemは最低12px相当の太線、ring / diamond / petal / shield輪郭を組み合わせる。完成は色だけでなく線の連続、輪郭、対称形で読めるため `state_change_not_color_only: True`。
- solve中はactive line全体を連続補間し、盤外へ出るfragmentを反対側から同時に描いてwrap-aroundを明示する。5px focus枠と矢印がaxis・line・directionを示す。
- frame 0はfade 0で盤を隠し、題字だけを残す。THINK中は初期盤面と中立progressのみで、Action順を参照しない。
- `CLEAR` と完成ringは最後のActionが完了した `solve_end` 以後にだけ現れる。transitionは左右から閉じるが、完全なblank frameにはしない。

### 7.3 Difficulty / Quality と未校正事項

Easy / Medium / targetの最短Action数は2 / 3 / 4。初期の2–3 / 3–5 / 5–8構成は短時間予測には重く、Medium 4手・target 6手の代表作を評価後に2 / 3 / 4へ再調整した。thinking timeはframe 0からrevealまで4.0 / 6.0 / 8.0秒を維持し、Action削減後の余裕を再評価する段階であり、人間のtiming校正はまだ行っていない。solverはdepth 8・362,880 node budgetのbounded BFSで最短depth、exact shortest path count、expanded nodesを記録する。`mosaic-exact-action-order-v1` では可換なAction順も別pathなので、採用候補はexact shortest pathが1本でなければならない。単一軸、交差のない独立line修正、少数misplaced fragment、既に完成した盤面は棄却する。

### 7.4 Acceptance

- canvas 720×720、cell 160px（下限144px）、盤・panelがsafe area内。
- reveal前の全frameはAction順を巡回させたalternate sceneとpixel hash一致し、reveal frameでは不一致。
- `shift_line` Actionのaxis / line / delta、各Action境界のtile state、wrap-around補間がsemantic snapshotと一致。
- bounded BFSでexact shortest path countが1、両軸が交差し、独立line修正ではない。
- 最後のshift前は `solved: False`、`solve_end` からのみ `solved: True` / `CLEAR`。
- `emblem_complete` cueは `state_mutation: False` を宣言する。
