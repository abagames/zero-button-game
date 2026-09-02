# Zero Button Game

Zero Button Gameは、maze / pipes / parking / packing / lights / fold の決定論的なパズルGIF/MP4と、**Easy → Medium → Hardの音声付き3問MP4**を生成・検証する制作環境です。CLI・生成・metadata内部のband名は `easy` / `medium` / `target` で、sequenceの視聴者表示だけが `target` を **HARD** と表します。

## 最短の使い方

### 1. 必要環境

- CPython 3.12以上（検証環境: 3.12.3）
- FFmpeg / ffprobe（検証環境: 6.1.1、`libx264` を含む）
- gifsicle（検証環境: 1.94）
- Python外部パッケージ依存なし。FFmpeg、ffprobe、ImageMagick、gifsicleなどのシステムツールは実行時に確認します

以降はプロジェクトルートで実行します。

```bash
python3 --version
ffmpeg -version
ffprobe -version
gifsicle --version
PYTHONPATH=src python3 -m zero_button_game --help
```

`PYTHONPATH=src` は、未インストールの `src/` 配下をPythonのimport対象に加えます。`-m` はこのプロジェクト独自ではなくPython標準のオプションで、`zero_button_game` packageの [`__main__.py`](src/zero_button_game/__main__.py) をモジュールとして実行する指定です。

旧CLI `PYTHONPATH=src python3 -m puzzle_gif` から、新CLI `PYTHONPATH=src python3 -m zero_button_game` へ移行しました。旧packageのalias・shim・re-exportは提供しません。

### 2. 6種の代表sequenceを一括生成する

`--output` には、既存成果物と重ならない新しいディレクトリを指定してください。

```bash
PYTHONPATH=src python3 -m zero_button_game generate-representatives \
  --seed 20260828 --max-candidates 500 --audio on \
  --output output/my-representatives-2026-08-28
```

6種それぞれの3問MP4と、collection rootの `manifest.json` を生成します。

### 3. 1種のsequenceを生成する

```bash
PYTHONPATH=src python3 -m zero_button_game generate-sequence \
  --type pipes --seed 20260828 --max-candidates 500 --audio on \
  --output output/my-pipes-sequence-2026-08-28
```

`--type` は `maze` / `pipes` / `parking` / `packing` / `lights` / `fold` のいずれかです。各sequenceは内部band `easy` → `medium` → `target` を生成し、画面上では `EASY` → `MEDIUM` → `HARD` と表示します。

### 4. 単体作品を生成する

```bash
PYTHONPATH=src python3 -m zero_button_game generate \
  --type lights --difficulty target --seed 8521 \
  --output output/my-lights-single-2026-08-28
```

既定は `--format gif,mp4` です。`--format gif` または `--format mp4` に絞ることもできます。生成可能なbandは全種共通で `easy` / `medium` / `target` です。

### 品質候補をlogic-onlyで監査する

```bash
PYTHONPATH=src python3 -m zero_button_game audit-quality \
  --type parking --difficulty easy --seed 20260822 --candidates 20
```

`audit-quality` は登録済みpluginの候補生成、構造検証、solver、解答検証、difficulty、quality rejectionを通常生成と同じ順で実行し、JSONを標準出力へ返します。描画・media生成・永続ファイル出力は行いません。`scanned`、`accepted`、`acceptance_rate`、`rejection_reasons`、difficulty / solver metric catalog、候補別hash、preset hash、`audit_sha256` が共通契約です。実行時間は記録せず、runtime情報は再現hashの対象外です。同じpreset bytes・型・band・seed・候補数なら結果とhashは同一になります。

型の選択肢は [`registry.py`](src/zero_button_game/registry.py) の登録内容から作られます。将来のgenreも [`protocol.py`](src/zero_button_game/protocol.py) の必須生成・solve・difficulty・quality境界を満たしてregistryへ登録すれば、genre名、ruleset、固定秒数、過去output pathに依存するaudit専用実装なしで参加します。候補生成時の `ValueError` が正常な棄却になり得るpluginだけは、既存のoptional `candidate_rejection_reason` で理由codeを公開します。結果の `capabilities` は共通機能と、全pluginで一律ではない solution uniqueness metric の有無を明示します。

### 5. audioを選ぶ

audioはsequence用です。

- `--audio on`: countdown、操作、完了、問題切替の4層cueを持つAAC音声付きMP4
- `--audio off` または省略: audio streamを持たない後方互換の無音MP4

単体 `generate` は無音のGIF / MP4を生成します。sequenceの音声・timeline・検証契約は [`sequence.py`](src/zero_button_game/sequence.py) が実装上の正本です。

### 6. 検証する

```bash
# 6種collectionまたは1種sequence
PYTHONPATH=src python3 -m zero_button_game validate-sequence \
  output/my-representatives-2026-08-28

# 単体runまたは単一instance
PYTHONPATH=src python3 -m zero_button_game validate \
  output/my-lights-single-2026-08-28 --strict
```

検証失敗は非ゼロ終了です。`validate-sequence` はcollection rootを渡すと配下の全sequenceを、sequence directoryを渡すとその1本を検証します。`validate --strict` はrun配下の全instanceを再帰的に検証できます。

### 7. 出力を確認する

6種一括生成:

```text
<collection>/
  manifest.json
  <puzzle-type>/<sequence-id>/
    sequence.mp4
    sequence.json
    validation.json
    components/<band>/<puzzle-type>/<instance-id>/...
```

単体生成:

```text
<run>/<puzzle-type>/<instance-id>/
  problem.json
  solution.json
  presentation.json
  metadata.json
  validation.json
  animation.gif            # --format に gif を含むとき
  preview.mp4              # --format に mp4 を含むとき
  contact_sheet.png        # 配布可、pre-revealのみ
  contact_sheet_full.png   # 解答を含むレビュー用
  keyframes/               # 配布可、pre-revealのみ
  keyframes_full/          # 解答を含むレビュー用
```

sequence componentは単体artifact一式を保持しますが、合成に使った一時PPM frame列は成功後に除去します。`sequence.json` はband別seed、instance、preset、thinking time、区間frame、component hash、presentation/audio、最終MP4 hashを記録します。

### 8. preset JSONを変更する

現行18組（6種×3band）の生成条件と標準thinking timeは、`src/` 内の重複dictではなく [presets/current](presets/current/) のJSONが実行時の正本です。[presets/shared](presets/shared/) の2件は共通timeline契約を記録します。`current/` / `shared/` のfilenameとpreset IDは安定名で、仕様世代は `ruleset` / `schema_version` / equivalence policy / presentation contractなどで管理します。通常変更するのは `current/` だけです。

```bash
PYTHONPATH=src python3 -c \
  'from zero_button_game.preset_loader import PresetLoader; print(PresetLoader().audit_catalog())'

PYTHONPATH=src python3 -m unittest discover \
  -s tests -p 'test_presets.py' -v
```

loaderは毎回JSONを読み直し、実際にparseしたsource bytesのSHA-256を単体metadataの `difficulty.quality_preset_sha256` とsequence metadataへ記録します。`quality_preset_source` は `current/<filename>` です。ファイル名・`name`・`puzzle_type`・`difficulty`・必須field・range・20fps格子が不整合ならfail closedです。`audit_catalog()` は current 18件とshared 2件だけを明示catalogと照合し、未登録JSON、欠落、ID重複を拒否します。

## 主要な制約

- **既存 `output/` を上書きしない。** 同一instanceは既定で `OUTPUT_CONFLICT` になります。再現検査も別の新規outputへ生成してください。単体の `--force` は既存instanceを `output/superseded/` へ退避しますが、通常運用では新規runを推奨します。sequenceに `--force` はありません。
- **標準外thinking timeは比較条件。** `--thinking-time` は2.5〜20.0秒の20fps格子で全plugin / bandに指定できます。JSON標準と異なる値は `comparison-override-not-standard` と記録され、標準作品ではありません。正本生成ではoverrideを省略してください。
- **一般利用者校正は未実施。** 既存種のthinking timeには単独評価者の個人内標準が含まれます。LightsのEasy 4.0秒 / Medium 6.0秒は2026-09-02に選んだ未校正候補で、Target 8.0秒だけが既存校正を維持します。構造難易度にも未校正bandがあり、sequence全体、title、countdown、HARD理解、音量・音の識別性は一般利用者未評価です。
- **difficulty scoreは種間比較不可。** score式とrangeはpluginごとに独立しています。
- **`_full` は解答を含む。** 公開・評価時は通常 `contact_sheet.png` / `keyframes/` を使い、`*_full` を配布しないでください。
- **`output/` は全体をGit管理外とする。** media、metadata、manifest、JSON / JSONL、一時stagingを含むローカル生成物はすべて `.gitignore` の対象です。再現性はseed、preset、コード、および `audit-quality` の標準出力などで担保します。
- `--keep-frames` は調査用です。720×720 RGB PPMは1 frame約1.5 MiBなので、通常は指定しません。

## 現行標準の概要

| 種 | 現行preset | 単体出力 | 3問sequence | thinking time Easy / Medium / Hard（秒） |
|---|---|---|---|---:|
| `maze` | [`maze-{easy,medium,target}.json`](presets/current/) | GIF / MP4 | MP4（音声あり／なし） | 2.5 / 2.5 / 3.5 |
| `pipes` | [`pipes-{easy,medium,target}.json`](presets/current/) | GIF / MP4 | MP4（音声あり／なし） | 4.0 / 6.0 / 8.0 |
| `parking` | [`parking-{easy,medium,target}.json`](presets/current/) | GIF / MP4 | MP4（音声あり／なし） | 4.0 / 4.0 / 8.0 |
| `packing` | [`packing-{easy,medium,target}.json`](presets/current/) | GIF / MP4 | MP4（音声あり／なし） | 4.0 / 4.0 / 8.0 |
| `lights` | [`lights-{easy,medium,target}.json`](presets/current/) | GIF / MP4 | MP4（音声あり／なし） | 4.0 / 6.0（未校正候補）/ 8.0（既存校正） |
| `fold` | [`fold-{easy,medium,target}.json`](presets/current/) | GIF / MP4 | MP4（音声あり／なし） | 4.0 / 6.0 / 6.0 |

内部band名は `easy` / `medium` / `target`、視聴者表示だけが `EASY` / `MEDIUM` / `HARD` です。単体は `--format gif` / `--format mp4` / `--format gif,mp4` で出力を選び、3問sequenceはMP4専用で `--audio on` / `--audio off` を選べます。

thinking timeはframe 0から `reveal_start` までです。各pluginのruleset・Action・score range・校正metadataは [src/zero_button_game](src/zero_button_game/) と [presets/current](presets/current/) が実装上の正本です。配色・レイアウト・視覚受入条件は [VISUAL_DESIGN.md](VISUAL_DESIGN.md) を参照してください。

## 文書の位置づけ

| 文書 | 役割 |
|---|---|
| このREADME | 利用者向けの生成・検証入口 |
| [VISUAL_DESIGN.md](VISUAL_DESIGN.md) | 現行の視覚仕様 |
| [FRAMEWORK_PROPOSAL.md](FRAMEWORK_PROPOSAL.md) | 将来のフレームワーク構想・改良案。現行仕様ではなく、実装済み・実装予定を示すものではない |

## 開発時の確認

```bash
PYTHONPATH=src python3 -m compileall -q src scripts tests
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/check_markdown_links.py
```

新pluginを追加するときは [`protocol.py`](src/zero_button_game/protocol.py)、[`registry.py`](src/zero_button_game/registry.py)、[`pipeline.py`](src/zero_button_game/pipeline.py) の境界を先に確認してください。
