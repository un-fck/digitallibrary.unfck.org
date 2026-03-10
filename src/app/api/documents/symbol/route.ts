import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/db/db";

interface DocumentDetailRow {
  recid: number;
  document_symbol: string | null;
  symbol_body: string | null;
  symbol_session: string | null;
  symbol_committee: string | null;
  title: string | null;
  title_statement: string | null;
  date_publication: string | null;
  date_text: string | null;
  publisher: string | null;
  pub_place: string | null;
  physical_desc: string | null;
  doc_class_code: string | null;
  doc_class_desc: string | null;
  languages: string[];
  subjects: string[];
  corporate_authors: Array<{ name: string; type: string | null }>;
  un_body: string | null;
  un_committee: string | null;
  notes: string[];
  summary: string | null;
  files: Array<{ url: string; lang: string | null; size: string | null; uuid: string | null }>;
  collections: string[];
  resource_type: string | null;
  resource_subtype: string | null;
  vote_summary: string | null;
  agenda_items: Array<{ doc: string | null; item: string | null; desc: string | null; topic: string | null }>;
  related_documents: Array<{ symbol: string; relationship: string | null }>;
  marcxml: string;
  harvested_at: string;
}

export async function GET(req: NextRequest) {
  const s = req.nextUrl.searchParams.get("s")?.trim();
  if (!s) return NextResponse.json({ error: "Missing symbol" }, { status: 400 });

  try {
    const rows = await query<DocumentDetailRow>(
      `SELECT
         recid, document_symbol, symbol_body, symbol_session, symbol_committee,
         title, title_statement,
         date_publication::text, date_text, publisher, pub_place, physical_desc,
         doc_class_code, doc_class_desc,
         languages, subjects, corporate_authors, un_body, un_committee,
         notes, summary, files, collections,
         resource_type, resource_subtype, vote_summary,
         agenda_items, related_documents,
         marcxml, harvested_at::text
       FROM digitallibrary.documents
       WHERE document_symbol ILIKE $1 AND deleted_at IS NULL
       ORDER BY date_publication DESC NULLS LAST
       LIMIT 1`,
      [s],
    );

    if (!rows[0]) {
      return NextResponse.json({ error: "Document not found" }, { status: 404 });
    }

    return NextResponse.json(rows[0]);
  } catch (err) {
    console.error(`Failed to load document by symbol '${s}':`, err);
    return NextResponse.json({ error: "Failed to load document" }, { status: 500 });
  }
}
