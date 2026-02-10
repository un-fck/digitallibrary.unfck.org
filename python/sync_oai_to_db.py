#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen
import xml.etree.ElementTree as ET

import psycopg

OAI_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "marc": "http://www.loc.gov/MARC21/slim",
}

DEFAULT_BASE_URL = "https://digitallibrary.un.org/oai2d"
DEFAULT_FROM = "2025-01-01T00:00:00Z"
DEFAULT_STATE_PATH = Path("python/.oai_sync_state.json")
DEFAULT_PREFIXES = ["oai_dc", "marcxml"]

DC_FIELDS = [
    "title",
    "creator",
    "subject",
    "description",
    "publisher",
    "contributor",
    "date",
    "type",
    "format",
    "identifier",
    "source",
    "language",
    "relation",
    "coverage",
    "rights",
]

SYMBOL_PATTERN = re.compile(r"^(A|S|E)/(RES|DEC)/[A-Z0-9().-]+(?:/[A-Z0-9().-]+)*$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_url(
    base_url: str,
    metadata_prefix: str,
    from_value: str | None,
    until_value: str | None,
    set_spec: str | None,
    token: str | None,
) -> str:
    params: dict[str, str] = {"verb": "ListRecords"}
    if token:
        params["resumptionToken"] = token
    else:
        params["metadataPrefix"] = metadata_prefix
        if from_value:
            params["from"] = from_value
        if until_value:
            params["until"] = until_value
        if set_spec:
            params["set"] = set_spec
    return f"{base_url}?{urlencode(params)}"


def fetch_xml(url: str, timeout: int) -> ET.Element:
    with urlopen(url, timeout=timeout) as resp:
        return ET.fromstring(resp.read())


def first(values: list[str]) -> str | None:
    return values[0] if values else None


def parse_recid(oai_identifier: str | None) -> int | None:
    if not oai_identifier:
        return None
    tail = oai_identifier.rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else None


def extract_symbol(identifiers: list[str]) -> str | None:
    for value in identifiers:
        v = value.strip()
        if SYMBOL_PATTERN.match(v):
            return v
    return None


def extract_dc_map(record: ET.Element) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {key: [] for key in DC_FIELDS}
    dc_root = record.find("oai:metadata/oai_dc:dc", OAI_NS)
    if dc_root is None:
        return output
    for field in DC_FIELDS:
        elems = dc_root.findall(f"dc:{field}", OAI_NS)
        output[field] = [elem.text.strip() for elem in elems if elem.text and elem.text.strip()]
    return output


def extract_marc_payload(record: ET.Element) -> tuple[str | None, dict[str, Any] | None]:
    metadata_el = record.find("oai:metadata", OAI_NS)
    if metadata_el is None:
        return None, None

    xml_str = ET.tostring(metadata_el, encoding="unicode")
    marc_record = metadata_el.find("marc:record", OAI_NS)
    if marc_record is None:
        return xml_str, None

    leader = marc_record.findtext("marc:leader", default=None, namespaces=OAI_NS)
    controlfields: list[dict[str, str]] = []
    datafields: list[dict[str, Any]] = []

    for cf in marc_record.findall("marc:controlfield", OAI_NS):
        tag = cf.attrib.get("tag", "")
        value = (cf.text or "").strip()
        controlfields.append({"tag": tag, "value": value})

    for df in marc_record.findall("marc:datafield", OAI_NS):
        tag = df.attrib.get("tag", "")
        ind1 = df.attrib.get("ind1", " ")
        ind2 = df.attrib.get("ind2", " ")
        subfields: list[dict[str, str]] = []
        for sf in df.findall("marc:subfield", OAI_NS):
            code = sf.attrib.get("code", "")
            value = (sf.text or "").strip()
            subfields.append({"code": code, "value": value})
        datafields.append({"tag": tag, "ind1": ind1, "ind2": ind2, "subfields": subfields})

    payload = {
        "leader": leader,
        "controlfields": controlfields,
        "datafields": datafields,
    }
    return xml_str, payload


UPSERT_DC_SQL = """
INSERT INTO digitallibrary.documents (
  oai_identifier,
  recid,
  datestamp,
  deleted,
  metadata_prefix,
  source_set,
  source_url,
  document_symbol,
  title_primary,
  publication_date_primary,
  dc_title,
  dc_creator,
  dc_subject,
  dc_description,
  dc_publisher,
  dc_contributor,
  dc_date,
  dc_type,
  dc_format,
  dc_identifier,
  dc_source,
  dc_language,
  dc_relation,
  dc_coverage,
  dc_rights,
  last_harvested_at
)
VALUES (
  %(oai_identifier)s,
  %(recid)s,
  %(datestamp)s::timestamptz,
  %(deleted)s,
  'oai_dc',
  %(source_set)s,
  %(source_url)s,
  %(document_symbol)s,
  %(title_primary)s,
  %(publication_date_primary)s,
  %(dc_title)s,
  %(dc_creator)s,
  %(dc_subject)s,
  %(dc_description)s,
  %(dc_publisher)s,
  %(dc_contributor)s,
  %(dc_date)s,
  %(dc_type)s,
  %(dc_format)s,
  %(dc_identifier)s,
  %(dc_source)s,
  %(dc_language)s,
  %(dc_relation)s,
  %(dc_coverage)s,
  %(dc_rights)s,
  NOW()
)
ON CONFLICT (oai_identifier) DO UPDATE SET
  recid = EXCLUDED.recid,
  datestamp = EXCLUDED.datestamp,
  deleted = EXCLUDED.deleted,
  source_set = EXCLUDED.source_set,
  source_url = EXCLUDED.source_url,
  document_symbol = EXCLUDED.document_symbol,
  title_primary = EXCLUDED.title_primary,
  publication_date_primary = EXCLUDED.publication_date_primary,
  dc_title = EXCLUDED.dc_title,
  dc_creator = EXCLUDED.dc_creator,
  dc_subject = EXCLUDED.dc_subject,
  dc_description = EXCLUDED.dc_description,
  dc_publisher = EXCLUDED.dc_publisher,
  dc_contributor = EXCLUDED.dc_contributor,
  dc_date = EXCLUDED.dc_date,
  dc_type = EXCLUDED.dc_type,
  dc_format = EXCLUDED.dc_format,
  dc_identifier = EXCLUDED.dc_identifier,
  dc_source = EXCLUDED.dc_source,
  dc_language = EXCLUDED.dc_language,
  dc_relation = EXCLUDED.dc_relation,
  dc_coverage = EXCLUDED.dc_coverage,
  dc_rights = EXCLUDED.dc_rights,
  metadata_prefix = 'oai_dc+marcxml',
  last_harvested_at = NOW();
"""


UPSERT_MARC_SQL = """
INSERT INTO digitallibrary.documents (
  oai_identifier,
  recid,
  datestamp,
  deleted,
  metadata_prefix,
  source_set,
  source_url,
  marcxml_xml,
  marcxml_json,
  last_harvested_at
)
VALUES (
  %(oai_identifier)s,
  %(recid)s,
  %(datestamp)s::timestamptz,
  %(deleted)s,
  'oai_dc+marcxml',
  %(source_set)s,
  %(source_url)s,
  %(marcxml_xml)s,
  %(marcxml_json)s::jsonb,
  NOW()
)
ON CONFLICT (oai_identifier) DO UPDATE SET
  recid = EXCLUDED.recid,
  datestamp = EXCLUDED.datestamp,
  deleted = EXCLUDED.deleted,
  source_set = EXCLUDED.source_set,
  source_url = EXCLUDED.source_url,
  metadata_prefix = 'oai_dc+marcxml',
  marcxml_xml = EXCLUDED.marcxml_xml,
  marcxml_json = EXCLUDED.marcxml_json,
  last_harvested_at = NOW();
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync UNDL OAI-PMH (DC + MARCXML) into Postgres")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--prefixes", default=",".join(DEFAULT_PREFIXES), help="Comma-separated prefixes, e.g. oai_dc,marcxml")
    parser.add_argument("--from", dest="from_value", default=DEFAULT_FROM)
    parser.add_argument("--until", dest="until_value")
    parser.add_argument("--set", dest="set_spec")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--max-records", type=int, default=0)
    return parser.parse_args()


def upsert_record(cur: psycopg.Cursor, prefix: str, row: dict[str, Any]) -> None:
    if prefix == "oai_dc":
        cur.execute(UPSERT_DC_SQL, row)
    elif prefix == "marcxml":
        cur.execute(UPSERT_MARC_SQL, row)
    else:
        raise ValueError(f"Unsupported prefix: {prefix}")


def run_prefix_sync(
    conn: psycopg.Connection,
    *,
    prefix: str,
    base_url: str,
    from_value: str | None,
    until_value: str | None,
    set_spec: str | None,
    timeout: int,
    sleep_s: float,
    max_pages: int,
    max_records: int,
    prefix_state: dict[str, Any],
) -> tuple[int, int, str | None, dict[str, Any] | None]:
    token = prefix_state.get("resumptionToken")
    pages = 0
    written = 0

    with conn.cursor() as cur:
        while True:
            if max_pages and pages >= max_pages:
                break
            if max_records and written >= max_records:
                break

            url = build_url(
                base_url=base_url,
                metadata_prefix=prefix,
                from_value=from_value,
                until_value=until_value,
                set_spec=set_spec,
                token=token,
            )
            root = fetch_xml(url, timeout=timeout)

            err = root.find("oai:error", OAI_NS)
            if err is not None:
                conn.rollback()
                msg = (err.text or "").strip()
                code = err.attrib.get("code", "unknown")
                return written, pages, token, {"code": code, "message": msg, "lastRequestUrl": url}

            records = root.findall(".//oai:record", OAI_NS)

            for record in records:
                header = record.find("oai:header", OAI_NS)
                if header is None:
                    continue

                oai_identifier = header.findtext("oai:identifier", default="", namespaces=OAI_NS)
                datestamp = header.findtext("oai:datestamp", default="", namespaces=OAI_NS)
                deleted = header.attrib.get("status") == "deleted"
                set_from_header = header.findtext("oai:setSpec", default=None, namespaces=OAI_NS)
                recid = parse_recid(oai_identifier)

                base_row: dict[str, Any] = {
                    "oai_identifier": oai_identifier,
                    "recid": recid,
                    "datestamp": datestamp,
                    "deleted": deleted,
                    "source_set": set_from_header,
                    "source_url": base_url,
                }

                if prefix == "oai_dc":
                    dc = extract_dc_map(record) if not deleted else {key: [] for key in DC_FIELDS}
                    row = {
                        **base_row,
                        "document_symbol": extract_symbol(dc["identifier"]),
                        "title_primary": first(dc["title"]),
                        "publication_date_primary": first(dc["date"]),
                        "dc_title": dc["title"],
                        "dc_creator": dc["creator"],
                        "dc_subject": dc["subject"],
                        "dc_description": dc["description"],
                        "dc_publisher": dc["publisher"],
                        "dc_contributor": dc["contributor"],
                        "dc_date": dc["date"],
                        "dc_type": dc["type"],
                        "dc_format": dc["format"],
                        "dc_identifier": dc["identifier"],
                        "dc_source": dc["source"],
                        "dc_language": dc["language"],
                        "dc_relation": dc["relation"],
                        "dc_coverage": dc["coverage"],
                        "dc_rights": dc["rights"],
                    }
                else:
                    marcxml_xml, marc_json_obj = extract_marc_payload(record)
                    row = {
                        **base_row,
                        "marcxml_xml": marcxml_xml,
                        "marcxml_json": json.dumps(marc_json_obj, ensure_ascii=False) if marc_json_obj is not None else None,
                    }

                upsert_record(cur, prefix, row)
                written += 1
                if max_records and written >= max_records:
                    break

            conn.commit()
            pages += 1

            token_el = root.find(".//oai:resumptionToken", OAI_NS)
            token = token_el.text.strip() if token_el is not None and token_el.text else None

            print(f"prefix={prefix} page={pages} records_in_page={len(records)} total_written={written} token={'yes' if token else 'no'}")

            if not token:
                break
            if sleep_s > 0:
                time.sleep(sleep_s)

    return written, pages, token, None


def main() -> int:
    args = parse_args()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2

    prefixes = [p.strip() for p in args.prefixes.split(",") if p.strip()]
    for p in prefixes:
        if p not in {"oai_dc", "marcxml"}:
            print(f"Unsupported prefix: {p}. Supported: oai_dc, marcxml", file=sys.stderr)
            return 2

    state_path = Path(args.state_file)
    state = load_state(state_path) if args.resume else {}
    state.setdefault("prefixes", {})

    with psycopg.connect(database_url, autocommit=False) as conn:
        for prefix in prefixes:
            prefix_state = state["prefixes"].get(prefix, {}) if args.resume else {}
            written, pages, token, err = run_prefix_sync(
                conn,
                prefix=prefix,
                base_url=args.base_url,
                from_value=args.from_value,
                until_value=args.until_value,
                set_spec=args.set_spec,
                timeout=args.timeout,
                sleep_s=args.sleep,
                max_pages=args.max_pages,
                max_records=args.max_records,
                prefix_state=prefix_state,
            )

            state["prefixes"][prefix] = {
                "updatedAt": utc_now(),
                "resumptionToken": token,
                "recordsWritten": written,
                "pagesFetched": pages,
                "from": args.from_value,
                "until": args.until_value,
                "set": args.set_spec,
            }

            if err is not None:
                state["prefixes"][prefix]["error"] = err
                save_state(state_path, state)
                print(f"OAI error [{err['code']}]: {err['message']} (prefix={prefix})", file=sys.stderr)
                return 3

            save_state(state_path, state)

    print("Done. Synced prefixes:", ", ".join(prefixes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
