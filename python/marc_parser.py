"""Parse a single MARCXML <record> element into a flat dict for DB upsert."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date

MARC_NS = {"m": "http://www.loc.gov/MARC21/slim"}
_NS_URI = "http://www.loc.gov/MARC21/slim"

# ISO 639-2/B codes used by UNDL (3-letter, no delimiters in 041$a)
_LANG_CODE_LEN = 3


def parse_record(record_xml: str) -> dict | None:
    """Parse a MARCXML <record> string into a dict keyed to DB columns.

    Returns None if the record has no valid 001 (recid).
    """
    root = ET.fromstring(record_xml)

    # Detect whether elements use the MARC namespace or are bare
    if root.tag == f"{{{_NS_URI}}}record" or root.tag == "record":
        rec = root
    else:
        rec = root.find("m:record", MARC_NS) or root.find("record")
        if rec is None:
            rec = root

    # Choose namespace prefix based on whether children are namespaced
    first_child = rec[0] if len(rec) > 0 else None
    ns = MARC_NS if (first_child is not None and first_child.tag.startswith("{")) else {}

    recid_str = _controlfield(rec, "001", ns)
    if not recid_str or not recid_str.strip().isdigit():
        return None

    # 245: Title
    title_a = _subfield_first(rec, "245", "a", ns) or ""
    title_b = _subfield_first(rec, "245", "b", ns) or ""
    title = _clean_title(title_a, title_b)

    # 269: Date (ISO)
    date_pub = _parse_date(_subfield_first(rec, "269", "a", ns))

    # 041: Languages
    raw_lang = _subfield_first(rec, "041", "a", ns) or ""
    languages = _parse_languages(raw_lang)

    # 650: Subjects
    subjects = _subfield_all(rec, "650", "a", ns)

    # 710: Corporate authors
    corporate_authors = []
    for df in _datafields(rec, "710", ns):
        name = _sf(df, "a", ns)
        atype = _sf(df, "9", ns)
        if name:
            corporate_authors.append({"name": name, "type": atype})

    # 856: Files
    files = []
    for df in _datafields(rec, "856", ns):
        url = _sf(df, "u", ns)
        if url:
            files.append({
                "url": url,
                "lang": _sf(df, "y", ns),
                "size": _sf(df, "s", ns),
                "uuid": _sf(df, "9", ns),
            })

    # 500: Notes
    notes = _subfield_all(rec, "500", "a", ns)

    # 991: Agenda items
    agenda_items = []
    for df in _datafields(rec, "991", ns):
        entry = {
            "doc": _sf(df, "a", ns),
            "item": _sf(df, "b", ns),
            "desc": _sf(df, "c", ns),
            "topic": _sf(df, "d", ns),
        }
        if any(entry.values()):
            agenda_items.append(entry)

    # 993: Related documents
    related_documents = []
    for df in _datafields(rec, "993", ns):
        sym = _sf(df, "a", ns)
        if sym:
            rel = df.attrib.get("ind1", "").strip() or None
            related_documents.append({"symbol": sym, "relationship": rel})

    return {
        "recid": int(recid_str.strip()),
        "document_symbol": _subfield_first(rec, "191", "a", ns),
        "symbol_body": _subfield_first(rec, "191", "b", ns),
        "symbol_session": _subfield_first(rec, "191", "c", ns),
        "symbol_committee": _subfield_first(rec, "191", "d", ns),
        "title": title,
        "title_statement": _subfield_first(rec, "245", "c", ns),
        "date_publication": date_pub,
        "date_text": _subfield_first(rec, "260", "c", ns),
        "publisher": _subfield_first(rec, "260", "b", ns),
        "pub_place": _subfield_first(rec, "260", "a", ns),
        "physical_desc": _subfield_first(rec, "300", "a", ns),
        "doc_class_code": _subfield_first(rec, "089", "b", ns),
        "doc_class_desc": _subfield_first(rec, "089", "a", ns),
        "languages": languages,
        "subjects": subjects,
        "corporate_authors": corporate_authors,
        "un_body": _subfield_first(rec, "981", "a", ns),
        "un_committee": _subfield_first(rec, "981", "b", ns),
        "notes": notes,
        "summary": _subfield_first(rec, "520", "a", ns),
        "files": files,
        "collections": _subfield_all(rec, "980", "a", ns),
        "resource_type": _subfield_first(rec, "989", "a", ns),
        "resource_subtype": _subfield_first(rec, "989", "b", ns),
        "vote_summary": _subfield_first(rec, "996", "a", ns),
        "agenda_items": agenda_items,
        "related_documents": related_documents,
        "marcxml": record_xml,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _controlfield(rec: ET.Element, tag: str, ns: dict[str, str]) -> str | None:
    prefix = "m:controlfield" if ns else "controlfield"
    for cf in rec.findall(prefix, ns):
        if cf.attrib.get("tag") == tag:
            return (cf.text or "").strip() or None
    return None


def _datafields(rec: ET.Element, tag: str, ns: dict[str, str]) -> list[ET.Element]:
    prefix = "m:datafield" if ns else "datafield"
    return [df for df in rec.findall(prefix, ns) if df.attrib.get("tag") == tag]


def _sf(df: ET.Element, code: str, ns: dict[str, str]) -> str | None:
    prefix = "m:subfield" if ns else "subfield"
    for sf in df.findall(prefix, ns):
        if sf.attrib.get("code") == code:
            val = (sf.text or "").strip()
            return val if val else None
    return None


def _subfield_first(rec: ET.Element, tag: str, code: str, ns: dict[str, str]) -> str | None:
    for df in _datafields(rec, tag, ns):
        val = _sf(df, code, ns)
        if val:
            return val
    return None


def _subfield_all(rec: ET.Element, tag: str, code: str, ns: dict[str, str]) -> list[str]:
    result: list[str] = []
    for df in _datafields(rec, tag, ns):
        val = _sf(df, code, ns)
        if val:
            result.append(val)
    return result


def _clean_title(a: str, b: str) -> str | None:
    parts = []
    a = a.strip().rstrip(" :/")
    b = b.strip().rstrip(" :/")
    if a:
        parts.append(a)
    if b:
        parts.append(b)
    combined = " ".join(parts).strip()
    return combined if combined else None


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    raw = raw.strip()
    from datetime import datetime
    # Try ISO formats: YYYY-MM-DD, YYYY-MM, YYYY
    for fmt, length in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        if len(raw) >= length:
            try:
                dt = datetime.strptime(raw[:length], fmt)
                return dt.date()
            except ValueError:
                continue
    # Fallback: extract a 4-digit year
    m = re.search(r"\b(\d{4})\b", raw)
    if m:
        try:
            return date(int(m.group(1)), 1, 1)
        except ValueError:
            pass
    return None


def _parse_languages(raw: str) -> list[str]:
    """Split concatenated 3-letter ISO 639 codes: 'arachiengfrerusspa' → ['ara','chi','eng','fre','rus','spa']."""
    raw = raw.strip()
    if not raw:
        return []
    # If already space/comma separated
    if " " in raw or "," in raw:
        return [c.strip() for c in re.split(r"[,\s]+", raw) if c.strip()]
    # Concatenated 3-char codes
    if len(raw) % _LANG_CODE_LEN == 0 and len(raw) >= _LANG_CODE_LEN:
        return [raw[i:i + _LANG_CODE_LEN] for i in range(0, len(raw), _LANG_CODE_LEN)]
    return [raw]
