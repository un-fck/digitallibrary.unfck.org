import { NextResponse } from "next/server";
import { randomBytes, createHash } from "crypto";
import { getCurrentUser } from "@/features/auth/service";
import { query } from "@/lib/db/db";
import { tables } from "@/lib/db/config";

const KEY_PREFIX = "undl_live_";

function generateApiKey(): string {
  return KEY_PREFIX + randomBytes(32).toString("hex");
}

function hashKey(raw: string): string {
  return createHash("sha256").update(raw).digest("hex");
}

export async function GET() {
  const user = await getCurrentUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  // Find or create api_user linked to this user
  let apiUser = await query<{ id: string }>(
    `SELECT id FROM ${tables.api_users} WHERE user_id = $1`,
    [user.id],
  );

  if (!apiUser[0]) {
    // Also check by email
    apiUser = await query<{ id: string }>(
      `SELECT id FROM ${tables.api_users} WHERE email = $1`,
      [user.email],
    );
    if (apiUser[0]) {
      // Link existing api_user to this user
      await query(
        `UPDATE ${tables.api_users} SET user_id = $1 WHERE id = $2`,
        [user.id, apiUser[0].id],
      );
    }
  }

  if (!apiUser[0]) {
    // Auto-create api_user for logged-in user
    apiUser = await query<{ id: string }>(
      `INSERT INTO ${tables.api_users} (email, user_id, verified_at) VALUES ($1, $2, NOW()) RETURNING id`,
      [user.email, user.id],
    );
  }

  const apiUserId = apiUser[0].id;

  // Get active key
  const keys = await query<{
    key_prefix: string;
    tier: string;
    rate_limit: number;
    created_at: string;
    last_used_at: string | null;
  }>(
    `SELECT key_prefix, tier, rate_limit, created_at, last_used_at FROM ${tables.api_keys} WHERE api_user_id = $1 AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 1`,
    [apiUserId],
  );

  if (!keys[0]) {
    // Auto-create first key
    const rawKey = generateApiKey();
    await query(
      `INSERT INTO ${tables.api_keys} (api_user_id, key_hash, key_prefix, tier, rate_limit) VALUES ($1, $2, $3, 'free', 60)`,
      [apiUserId, hashKey(rawKey), rawKey.substring(0, 12)],
    );
    return NextResponse.json({
      api_key: rawKey,
      key_prefix: rawKey.substring(0, 12),
      tier: "free",
      rate_limit: 60,
      is_new: true,
    });
  }

  return NextResponse.json({
    key_prefix: keys[0].key_prefix,
    tier: keys[0].tier,
    rate_limit: keys[0].rate_limit,
    created_at: keys[0].created_at,
    last_used_at: keys[0].last_used_at,
    is_new: false,
  });
}

export async function POST() {
  // Rotate key
  const user = await getCurrentUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const apiUser = await query<{ id: string; tier: string }>(
    `SELECT au.id, au.tier FROM ${tables.api_users} au WHERE au.user_id = $1`,
    [user.id],
  );
  if (!apiUser[0]) return NextResponse.json({ error: "No API user found" }, { status: 404 });

  const apiUserId = apiUser[0].id;
  const tier = apiUser[0].tier;
  const rateLimit = tier === "institutional" ? 1000 : tier === "research" ? 300 : 60;

  // Revoke existing keys
  await query(
    `UPDATE ${tables.api_keys} SET revoked_at = NOW() WHERE api_user_id = $1 AND revoked_at IS NULL`,
    [apiUserId],
  );

  // Create new key
  const rawKey = generateApiKey();
  await query(
    `INSERT INTO ${tables.api_keys} (api_user_id, key_hash, key_prefix, tier, rate_limit) VALUES ($1, $2, $3, $4, $5)`,
    [apiUserId, hashKey(rawKey), rawKey.substring(0, 12), tier, rateLimit],
  );

  return NextResponse.json({
    api_key: rawKey,
    key_prefix: rawKey.substring(0, 12),
    tier,
    rate_limit: rateLimit,
    is_new: true,
  });
}
