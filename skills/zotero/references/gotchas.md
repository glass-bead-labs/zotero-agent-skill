# Gotchas

Read this when something fails or looks odd.

- Library id (`L21`), group id (`6713340`), collection key (`GLCG2D2J`) and connector collection id (`C288`) differ; the script maps names to all of them. Collection names must be unique within a library.
- Local-API writes need `Zotero-Server-ID` (any response header) and a single-use `Zotero-API-Key` from `POST /api/local/authorize`; more than 5 dialog-triggering requests a minute get 429.
- After `organize`, papers live in subcollections: any lookup must search the parent and its subcollections (the script does; ad-hoc scripts must too). A batch write whose objects are all unchanged returns `unchanged`, not `successful`, and still costs a dialog: check the payload before writing.
- The connector does not dedupe; the script does (DOI/arXiv ID, title for unverified, fuzzy text for notes). Trashed items do not count.
- Crossref 429s under bursts (script backs off); arXiv asks for ~3 s between calls and the script does not wait, so a long run of arXiv IDs can be throttled; some publisher/ISLS PDFs 403 scripts, so list them for manual download.
- `organize` waits 3 min per approval dialog. If nobody clicks Allow it stops with `NOT applied` (item batch) or `NOT created` (subcollections); batches already approved stay applied. Run `organize` again: it works from the library's current state and skips items already correct (a dry run on three already-organized specs showed 0 changes).
- `attach --item` makes the (empty) attachment item before the upload. If the upload or registration then fails or is denied, that empty attachment stays under the parent; the error names its key (`trash LIB KEY`). A file already attached is not detected: attaching the same PDF twice makes two attachments.
