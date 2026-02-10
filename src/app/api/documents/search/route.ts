import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/db/db";

interface DocumentRow {
  recid: number | null;
  display_identifier: string | null;
  title_primary: string | null;
  publication_date_primary: string | null;
  datestamp: string;
}

export async function GET(req: NextRequest) {
  const q = req.nextUrl.searchParams.get("q")?.trim();
  if (!q || q.length < 2) return NextResponse.json([]);

  const rows = await query<DocumentRow>(
    `SELECT
       recid,
       COALESCE(
         NULLIF(document_symbol, ''),
         (
           SELECT ident
           FROM unnest(dc_identifier) AS ident
           WHERE ident !~ '^https?://'
           LIMIT 1
         )
       ) AS display_identifier,
       title_primary,
       publication_date_primary,
       datestamp::text
     FROM digitallibrary.documents
     WHERE deleted = FALSE
       AND (
         document_symbol ILIKE $1 || '%'
         OR title_primary ILIKE '%' || $1 || '%'
         OR EXISTS (
           SELECT 1
           FROM unnest(dc_identifier) AS ident
           WHERE ident ILIKE $1 || '%'
              OR ident ILIKE '%' || $1 || '%'
         )
       )
     ORDER BY
       CASE
         WHEN document_symbol ILIKE $1 || '%' THEN 0
         WHEN EXISTS (
           SELECT 1
           FROM unnest(dc_identifier) AS ident
           WHERE ident !~ '^https?://'
             AND ident ILIKE $1 || '%'
         ) THEN 1
         ELSE 2
       END,
       datestamp DESC
     LIMIT 20`,
    [q],
  );

  return NextResponse.json(
    rows.map((r) => ({
      recid: r.recid,
      symbol: r.display_identifier,
      title: r.title_primary?.replace(/\s*:\s*$/, "").trim() || null,
      date: r.publication_date_primary,
      datestamp: r.datestamp,
    })),
  );
}
