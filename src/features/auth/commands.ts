"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { tables } from "@/lib/db/config";
import { query } from "@/lib/db/db";
import { sendMagicLink } from "./mail";
import {
  clearSession,
  createMagicToken,
  createSession,
  getCurrentUser,
  isAllowedDomain,
  recentTokenExists,
  upsertUser,
  verifyMagicToken as verifyMagicTokenService,
} from "./service";

type ActionResult<T = void> =
  | { success: true; data?: T }
  | { success: false; error: string };

export async function requestMagicLink(email: string): Promise<ActionResult> {
  if (!email || typeof email !== "string" || !email.trim()) {
    return { success: false, error: "Email required" };
  }
  const trimmedEmail = email.trim();
  if (!(await isAllowedDomain(trimmedEmail))) {
    return { success: false, error: "Email domain not allowed" };
  }
  if (await recentTokenExists(trimmedEmail)) {
    return {
      success: false,
      error:
        "A magic link was recently sent. Please check your email or wait a few minutes.",
    };
  }
  try {
    const token = await createMagicToken(trimmedEmail);
    await sendMagicLink(trimmedEmail, token);
    return { success: true };
  } catch (error) {
    console.error("Error sending magic link:", error);
    return { success: false, error: "Failed to send email. Please try again." };
  }
}

export async function checkEntityForToken(
  token: string,
): Promise<
  ActionResult<{ email: string; hasEntity: boolean; entity: string | null }>
> {
  if (!token || typeof token !== "string") {
    return { success: false, error: "Missing token" };
  }
  const tokenRows = await query<{ email: string }>(
    `SELECT email FROM ${tables.magic_tokens} WHERE token = $1 AND expires_at > NOW() AND used_at IS NULL`,
    [token],
  );
  if (!tokenRows[0]) {
    return { success: false, error: "Invalid or expired token" };
  }
  const email = tokenRows[0].email;
  const userRows = await query<{ entity: string | null }>(
    `SELECT entity FROM ${tables.users} WHERE email = $1`,
    [email.toLowerCase()],
  );
  const existingEntity = userRows[0]?.entity || null;
  return {
    success: true,
    data: { email, hasEntity: !!existingEntity, entity: existingEntity },
  };
}

export async function verifyMagicToken(
  token: string,
  entity?: string,
): Promise<ActionResult> {
  if (!token || typeof token !== "string") {
    return { success: false, error: "Missing token" };
  }
  const email = await verifyMagicTokenService(token);
  if (!email) {
    return { success: false, error: "Invalid or expired link" };
  }
  const userId = await upsertUser(email);
  if (entity && typeof entity === "string" && entity.trim()) {
    await query(`UPDATE ${tables.users} SET entity = $1 WHERE id = $2`, [
      entity.trim(),
      userId,
    ]);
  }
  await createSession(userId);
  revalidatePath("/", "layout");
  return { success: true };
}

export async function updateEntity(entity: string): Promise<ActionResult> {
  const user = await getCurrentUser();
  if (!user) {
    return { success: false, error: "Unauthorized" };
  }
  if (!entity || typeof entity !== "string" || !entity.trim()) {
    return { success: false, error: "Entity is required" };
  }
  await query(`UPDATE ${tables.users} SET entity = $1 WHERE id = $2`, [
    entity.trim(),
    user.id,
  ]);
  revalidatePath("/", "layout");
  return { success: true };
}

export async function logout(): Promise<void> {
  await clearSession();
  redirect("/about");
}
