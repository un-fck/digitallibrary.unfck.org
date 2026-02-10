import { NextResponse } from "next/server";
import { clearSession } from "@/features/auth/service";

export async function POST() {
  await clearSession();
  return NextResponse.json({ ok: true });
}
