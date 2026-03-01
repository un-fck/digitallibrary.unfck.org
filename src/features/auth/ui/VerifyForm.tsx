"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { useState } from "react";
import { verifyMagicToken } from "@/features/auth/commands";

export function VerifyForm() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams.get("token");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!token)
    return <p className="text-red-600">Missing verification token.</p>;

  const handleVerify = async () => {
    setLoading(true);
    const result = await verifyMagicToken(token);
    if (!result.success) {
      setError(result.error);
      setLoading(false);
      return;
    }
    router.push("/");
  };

  return (
    <div className="space-y-6">
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button
        onClick={handleVerify}
        disabled={loading}
        className="w-full rounded-lg bg-un-blue px-4 py-2.5 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {loading ? "Signing in..." : "Complete sign-in"}
      </button>
    </div>
  );
}
