"use client";

import { useMemo, useState } from "react";
import { DocumentSearch, type SearchResult } from "@/components/DocumentSearch";
import { JsonView, allExpanded, defaultStyles } from "react-json-view-lite";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";

interface FileEntry {
  url: string;
  lang: string | null;
  size: string | null;
  uuid: string | null;
}

interface AgendaItem {
  doc: string | null;
  item: string | null;
  desc: string | null;
  topic: string | null;
}

interface RelatedDoc {
  symbol: string;
  relationship: string | null;
}

interface CorporateAuthor {
  name: string;
  type: string | null;
}

interface DocumentDetail {
  recid: number;
  document_symbol: string | null;
  symbol_body: string | null;
  symbol_session: string | null;
  symbol_committee: string | null;
  title: string | null;
  title_statement: string | null;
  date_publication: string | null;
  date_text: string | null;
  publisher: string | null;
  pub_place: string | null;
  physical_desc: string | null;
  doc_class_code: string | null;
  doc_class_desc: string | null;
  languages: string[];
  subjects: string[];
  corporate_authors: CorporateAuthor[];
  un_body: string | null;
  un_committee: string | null;
  notes: string[];
  summary: string | null;
  files: FileEntry[];
  collections: string[];
  resource_type: string | null;
  resource_subtype: string | null;
  vote_summary: string | null;
  agenda_items: AgendaItem[];
  related_documents: RelatedDoc[];
  marcxml: string;
  harvested_at: string;
}

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-block rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-700">
      {children}
    </span>
  );
}

function Section({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[160px_1fr] gap-3 px-4 py-2.5">
      <div className="text-xs font-semibold tracking-wide text-gray-500 uppercase">
        {label}
      </div>
      <div className="text-sm text-gray-800 break-words">{children}</div>
    </div>
  );
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
        const payload = (await res.json().catch(() => ({}))) as {
          error?: string;
        };
        throw new Error(
          payload.error || `Failed to load document ${item.recid}`,
        );
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

  const jsonData = useMemo(() => {
    if (!doc) return {};
    const { marcxml: _, ...rest } = doc;
    return rest;
  }, [doc]);

  return (
    <section className="space-y-6">
      <DocumentSearch
        onSelect={handleSelect}
        placeholder="Search by symbol, title, or record ID..."
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
          {/* View mode tabs */}
          <div className="flex gap-2">
            {(["table", "json", "xml"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                className={`rounded-md px-3 py-1.5 text-sm ${viewMode === mode ? "bg-un-blue text-white" : "bg-gray-100 text-gray-700"}`}
                onClick={() => setViewMode(mode)}
              >
                {mode === "table"
                  ? "Metadata"
                  : mode === "json"
                    ? "JSON"
                    : "MARCXML"}
              </button>
            ))}
          </div>

          {/* Metadata table view */}
          {viewMode === "table" && (
            <div className="divide-y divide-gray-200 rounded-lg border border-gray-200 bg-white">
              {/* Identity */}
              <Section label="Record ID">{doc.recid}</Section>
              {doc.document_symbol && (
                <Section label="Symbol">{doc.document_symbol}</Section>
              )}
              {doc.title && <Section label="Title">{doc.title}</Section>}
              {doc.title_statement && (
                <Section label="Responsibility">{doc.title_statement}</Section>
              )}

              {/* Classification */}
              {doc.un_body && (
                <Section label="UN Body">
                  {doc.un_body}
                  {doc.un_committee && ` / ${doc.un_committee}`}
                </Section>
              )}
              {doc.resource_type && (
                <Section label="Type">
                  {doc.resource_type}
                  {doc.resource_subtype && ` / ${doc.resource_subtype}`}
                </Section>
              )}
              {doc.doc_class_desc && (
                <Section label="Classification">
                  {doc.doc_class_desc}
                  {doc.doc_class_code && (
                    <span className="ml-1 text-gray-400">
                      ({doc.doc_class_code})
                    </span>
                  )}
                </Section>
              )}

              {/* Dates */}
              {(doc.date_publication || doc.date_text) && (
                <Section label="Date">
                  {doc.date_publication}
                  {doc.date_text && doc.date_text !== doc.date_publication && (
                    <span className="ml-2 text-gray-500">
                      ({doc.date_text})
                    </span>
                  )}
                </Section>
              )}

              {/* Languages */}
              {doc.languages.length > 0 && (
                <Section label="Languages">
                  <div className="flex flex-wrap gap-1">
                    {doc.languages.map((l) => (
                      <Tag key={l}>{l}</Tag>
                    ))}
                  </div>
                </Section>
              )}

              {/* Summary */}
              {doc.summary && (
                <Section label="Summary">{doc.summary}</Section>
              )}

              {/* Subjects */}
              {doc.subjects.length > 0 && (
                <Section label="Subjects">
                  <div className="flex flex-wrap gap-1">
                    {doc.subjects.map((s) => (
                      <Tag key={s}>{s}</Tag>
                    ))}
                  </div>
                </Section>
              )}

              {/* Authors */}
              {doc.corporate_authors.length > 0 && (
                <Section label="Authors">
                  <div className="flex flex-wrap gap-1">
                    {doc.corporate_authors.map((a, i) => (
                      <Tag key={`${a.name}-${i}`}>
                        {a.name}
                        {a.type && (
                          <span className="ml-1 text-gray-400">
                            [{a.type}]
                          </span>
                        )}
                      </Tag>
                    ))}
                  </div>
                </Section>
              )}

              {/* Notes */}
              {doc.notes.length > 0 && (
                <Section label="Notes">
                  <ul className="list-inside list-disc space-y-0.5">
                    {doc.notes.map((n, i) => (
                      <li key={i}>{n}</li>
                    ))}
                  </ul>
                </Section>
              )}

              {/* Files */}
              {doc.files.length > 0 && (
                <Section label="Files">
                  <div className="flex flex-wrap gap-2">
                    {doc.files.map((f, i) => (
                      <a
                        key={i}
                        href={f.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 rounded bg-un-blue/10 px-2 py-1 text-xs text-un-blue hover:bg-un-blue/20"
                      >
                        {f.lang || "Download"}
                        {f.size && (
                          <span className="text-gray-400">
                            ({Math.round(Number(f.size) / 1024)}KB)
                          </span>
                        )}
                      </a>
                    ))}
                  </div>
                </Section>
              )}

              {/* Voting */}
              {doc.vote_summary && (
                <Section label="Vote">{doc.vote_summary}</Section>
              )}

              {/* Agenda items */}
              {doc.agenda_items.length > 0 && (
                <Section label="Agenda">
                  <ul className="list-inside list-disc space-y-0.5">
                    {doc.agenda_items.map((a, i) => (
                      <li key={i}>
                        {a.item && (
                          <span className="font-medium">Item {a.item}: </span>
                        )}
                        {a.topic || a.desc || a.doc}
                      </li>
                    ))}
                  </ul>
                </Section>
              )}

              {/* Related documents */}
              {doc.related_documents.length > 0 && (
                <Section label="Related">
                  <div className="flex flex-wrap gap-1">
                    {doc.related_documents.map((r, i) => (
                      <Tag key={`${r.symbol}-${i}`}>{r.symbol}</Tag>
                    ))}
                  </div>
                </Section>
              )}

              {/* Publication */}
              {(doc.publisher || doc.pub_place) && (
                <Section label="Publisher">
                  {[doc.pub_place, doc.publisher].filter(Boolean).join(", ")}
                </Section>
              )}
              {doc.physical_desc && (
                <Section label="Extent">{doc.physical_desc}</Section>
              )}

              {/* Housekeeping */}
              <Section label="Harvested">{doc.harvested_at}</Section>
            </div>
          )}

          {/* JSON view */}
          {viewMode === "json" && (
            <div className="overflow-auto rounded-lg border border-gray-200 bg-white p-4 text-sm">
              <JsonView
                data={jsonData}
                shouldExpandNode={allExpanded}
                style={defaultStyles}
              />
            </div>
          )}

          {/* XML view */}
          {viewMode === "xml" && doc.marcxml && (
            <div className="overflow-auto rounded-lg border border-gray-200 bg-white">
              <SyntaxHighlighter
                language="xml"
                style={oneLight}
                customStyle={{ margin: 0, padding: "1rem" }}
                wrapLongLines
              >
                {doc.marcxml}
              </SyntaxHighlighter>
            </div>
          )}
          {viewMode === "xml" && !doc.marcxml && (
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm text-gray-600">
              No MARCXML payload available for this record.
            </div>
          )}
        </div>
      )}
    </section>
  );
}
