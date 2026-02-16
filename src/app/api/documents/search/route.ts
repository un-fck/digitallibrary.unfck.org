import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/db/db";

interface SearchRow {
  recid: number;
  document_symbol: string | null;
  title: string | null;
  date_publication: string | null;
  un_body: string | null;
  resource_type: string | null;
}

export async function GET(req: NextRequest) {
  const q = req.nextUrl.searchParams.get("q")?.trim();
  if (!q || q.length < 2) return NextResponse.json([]);

  const rows = await query<SearchRow>(
    `SELECT
       recid,
       document_symbol,
       title,
       date_publication::text,
       un_body,
       resource_type
     FROM digitallibrary.documents
     WHERE deleted_at IS NULL
       AND (
         document_symbol ILIKE $1 || '%'
         OR title ILIKE '%' || $1 || '%'
         OR recid::text = $1
       )
     ORDER BY
       CASE
         WHEN document_symbol ILIKE $1 || '%' THEN 0
         WHEN title ILIKE $1 || '%' THEN 1
         ELSE 2
       END,
       date_publication DESC NULLS LAST
     LIMIT 20`,
    [q],
  );

  return NextResponse.json(
    rows.map((r) => ({
      recid: r.recid,
      symbol: r.document_symbol,
      title: r.title,
      date: r.date_publication,
      body: r.un_body,
      type: r.resource_type,
    })),
  );
}
