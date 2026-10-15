# Gotchas

Read this when something fails or looks odd.

- Library id (`L21`), group id (`6713340`), collection key (`GLCG2D2J`) and connector collection id (`C288`) differ; the script maps names to all of them. Collection names must be unique within a library.
- Local-API writes need `Zotero-Server-ID` (any response header) and a single-use `Zotero-API-Key` from `POST /api/local/authorize`; more than 5 dialog-triggering requests a minute get 429.
- After `organize`, papers live in subcollections: any lookup must search the parent and its subcollections (the script does; ad-hoc scripts must too). A batch write whose objects are all unchanged returns `unchanged`, not `successful`, and still costs a dialog: check the payload before writing.
- The connector does not dedupe; the script does (DOI/arXiv ID, title for unverified, fuzzy text for notes). Trashed items do not count.
- Crossref 429s under bursts (script backs off); arXiv asks for ~3 s between calls and the script does not wait, so a long run of arXiv IDs can be throttled; some publisher/ISLS PDFs 403 scripts, so list them for manual download.
