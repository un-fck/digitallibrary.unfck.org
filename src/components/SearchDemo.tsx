"use client";

import { useState } from "react";
import { EntityCombobox, type EntityOption } from "./EntityCombobox";
import { DocumentSearch } from "./DocumentSearch";

interface Props {
  entities: EntityOption[];
}

export function SearchDemo({ entities }: Props) {
  const [entityValue, setEntityValue] = useState("");
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <EntityCombobox
        value={entityValue}
        onChange={setEntityValue}
        entities={entities}
        placeholder="Search entities..."
      />
      <DocumentSearch placeholder="Search documents..." />
    </div>
  );
}
