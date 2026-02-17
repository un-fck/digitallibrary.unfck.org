import { NextResponse } from "next/server";
import { getCurrentUser } from "@/features/auth/service";
import { query } from "@/lib/db/db";
import { tables } from "@/lib/db/config";

export async function GET() {
  const user = await getCurrentUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const apiUser = await query<{ id: string }>(
    `SELECT id FROM ${tables.api_users} WHERE user_id = $1`,
    [user.id],
  );
  if (!apiUser[0]) {
    return NextResponse.json({
      requests_today: 0,
      requests_this_month: 0,
      daily: [],
    });
  }

  // Get active key
  const keys = await query<{ id: string; tier: string; rate_limit: number }>(
    `SELECT id, tier, rate_limit FROM ${tables.api_keys} WHERE api_user_id = $1 AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 1`,
    [apiUser[0].id],
  );
  if (!keys[0]) {
    return NextResponse.json({
      requests_today: 0,
      requests_this_month: 0,
      daily: [],
    });
  }

  const keyId = keys[0].id;

  // Today's count
  const todayRows = await query<{ count: string }>(
    `SELECT count(*) FROM ${tables.api_usage_log} WHERE key_id = $1 AND requested_at >= CURRENT_DATE`,
    [keyId],
  );

  // This month's count
  const monthRows = await query<{ count: string }>(
    `SELECT count(*) FROM ${tables.api_usage_log} WHERE key_id = $1 AND requested_at >= date_trunc('month', CURRENT_DATE)`,
    [keyId],
  );

  // Last 7 days daily breakdown
  const dailyRows = await query<{ day: string; count: string }>(
    `SELECT date_trunc('day', requested_at)::date::text AS day, count(*)
     FROM ${tables.api_usage_log}
     WHERE key_id = $1 AND requested_at >= CURRENT_DATE - INTERVAL '7 days'
     GROUP BY day ORDER BY day`,
    [keyId],
  );

  return NextResponse.json({
    requests_today: parseInt(todayRows[0]?.count || "0"),
    requests_this_month: parseInt(monthRows[0]?.count || "0"),
    daily: dailyRows.map((r) => ({ day: r.day, count: parseInt(r.count) })),
  });
}
