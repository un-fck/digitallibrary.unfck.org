"use client";

import { useMemo, useState } from "react";
import { DocumentSearch, type SearchResult } from "@/components/DocumentSearch";
import { JsonView, allExpanded, defaultStyles } from "react-json-view-lite";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";
import {
  Calendar,
  Building2,
  ExternalLink,
  FileDown,
  FileText,
  Globe,
  Hash,
  Loader2,
  Scale,
  Tag as TagIcon,
  Users,
  BookOpen,
  Link as LinkIcon,
  ClipboardList,
  Vote,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function Chip({
  children,
  variant = "default",
}: {
  children: React.ReactNode;
  variant?: "default" | "blue" | "muted";
}) {
  const styles = {
    default:
      "bg-gray-100 text-gray-700 border-gray-200",
    blue: "bg-un-blue/8 text-un-blue border-un-blue/15",
    muted: "bg-gray-50 text-gray-500 border-gray-200",
  };
  return (
    <span
      className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${styles[variant]}`}
    >
      {children}
    </span>
  );
}

function SectionCard({
  icon: Icon,
  title,
  children,
}: {
  icon: React.ElementType;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
      <div className="flex items-center gap-2 border-b border-gray-100 px-4 py-3">
        <Icon className="h-4 w-4 text-gray-400" />
        <h4 className="text-xs font-semibold tracking-wide text-gray-500 uppercase">
          {title}
        </h4>
      </div>
      <div className="px-4 py-3">{children}</div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="py-1.5">
      <dt className="text-xs font-medium text-gray-400">{label}</dt>
      <dd className="mt-0.5 text-sm text-gray-800">{children}</dd>
    </div>
  );
}

function getDocumentUrls(symbol: string) {
  const encoded = encodeURIComponent(symbol);
  return {
    docsPage: `https://docs.un.org/en/${encoded}`,
    pdfDownload: `https://documents.un.org/api/symbol/access?s=${encoded}&l=en&t=pdf`,
    docxDownload: `https://documents.un.org/api/symbol/access?s=${encoded}&l=en&t=doc`,
  };
}

function formatSize(bytes: string | null): string {
  if (!bytes) return "";
  const n = Number(bytes);
  if (n >= 1_048_576) return `${(n / 1_048_576).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

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

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center gap-2 rounded-xl border border-gray-200 bg-white py-12 shadow-sm">
          <Loader2 className="h-5 w-5 animate-spin text-un-blue" />
          <span className="text-sm text-gray-500">Loading document...</span>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Document detail */}
      {doc && (
        <div className="space-y-4">
          {/* Title card */}
          <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                {doc.document_symbol && (
                  <div className="mb-1.5 flex items-center gap-2">
                    <Chip variant="blue">{doc.document_symbol}</Chip>
                    {doc.resource_subtype && (
                      <Chip variant="muted">{doc.resource_subtype}</Chip>
                    )}
                  </div>
                )}
                <h3 className="text-lg leading-snug font-semibold text-gray-900">
                  {doc.title || `Record ${doc.recid}`}
                </h3>
                {doc.title_statement && (
                  <p className="mt-1 text-sm text-gray-500">
                    {doc.title_statement}
                  </p>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-1.5 text-xs text-gray-400">
                <Hash className="h-3.5 w-3.5" />
                {doc.recid}
              </div>
            </div>

            {/* Quick facts row */}
            <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-gray-100 pt-3 text-xs text-gray-500">
              {doc.un_body && (
                <span className="flex items-center gap-1">
                  <Building2 className="h-3.5 w-3.5" />
                  {doc.un_body}
                  {doc.un_committee && (
                    <span className="text-gray-400">
                      / {doc.un_committee}
                    </span>
                  )}
                </span>
              )}
              {doc.date_publication && (
                <span className="flex items-center gap-1">
                  <Calendar className="h-3.5 w-3.5" />
                  {doc.date_publication}
                  {doc.date_text &&
                    doc.date_text !== doc.date_publication && (
                      <span className="text-gray-400">({doc.date_text})</span>
                    )}
                </span>
              )}
              {doc.languages.length > 0 && (
                <span className="flex items-center gap-1">
                  <Globe className="h-3.5 w-3.5" />
                  {doc.languages.join(", ")}
                </span>
              )}
              {doc.resource_type && (
                <span className="flex items-center gap-1">
                  <BookOpen className="h-3.5 w-3.5" />
                  {doc.resource_type}
                </span>
              )}
            </div>

            {/* ODS action buttons */}
            {doc.document_symbol && (() => {
              const urls = getDocumentUrls(doc.document_symbol);
              return (
                <div className="mt-3 flex flex-wrap gap-2 border-t border-gray-100 pt-3">
                  <a
                    href={urls.docsPage}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 rounded-lg bg-un-blue px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-un-blue/90"
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                    Open on docs.un.org
                  </a>
                  <a
                    href={urls.pdfDownload}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50"
                  >
                    <FileDown className="h-3.5 w-3.5" />
                    Download PDF
                  </a>
                  <a
                    href={urls.docxDownload}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50"
                  >
                    <FileText className="h-3.5 w-3.5" />
                    Download DOCX
                  </a>
                  <a
                    href={`https://digitallibrary.un.org/record/${doc.recid}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50"
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                    Digital Library
                  </a>
                </div>
              );
            })()}
          </div>

          {/* View mode tabs */}
          <div className="flex gap-1 rounded-lg bg-gray-100 p-1">
            {(
              [
                ["table", "Metadata"],
                ["json", "JSON"],
                ["xml", "MARCXML"],
              ] as const
            ).map(([mode, label]) => (
              <button
                key={mode}
                type="button"
                className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  viewMode === mode
                    ? "bg-white text-gray-900 shadow-sm"
                    : "text-gray-500 hover:text-gray-700"
                }`}
                onClick={() => setViewMode(mode)}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Metadata view */}
          {viewMode === "table" && (
            <div className="grid gap-4 md:grid-cols-2">
              {/* Summary & notes */}
              {(doc.summary || doc.notes.length > 0) && (
                <div className="md:col-span-2">
                  <SectionCard icon={BookOpen} title="Summary">
                    {doc.summary && (
                      <p className="text-sm leading-relaxed text-gray-700">
                        {doc.summary}
                      </p>
                    )}
                    {doc.notes.length > 0 && (
                      <ul className="mt-2 space-y-1">
                        {doc.notes.map((n, i) => (
                          <li
                            key={i}
                            className="text-sm leading-relaxed text-gray-600"
                          >
                            {n}
                          </li>
                        ))}
                      </ul>
                    )}
                  </SectionCard>
                </div>
              )}

              {/* Subjects */}
              {doc.subjects.length > 0 && (
                <SectionCard icon={TagIcon} title="Subjects">
                  <div className="flex flex-wrap gap-1.5">
                    {doc.subjects.map((s) => (
                      <Chip key={s}>{s}</Chip>
                    ))}
                  </div>
                </SectionCard>
              )}

              {/* Authors */}
              {doc.corporate_authors.length > 0 && (
                <SectionCard icon={Users} title="Authors">
                  <div className="flex flex-wrap gap-1.5">
                    {doc.corporate_authors.map((a, i) => (
                      <Chip key={`${a.name}-${i}`}>
                        {a.name}
                        {a.type && (
                          <span className="ml-1 text-gray-400">[{a.type}]</span>
                        )}
                      </Chip>
                    ))}
                  </div>
                </SectionCard>
              )}

              {/* Files */}
              {doc.files.length > 0 && (
                <SectionCard icon={FileDown} title="Files">
                  <div className="grid gap-2 sm:grid-cols-2">
                    {doc.files.map((f, i) => (
                      <a
                        key={i}
                        href={f.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm transition-colors hover:border-un-blue/30 hover:bg-un-blue/5"
                      >
                        <FileDown className="h-4 w-4 shrink-0 text-un-blue" />
                        <span className="font-medium text-gray-800">
                          {f.lang || "Download"}
                        </span>
                        {f.size && (
                          <span className="ml-auto text-xs text-gray-400">
                            {formatSize(f.size)}
                          </span>
                        )}
                      </a>
                    ))}
                  </div>
                </SectionCard>
              )}

              {/* Classification */}
              {(doc.doc_class_desc || doc.publisher) && (
                <SectionCard icon={Scale} title="Classification">
                  {doc.doc_class_desc && (
                    <Field label="Document class">
                      {doc.doc_class_desc}
                      {doc.doc_class_code && (
                        <span className="ml-1 text-gray-400">
                          ({doc.doc_class_code})
                        </span>
                      )}
                    </Field>
                  )}
                  {doc.publisher && (
                    <Field label="Publisher">
                      {[doc.pub_place, doc.publisher]
                        .filter(Boolean)
                        .join(", ")}
                    </Field>
                  )}
                  {doc.physical_desc && (
                    <Field label="Extent">{doc.physical_desc}</Field>
                  )}
                </SectionCard>
              )}

              {/* Voting */}
              {doc.vote_summary && (
                <SectionCard icon={Vote} title="Voting">
                  <p className="text-sm text-gray-700">{doc.vote_summary}</p>
                </SectionCard>
              )}

              {/* Agenda items */}
              {doc.agenda_items.length > 0 && (
                <SectionCard icon={ClipboardList} title="Agenda">
                  <ul className="space-y-1.5">
                    {doc.agenda_items.map((a, i) => (
                      <li key={i} className="text-sm text-gray-700">
                        {a.item && (
                          <span className="mr-1 font-medium text-gray-900">
                            Item {a.item}:
                          </span>
                        )}
                        {a.topic || a.desc || a.doc}
                      </li>
                    ))}
                  </ul>
                </SectionCard>
              )}

              {/* Related documents */}
              {doc.related_documents.length > 0 && (
                <SectionCard icon={LinkIcon} title="Related Documents">
                  <div className="flex flex-wrap gap-1.5">
                    {doc.related_documents.map((r, i) => (
                      <Chip key={`${r.symbol}-${i}`} variant="blue">
                        {r.symbol}
                      </Chip>
                    ))}
                  </div>
                </SectionCard>
              )}
            </div>
          )}

          {/* JSON view */}
          {viewMode === "json" && (
            <div className="overflow-auto rounded-xl border border-gray-200 bg-white p-4 text-sm shadow-sm">
              <JsonView
                data={jsonData}
                shouldExpandNode={allExpanded}
                style={defaultStyles}
              />
            </div>
          )}

          {/* XML view */}
          {viewMode === "xml" && doc.marcxml && (
            <div className="overflow-auto rounded-xl border border-gray-200 bg-white shadow-sm">
              <SyntaxHighlighter
                language="xml"
                style={oneLight}
                customStyle={{ margin: 0, padding: "1rem", borderRadius: "0.75rem" }}
                wrapLongLines
              >
                {doc.marcxml}
              </SyntaxHighlighter>
            </div>
          )}
          {viewMode === "xml" && !doc.marcxml && (
            <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 text-sm text-gray-500">
              No MARCXML payload available for this record.
            </div>
          )}
        </div>
      )}

      {/* Empty state */}
      {!doc && !loading && !error && (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-gray-300 bg-white/50 py-16 text-center">
          <BookOpen className="mb-3 h-10 w-10 text-gray-300" />
          <p className="text-sm font-medium text-gray-400">
            Search for a document to get started
          </p>
          <p className="mt-1 text-xs text-gray-400">
            Try a symbol like A/RES/78/1 or a keyword
          </p>
        </div>
      )}
    </section>
  );
}
