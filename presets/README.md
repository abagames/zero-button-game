# Preset layout

The only runtime sources of truth that users normally modify are the 21 files in [current](current/) (7 plugins × `easy` / `medium` / `target`). The JSON bytes are used directly as the input to `quality_preset_sha256`.

The two files in [shared](shared/) record the common timeline contract. They are separate from the current band presets; current runtime values are read from each band's JSON file.

The filenames and preset IDs under `current/` and `shared/` are stable, user-facing names. Technical specification generations are identified by each JSON file's `ruleset`, `schema_version`, equivalence policy, presentation contract, and related fields, not by its filename.

`PresetLoader().audit_catalog()` treats exactly 23 files—the 21 current presets and 2 shared presets—as the canonical catalog. It fails closed on unregistered JSON files, missing files, or mismatches between IDs and filenames.
