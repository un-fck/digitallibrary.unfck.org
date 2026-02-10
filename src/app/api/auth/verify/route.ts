import { NextResponse } from "next/server";
import {
  verifyMagicToken,
  upsertUser,
  createSession,
} from "@/features/auth/service";
import { query } from "@/lib/db/db";
import { tables } from "@/lib/db/config";

export async function POST(request: Request) {
  const { token, entity } = await request.json();
  if (!token)
    return NextResponse.json({ error: "Missing token" }, { status: 400 });
  const email = await verifyMagicToken(token);
  if (!email)
    return NextResponse.json(
      { error: "Invalid or expired link" },
      { status: 400 },
    );
  const userId = await upsertUser(email);
  if (entity && typeof entity === "string" && entity.trim()) {
    await query(`UPDATE ${tables.users} SET entity = $1 WHERE id = $2`, [
      entity.trim(),
      userId,
    ]);
  }
  await createSession(userId);
  return NextResponse.json({ ok: true });
}
