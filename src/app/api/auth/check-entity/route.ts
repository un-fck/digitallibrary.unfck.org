import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/db/db";
import { tables } from "@/lib/db/config";

export async function POST(req: NextRequest) {
  const { token } = await req.json();
  if (!token)
    return NextResponse.json({ error: "Missing token" }, { status: 400 });
  const tokenRows = await query<{ email: string }>(
    `SELECT email FROM ${tables.magic_tokens} WHERE token = $1 AND expires_at > NOW() AND used_at IS NULL`,
    [token],
  );
  if (!tokenRows[0])
    return NextResponse.json(
      { error: "Invalid or expired token" },
      { status: 400 },
    );
  const email = tokenRows[0].email;
  const userRows = await query<{ entity: string | null }>(
    `SELECT entity FROM ${tables.users} WHERE email = $1`,
    [email.toLowerCase()],
  );
  const existingEntity = userRows[0]?.entity || null;
  return NextResponse.json({
    email,
    hasEntity: !!existingEntity,
    entity: existingEntity,
  });
}
