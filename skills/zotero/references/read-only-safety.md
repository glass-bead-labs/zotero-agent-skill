# Why the read-only commands are read-only

`SKILL.md` lists the commands pre-approved in `allowed-tools` (`status`, `libs`, `colls`, `ls`, `find`, `notes`, `path`, `plan`, `where`, `selftest`) and states that they only issue HTTP GET to `127.0.0.1:23119` and create, modify or delete nothing. This page is the argument for that, so it can be checked rather than trusted. It also covers `verify`, which is pre-approved with a wider but still bounded guard (see the end).

This safety is defended in depth:

1. *Zotero's own docs.* The Local API page states reads need no authentication and that "write requests (Zotero 10+) require a local API key" the user grants in a Zotero confirmation dialog: <https://www.zotero.org/support/dev/web_api/v3/local_api>. Writes use POST/PUT/PATCH/DELETE (<https://www.zotero.org/support/dev/web_api/v3/write_requests>); GET is a *safe* method, i.e. read-only by definition (RFC 9110 section 9.2.1: <https://httpwg.org/specs/rfc9110.html#safe.methods>). So even a buggy GET cannot change the library: Zotero would reject a write without a key that the user approved.
2. *The script's runtime guard.* Before any read-only command runs, `http()` in `scripts/zotero.py` is given `GUARD = LOCAL_ONLY`; any non-GET or non-`127.0.0.1` request (including a redirect to one) exits with "BUG: read-only command attempted ...". Prove it without Zotero: `python3 -I ${CLAUDE_SKILL_DIR}/scripts/zotero.py selftest` (every write verb and off-host URL must print `BLOCKED ok`).
3. *Reading the code.* Only `local_write()` (dialog-gated) and `connector()` (import path) can write. `grep -n "connector(\|local_write(" scripts/zotero.py` shows every call site; none is reachable from the commands above (`cmd_plan` calls `build_import()`, `describe()` and `organize(dry=True)`; they only call `zget()`, and `organize` reaches `local_write()` only when `dry` is false).

## `verify`

`verify` is pre-approved but is not read-only: it resolves identifiers over the network and writes one file. Its bounds are the same kind of checkable claim:

1. *Network.* Before `verify` runs, `GUARD` is set to `REMOTE_READ`: GET only, and only to Zotero (`127.0.0.1:23119`), `https://api.crossref.org/`, `https://export.arxiv.org/` and `https://api.openalex.org/`. Redirects are re-checked (`_GuardedRedirect`). `selftest` prints a `BLOCKED ok` line for a POST, another host, a look-alike host, a `user@host` trick, plain http and an off-host redirect. The only data sent is the DOI/arXiv identifier, inside the URL.
2. *Disk.* `verify` writes one file, at the path `resolved_for()` returns for the spec. That path is always inside `run_dir()`, which the script creates mode 0700 under the system temp dir and refuses to use if it is not a directory owned by the user. There is no option to choose the output path.
3. *Reading the code.* `cmd_verify` reaches the network only through `crossref()`, `arxiv()` and `pdf_candidates()` (OpenAlex), plus the version ping in `require_zotero10()`; it does not call `connector()` or `local_write()`.
