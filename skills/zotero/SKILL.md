---
name: zotero
description: |
  Read and organize the user's local Zotero 10 library, and import references into a specific library or collection with child notes, native tags and PDFs, after verifying every DOI/arXiv ID against Crossref/arXiv. Use whenever the user mentions Zotero, a Zotero collection or group library, adding papers by DOI or arXiv ID, putting a bibliography/reading list/annotations into Zotero, or moving, tagging or trashing items. Always use it when turning an AI-written or pasted bibliography into Zotero entries, because LLM-generated DOIs are often wrong.
compatibility: Zotero 10+ desktop app running locally with Settings > Advanced > "Allow other applications on this computer to communicate with Zotero" ticked. python3 (stdlib only).
allowed-tools: Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py status) Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py libs) Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py selftest) Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py colls *) Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py ls *) Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py find *) Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py plan *) Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py where) Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py where *) Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py verify *)
---

# Zotero (10+, local API)

Scope. This skill is not for doing research. It fetches from and updates Zotero. The assumption is that if you are entering new references, you have already identified them, outside the scope of this skill (ideally with a DOI or arXiv ID in hand). `verify` fetches the metadata from Crossref/arXiv and checks it matches the claim, and comes after identifying references.

One stdlib-only CLI, `scripts/zotero.py`, against the running desktop app on `127.0.0.1:23119`. No API key, no cloud, no MCP. Run it as `python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py <command>` (if the variable is not expanded, use this skill's directory).

## Workflow: references -> collection

1. Spec JSON (`references/spec-example.json`): per entry `id`, `claimed`, optional `notes` (HTML), `tags`, `subcollection`, `pdf`, or `manual` + `user_affirmed`; `standalone_notes`; `tags` (all entries); `tag_type`.
2. Verify: `verify spec.json`. The output is stored under `$TMPDIR`, in a private directory, at a path derived from the spec's path. Every later command takes the same spec path (`plan spec.json`); `where spec.json` prints the actual location. `verify` makes one request per entry; for a long spec raise the Bash timeout or run it in the background. Statuses `ok | accepted | unverified | mismatch | notfound | refused`. Fix IDs or leave entries out and tell the user; `accepted` needs an explicit `accept_mismatch: "<reason>"`.
3. Plan (read-only): `plan spec.json`. Show the user target, counts, new tags, unverified entries, skips, and the placement preview (`-- placement/tags of items already in the collection tree`).
4. Pilot 1-2 entries into a shared library; inspect (`ls`, children); then the rest.
5. Import: `import spec.json [--allow-unverified] [--no-pdf] [--max-pdf-mb N]`. Connector writes (items, notes, PDFs) show no dialog, so the plan step is the user's review. Prefer PDFs from open-access locations (arXiv, OpenAlex `pdf_url`, Pubmed Central), but it is not your role to diagnose or enforce copyright. PDFs must be real, default cap 50 MB.
   - Group-library PDFs count against the group owner's storage quota: report total MB before bulk PDF imports.
6. Check: `ls "<Library>" "<Collection>"`; report items, notes, PDFs, skipped and failed entries.

If something fails or looks odd (ID mapping, 429s, dedupe, subcollection lookups, dialogs): `references/gotchas.md`.

## Pre-approved commands (`allowed-tools`)

### Read-only

`status`, `libs`, `colls LIB`, `ls LIB [COLL]`, `find LIB QUERY`, `plan SPEC`, `where [SPEC]`, `selftest`.

They only issue HTTP GET to `127.0.0.1:23119` (`/connector/ping`, `/api/users/0/groups`, `/api/<lib>/collections`, `.../items`, `.../items/top`, `.../tags`) and, for `plan`, read the verify output of the spec named on the command line (a file in the private temp directory; see the Verify step). They send no data anywhere else and create, modify or delete nothing in Zotero or on disk. `plan` is the dry run of `import`: it computes what `import` would send (new items, notes, tags, skipped duplicates) and stops. `where` only lists or prints paths in that temp directory.

This safety is defended in depth (Zotero's own docs, the script's runtime guard, reading the code); the argument and how to check it yourself: `references/read-only-safety.md`. `selftest` proves the guard without Zotero.

### `verify` (network read, one local file)

`verify SPEC` is not read-only, but it is bounded the same way: the runtime guard allows only HTTP GET to Zotero, `api.crossref.org`, `export.arxiv.org` and `api.openalex.org` (redirects included); it sends only the DOI/arXiv identifiers (never titles or notes); it only pings Zotero (to check the version) and never reads or changes the library; and its one write is its output file, in the private temp directory described in the Verify step. `selftest` proves the guard.

Deliberately not pre-approved: `import`, `organize`, `trash`, `write`.

## The rule: never type bibliographic metadata

An identifier is needed only to *add* a new reference, because `verify` resolves it. Reading, finding, tagging, moving and trashing items never need one. An attempt should have been made to find a real ID (e.g., arXiv or Crossref `query.bibliographic`, title + author) prior to the work with Zotero described in this skill. If not, look for an ID so you can run `verify` (but don't repeat effort!).

Extract *identifiers* (DOI, `arXiv:ID`) and prose to attach.

### Escape hatch: no identifier exists

Some references have no DOI/arXiv ID. Do not invent one and do not silently fill metadata. Instead, only if the user has explicitly said to insert unverified/LLM-supplied metadata for those entries, give the spec entry a `manual` block plus `user_affirmed` (shape, and what `verify`, `plan` and `import` do with it: `references/escape-hatch.md`; example in `references/spec-example.json`).

Use the hatch only for references with no identifier, and list every such entry back to the user.

## Zotero-native, human-first

The library is a human's working space; write what a human would write by hand.

- Subcollections say what a reference is about; tags say everything else. A topic or section is a subcollection, never also a tag: no tag repeats a subcollection name. Status (`needs review`), source labels and caveats are tags. Notes hold prose only, never classification.
- Tags are regular (manual, blue) tags by default. The connector forces *automatic* tags (red; hidden when "Show Automatic" is off; wiped by "Delete Automatic Tags in This Library", which is library-wide; docs: <https://www.zotero.org/support/collections_and_tags>), so `import` follows up with `organize`, one batched local-API write that sets them as regular tags (dialog counts: the table under Write commands). Say so before running. Set `"tag_type": "automatic"` in the spec only for disposable machine tags. `organize SPEC` also repairs items imported earlier.
- Suggested vocabulary: a batch/project tag (named by the user); `needs review` on anything holding LLM text (the human removes it when checked); the source's own labels verbatim (except topic/section headings, which are subcollections); `unverified metadata` (enforced by the script). `plan` prints which tags are new vs already in the library: reuse existing names.
- File each reference into the fitting topical subcollection. List the existing subcollections under the spec's `collection` (`colls LIB`) and give each entry `"subcollection": "<name>"` (string, or list when a paper belongs to several), using the existing name exactly. If none fits, ask the user before creating a new one; do not invent a name and do not fall back to a tag. `organize` creates any name that does not exist yet, so an invented name becomes a new subcollection. Don't number them; Zotero sorts alphabetically. `organize` places them under the spec's `collection` and moves items out of the parent; memberships outside that tree are preserved. Tell the user the parent will look nearly empty unless View > "Show Items from Subcollections" is on. Entries without a subcollection stay in the parent.
- Notes: the first line is the note's title in the item tree, so make it say what and where from ("Opus lit review: annotation (LLM-generated, unreviewed)"). One note per source annotation; LLM-written text is always labelled as such; keep a small footer with the citation as the source wrote it, and a line when an identifier was corrected. Document-level material goes in one standalone note in the collection.
- Humans edit. Imported notes get edited. Matching of "already there" is fuzzy for notes and the `organize` retrofit skips notes whose `dateModified` differs from `dateAdded`. Never overwrite human edits.

## Write commands and the modal

| Command | Path | Zotero dialog |
| --- | --- | --- |
| `import` | connector (items, notes, PDFs); then `organize` | none for the import; then as `organize` |
| `organize SPEC [--dry-run]` | local API: create subcollections, set regular tags, move items | 1 to create subcollections + 1 per 50 items |
| `trash LIB KEY...` | local API (`deleted:1`, reversible) | 1 per request |
| `write LIB FILE.json` | local API: array of `{key, version, ...fields}` | 1 per request (max 50 objects) |

The dialog is the user's review gate and the key is single-use: put related edits in ONE request and say how many dialogs to expect; never loop single-item writes. Moving an item to another collection: `write` `[{"key":"K","version":12,"collections":["C1","C2"]}]` (replaces membership; read current `collections` first). Raw reads: `curl -s -H 'Zotero-API-Version: 3' localhost:23119/api/groups/<ID>/collections/<KEY>/items/top` (user library: `/api/users/0/...`).
