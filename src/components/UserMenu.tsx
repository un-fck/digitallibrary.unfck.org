"use client";

import { useState } from "react";
import { EntityChangeDialog } from "./EntityChangeDialog";
import type { EntityOption } from "./EntityCombobox";
import { logout } from "@/features/auth/commands";

interface Props {
  email: string;
  entity?: string | null;
  entities: EntityOption[];
}

export function UserMenu({ email, entity, entities }: Props) {
  const [isDialogOpen, setIsDialogOpen] = useState(false);

  async function handleLogout() {
    await logout();
  }

  return (
    <>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span className="text-sm text-gray-600">{email}</span>
          {entity && (
            <button
              onClick={() => setIsDialogOpen(true)}
              className="rounded-full bg-un-blue/10 px-2 py-0.5 text-xs font-medium text-un-blue transition-colors hover:bg-un-blue/20"
              title="Click to update entity"
            >
              {entity}
            </button>
          )}
        </div>
        <div className="h-4 w-px bg-gray-200" />
        <button
          onClick={handleLogout}
          className="text-sm text-gray-500 transition-colors hover:text-gray-900"
        >
          Logout
        </button>
      </div>
      <EntityChangeDialog
        isOpen={isDialogOpen}
        onClose={() => setIsDialogOpen(false)}
        currentEntity={entity || null}
        entities={entities}
      />
    </>
  );
}
