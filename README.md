# zotero-agent-skill

An agent skill plus a small stdlib-only CLI for working with a local Zotero 10+ library from Claude Code (or any agent that reads [Agent Skills](https://agentskills.io)): look things up, and import references with verified metadata, child notes, native tags, subcollections and open-access PDFs.

```
skills/zotero/            <- the skill (this is what gets installed)
  SKILL.md                   instructions + allowlist
  scripts/zotero.py          the CLI (python3 stdlib only)
  references/                spec-example.json, read-only-safety.md, escape-hatch.md, gotchas.md
install.sh                <- symlinks (or --copy) the skill into ~/.agents/skills and ~/.claude/skills
README.md                 <- this file: decisions and why
```

## Install

```bash
./install.sh            # symlink; edits here are live
./install.sh --copy     # or a snapshot copy
python3 -I ~/.agents/skills/zotero/scripts/zotero.py selftest   # needs no Zotero
python3 -I ~/.agents/skills/zotero/scripts/zotero.py status     # needs Zotero 10+ running
```

Zotero must have Settings > Advanced > "Allow other applications on this computer to communicate with Zotero" ticked. `~/.agents/skills` is the vendor-neutral location (same place the Tavily skills live); Claude Code only reads `~/.claude/skills`, so that is a symlink to it. Claude Code follows symlinked skill directories.

## Where this came from

2026-10-11, Dav + Claude. The concrete task: take the "Group 1" annotated bibliography (an LLM-written lit review inside the Google Doc "CogSec 2026: CVMC Jam") and put it in the CogSec Zotero group library, collection "Risks in Learning", with the annotations as child notes and PDFs where available. The skill is what was left after doing that by hand once. The Zotero forum thread that started it (and the MCP landscape it shows) is in References below.

## Decisions and why

Skill + one CLI, not an MCP server. The Zotero MCP space is fragmented (at least five servers, all different, none clearly canonical) and an MCP server is another process, config and auth surface to keep alive. A skill that tells the agent which commands to run, plus a CLI the agent can read and audit in one sitting, was judged simpler and more reviewable. This follows the pattern of Tavily's CLI-plus-skill approach, which the user already liked ("Tivaly" in the original message is Tavily).

Zotero 10+ and the local API only. Zotero 10 added write support to the local API (POST/PUT/PATCH/DELETE on `localhost:23119/api`, gated by a per-request approval dialog) and the same endpoint serves reads in standard Web API shape. That removes the API key, the cloud round trip, and counts that disagree with what the app shows (a complaint in the forum thread). Supporting Zotero 7-9 would have meant a second, read-only mode and hedged docs; we chose to be specific. SQLite is never touched (writing while Zotero runs corrupts sync).

Never type metadata: verify, then import. The model supplies identifiers; Crossref/arXiv supply fields; `verify` compares the source's claimed title/authors/year with what resolved. Why: in the real run, 15 of 29 references in the LLM-written bibliography had a wrong DOI (9, all fixable by Crossref title search), no ID (3), or something unresolved (3: a DOI that 404'd under a garbled title, an arXiv ID that resolved to a different paper than the doc's title, and one that could not be found). Two of those three were later resolved by the user: the garbled one was a real paper, recognisable by its acronym (EVAES) even though the doc had invented the expansion, DOI and issue, so "not found" should be reported as "not found by these queries", and a recognisable acronym, author pair and venue are worth a search before giving up. Nearly all the papers were real; the identifiers were the problem, and a DOI that 404s or resolves to an unrelated paper is silent damage in a library.

Escape hatch for references with no identifier. Refusing outright would drop legitimate references (books, reports, old papers). So a `manual` entry is allowed only with `user_affirmed` (the user's words, quoted). It is marked `unverified`, skipped by `import` unless `--allow-unverified`, tagged `unverified metadata`, and gets a child note quoting the affirmation. The skill says an attempt to find an ID (Crossref search) should have been made before the skill is used; if not, look once, and don't repeat effort.

Two write paths on purpose.
- *Connector* (`/connector/saveItems`, `updateSession`, `saveAttachment`; the browser-extension path): creates items with child notes, standalone notes and stored PDFs, and shows no dialog. It is the only way found to attach PDFs, and the only way to get notes in the same request. Because it is silent, the review gate is `plan` (a dry run), which the agent must show first.
- *Local API writes* (tags, moves, renames, trash): each request needs a single-use key from `POST /api/local/authorize`, which pops a Zotero dialog. The user explicitly values this as a way to catch unintended changes, so the skill batches related edits into one request (max 50 objects) and tells the user how many dialogs to expect. Hard-won details: every write needs the `Zotero-Server-ID` header (seen on any response); keys are single-use; more than 5 dialog-triggering requests a minute get 429; a batch whose objects are all unchanged comes back `unchanged` but still costs a dialog.

Read-only commands are pre-approved, and that claim is checkable. `status libs colls ls find plan where selftest` are in `allowed-tools`. So is `verify`, which is not read-only but is bounded by the same kind of runtime guard (GET only, to Zotero, Crossref, arXiv and OpenAlex) and writes one file under `$TMPDIR`. The argument (Zotero's docs, HTTP safe-method semantics, the guard, reading the code) is in `skills/zotero/references/read-only-safety.md`; `selftest` proves the guard without Zotero.

verify output goes to a temp directory, named from the spec. `verify` writes its result under `$TMPDIR` at a path computed from the spec's path, and `plan`, `import` and `organize` compute the same path. There is no output flag, so nothing can write to an arbitrary path, parallel agents with different specs cannot collide, and no repo needs a `.gitignore` entry.

Tags are regular (manual) tags; structure is subcollections; notes are prose. This came from a visible mistake: the first import put classification labels inside note text and the connector then stored every tag as *automatic* (type 1: red icon, hidden when "Show Automatic" is off, wiped library-wide by "Delete Automatic Tags in This Library"). The user could not see them. Zotero's own save code keeps an explicit `type: 0` but the connector path forces type 1, so `import` follows up with `organize`, one batched local-API write that sets them as regular tags. The source's sections became subcollections (items are *moved* out of the parent; memberships outside the tree are preserved) rather than `section:` tags, because collections are the folder-like structure humans browse. We first numbered them to preserve source order and then dropped the numbers: alphabetical order was fine. The workflow tag `needs review` goes on anything holding LLM text; the human removes it as they verify (with the explicit caveat that it may become an inbox-style pile).

Notes: title says what and where from; LLM text is labelled; humans edit. The first line of a note is its title in the item tree, so child notes start "Opus lit review: annotation (LLM-generated, unreviewed)". Document-level material goes in one standalone note. Humans do edit imported notes (the user retitled the top-level note within minutes), so "is it already there?" matching for notes is fuzzy (difflib ratio >= 0.8 on the first 600 characters), and note replacement skips anything whose `dateModified != dateAdded`.

PDFs: open access only. Sources are arXiv and OpenAlex `best_oa_location`/`locations[].pdf_url` (no email needed, unlike Unpaywall, which wants one). The bytes must start with `%PDF-`; default cap 50 MB. Closed-access papers simply get none. Group-library PDFs count against the group owner's storage quota (the first run put ~78 MB into a shared group; report totals before bulk imports). Some ISLS/publisher PDFs 403 non-browser clients.

Location. Source of truth is this repo; `~/.agents/skills/zotero` is a symlink to `skills/zotero`, and `~/.claude/skills/zotero` points at that. No `allowed-tools` for write commands, so Claude Code's normal permission prompts apply to them.

## What was imported (CogSec Group 1, as a sanity reference)

29 items (15 articles, 6 conference papers, 7 preprints, 1 chapter), 30 child notes, 1 standalone note, 16 PDFs, 7 subcollections. The 3 initially held-back references were resolved by the user in the same session: Mameli & Passini (user supplied the real DOI) and arXiv:2604.10991 were imported with an explicit caveat note, and Sun et al. 2020 was dropped. The top-level note records this. 2 pilot items sit in the group's trash.

## Known gaps and ideas

- The `Zotero < 10` branch of the version gate is untested (no older Zotero available).
- Whether Claude Code's permission matcher expands `${CLAUDE_SKILL_DIR}` through the symlink chain in `allowed-tools` was not verified in-session; check that the pre-approved commands (read-only ones, `verify` and `where`) run without prompts in a fresh session.
- Pipeline scripts used for the CogSec doc (doc text -> spec JSON, note retrofit) were one-offs and are not included; the shape is `references/spec-example.json`.
- No `update` for existing item *fields*; `write` is the generic escape (array of `{key, version, ...}`).
- Possible: PDF annotation import, Unpaywall (needs an email), per-entry target collections, a `find` that searches the whole subtree.

## References

The thread that started it
- Zotero forum, "MCP for Zotero: connect your library to Claude, ChatGPT and other AI assistants": <https://forums.zotero.org/discussion/130133/mcp-for-zotero-connect-your-library-to-claude-chatgpt-and-other-ai-assistants>
  - Related threads it links: <https://forums.zotero.org/discussion/128934> (zotero-mcp-plugin), <https://forums.zotero.org/discussion/128857> ("clean and solid MCP server for Zotero"), a comment at <https://forums.zotero.org/discussion/comment/505280>

Zotero MCP servers / Claude integrations seen
- MCP for Zotero (hosted proxy to the Web API): <https://mcpforzotero.alejandroarnaud.dev>
- zotero-native-mcp (local API, Zotero 10+, write approval dialog, 28 tools; also the source for the Zotero-10-writes insight): <https://github.com/dvdsosa/zotero-native-mcp>
- Zoteus (TypeScript, local API on Zotero 10+, connector protocol on older): <https://github.com/oscardvs/zoteus>, docs <https://zoteus.com/docs>
- 54yyyu/zotero-mcp (the more established Python one; writes, Better BibTeX lookup, semantic search): <https://github.com/54yyyu/zotero-mcp>
- claude-zotero-skills (two Claude skill files for reading Zotero files): <https://github.com/dougwyu/claude-zotero-skills>

Zotero CLIs for agents
- zot / zotero-cli-cc (PyPI `zotero-cli-ai`; SQLite reads + Web API writes; ships a Claude Code skill; AGPL): <https://github.com/Agents365-ai/zotero-cli-cc>
- cli-anything-zotero (40+ commands, connector/local API + a JS-bridge plugin; Apache-2.0): <https://forums.zotero.org/discussion/130793/cli-anything-zotero-let-ai-agents-manage-your-zotero-library-40-cli-commands-for-zotero-7-8>
- pyzotero (library, supports the local API): <https://github.com/urschrei/pyzotero>
- zotero-cli-agent (semantic search, `zsearch`; semantic-craft) and the older Tinyscript `zotero-cli-tool`: surfaced in a web search, not examined.

The skill + CLI pattern
- Tavily: <https://www.tavily.com/>; its skills <https://github.com/tavily-ai/skills>; the `skills` CLI that manages `~/.agents/skills`: <https://github.com/vercel-labs/skills>
- Agent Skills standard: <https://agentskills.io>; Claude Code skills docs: <https://code.claude.com/docs/en/skills>

Zotero API and behaviour (canonical)
- Local API (read/write split, authorize flow, Server-ID header, Zotero 10 notes): <https://www.zotero.org/support/dev/web_api/v3/local_api>
- Web API basics and write requests (multi-object writes, 50-object limit, tag objects): <https://www.zotero.org/support/dev/web_api/v3/basics>, <https://www.zotero.org/support/dev/web_api/v3/write_requests>
- Automatic vs manual tags, "Show Automatic", "Delete Automatic Tags": <https://www.zotero.org/support/collections_and_tags>
- Announcement of the local read-only API in Zotero 7 beta: <https://groups.google.com/g/zotero-dev/c/ElvHhIFAXrY>
- Why connector tags come out automatic (tag type handling in the item saver): <https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/translation/translate_item.js>
- HTTP safe methods: <https://httpwg.org/specs/rfc9110.html#safe.methods>

Metadata and PDF sources
- Crossref REST API: <https://api.crossref.org>; arXiv API: <https://info.arxiv.org/help/api/>; OpenAlex: <https://docs.openalex.org>
