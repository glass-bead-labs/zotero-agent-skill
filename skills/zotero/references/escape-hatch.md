# Escape hatch: references with no identifier

Use this only for references with no DOI/arXiv ID, and only if the user has explicitly said to insert unverified/LLM-supplied metadata for those entries. Do not invent an identifier and do not silently fill metadata. List every such entry back to the user.

The spec entry carries a `manual` block (`itemType`, `title`, `creators`, `date`, ...) plus `user_affirmed` (the user's words, quoted, with date), alongside the usual `claimed` and `tags`. The complete shape is the entry with `manual` in `spec-example.json`.

`verify` marks it `unverified` (`refused` without `user_affirmed`). `plan` lists it prominently. `import` skips it unless `--allow-unverified` is passed, and then adds the tag `unverified metadata` plus a child note quoting the affirmation. Entries are de-duplicated by title.
