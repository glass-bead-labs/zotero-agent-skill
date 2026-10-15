---
name: zotero
description: |
  Read and organize the user's local Zotero 10 library, and import references into a specific library or collection with child notes, native tags and PDFs, after verifying every DOI/arXiv ID against Crossref/arXiv. Use whenever the user mentions Zotero, a Zotero collection or group library, adding papers by DOI or arXiv ID, putting a bibliography/reading list/annotations into Zotero, or moving, tagging or trashing items. Always use it when turning an AI-written or pasted bibliography into Zotero entries, because LLM-generated DOIs are often wrong.
compatibility: Zotero 10+ desktop app running locally with Settings > Advanced > "Allow other applications on this computer to communicate with Zotero" ticked. python3 (stdlib only).
allowed-tools: Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py status) Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py libs) Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py selftest) Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py colls *) Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py ls *) Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py find *) Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py plan *)
---

# Zotero (10+, local API)

**Scope.** This skill is not for doing research. It fetches from and updates Zotero. The assumption is that if you are entering new references, you have already identified them, outside the scope of this skill (ideally with a DOI or arXiv ID in hand). `verify` fetches the metadata from Crossref/arXiv and checks it matches the claim, and comes after identifying references.

One stdlib-only CLI, `scripts/zotero.py`, against the running desktop app on `127.0.0.1:23119`. No API key, no cloud, no MCP. Run it as `python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py <command>` (if the variable is not expanded, use this skill's directory).

## Read-only commands (pre-approved in `allowed-tools`)

`status`, `libs`, `colls LIB`, `ls LIB [COLL]`, `find LIB QUERY`, `plan RESOLVED`, `selftest`.

They only issue **HTTP GET to `127.0.0.1:23119`** (`/connector/ping`, `/api/users/0/groups`, `/api/<lib>/collections`, `.../items`, `.../items/top`, `.../tags`) and, for `plan`, read the local JSON file named on the command line. They send no data anywhere else and create, modify or delete nothing in Zotero or on disk. `plan` is the dry run of `import`: it computes what `import` would send (new items, notes, tags, skipped duplicates) and stops.

This safety is defended in depth:

1. *Zotero's own docs.* The Local API page states reads need no authentication and that "write requests (Zotero 10+) require a local API key" the user grants in a Zotero confirmation dialog: <https://www.zotero.org/support/dev/web_api/v3/local_api>. Writes use POST/PUT/PATCH/DELETE (<https://www.zotero.org/support/dev/web_api/v3/write_requests>); GET is a *safe* method, i.e. read-only by definition (RFC 9110 section 9.2.1: <https://httpwg.org/specs/rfc9110.html#safe.methods>). So even a buggy GET cannot change the library: Zotero would reject a write without a key that the user approved.
2. *The script's runtime guard.* Before any read-only command runs, `http()` in `scripts/zotero.py` is switched to `READ_ONLY`; any non-GET or non-`127.0.0.1` request exits with "BUG: read-only command attempted ...". Prove it without Zotero: `python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py selftest` (every write verb and off-host URL must print `BLOCKED ok`).
3. *Reading the code.* Only `local_write()` (dialog-gated) and `connector()` (import path) can write. `grep -n "connector(\|local_write(" scripts/zotero.py` shows every call site; none is reachable from the commands above (`cmd_plan` calls `build_import()` + `describe()`, which only call `zget()`).

Deliberately **not** pre-approved: `verify` (queries Crossref/arXiv/OpenAlex with the identifiers and titles, and writes the `-o` file), `import`, `organize`, `trash`, `write`.

## The rule: never type bibliographic metadata

An attempt should have been made to find a real ID (e.g., arXiv or Crossref `query.bibliographic`, title + author) prior to the work with Zotero described in this skill. If not, look for an ID so you can run `verify` (but don't repeat effort!).

Extract *identifiers* (DOI, `arXiv:ID`) and prose to attach. `verify` compares the source's claimed title/authors/year with what resolved. In the run that produced this skill, 15 of 29 references in an LLM-written bibliography had a wrong DOI, no ID, or an invented title, though nearly all the papers were real.

### Escape hatch: no identifier exists

Some references have no DOI/arXiv ID. Do **not** invent one and do **not** silently fill metadata. Instead, only if the user has explicitly said to insert unverified/LLM-supplied metadata for those entries, add to the spec entry:

```json
{"manual": {"itemType": "journalArticle", "title": "...", "creators": [{"creatorType": "author", "firstName": "A.", "lastName": "Name"}], "date": "2020", "publicationTitle": "..."},
 "user_affirmed": "<the user's words, quoted, with date>", "claimed": {...}, "tags": [...]}
```

`verify` marks it `unverified` (`refused` without `user_affirmed`). `plan` lists it prominently. `import` skips it unless `--allow-unverified` is passed, and then adds the tag `unverified metadata` plus a child note quoting the affirmation. Entries are de-duplicated by title.

Use the hatch only for references with no identifier, and list every such entry back to the user

## Zotero-native, human-first

The library is a human's working space; write what a human would write by hand.

- **Classification goes in tags, never in note text.** Source labels, topic/section, status: all tags. Notes hold prose only.
- **Tags are regular (manual, blue) tags by default.** The connector forces *automatic* tags (red; hidden when "Show Automatic" is off; wiped by "Delete Automatic Tags in This Library", which is library-wide; docs: <https://www.zotero.org/support/collections_and_tags>), so `import` follows up with `organize`, one batched local-API write that sets them as regular tags: **one Zotero approval dialog per 50 items**. Say so before running. Set `"tag_type": "automatic"` in the spec only for disposable machine tags. `organize RESOLVED` also repairs items imported earlier.
- **Suggested vocabulary:** a batch/project tag (named by the user); `needs review` on anything holding LLM text (the human removes it when checked); the source's own labels verbatim; `unverified metadata` (enforced by the script). `plan` prints which tags are new vs already in the library: reuse existing names.
- **Structure goes in subcollections.** If the source groups references into sections, give each entry `"subcollection": "<name>"` (string, or list when a paper belongs to several). Don't number them (the user tried it and preferred plain names; Zotero sorts alphabetically). `organize` creates them under the spec's `collection` and **moves** items out of the parent; memberships outside that tree are preserved. Tell the user the parent will look nearly empty unless View > "Show Items from Subcollections" is on. Entries without a subcollection stay in the parent.
- **Notes:** the first line is the note's title in the item tree, so make it say what and where from ("Opus lit review: annotation (LLM-generated, unreviewed)"). One note per source annotation; LLM-written text is always labelled as such; keep a small footer with the citation as the source wrote it, and a line when an identifier was corrected. Document-level material goes in one standalone note in the collection.
- **Humans edit.** Imported notes get edited. Matching of "already there" is fuzzy for notes and the retrofit skips notes whose `dateModified` differs from `dateAdded`. Never overwrite human edits.

## Workflow: references -> collection

1. **Spec** JSON (`references/spec-example.json`): per entry `id`, `claimed`, optional `notes` (HTML), `tags`, `subcollection`, `pdf`, or `manual` + `user_affirmed`; `standalone_notes`; `tags` (all entries); `tag_type`.
2. **Verify**: `verify spec.json -o resolved.json`. Statuses `ok | accepted | unverified | mismatch | notfound | refused`. Fix IDs or leave entries out and tell the user; `accepted` needs an explicit `accept_mismatch: "<reason>"`.
3. **Plan** (read-only): `plan resolved.json`. Show the user target, counts, new tags, unverified entries, skips, and the placement preview (`-- placement/tags of items already in the collection tree`).
4. **Pilot** 1-2 entries into a shared library; inspect (`ls`, children); then the rest.
5. **Import**: `import resolved.json [--allow-unverified] [--no-pdf] [--max-pdf-mb N]`. Connector writes (items, notes, PDFs) show no dialog, so the plan step is the user's review. Prefer PDFs from open-access locations (arXiv, OpenAlex `pdf_url`, Pubmed Central), but it is not your role to diagnose or enforce copyright. PDFs must be real, default cap 50 MB.
6. **Check**: `ls "<Library>" "<Collection>"`; report items, notes, PDFs, skipped and failed entries.

## Write commands and the modal

| Command | Path | Zotero dialog |
| --- | --- | --- |
| `import` | connector (items, notes, PDFs); then `organize` | none for the import; then as `organize` |
| `organize RESOLVED [--dry-run]` | local API: create subcollections, set regular tags, move items | 1 to create subcollections + 1 per 50 items |
| `trash LIB KEY...` | local API (`deleted:1`, reversible) | 1 per request |
| `write LIB FILE.json` | local API: array of `{key, version, ...fields}` | 1 per request (max 50 objects) |

The dialog is the user's review gate and the key is single-use: put related edits in ONE request and say how many dialogs to expect; never loop single-item writes. Moving an item to another collection: `write` `[{"key":"K","version":12,"collections":["C1","C2"]}]` (replaces membership; read current `collections` first). Raw reads: `curl -s -H 'Zotero-API-Version: 3' localhost:23119/api/groups/<ID>/collections/<KEY>/items/top` (user library: `/api/users/0/...`).

## Gotchas

- Library id (`L21`), group id (`6713340`), collection key (`GLCG2D2J`) and connector collection id (`C288`) differ; the script maps names to all of them. Collection names must be unique within a library.
- Local-API writes need `Zotero-Server-ID` (any response header) and a single-use `Zotero-API-Key` from `POST /api/local/authorize`; more than 5 dialog-triggering requests a minute get 429.
- After `organize`, papers live in subcollections: any lookup must search the parent **and** its subcollections (the script does; ad-hoc scripts must too). A batch write whose objects are all unchanged returns `unchanged`, not `successful`, and still costs a dialog: check the payload before writing.
- The connector does not dedupe; the script does (DOI/arXiv ID, title for unverified, fuzzy text for notes). Trashed items do not count.
- Group-library PDFs count against the group owner's storage quota: report total MB before bulk PDF imports.
- Crossref 429s under bursts (script backs off); arXiv wants ~3 s between calls; some publisher/ISLS PDFs 403 scripts, so list them for manual download.
