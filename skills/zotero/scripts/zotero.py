#!/usr/bin/env python3
"""zotero.py - verify-then-import helper for a running Zotero 10+ desktop app.

Stdlib only. Talks to Zotero on 127.0.0.1:23119:
  * /api/...        local API; reads are open, writes need a per-request approval click in Zotero
  * /connector/...  browser-connector endpoints; `import` uses these (items, child notes, PDFs; no dialog)

Read-only (GET to 127.0.0.1 only; enforced at runtime, see GUARD):
    status | libs | colls LIB | ls LIB [COLL] | find LIB QUERY | notes LIB ITEM... | path LIB (ITEM... | --coll COLL) | plan SPEC | where [SPEC] | selftest
Network read, one local file (GET only, to Zotero + Crossref/arXiv/OpenAlex; enforced at runtime, see GUARD):
    verify SPEC        resolves identifiers; writes one output file (location: resolved_for()); never touches
                       Zotero. plan/import/organize take the same SPEC; `where SPEC` prints the location.
Not read-only:
    import SPEC        writes items/notes/PDFs through the connector (no dialog), then runs `organize`
    organize SPEC      regular tags + subcollection placement (local-API writes: one dialog to create
                       subcollections, then one per 50 items); idempotent, also fixes earlier imports
    attach LIB FILE (--item KEY | --new ID)
                       local file -> existing item (local-API upload: 3 approval dialogs) or new item resolved
                       from a DOI/arXiv ID (connector: no dialog), in one call
    trash | write      local-API writes (one approval dialog per request)

Design rule: the model never types bibliographic metadata. It supplies identifiers
(DOI / arXiv ID); metadata comes from Crossref / arXiv and is compared with what the
source document claimed before anything is written.
"""
import argparse, difflib, hashlib, html, json, mimetypes, os, re, stat, sys, tempfile, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

HOST = os.environ.get("ZOTERO_HOST", "http://127.0.0.1:23119")
UA = {"User-Agent": "zotero-skill/0.1"}
SIM_OK = 0.85


# ---------------------------------------------------------------- http helpers
# GUARD is None (write commands: unguarded) or a tuple of URL prefixes; set in main(). While set, http() allows
# only GET to those prefixes (redirects included) and anything else is fatal.
GUARD = None
LOCAL_ONLY = (HOST + "/",)
REMOTE_READ = LOCAL_ONLY + ("https://api.crossref.org/", "https://export.arxiv.org/", "https://api.openalex.org/")
READ_ONLY_CMDS = {"status", "libs", "colls", "ls", "find", "notes", "path", "plan", "where", "selftest"}  # GUARD = LOCAL_ONLY
VERIFY_CMDS = {"verify"}                                                                  # GUARD = REMOTE_READ


def read_only_violation(url, data, method, allowed=LOCAL_ONLY):
    """None if (url, data, method) is a GET to one of the `allowed` URL prefixes, else an error string."""
    verb = method or ("POST" if data is not None else "GET")
    if verb != "GET" or not url.startswith(allowed):
        if allowed == LOCAL_ONLY:
            return "BUG: read-only command attempted %s %s" % (verb, url)
        return "BUG: verify attempted %s %s (only GET to Zotero, Crossref, arXiv, OpenAlex is allowed)" % (verb, url)
    return None


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Re-applies the guard to redirect targets, so an allowed host cannot bounce a request elsewhere."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        err = read_only_violation(newurl, None, "GET", GUARD or LOCAL_ONLY)
        if err:
            sys.exit(err)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def cmd_selftest(a):
    """No Zotero needed. Proves the guard that protects the read-only commands."""
    h = HOST
    cr, ax = "https://api.crossref.org/works/10.1/x", "https://export.arxiv.org/api/query?id_list=1"
    # (label, (url, data, method), must_block, allowed prefixes)
    cases = [("GET local api", (h + "/api/users/0/groups", None, None), False, LOCAL_ONLY),
             ("GET connector ping", (h + "/connector/ping", None, None), False, LOCAL_ONLY),
             ("POST (implicit, data given)", (h + "/api/groups/1/items", b"{}", None), True, LOCAL_ONLY),
             ("PATCH", (h + "/api/groups/1/items/ABC", None, "PATCH"), True, LOCAL_ONLY),
             ("DELETE", (h + "/api/groups/1/items/ABC", None, "DELETE"), True, LOCAL_ONLY),
             ("connector POST", (h + "/connector/saveItems", b"{}", "POST"), True, LOCAL_ONLY),
             ("GET off-host (Crossref)", (cr, None, None), True, LOCAL_ONLY),
             ("verify: GET Crossref", (cr, None, None), False, REMOTE_READ),
             ("verify: GET arXiv", (ax, None, None), False, REMOTE_READ),
             ("verify: GET local ping", (h + "/connector/ping", None, None), False, REMOTE_READ),
             ("verify: POST Crossref", (cr, b"{}", "POST"), True, REMOTE_READ),
             ("verify: POST local connector", (h + "/connector/saveItems", b"{}", "POST"), True, REMOTE_READ),
             ("verify: GET other host", ("https://example.org/x", None, None), True, REMOTE_READ),
             ("verify: lookalike host", ("https://api.crossref.org.evil.example/x", None, None), True, REMOTE_READ),
             ("verify: userinfo trick", ("https://api.crossref.org@evil.example/x", None, None), True, REMOTE_READ),
             ("verify: plain http", ("http://api.crossref.org/works/1", None, None), True, REMOTE_READ)]
    bad = 0
    for label, args, must_block, allowed in cases:
        blocked = read_only_violation(*args, allowed=allowed) is not None
        bad += blocked != must_block
        print("%-30s %s %s" % (label, "BLOCKED" if blocked else "allowed", "ok" if blocked == must_block else "FAIL"))
    # a redirect from an allowed host to an off-host URL must be refused
    global GUARD
    saved, GUARD = GUARD, REMOTE_READ
    try:
        _GuardedRedirect().redirect_request(urllib.request.Request(cr), None, 302, "Found", {}, "https://example.org/x")
        print("%-30s allowed FAIL" % "verify: redirect off-host")
        bad += 1
    except SystemExit:
        print("%-30s BLOCKED ok" % "verify: redirect off-host")
    finally:
        GUARD = saved
    print("read-only commands:", ", ".join(sorted(READ_ONLY_CMDS)))
    print("network-read commands:", ", ".join(sorted(VERIFY_CMDS)))
    sys.exit(1 if bad else 0)


def http(url, data=None, headers=None, method=None, timeout=60, raw=False):
    if GUARD:
        err = read_only_violation(url, data, method, GUARD)
        if err:
            sys.exit(err)
    h = dict(UA)
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    for attempt in range(4):
        try:
            with (urllib.request.build_opener(_GuardedRedirect).open if GUARD else urllib.request.urlopen)(req, timeout=timeout) as r:
                body = r.read()
                return (body, r.headers) if raw else body
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(4 * (attempt + 1))
                continue
            raise


def zget(path, **params):
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    try:
        body, hdr = http(HOST + "/api" + path + q, headers={"Zotero-API-Version": "3"}, raw=True)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            sys.exit("Zotero local API is disabled: Settings > Advanced > 'Allow other applications on this computer to communicate with Zotero'")
        raise
    except urllib.error.URLError:
        sys.exit("Zotero is not running (nothing on %s)" % HOST)
    return json.loads(body), hdr


def connector(endpoint, payload=None, headers=None, data=None):
    h = {"Content-Type": "application/json", "X-Zotero-Connector-API-Version": "3"}
    h.update(headers or {})
    if data is None:
        data = json.dumps(payload if payload is not None else {}).encode()
    body = http(HOST + "/connector/" + endpoint, data=data, headers=h, method="POST")
    return json.loads(body) if body.strip()[:1] in (b"{", b"[") else body


# ---------------------------------------------------------------- libraries / collections
def groups():
    out, _ = zget("/users/0/groups", limit=100)
    return {g["data"]["name"]: g["id"] for g in out}


def lib_path(name):
    if name.lower() in ("my library", "user", "me", "0"):
        return "/users/0"
    g = {n.lower(): i for n, i in groups().items()}
    if name.lower() not in g:
        sys.exit("No library %r. Available: My Library, %s" % (name, ", ".join(sorted(groups()))))
    return "/groups/%s" % g[name.lower()]


def all_collections(lp):
    out, start = [], 0
    while True:
        page, hdr = zget(lp + "/collections", limit=100, start=start)
        out += page
        start += len(page)
        if not page or start >= int(hdr.get("Total-Results", start)):
            return out


def collection_key(lp, name):
    m = [c for c in all_collections(lp) if c["data"]["name"].lower() == name.lower()]
    if len(m) != 1:
        sys.exit("Collection %r: %d matches (collection names must be unique in the library)" % (name, len(m)))
    return m[0]["key"]


def connector_target(library, collection):
    """Map names -> connector target ids ('L21', 'C288') using the connector's tree listing."""
    sel = connector("getSelectedCollection")
    lib_id, in_lib, matches = None, False, []
    for t in sel["targets"]:
        if t["id"].startswith("L"):
            in_lib = t["name"].lower() == library.lower() or (library.lower() in ("my library", "user") and t["id"] == "L1")
            if in_lib:
                lib_id = t["id"]
                if not collection:
                    return lib_id, t["name"]
        elif in_lib and collection and t["name"].lower() == collection.lower():
            matches.append(t)
    if len(matches) == 1:
        return matches[0]["id"], matches[0]["name"]
    sys.exit("Collection %r in library %r: %d matches (names must be unique)" % (collection, library, len(matches)))


# ---------------------------------------------------------------- identifier resolution
def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", html.unescape(s or "").lower()).strip()


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).replace("\n", " ").strip()


def clean(s):
    return re.sub(r"\s+", " ", strip_tags(s)).strip()


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a.startswith(b) or b.startswith(a):  # truncated titles
        return 1.0
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 2)


def creators(people, kind="author"):
    out = []
    for p in people or []:
        if p.get("family"):
            out.append({"creatorType": kind, "firstName": p.get("given", ""), "lastName": p["family"]})
        elif p.get("name"):
            out.append({"creatorType": kind, "lastName": p["name"], "fieldMode": 1})
    return out


def date_of(parts):
    parts = (parts or [[]])[0]
    return "-".join("%02d" % p if i else str(p) for i, p in enumerate(parts) if p)


def crossref(doi):
    try:
        m = json.loads(http("https://api.crossref.org/works/" + urllib.parse.quote(doi)))["message"]
    except urllib.error.HTTPError as e:
        return None, "Crossref HTTP %d" % e.code
    title = clean((m.get("title") or [""])[0])
    sub = clean((m.get("subtitle") or [""])[0])
    if sub and norm(sub) not in norm(title):
        title = f"{title}: {sub}"
    t = m.get("type", "")
    cont = clean((m.get("container-title") or [""])[0])
    it = {"title": title, "creators": creators(m.get("author")), "date": date_of((m.get("issued") or {}).get("date-parts")),
          "DOI": m.get("DOI", doi), "abstractNote": clean(m.get("abstract", "")), "libraryCatalog": "Crossref"}
    if t == "journal-article":
        it.update(itemType="journalArticle", publicationTitle=cont, volume=m.get("volume", ""), issue=m.get("issue", ""),
                  pages=m.get("page", ""), ISSN=(m.get("ISSN") or [""])[0])
    elif t == "proceedings-article":
        it.update(itemType="conferencePaper", proceedingsTitle=cont, pages=m.get("page", ""), publisher=m.get("publisher", ""),
                  conferenceName=(m.get("event") or {}).get("name", ""))
    elif t == "book-chapter":
        it.update(itemType="bookSection", bookTitle=cont, pages=m.get("page", ""), publisher=m.get("publisher", ""),
                  ISBN=(m.get("ISBN") or [""])[0])
        it["creators"] += creators(m.get("editor"), "editor")
    elif t == "posted-content":
        it.update(itemType="preprint", repository=m.get("institution", [{}])[0].get("name", m.get("publisher", "")))
    else:
        it.update(itemType="document", publisher=m.get("publisher", ""))
    return {k: v for k, v in it.items() if v not in ("", None, [])}, None


def arxiv(aid):
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    try:
        root = ET.fromstring(http("https://export.arxiv.org/api/query?id_list=" + aid))
    except urllib.error.HTTPError as e:
        return None, "arXiv HTTP %d" % e.code
    e = root.find("a:entry", ns)
    if e is None or e.find("a:title", ns) is None or "Error" in (e.find("a:title", ns).text or ""):
        return None, "arXiv: not found"
    cat = e.find("x:primary_category", ns)
    it = {"itemType": "preprint", "title": clean(e.find("a:title", ns).text),
          "creators": [{"creatorType": "author", **split_name(a.find("a:name", ns).text)} for a in e.findall("a:author", ns)],
          "abstractNote": clean(e.find("a:summary", ns).text), "date": e.find("a:published", ns).text[:10],
          "repository": "arXiv", "archiveID": "arXiv:" + aid, "url": "https://arxiv.org/abs/" + aid,
          "DOI": "10.48550/arXiv." + aid, "libraryCatalog": "arXiv.org",
          "extra": "arXiv:%s [%s]" % (aid, cat.get("term") if cat is not None else "")}
    pub = e.find("x:doi", ns)
    if pub is not None:
        it["extra"] += "\nPublished version DOI: " + pub.text
    return it, None


def split_name(n):
    parts = n.strip().split()
    return {"firstName": " ".join(parts[:-1]), "lastName": parts[-1]} if len(parts) > 1 else {"lastName": n, "fieldMode": 1}


def pdf_candidates(entry_id, doi):
    if entry_id.lower().startswith("arxiv:"):
        return ["https://arxiv.org/pdf/" + entry_id.split(":", 1)[1]]
    if not doi:
        return []
    try:
        w = json.loads(http("https://api.openalex.org/works/https://doi.org/%s?select=best_oa_location,locations" % urllib.parse.quote(doi)))
    except Exception as ex:
        print("warning: OpenAlex lookup failed for %s (%s); no PDF candidates from it" % (doi, ex), file=sys.stderr)
        return []
    urls = []
    for loc in [w.get("best_oa_location") or {}] + (w.get("locations") or []):
        u = loc.get("pdf_url")
        if u and loc.get("is_oa", True) and u not in urls:
            urls.append(u)
    return urls[:3]


def title_matches(claimed, item):
    c = claimed or {}
    notes = []
    s = sim(c.get("title", ""), item["title"]) if c.get("title") else None
    ok = s is None or s >= SIM_OK
    a = c.get("authors") or ""
    if a and not a.lower().startswith("authors unknown"):
        first = norm(a.split(",")[0])
        lasts = [norm(x.get("lastName", "")) for x in item.get("creators", [])]
        if first and not any(first in l or l in first for l in lasts if l):
            ok = False
            notes.append("first author %r not among resolved authors" % a.split(",")[0])
    y = c.get("year")
    ry = (item.get("date") or "")[:4]
    if y and ry.isdigit() and abs(int(y) - int(ry)) > 1:
        notes.append("year %s vs resolved %s" % (y, ry))
    return ok, s, notes


# ---------------------------------------------------------------- commands
def cmd_status(a):
    print("Zotero %s  connector: up" % ZOTERO_VERSION)
    print("local API: up; %d group libraries" % len(groups()))


def cmd_libs(a):
    print("My Library\t/users/0")
    for n, i in sorted(groups().items()):
        print("%s\t/groups/%s" % (n, i))


def cmd_colls(a):
    cs = all_collections(lib_path(a.lib))
    by = {c["key"]: c for c in cs}
    def depth(c):
        d = 0
        while c["data"].get("parentCollection"):
            c = by[c["data"]["parentCollection"]]; d += 1
        return d
    for c in sorted(cs, key=lambda c: c["data"]["name"].lower()):
        print("%s%s  [%s]  %s items" % ("  " * depth(c), c["data"]["name"], c["key"], c["meta"].get("numItems", "?")))


def cmd_ls(a):
    lp = lib_path(a.lib)
    if a.coll:
        lp += "/collections/" + collection_key(lp, a.coll)
    items, _ = zget(lp + "/items/top", limit=100, include="data,meta")
    for i in items:
        d = i["data"]
        print("%s  %-15s %s | %-70s | children=%s" % (i["key"], d.get("itemType"), (d.get("date") or "")[:4],
              (d.get("title") or d.get("note", "")[:60]).replace("\n", " ")[:70], i["meta"].get("numChildren", 0)))
    print("-- %d top-level items" % len(items))


def cmd_find(a):
    items, _ = zget(lib_path(a.lib) + "/items", q=a.query, qmode="everything", limit=25, itemType="-attachment")
    for i in items:
        d = i["data"]
        print("%s  %s | %s | %s" % (i["key"], d.get("itemType"), d.get("title", "")[:70], d.get("DOI", "")))


def cmd_notes(a):
    """Child notes of each ITEM (key, dates, tags, text); an ITEM that is itself a note is shown as is. Use before adding
    a note with `write`, which does not dedupe."""
    lp = lib_path(a.lib)
    n = 0
    for k in a.items:
        try:
            item, _ = zget(lp + "/items/" + k, include="data")
            d = item["data"]
            notes = [item] if d.get("itemType") == "note" else zget(lp + "/items/%s/children" % k, itemType="note", include="data")[0]
        except urllib.error.HTTPError as e:
            sys.exit("No item %s in %s (HTTP %d)" % (k, a.lib, e.code))
        if d.get("itemType") != "note":
            print("%s  %s" % (k, (d.get("title") or "")[:70]))
        for i in notes:
            d = i["data"]
            text = clean(d.get("note", ""))
            n += 1
            print("  note %s | parent %s | added %s | modified %s | tags: %s%s" % (
                i["key"], d.get("parentItem", "-"), d.get("dateAdded", "")[:10], d.get("dateModified", "")[:10],
                ", ".join(t["tag"] for t in d.get("tags", [])) or "-", " | in trash" if d.get("deleted") else ""))
            print("    " + (text if a.full else text[:200] + ("..." if len(text) > 200 else "")))
    print("-- %d notes" % n)


def cmd_path(a):
    """Local file path of each attachment of the ITEMs (an ITEM that is itself an attachment counts as one), or of
    every attachment in collection --coll (not recursive, like `ls`).

    Uses /file/view/url (200, URL in the body); /file and /file/view answer 302 to file://, which the guard refuses.
    Zotero returns that URL even when nothing is on disk, so existence is checked here."""
    if bool(a.items) == bool(a.coll):
        sys.exit("usage: path LIB ITEM [ITEM...]  |  path LIB --coll COLL")
    lp = lib_path(a.lib)
    atts = []
    if a.coll:
        start = 0
        while True:  # one list covers child attachments and standalone ones
            page, hdr = zget(lp + "/collections/%s/items" % collection_key(lp, a.coll), itemType="attachment", limit=100, start=start, include="data")
            atts += page
            start += len(page)
            if not page or start >= int(hdr.get("Total-Results", start)):
                break
    for k in a.items:
        try:
            item, _ = zget(lp + "/items/" + k, include="data")
            atts += [item] if item["data"].get("itemType") == "attachment" else \
                zget(lp + "/items/%s/children" % k, itemType="attachment", include="data")[0]
        except urllib.error.HTTPError as e:
            sys.exit("No item %s in %s (HTTP %d)" % (k, a.lib, e.code))
    found = 0
    for i in atts:
        d = i["data"]
        row = [d.get("parentItem", "-"), i["key"], d.get("contentType", "")]
        try:
            url = http(HOST + "/api" + lp + "/items/%s/file/view/url" % i["key"], headers={"Zotero-API-Version": "3"}).decode().strip()
        except urllib.error.HTTPError:  # 400 "Not a file attachment": a web link
            print("\t".join(["nofile"] + row + [d.get("url", "")]))
            continue
        path = urllib.request.url2pathname(urllib.parse.urlparse(url).path)
        ok = os.path.isfile(path)
        found += ok
        print("\t".join(["ok" if ok else "missing"] + row + [path]))
    print("-- %d attachments, %d on disk" % (len(atts), found))


def note_present(html_note, existing_texts, threshold=0.8):
    """True if a (possibly human-edited) top-level note resembling this one already exists."""
    mine = norm(clean(html_note)[:600])
    return any(difflib.SequenceMatcher(None, mine, t).ratio() >= threshold for t in existing_texts)


def library_index(lp):
    """(identifiers, normalized titles, note texts) of top-level, non-trashed items in the library."""
    ids, titles, notes, start = set(), set(), [], 0
    while True:
        page, hdr = zget(lp + "/items/top", limit=100, start=start, include="data")
        for i in page:
            d = i["data"]
            titles.add(norm(d.get("title", "")))
            if d.get("itemType") == "note":
                notes.append(norm(clean(d.get("note", ""))[:600]))
            for v in (d.get("DOI"), d.get("archiveID"), d.get("url")):
                if v:
                    ids.add(v.lower().replace("https://doi.org/", "").replace("https://arxiv.org/abs/", "arxiv:"))
        start += len(page)
        if not page or start >= int(hdr.get("Total-Results", start)):
            return ids, titles, notes


def library_tags(lp):
    names, start = set(), 0
    while True:
        page, hdr = zget(lp + "/tags", limit=100, start=start)
        names |= {t["tag"] for t in page}
        start += len(page)
        if not page or start >= int(hdr.get("Total-Results", start)):
            return names


def local_authorize():
    """Ask the user to approve ONE write request. Returns the headers for it (the key is single-use)."""
    _, hdr = http(HOST + "/api/", headers={"Zotero-API-Version": "3"}, raw=True)
    sid = hdr["Zotero-Server-ID"]
    print("Zotero will show an approval dialog - click Allow in the Zotero window (waiting up to 3 min)...", file=sys.stderr)
    body = http(HOST + "/api/local/authorize", data=json.dumps({"appName": "zotero skill"}).encode(), method="POST", timeout=180,
                headers={"Zotero-API-Version": "3", "Zotero-Server-ID": sid, "Content-Type": "application/json"})
    return {"Zotero-API-Version": "3", "Zotero-Server-ID": sid, "Zotero-API-Key": json.loads(body)["key"]}


def local_write(lib, items, kind="items"):
    """Batch-write objects ([{key, version, ...fields}]) to /items (or /collections) via the local API.

    Zotero asks the user to approve every write request (the key from /api/local/authorize is
    single-use), so always send ONE request holding as many objects as possible (max 50)."""
    lp = lib_path(lib)
    h = local_authorize()
    h["Content-Type"] = "application/json"
    out = http(HOST + "/api" + lp + "/" + kind, data=json.dumps(items).encode(), method="POST", headers=h)
    return json.loads(out)


def read_local_file(path, max_mb):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        sys.exit("No such file: %s" % path)
    if os.path.getsize(path) > max_mb * 1e6:
        sys.exit("%s is %.0f MB, over --max-mb %d" % (path, os.path.getsize(path) / 1e6, max_mb))
    with open(path, "rb") as f:
        body = f.read()
    ctype = "application/pdf" if body[:5] == b"%PDF-" else mimetypes.guess_type(path)[0] or "application/octet-stream"
    if path.lower().endswith(".pdf") and ctype != "application/pdf":
        sys.exit("%s is named .pdf but is not a PDF (no %%PDF- header)" % path)
    return path, body, ctype


def attach_existing(lib, key, path, body, ctype, title):
    """Store a copy of the file as a new child attachment of item KEY, using the Web API's file-upload protocol that
    Zotero 10 serves locally. Each of the three write requests needs its own approval click:
    (1) create the attachment item, (2) authorize the upload, (3) register it. The bytes go to a staging URL
    that needs no approval."""
    lp = lib_path(lib)
    try:
        parent, _ = zget(lp + "/items/" + key, include="data")
    except urllib.error.HTTPError as e:
        sys.exit("No item %s in %s (HTTP %d)" % (key, lib, e.code))
    pd = parent["data"]
    if pd.get("itemType") in ("attachment", "note"):
        sys.exit("%s is a%s %s; give the parent item's key" % (key, "n" if pd["itemType"] == "attachment" else "", pd["itemType"]))
    if pd.get("deleted"):
        sys.exit("%s is in the trash" % key)
    name = os.path.basename(path)
    md5 = hashlib.md5(body).hexdigest()
    mtime = int(os.path.getmtime(path) * 1000)

    print("1/3 create attachment item under %s (%s)" % (key, (pd.get("title") or "")[:60]), flush=True)
    r = local_write(lib, [{"itemType": "attachment", "linkMode": "imported_file", "parentItem": key, "title": title,
                           "contentType": ctype, "filename": name, "tags": [], "relations": {}}])
    if not r.get("success"):
        sys.exit("could not create the attachment item: %s" % json.dumps(r.get("failed")))
    akey = r["success"]["0"]

    def finish(msg):
        sys.exit("%s\nThe empty attachment item %s remains under %s: re-run, or `trash %s %s`." % (msg, akey, key, lib, akey))

    print("2/3 authorize upload of %s (%.0f KB)" % (name, len(body) / 1024), flush=True)
    h = local_authorize()
    h.update({"Content-Type": "application/x-www-form-urlencoded", "If-None-Match": "*"})
    form = urllib.parse.urlencode({"md5": md5, "filename": name, "filesize": len(body), "mtime": mtime}).encode()
    try:
        auth = json.loads(http(HOST + "/api%s/items/%s/file" % (lp, akey), data=form, method="POST", headers=h))
    except urllib.error.HTTPError as e:
        finish("upload authorization failed (HTTP %d: %s)" % (e.code, e.read().decode(errors="replace")[:200]))
    if auth.get("exists"):
        finish("Zotero reports this file already exists for the new item (unexpected)")
    # prefix + bytes + suffix is the Web API's S3 body convention; locally both are empty
    try:
        http(auth["url"], data=auth.get("prefix", "").encode() + body + auth.get("suffix", "").encode(), method="POST",
             headers={"Content-Type": auth.get("contentType") or ctype}, timeout=180)
    except urllib.error.HTTPError as e:
        finish("upload failed (HTTP %d: %s)" % (e.code, e.read().decode(errors="replace")[:200]))

    print("3/3 register upload", flush=True)
    h = local_authorize()
    h.update({"Content-Type": "application/x-www-form-urlencoded", "If-None-Match": "*"})
    try:
        http(HOST + "/api%s/items/%s/file" % (lp, akey), data=urllib.parse.urlencode({"upload": auth["uploadKey"]}).encode(),
             method="POST", headers=h)
    except urllib.error.HTTPError as e:
        finish("registering the upload failed (HTTP %d: %s)" % (e.code, e.read().decode(errors="replace")[:200]))
    return akey


def attach_new(lib, coll, ident, path, body, ctype, title):
    """Create a top-level item from a DOI/arXiv ID (metadata from Crossref/arXiv, never typed) and attach the local
    file to it, through the connector in one session: no approval dialog."""
    lp = lib_path(lib)
    item, err = arxiv(ident.split(":", 1)[1]) if ident.lower().startswith("arxiv:") else crossref(ident)
    if item is None:
        sys.exit("%s did not resolve: %s" % (ident, err))
    ids, _, _ = library_index(lp)
    for v in (item.get("DOI", "").lower(), ident.lower()):
        if v and v in ids:
            sys.exit("%s is already in %s (`find %s \"%s\"` for its key); use --item KEY to attach the file to it" % (ident, lib, lib, v))
    target, tname = connector_target(lib, coll)
    session = "zskill-%d" % int(time.time())
    item = dict(item, id="i1", tags=[], notes=[], attachments=[])
    print("new item: %s | %s" % ((item.get("title") or "")[:70], "; ".join(c.get("lastName", "") for c in item.get("creators", [])[:3])))
    connector("saveItems", {"sessionID": session, "uri": "http://localhost/zotero-skill", "items": [item]})
    connector("updateSession", {"sessionID": session, "target": target, "tags": ""})
    # a bare file name as `url`: Zotero uses it to name the file, and a local path must not end up in the (synced) record
    meta = {"id": "ai1", "url": os.path.basename(path), "contentType": ctype, "parentItemID": "i1",
            "title": title, "sessionID": session}
    connector("saveAttachment", headers={"Content-Type": ctype, "X-Metadata": json.dumps(meta)}, data=body)
    return tname


def cmd_attach(a):
    """Attach a local file (PDF, or e.g. docx) to an existing item, or to a new item created from an identifier."""
    if bool(a.item) == bool(a.new):
        sys.exit("usage: attach LIB FILE --item KEY  |  attach LIB FILE --new DOI_OR_ARXIV_ID [--coll COLL]")
    if a.coll and not a.new:
        sys.exit("--coll only applies to --new (an existing item keeps its collections)")
    path, body, ctype = read_local_file(a.file, a.max_mb)
    title = a.title or ("Full Text PDF" if ctype == "application/pdf" else os.path.basename(path))
    if a.item:
        k = attach_existing(a.lib, a.item, path, body, ctype, title)
        print("attached %s (%.0f KB) to %s as attachment %s" % (os.path.basename(path), len(body) / 1024, a.item, k))
    else:
        t = attach_new(a.lib, a.coll, a.new, path, body, ctype, title)
        print("created %s and attached %s (%.0f KB) -> %s" % (a.new, os.path.basename(path), len(body) / 1024, t))


def cmd_trash(a):
    lp = lib_path(a.lib)
    objs = []
    for k in a.keys:
        it, _ = zget(lp + "/items/" + k)
        objs.append({"key": k, "version": it["version"], "deleted": 1})
    r = local_write(a.lib, objs)
    print("trashed:", sorted(r.get("successful", {}) and [v["key"] for v in r["successful"].values()]), "failed:", r.get("failed"))


def cmd_write(a):
    r = local_write(a.lib, json.load(open(a.json)), kind="collections" if a.collections else "items")
    print("successful: %d | unchanged: %d | failed: %d" % (len(r.get("successful", {})), len(r.get("unchanged", {})), len(r.get("failed", {}))))
    for i, v in sorted((r.get("successful") or {}).items(), key=lambda kv: int(kv[0])):
        print("  object %s -> %s" % (i, v.get("key")))
    for k, v in (r.get("failed") or {}).items():
        print("  failed %s: %s" % (k, v))


def run_dir(create=False):
    """The directory for verify output: <system temp dir>/zotero-skill. The temp dir honours $TMPDIR.
    The directory is private (mode 0700, owned by us, never a symlink), so the fixed name is safe even in a
    shared /tmp. Outputs for different specs are told apart by the spec path (see resolved_for)."""
    d = os.path.join(tempfile.gettempdir(), "zotero-skill")
    if create:
        os.makedirs(d, mode=0o700, exist_ok=True)
        st = os.lstat(d)
        if not stat.S_ISDIR(st.st_mode) or (hasattr(os, "getuid") and st.st_uid != os.getuid()):
            sys.exit("refusing to use %s: not a directory owned by you" % d)
        os.chmod(d, 0o700)
    return d


def resolved_for(spec_arg, create=False):
    """The single definition of where a spec's verify output lives:
    <run_dir()>/<spec file stem>-<8 hex of the spec's real path>.resolved.json.
    It depends on the spec path alone, so verify, plan, import, organize and where agree without a second
    file name, and two different specs can never share an output file. create=True makes the directory."""
    spec_path = os.path.realpath(spec_arg)
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.splitext(os.path.basename(spec_path))[0])[:60] or "spec"
    return os.path.join(run_dir(create), "%s-%s.resolved.json" % (stem, hashlib.sha1(spec_path.encode()).hexdigest()[:8]))


def load_resolved(spec_arg):
    p = resolved_for(spec_arg)
    if not os.path.isfile(p):
        sys.exit("no verify output for %s (expected %s); run `verify %s` first" % (spec_arg, p, spec_arg))
    return json.load(open(p))


IMPORTABLE = ("ok", "accepted", "unverified")  # verify statuses that import / organize act on


def importable_entries(spec):
    return [e for e in spec["entries"] if e["status"] in IMPORTABLE]


def cmd_where(a):
    d = run_dir()
    if a.spec:
        p = resolved_for(a.spec)
        print(p)
        sys.exit(0 if os.path.isfile(p) else 1)
    print(d)
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        st = os.stat(os.path.join(d, n))
        print("  %-50s %8d bytes  %s" % (n, st.st_size, time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))))


def cmd_verify(a):
    spec = json.load(open(a.spec))
    out_path = resolved_for(a.spec, create=True)
    res = {"library": spec["library"], "collection": spec.get("collection"), "tags": spec.get("tags", []),
           "tag_type": spec.get("tag_type", "manual"),
           "standalone_notes": spec.get("standalone_notes", []), "entries": []}
    for e in spec["entries"]:
        r = dict(e)
        ident = e.get("id") or "(manual) " + (e.get("manual") or {}).get("title", "?")[:24]
        item, err = None, None
        if e.get("manual"):
            # Escape hatch: metadata NOT resolved from Crossref/arXiv. Only with the user's explicit, quoted approval.
            m = e["manual"]
            if not e.get("user_affirmed"):
                r.update(status="refused", detail="manual metadata needs user_affirmed: the user's explicit approval, quoted")
            elif not (m.get("itemType") and m.get("title")):
                r.update(status="refused", detail="manual metadata needs itemType and title")
            else:
                r.update(status="unverified", item=dict(m), detail="UNVERIFIED; user affirmed: %s" % e["user_affirmed"][:80])
        elif not e.get("id"):
            r.update(status="refused", detail="entry has neither id nor manual metadata")
        else:
            try:
                item, err = arxiv(ident.split(":", 1)[1]) if ident.lower().startswith("arxiv:") else crossref(ident)
            except urllib.error.URLError as ex:
                item, err = None, str(ex)
            if item is None:
                r.update(status="notfound", detail=err)
        if not r.get("status"):
            ok, s, notes = title_matches(e.get("claimed"), item)
            r.update(item=item, title_sim=s, detail="; ".join(notes))
            r["status"] = "ok" if ok else ("accepted" if e.get("accept_mismatch") else "mismatch")
            if not a.no_pdf and r["status"] in ("ok", "accepted"):
                r["pdf_urls"] = ([e["pdf"]] if e.get("pdf") else pdf_candidates(ident, item.get("DOI")))
        res["entries"].append(r)
        flag = {"ok": "  ok  ", "accepted": "accept", "mismatch": "MISMATCH", "notfound": "NOTFOUND",
                "unverified": "UNVERIF", "refused": "REFUSED"}[r["status"]]
        print("%-8s %-34s sim=%-4s pdf=%-3s %s | %s" % (flag, ident, r.get("title_sim"), len(r.get("pdf_urls", [])),
              (r.get("item") or {}).get("title", "")[:48], r.get("detail", "")))
    with open(out_path, "w") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    n = {k: sum(1 for x in res["entries"] if x["status"] == k) for k in ("ok", "accepted", "unverified", "mismatch", "notfound", "refused")}
    print("\n%s\nwrote %s" % (n, out_path))


def fetch_pdf(urls):
    for u in urls:
        try:
            body = http(u, headers={"Accept": "application/pdf"}, timeout=90)
        except Exception as ex:
            continue
        if body[:5] == b"%PDF-" and len(body) < 150_000_000:
            return u, body
    return None, None


def note_html(inner):
    return inner if inner.lstrip().startswith("<") else "<p>%s</p>" % html.escape(inner)


def tag_objs(names, kind):
    """Zotero-native tag objects. type 0 = manual (default: regular tags a human can browse by);
    type 1 = automatic (red icon, hidden when 'Show Automatic' is off, bulk-deletable library-wide).
    NOTE: the connector's save path forces type 1; manual tags are set afterwards by organize()."""
    seen, out = set(), []
    for t in names:
        t = " ".join(str(t).split())
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append({"tag": t, "type": kind})
    return out


def wanted_names(spec, e):
    names = list(spec.get("tags", [])) + list(e.get("tags", []))
    if e.get("status") == "unverified":
        names.append("unverified metadata")
    return names


def build_import(spec, allow_unverified):
    """Pure planning: reads Zotero (GET only) and returns what `import` would send."""
    lp = lib_path(spec["library"])
    coll_key = collection_key(lp, spec["collection"]) if spec.get("collection") else None
    ids, titles, note_texts = library_index(lp)
    kind = 1 if spec.get("tag_type") == "automatic" else 0
    items, entries, skipped = [], [], []
    for i, e in enumerate(spec["entries"], 1):
        label = e.get("id") or (e.get("manual") or {}).get("title", "?")[:50]
        st = e["status"]
        if st == "unverified" and not allow_unverified:
            skipped.append((label, "unverified metadata: needs --allow-unverified")); continue
        if st not in IMPORTABLE:
            skipped.append((label, st)); continue
        item = e["item"]
        doi = item.get("DOI", "").lower()
        if (doi and doi in ids) or (e.get("id") and e["id"].lower() in ids) or (st == "unverified" and norm(item["title"]) in titles):
            skipped.append((label, "already in library")); continue
        names = wanted_names(spec, e)
        notes = list(e.get("notes", []))
        if st == "unverified":
            notes.append("<h3>Unverified metadata</h3><p>The fields of this record were not resolved from Crossref/arXiv; "
                         "they were supplied by an LLM or copied from a source citation, and imported because the user "
                         "explicitly approved it:</p><blockquote>%s</blockquote><p>Check against the paper, then remove "
                         "the tag &ldquo;unverified metadata&rdquo;.</p>" % html.escape(e["user_affirmed"]))
        it = dict(item)
        it["id"] = "i%d" % i
        it["tags"] = tag_objs(names, kind)
        it["notes"] = [{"note": note_html(n)} for n in notes]
        it["attachments"] = []
        items.append(it)
        entries.append(e)
    n_entries = len(items)
    for n in spec.get("standalone_notes", []):
        if note_present(n, note_texts):
            skipped.append(("standalone note %r" % clean(n)[:40], "already in library"))
            continue
        items.append({"id": "n%d" % len(items), "itemType": "note", "note": note_html(n),
                      "tags": tag_objs(spec.get("tags", []), kind)})
    return {"lp": lp, "coll_key": coll_key, "items": items, "entries": entries, "n_entries": n_entries,
            "skipped": skipped, "kind": kind}


def describe(spec, b):
    kind = "automatic" if b["kind"] == 1 else "regular"
    print("target: %s / %s%s" % (spec["library"], spec.get("collection") or "(library root)",
                                 "  [collection key %s]" % b["coll_key"] if b["coll_key"] else ""))
    items = b["items"]
    print("new items: %d | child notes: %d | standalone notes: %d | PDF candidates: %d entries"
          % (b["n_entries"], sum(len(i.get("notes", [])) for i in items), len(items) - b["n_entries"],
             sum(1 for e in b["entries"] if e.get("pdf_urls"))))
    unv = [e for e in b["entries"] if e["status"] == "unverified"]
    for e in unv:
        print("  UNVERIFIED (user affirmed): %s | %s" % ((e["item"].get("title") or "")[:60], e["user_affirmed"][:80]))
    used = {t["tag"] for i in items for t in i.get("tags", [])}
    have = library_tags(b["lp"])
    print("tags (%s): new %s | already in library %s" % (kind, sorted(used - have), sorted(used & have)))
    if b["kind"] == 0 and items:
        print("note: import will follow with ONE Zotero approval dialog per 50 items to set these as regular tags")
    for s_ in b["skipped"]:
        print("  skip", *s_)


def subcolls_of(e):
    v = e.get("subcollection")
    return [] if not v else ([v] if isinstance(v, str) else list(v))


def organize(spec, entries, note_htmls, dry=False):
    """Bring items already in the target collection tree to the spec's state (local-API writes).

    * tags: set as regular (manual) tags, because the connector forces automatic ones;
    * placement: items with `subcollection` are MOVED out of the parent collection into those
      subcollections (created when missing); memberships outside this tree are preserved.
    One approval dialog to create subcollections, then one per 50 items."""
    lp = lib_path(spec["library"])
    parent = collection_key(lp, spec["collection"]) if spec.get("collection") else None
    kind = 1 if spec.get("tag_type") == "automatic" else 0
    colls = all_collections(lp)
    subs = {c["data"]["name"].lower(): c["key"] for c in colls if parent and c["data"].get("parentCollection") == parent}
    want_names = []
    for e in entries:
        for n in subcolls_of(e):
            if n.lower() not in [w.lower() for w in want_names]:
                want_names.append(n)
    missing = sorted(n for n in want_names if n.lower() not in subs)
    if missing:
        print("subcollections to create under %r: %s" % (spec["collection"], missing))
        if dry:
            for n in missing:
                subs[n.lower()] = "NEW:" + n
        else:
            try:
                r = local_write(spec["library"], [{"name": n, "parentCollection": parent} for n in missing], kind="collections")
            except TimeoutError:
                sys.exit("subcollections NOT created (no approval within 3 min); run `organize` again")
            made = r.get("success") or {k: v["key"] for k, v in r.get("successful", {}).items()}
            if len(made) != len(missing):
                sys.exit("could not create all subcollections: %s" % json.dumps(r.get("failed")))
            for i, n in enumerate(missing):
                subs[n.lower()] = made[str(i)]
            print("  created %d subcollections" % len(missing))
    tree_keys = ({parent} if parent else set()) | {k for k in subs.values() if not k.startswith("NEW:")}

    # items that currently live anywhere in the tree (parent + its subcollections)
    recs, start_paths = {}, [lp + ("/collections/" + k if k else "") for k in sorted(tree_keys)] or [lp]
    for base in start_paths:
        start = 0
        while True:
            page, hdr = zget(base + "/items/top", limit=100, start=start, include="data")
            for i in page:
                recs[i["key"]] = i
            start += len(page)
            if not page or start >= int(hdr.get("Total-Results", start)):
                break
    by_id, by_title, notes = {}, {}, []
    for i in recs.values():
        d = i["data"]
        for v in (d.get("DOI"), d.get("archiveID")):
            if v:
                by_id[v.lower()] = i
        if d.get("title"):
            by_title[norm(d["title"])] = i
        if d.get("itemType") == "note":
            notes.append((i, norm(clean(d.get("note", ""))[:600])))

    objs, missing_items, moves = [], [], []
    def plan_change(i, names, subnames, label):
        if i is None:
            missing_items.append(label); return
        d = i["data"]
        want_tags = tag_objs(names, kind)
        tags_differ = sorted((t["tag"], t["type"]) for t in want_tags) != sorted((t["tag"], t.get("type", 0)) for t in d.get("tags", []))
        cur = list(d.get("collections", []))
        new = cur
        if subnames:
            dest = [subs[n.lower()] for n in subnames]
            new = [k for k in cur if k not in tree_keys and not k.startswith("NEW:")] + dest
        elif parent and parent not in cur:
            new = cur + [parent]
        coll_differ = sorted(new) != sorted(cur)
        if tags_differ or coll_differ:
            o = {"key": i["key"], "version": i["version"]}
            if tags_differ:
                o["tags"] = want_tags
            if coll_differ:
                o["collections"] = new
                moves.append((label, subnames))
            objs.append(o)
    for e in entries:
        item = e.get("item") or {}
        i = (by_id.get(item.get("DOI", "").lower()) or by_id.get((e.get("id") or "").lower())
             or by_title.get(norm(item.get("title", ""))))
        plan_change(i, wanted_names(spec, e), subcolls_of(e), e.get("id") or item.get("title", "?")[:40])
    for n in note_htmls:
        mine = norm(clean(n)[:600])
        best = max(notes, key=lambda rt: difflib.SequenceMatcher(None, mine, rt[1]).ratio(), default=None)
        ok = best and difflib.SequenceMatcher(None, mine, best[1]).ratio() >= 0.8
        plan_change(best[0] if ok else None, spec.get("tags", []), [], "standalone note")
    print("organize: %d items to update (%d tag changes, %d placements), %d not found in the collection tree" %
          (len(objs), sum(1 for o in objs if "tags" in o), len(moves), len(missing_items)))
    for m in missing_items:
        print("  not found:", m)
    if dry:
        from collections import Counter
        for name, c in sorted(Counter(n for _, ns in moves for n in ns).items()):
            print("  -> %-50s %d item(s)" % (name, c))
        return
    for i in range(0, len(objs), 50):
        try:
            r = local_write(spec["library"], objs[i:i + 50])
            print("  batch %d: updated %d, failed %d" % (i // 50 + 1, len(r.get("successful", {})), len(r.get("failed", {}))))
            for k, v in (r.get("failed") or {}).items():
                print("    failed:", v)
        except (urllib.error.HTTPError, TimeoutError) as ex:  # no click within local_write's 3 min is a TimeoutError
            why = "no approval within 3 min" if isinstance(ex, TimeoutError) else "HTTP %d: approval denied or expired" % ex.code
            print("  batch %d NOT applied (%s); run `organize` again later" % (i // 50 + 1, why))
            break


def cmd_organize(a):
    spec = load_resolved(a.spec)
    organize(spec, importable_entries(spec), spec.get("standalone_notes", []), dry=a.dry_run)


def cmd_plan(a):
    spec = load_resolved(a.spec)
    describe(spec, build_import(spec, True))
    print("-- placement/tags of items already in the collection tree:")
    organize(spec, importable_entries(spec), spec.get("standalone_notes", []), dry=True)


def cmd_import(a):
    spec = load_resolved(a.spec)
    b = build_import(spec, a.allow_unverified)
    target, tname = connector_target(spec["library"], spec.get("collection"))
    describe(spec, b)
    items, entries = b["items"], b["entries"]
    if not items:
        return
    session = "zskill-%d" % int(time.time())
    connector("saveItems", {"sessionID": session, "uri": "http://localhost/zotero-skill", "items": items})
    connector("updateSession", {"sessionID": session, "target": target, "tags": ""})
    got = 0
    for it, e in zip(items, entries):
        if a.no_pdf or not e.get("pdf_urls"):
            continue
        url, body = fetch_pdf(e["pdf_urls"])
        if not body:
            print("  no PDF retrievable:", e.get("id")); continue
        if len(body) > a.max_pdf_mb * 1e6:
            print("  PDF too large, skipped (%.0f MB > %d):" % (len(body) / 1e6, a.max_pdf_mb), e.get("id"), url); continue
        meta = {"id": "a" + it["id"], "url": url, "contentType": "application/pdf", "parentItemID": it["id"],
                "title": "Full Text PDF", "sessionID": session}
        connector("saveAttachment", headers={"Content-Type": "application/pdf", "X-Metadata": json.dumps(meta)}, data=body)
        got += 1
        print("  PDF attached: %-30s %6.0f KB  %s" % (e.get("id"), len(body) / 1024, url))
    print("imported %d items (+%d standalone notes), %d PDFs -> %s" % (b["n_entries"], len(items) - b["n_entries"], got, tname))
    if (b["kind"] == 0 and any(i.get("tags") for i in items)) or any(subcolls_of(e) for e in entries):
        print("placing items and setting regular tags: Zotero will ask for approval...")
        organize(spec, entries, [i["note"] for i in items if i.get("itemType") == "note"])


ZOTERO_VERSION = None


def require_zotero10():
    """Fail fast unless Zotero 10+ is running (older versions lack local-API writes)."""
    global ZOTERO_VERSION
    try:
        _, hdr = http(HOST + "/connector/ping", raw=True)
    except urllib.error.URLError:
        sys.exit("Zotero is not running (nothing on %s)" % HOST)
    ZOTERO_VERSION = hdr.get("X-Zotero-Version", "0")
    if int(re.match(r"\d+", ZOTERO_VERSION).group()) < 10:
        sys.exit("Requires Zotero 10+ (found %s)" % ZOTERO_VERSION)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("status").set_defaults(f=cmd_status)
    sp.add_parser("selftest", help="verify the read-only guard (needs no Zotero)").set_defaults(f=cmd_selftest)
    sp.add_parser("libs").set_defaults(f=cmd_libs)
    s = sp.add_parser("where", help="print the directory for verify output (and its files), or the output path for SPEC")
    s.add_argument("spec", nargs="?"); s.set_defaults(f=cmd_where)
    s = sp.add_parser("colls"); s.add_argument("lib"); s.set_defaults(f=cmd_colls)
    s = sp.add_parser("ls"); s.add_argument("lib"); s.add_argument("coll", nargs="?"); s.set_defaults(f=cmd_ls)
    s = sp.add_parser("find"); s.add_argument("lib"); s.add_argument("query"); s.set_defaults(f=cmd_find)
    s = sp.add_parser("notes", help="child notes of the ITEMs (key, dates, tags, text); check before adding one with `write`")
    s.add_argument("lib"); s.add_argument("items", nargs="+", metavar="ITEM"); s.add_argument("--full", action="store_true", help="whole note text, not 200 chars")
    s.set_defaults(f=cmd_notes)
    s = sp.add_parser("path", help="local file path(s) of attachments: of the ITEMs (parent or attachment keys), or of all in --coll; flags missing files")
    s.add_argument("lib"); s.add_argument("items", nargs="*", metavar="ITEM"); s.add_argument("--coll", help="every attachment in this collection instead of ITEMs")
    s.set_defaults(f=cmd_path)
    s = sp.add_parser("verify", help="resolve IDs via Crossref/arXiv; output goes to a private temp dir (see `where SPEC`)")
    s.add_argument("spec")
    s.add_argument("--no-pdf", action="store_true"); s.set_defaults(f=cmd_verify)
    s = sp.add_parser("plan", help="READ-ONLY: show what import would create; writes nothing"); s.add_argument("spec"); s.set_defaults(f=cmd_plan)
    s = sp.add_parser("import", help="write items/notes/PDFs via the connector (no Zotero dialog)"); s.add_argument("spec")
    s.add_argument("--allow-unverified", action="store_true", help="also import user-affirmed entries with unverified (manual) metadata")
    s.add_argument("--no-pdf", action="store_true"); s.add_argument("--max-pdf-mb", type=int, default=50)
    s.set_defaults(f=cmd_import)
    s = sp.add_parser("organize", help="set tags as regular tags and place items into subcollections for items already imported (local-API writes)")
    s.add_argument("spec"); s.add_argument("--dry-run", action="store_true", help="show the changes, write nothing")
    s.set_defaults(f=cmd_organize)
    s = sp.add_parser("attach", help="attach a local file to an existing item (--item KEY; 3 approval dialogs) or to a new item made from a DOI/arXiv ID (--new ID; no dialog)")
    s.add_argument("lib"); s.add_argument("file")
    s.add_argument("--item", help="key of the existing parent item"); s.add_argument("--new", metavar="ID", help="DOI or arXiv:ID of a new item to create")
    s.add_argument("--coll", help="with --new: collection to put it in (default: library root)"); s.add_argument("--title", help="attachment title (default: Full Text PDF / file name)")
    s.add_argument("--max-mb", type=int, default=50); s.set_defaults(f=cmd_attach)
    s = sp.add_parser("trash", help="move items to Zotero's trash (one approval dialog)"); s.add_argument("lib"); s.add_argument("keys", nargs="+"); s.set_defaults(f=cmd_trash)
    s = sp.add_parser("write", help="POST a JSON array of item (or --collections) objects via the local API (one approval dialog)"); s.add_argument("lib"); s.add_argument("json")
    s.add_argument("--collections", action="store_true", help="the objects are collections ({key, version, name, ...}), not items")
    s.set_defaults(f=cmd_write)
    a = p.parse_args()
    global GUARD
    GUARD = LOCAL_ONLY if a.cmd in READ_ONLY_CMDS else REMOTE_READ if a.cmd in VERIFY_CMDS else None
    if a.cmd in ("selftest", "where"):  # need no Zotero
        return a.f(a)
    require_zotero10()
    a.f(a)


if __name__ == "__main__":
    main()
