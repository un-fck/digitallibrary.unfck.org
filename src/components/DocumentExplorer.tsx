"use client";

import { useMemo, useState } from "react";
import { DocumentSearch, type SearchResult } from "@/components/DocumentSearch";
import { JsonView, allExpanded, defaultStyles } from "react-json-view-lite";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";

interface DocumentDetail {
  oai_identifier: string;
  recid: number | null;
  datestamp: string;
  deleted: boolean;
  metadata_prefix: string;
  source_set: string | null;
  source_url: string | null;
  document_symbol: string | null;
  title_primary: string | null;
  publication_date_primary: string | null;
  dc_title: string[];
  dc_creator: string[];
  dc_subject: string[];
  dc_description: string[];
  dc_publisher: string[];
  dc_contributor: string[];
  dc_date: string[];
  dc_type: string[];
  dc_format: string[];
  dc_identifier: string[];
  dc_source: string[];
  dc_language: string[];
  dc_relation: string[];
  dc_coverage: string[];
  dc_rights: string[];
  metadata_xml: string | null;
  metadata_json: Record<string, unknown> | null;
}

function renderValue(value: string | string[] | number | boolean | null) {
  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    return value.join(" | ");
  }
  if (value === null) return "";
  return String(value);
}

export function DocumentExplorer() {
  const [selected, setSelected] = useState<SearchResult | null>(null);
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"table" | "json" | "xml">("table");

  async function handleSelect(item: SearchResult) {
    setSelected(item);
    setDoc(null);
    setError(null);
    if (!item.recid) {
      setError("Selected result has no recid.");
      return;
    }

    setLoading(true);
    try {
      const res = await fetch(`/api/documents/${item.recid}`);
      if (!res.ok) {
        const payload = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(payload.error || `Failed to load document ${item.recid}`);
      }
      const payload = (await res.json()) as DocumentDetail;
      setDoc(payload);
      setViewMode("table");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  const rows = useMemo(() => {
    if (!doc) return [];
    return [
      ["oai_identifier", doc.oai_identifier],
      ["recid", doc.recid],
      ["datestamp", doc.datestamp],
      ["deleted", doc.deleted],
      ["metadata_prefix", doc.metadata_prefix],
      ["source_set", doc.source_set],
      ["source_url", doc.source_url],
      ["document_symbol", doc.document_symbol],
      ["title_primary", doc.title_primary],
      ["publication_date_primary", doc.publication_date_primary],
      ["dc_title", doc.dc_title],
      ["dc_creator", doc.dc_creator],
      ["dc_subject", doc.dc_subject],
      ["dc_description", doc.dc_description],
      ["dc_publisher", doc.dc_publisher],
      ["dc_contributor", doc.dc_contributor],
      ["dc_date", doc.dc_date],
      ["dc_type", doc.dc_type],
      ["dc_format", doc.dc_format],
      ["dc_identifier", doc.dc_identifier],
      ["dc_source", doc.dc_source],
      ["dc_language", doc.dc_language],
      ["dc_relation", doc.dc_relation],
      ["dc_coverage", doc.dc_coverage],
      ["dc_rights", doc.dc_rights],
    ] as Array<[string, string | string[] | number | boolean | null]>;
  }, [doc]);

  return (
    <section className="space-y-6">
      <DocumentSearch
        onSelect={handleSelect}
        placeholder="Search by symbol, title, or identifier..."
      />
      {selected && (
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700">
          Selected: {selected.symbol || selected.title || selected.recid}
        </div>
      )}
      {loading && (
        <div className="rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-600">
          Loading metadata...
        </div>
      )}
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}
      {doc && (
        <div className="space-y-3">
          <div className="flex gap-2">
            <button
              type="button"
              className={`rounded-md px-3 py-1.5 text-sm ${viewMode === "table" ? "bg-un-blue text-white" : "bg-gray-100 text-gray-700"}`}
              onClick={() => setViewMode("table")}
            >
              Metadata Table
            </button>
            <button
              type="button"
              className={`rounded-md px-3 py-1.5 text-sm ${viewMode === "json" ? "bg-un-blue text-white" : "bg-gray-100 text-gray-700"}`}
              onClick={() => setViewMode("json")}
            >
              JSON Tree
            </button>
            <button
              type="button"
              className={`rounded-md px-3 py-1.5 text-sm ${viewMode === "xml" ? "bg-un-blue text-white" : "bg-gray-100 text-gray-700"}`}
              onClick={() => setViewMode("xml")}
            >
              XML
            </button>
          </div>
          {viewMode === "table" && (
            <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
              <div className="grid grid-cols-1 divide-y divide-gray-200 md:grid-cols-2 md:divide-x md:divide-y-0">
                {rows.map(([key, value]) => (
                  <div key={key} className="grid grid-cols-[180px_1fr] gap-3 p-3">
                    <div className="text-xs font-semibold tracking-wide text-gray-500 uppercase">
                      {key}
                    </div>
                    <div className="text-sm text-gray-800 break-words">
                      {renderValue(value)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {viewMode === "json" && (
            <div className="overflow-auto rounded-lg border border-gray-200 bg-white p-4 text-sm">
              <JsonView
                data={doc.metadata_json || {}}
                shouldExpandNode={allExpanded}
                style={defaultStyles}
              />
            </div>
          )}
          {viewMode === "xml" && (
            <div className="overflow-auto rounded-lg border border-gray-200 bg-white">
              <SyntaxHighlighter
                language="xml"
                style={oneLight}
                customStyle={{ margin: 0, padding: "1rem" }}
                wrapLongLines
              >
                {doc.metadata_xml || ""}
              </SyntaxHighlighter>
            </div>
          )}
          {viewMode === "xml" && !doc.metadata_xml && (
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm text-gray-600">
              No XML payload available for this record.
            </div>
          )}
          {viewMode === "json" && !doc.metadata_json && (
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm text-gray-600">
              No JSON payload available for this record.
            </div>
          )}
        </div>
      )}
    </section>
  );
}
