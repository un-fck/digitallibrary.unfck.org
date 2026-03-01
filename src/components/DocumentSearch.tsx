"use client";
import { useState, useRef, useEffect, useCallback } from "react";
import { Search, Loader2, FileText, Building2, Calendar } from "lucide-react";

export interface SearchResult {
  recid: number | null;
  symbol: string | null;
  title: string | null;
  date: string | null;
  body: string | null;
  type: string | null;
}

interface Props {
  onSelect?: (doc: SearchResult) => void;
  placeholder?: string;
}

export function DocumentSearch({
  onSelect,
  placeholder = "Search documents...",
}: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(-1);
  const debounceRef = useRef<NodeJS.Timeout | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const search = useCallback((q: string) => {
    if (q.length < 2) {
      setResults([]);
      setOpen(false);
      return;
    }
    setSearching(true);
    fetch(`/api/documents/search?q=${encodeURIComponent(q)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
      .then((data) => {
        setResults(data);
        setOpen(true);
        setHighlighted(data.length > 0 ? 0 : -1);
      })
      .catch(() => {
        setResults([]);
        setOpen(false);
      })
      .finally(() => setSearching(false));
  }, []);

  const handleChange = (value: string) => {
    setQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => search(value), 200);
  };

  const handleSelect = (doc: SearchResult) => {
    onSelect?.(doc);
    setQuery(doc.symbol || doc.title || String(doc.recid || ""));
    setOpen(false);
    setHighlighted(-1);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!open || results.length === 0) return;
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setHighlighted((i) => (i + 1) % results.length);
        break;
      case "ArrowUp":
        e.preventDefault();
        setHighlighted((i) => (i - 1 + results.length) % results.length);
        break;
      case "Enter":
        e.preventDefault();
        if (highlighted >= 0) handleSelect(results[highlighted]);
        break;
      case "Escape":
        setOpen(false);
        setHighlighted(-1);
        break;
    }
  };

  // Scroll highlighted item into view
  useEffect(() => {
    if (highlighted >= 0 && listRef.current) {
      const el = listRef.current.children[highlighted] as HTMLElement;
      el?.scrollIntoView({ block: "nearest" });
    }
  }, [highlighted]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      )
        setOpen(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div ref={containerRef} className="relative w-full">
      <div className="relative">
        <Search className="absolute top-1/2 left-4 h-5 w-5 -translate-y-1/2 text-gray-400" />
        <input
          type="text"
          value={query}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => results.length > 0 && setOpen(true)}
          placeholder={placeholder}
          className="w-full rounded-xl border border-gray-200 bg-white py-3.5 pr-12 pl-12 text-sm shadow-sm transition-all placeholder:text-gray-400 focus:border-un-blue focus:ring-2 focus:ring-un-blue/20 focus:outline-none"
        />
        {searching && (
          <Loader2 className="absolute top-1/2 right-4 h-5 w-5 -translate-y-1/2 animate-spin text-un-blue" />
        )}
      </div>

      {open && results.length > 0 && (
        <div
          ref={listRef}
          className="absolute z-50 mt-2 max-h-96 w-full overflow-y-auto rounded-xl border border-gray-200 bg-white py-1 shadow-xl"
        >
          {results.map((doc, i) => (
            <button
              key={doc.recid ?? `${doc.symbol}-${i}`}
              onClick={() => handleSelect(doc)}
              onMouseEnter={() => setHighlighted(i)}
              className={`w-full px-4 py-3 text-left transition-colors ${
                highlighted === i ? "bg-un-blue/5" : "hover:bg-gray-50"
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    {doc.symbol ? (
                      <span className="inline-flex items-center rounded-md border border-un-blue/15 bg-un-blue/8 px-2 py-0.5 text-xs font-semibold text-un-blue">
                        {doc.symbol}
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-xs text-gray-400">
                        <FileText className="h-3 w-3" />
                        #{doc.recid ?? "?"}
                      </span>
                    )}
                    {doc.type && (
                      <span className="text-xs text-gray-400">{doc.type}</span>
                    )}
                  </div>
                  {doc.title && (
                    <p className="mt-1 truncate text-sm text-gray-700">
                      {doc.title}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1 pt-0.5">
                  {doc.date && (
                    <span className="flex items-center gap-1 text-xs text-gray-400">
                      <Calendar className="h-3 w-3" />
                      {doc.date}
                    </span>
                  )}
                  {doc.body && (
                    <span className="flex items-center gap-1 text-xs text-gray-400">
                      <Building2 className="h-3 w-3" />
                      {doc.body}
                    </span>
                  )}
                </div>
              </div>
            </button>
          ))}
        </div>
      )}

      {open && query.length >= 2 && results.length === 0 && !searching && (
        <div className="absolute z-50 mt-2 w-full rounded-xl border border-gray-200 bg-white p-6 text-center shadow-xl">
          <Search className="mx-auto mb-2 h-6 w-6 text-gray-300" />
          <p className="text-sm font-medium text-gray-500">
            No documents found
          </p>
          <p className="mt-0.5 text-xs text-gray-400">
            Try a different symbol, title, or record ID
          </p>
        </div>
      )}
    </div>
  );
}
