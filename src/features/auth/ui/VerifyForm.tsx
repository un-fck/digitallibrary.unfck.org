"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { useState, useEffect } from "react";
import {
  EntityCombobox,
  type EntityOption,
} from "../../../components/EntityCombobox";
import {
  checkEntityForToken,
  verifyMagicToken,
} from "@/features/auth/commands";

interface Props {
  entities: EntityOption[];
}

export function VerifyForm({ entities }: Props) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams.get("token");
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedEntity, setSelectedEntity] = useState("");
  const [otherEntity, setOtherEntity] = useState("");
  const [hasExistingEntity, setHasExistingEntity] = useState(false);
  const [userEmail, setUserEmail] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      queueMicrotask(() => setChecking(false));
      return;
    }
    checkEntityForToken(token)
      .then((result: Awaited<ReturnType<typeof checkEntityForToken>>) => {
        if (!result.success) {
          setError(result.error);
        } else if (result.data) {
          setUserEmail(result.data.email);
          setHasExistingEntity(result.data.hasEntity);
          if (result.data.entity) setSelectedEntity(result.data.entity);
        }
        setChecking(false);
      })
      .catch(() => {
        setError("Failed to verify token");
        setChecking(false);
      });
  }, [token]);

  const handleVerify = async () => {
    if (!token) return;
    const entity = hasExistingEntity
      ? undefined
      : selectedEntity === "Other – Please Specify"
        ? otherEntity.trim()
        : selectedEntity;
    if (!hasExistingEntity && !entity) {
      setError("Please select your organisational entity");
      return;
    }
    setLoading(true);
    const result = await verifyMagicToken(token, entity);
    if (!result.success) {
      setError(result.error);
      setLoading(false);
      return;
    }
    router.push("/");
  };

  if (!token)
    return <p className="text-red-600">Missing verification token.</p>;
  if (checking) return <p className="text-gray-500">Verifying...</p>;

  if (hasExistingEntity) {
    return (
      <div className="space-y-6">
        <p className="text-sm text-gray-600">
          Signing in as <span className="font-medium">{userEmail}</span>
        </p>
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

  return (
    <div className="space-y-6">
      {userEmail && (
        <p className="text-sm text-gray-600">
          Signing in as <span className="font-medium">{userEmail}</span>
        </p>
      )}
      <div>
        <label className="mb-2 block text-sm font-medium text-gray-700">
          Select your organisational entity
        </label>
        <EntityCombobox
          value={selectedEntity}
          onChange={setSelectedEntity}
          entities={entities}
          placeholder="Choose entity..."
        />
      </div>
      {selectedEntity === "Other – Please Specify" && (
        <input
          type="text"
          placeholder="Enter your organisational entity"
          value={otherEntity}
          onChange={(e) => setOtherEntity(e.target.value)}
          className="w-full rounded-lg border border-gray-300 px-3 py-2.5 text-sm focus:border-un-blue focus:ring-1 focus:ring-un-blue focus:outline-none"
        />
      )}
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button
        onClick={handleVerify}
        disabled={
          loading ||
          !selectedEntity ||
          (selectedEntity === "Other – Please Specify" && !otherEntity.trim())
        }
        className="w-full rounded-lg bg-un-blue px-4 py-2.5 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {loading ? "Signing in..." : "Complete sign-in"}
      </button>
    </div>
  );
}
