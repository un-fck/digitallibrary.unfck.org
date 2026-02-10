"use client";

import { useState, useEffect } from "react";
import { X } from "lucide-react";
import { EntityCombobox, type EntityOption } from "./EntityCombobox";
import { updateEntity } from "@/features/auth/commands";

interface Props {
  isOpen: boolean;
  onClose: () => void;
  currentEntity: string | null;
  entities: EntityOption[];
}

export function EntityChangeDialog({
  isOpen,
  onClose,
  currentEntity,
  entities,
}: Props) {
  const [selectedEntity, setSelectedEntity] = useState("");
  const [otherEntity, setOtherEntity] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      setSelectedEntity(currentEntity || "");
      setOtherEntity("");
      setError(null);
    }
  }, [isOpen, currentEntity]);

  const handleSubmit = async () => {
    const entity =
      selectedEntity === "Other – Please Specify"
        ? otherEntity.trim()
        : selectedEntity;
    if (!entity) {
      setError("Please select an entity");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await updateEntity(entity);
      if (result.success) {
        window.location.reload();
      } else {
        setError(result.error);
      }
    } catch {
      setError("Failed to update entity");
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/50" onClick={onClose} />
      <div className="fixed top-1/2 left-1/2 z-50 w-full max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-lg bg-white p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">Update Entity</h2>
          <button
            onClick={onClose}
            className="rounded-lg p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="space-y-4">
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
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="flex-1 rounded-lg border border-gray-300 px-4 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              onClick={handleSubmit}
              disabled={
                loading ||
                !selectedEntity ||
                (selectedEntity === "Other – Please Specify" &&
                  !otherEntity.trim())
              }
              className="flex-1 rounded-lg bg-un-blue px-4 py-2.5 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? "Updating..." : "Update"}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
