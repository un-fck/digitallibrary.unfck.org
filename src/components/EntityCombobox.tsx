"use client";

import { useState, useRef, useEffect } from "react";
import { Building2 } from "lucide-react";

export interface EntityOption {
  entity: string;
  entity_long?: string;
}

interface Props {
  value: string;
  onChange: (value: string) => void;
  entities: EntityOption[];
  placeholder?: string;
  allowCustom?: boolean;
}

export function EntityCombobox({
  value,
  onChange,
  entities,
  placeholder = "Search entities...",
  allowCustom = true,
}: Props) {
  const [query, setQuery] = useState(value);
  const [filtered, setFiltered] = useState<EntityOption[]>(entities);
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setQuery(value);
  }, [value]);

  useEffect(() => {
    if (!query) {
      setFiltered(entities);
      return;
    }
    const q = query.toLowerCase();
    setFiltered(
      entities.filter(
        (e) =>
          e.entity.toLowerCase().includes(q) ||
          e.entity_long?.toLowerCase().includes(q),
      ),
    );
  }, [query, entities]);

  const handleChange = (val: string) => {
    setQuery(val);
    setOpen(true);
    setHighlighted(0);
  };

  const handleSelect = (entity: EntityOption | string) => {
    const val = typeof entity === "string" ? entity : entity.entity;
    setQuery(val);
    onChange(val);
    setOpen(false);
    setHighlighted(-1);
  };

  const showCustomOption =
    allowCustom &&
    query &&
    !entities.some((e) => e.entity.toLowerCase() === query.toLowerCase());
  const totalItems = filtered.length + (showCustomOption ? 1 : 0);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!open || totalItems === 0) return;
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setHighlighted((i) => (i + 1) % totalItems);
        break;
      case "ArrowUp":
        e.preventDefault();
        setHighlighted((i) => (i - 1 + totalItems) % totalItems);
        break;
      case "Enter":
        e.preventDefault();
        if (highlighted >= 0 && highlighted < filtered.length)
          handleSelect(filtered[highlighted]);
        else if (showCustomOption && highlighted === filtered.length)
          handleSelect(query);
        break;
      case "Escape":
        setOpen(false);
        setHighlighted(-1);
        break;
    }
  };

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div ref={containerRef} className="relative w-full">
      <div className="relative">
        <Building2 className="absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-gray-400" />
        <input
          type="text"
          value={query}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setOpen(true)}
          placeholder={placeholder}
          className="w-full rounded-lg border border-gray-300 py-2 pr-4 pl-10 text-sm focus:border-un-blue focus:ring-1 focus:ring-un-blue focus:outline-none"
        />
      </div>

      {open && (filtered.length > 0 || showCustomOption) && (
        <div className="absolute z-50 mt-1 max-h-60 w-full overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-lg">
          {filtered.map((entity, i) => (
            <button
              key={entity.entity}
              type="button"
              onClick={() => handleSelect(entity)}
              onMouseEnter={() => setHighlighted(i)}
              className={`w-full px-3 py-2 text-left ${highlighted === i ? "bg-gray-100" : ""}`}
            >
              <span className="text-sm font-medium text-un-blue">
                {entity.entity}
              </span>
              {entity.entity_long && (
                <p className="truncate text-xs text-gray-500">
                  {entity.entity_long}
                </p>
              )}
            </button>
          ))}
          {showCustomOption && (
            <button
              type="button"
              onClick={() => handleSelect(query)}
              onMouseEnter={() => setHighlighted(filtered.length)}
              className={`w-full border-t border-gray-100 px-3 py-2 text-left text-sm ${highlighted === filtered.length ? "bg-gray-100" : ""}`}
            >
              <span className="text-gray-500">Other: </span>
              <span className="font-medium">{query}</span>
            </button>
          )}
        </div>
      )}
    </div>
  );
}
