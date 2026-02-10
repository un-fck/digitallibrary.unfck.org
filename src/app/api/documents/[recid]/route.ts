import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/db/db";

interface DocumentDetailRow {
  oai_identifier: string;
  recid: number | null;
  datestamp: string;
  deleted: boolean;
  metadata_prefix: string;
  source_set: string | null;
  source_url: string | null;
  document_symbol: string | null;
  title_primary: string | null;
  publication_date_primary: string | null;
  dc_title: string[];
  dc_creator: string[];
  dc_subject: string[];
  dc_description: string[];
  dc_publisher: string[];
  dc_contributor: string[];
  dc_date: string[];
  dc_type: string[];
  dc_format: string[];
  dc_identifier: string[];
  dc_source: string[];
  dc_language: string[];
  dc_relation: string[];
  dc_coverage: string[];
  dc_rights: string[];
  metadata_xml: string | null;
  metadata_json: unknown;
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ recid: string }> },
) {
  const { recid } = await params;
  const parsed = Number.parseInt(recid, 10);
  if (!Number.isFinite(parsed)) {
    return NextResponse.json({ error: "Invalid recid" }, { status: 400 });
  }

  const rows = await query<DocumentDetailRow>(
    `SELECT
       oai_identifier,
       recid,
       datestamp::text,
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
       marcxml_xml AS metadata_xml,
       marcxml_json AS metadata_json
     FROM digitallibrary.documents
     WHERE recid = $1
     ORDER BY datestamp DESC
     LIMIT 1`,
    [parsed],
  );

  if (!rows[0]) {
    return NextResponse.json({ error: "Document not found" }, { status: 404 });
  }

  return NextResponse.json(rows[0]);
}
