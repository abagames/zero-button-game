# Preset layout

利用者が通常変更する実行時正本は [current](current/) の18件（6 plugin × `easy` / `medium` / `target`）だけです。JSON bytesがそのまま `quality_preset_sha256` の入力になります。

[shared](shared/) の2件は共通timeline契約の記録です。現行band presetとは別物で、現在のruntime値は各band JSONから読みます。

`current/` / `shared/` のfilenameとpreset IDは利用者向けの安定名です。技術的な仕様世代はfilenameではなく、各JSONの `ruleset`、`schema_version`、equivalence policy、presentation contractなどで管理します。

`PresetLoader().audit_catalog()` はこの20件だけを正規catalogとして監査します。未登録JSON、欠落、IDとfilenameの不一致はfail closedです。
