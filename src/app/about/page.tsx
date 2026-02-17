import Link from "next/link";
import {
  ArrowRight,
  Database,
  Search,
  RefreshCw,
  BookOpen,
  Code2,
} from "lucide-react";
import { getCurrentUser } from "@/features/auth/service";
import { Header } from "@/components/Header";
import { Footer } from "@/components/Footer";

export const dynamic = "force-dynamic";

function FeatureCard({
  icon: Icon,
  title,
  description,
}: {
  icon: React.ElementType;
  title: string;
  description: string;
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-lg bg-un-blue/10">
        <Icon className="h-5 w-5 text-un-blue" />
      </div>
      <h4 className="mb-1.5 text-sm font-semibold text-gray-900">{title}</h4>
      <p className="text-sm leading-relaxed text-gray-500">{description}</p>
    </div>
  );
}

function StatCard({ value, label }: { value: string; label: string }) {
  return (
    <div className="text-center">
      <div className="text-3xl font-bold tracking-tight text-gray-900">
        {value}
      </div>
      <div className="mt-1 text-xs font-medium text-gray-400 uppercase tracking-wide">
        {label}
      </div>
    </div>
  );
}

export default async function AboutPage() {
  const user = await getCurrentUser();

  return (
    <div className="flex min-h-screen flex-col bg-gray-50">
      <Header user={user} maxWidth="5xl" activePage="about" />
      <main className="flex-1">
        {/* Hero */}
        <section className="bg-white border-b border-gray-200">
          <div className="mx-auto max-w-5xl px-4 py-16 text-center sm:px-6">
            <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-un-blue/10">
              <BookOpen className="h-7 w-7 text-un-blue" />
            </div>
            <h2 className="mb-3 text-3xl font-bold tracking-tight text-gray-900">
              UN Digital Library
            </h2>
            <p className="mx-auto max-w-2xl text-base leading-relaxed text-gray-500">
              An open-source interface to explore 767,000+ United Nations
              documents including resolutions, reports, meeting records, and
              more. Built on data harvested directly from{" "}
              <a
                href="https://digitallibrary.un.org"
                target="_blank"
                rel="noopener noreferrer"
                className="text-un-blue underline decoration-un-blue/30 underline-offset-2 hover:decoration-un-blue"
              >
                digitallibrary.un.org
              </a>
              .
            </p>
            <div className="mt-8">
              <Link
                href="/"
                className="inline-flex items-center gap-2 rounded-lg bg-un-blue px-5 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-un-blue/90"
              >
                Explore Documents
                <ArrowRight className="h-4 w-4" />
              </Link>
            </div>
          </div>
        </section>

        {/* Stats */}
        <section className="mx-auto max-w-5xl px-4 py-12 sm:px-6">
          <div className="grid grid-cols-3 gap-8 rounded-xl border border-gray-200 bg-white p-8 shadow-sm">
            <StatCard value="767K+" label="Documents" />
            <StatCard value="6" label="UN Languages" />
            <StatCard value="Nightly" label="Sync" />
          </div>
        </section>

        {/* Features */}
        <section className="mx-auto max-w-5xl px-4 pb-12 sm:px-6">
          <h3 className="mb-6 text-center text-xs font-semibold tracking-widest text-gray-400 uppercase">
            Features
          </h3>
          <div className="grid gap-4 md:grid-cols-3">
            <FeatureCard
              icon={Search}
              title="Full-text Search"
              description="Search by document symbol, title, or record ID across the entire UN Documents and Publications collection."
            />
            <FeatureCard
              icon={Database}
              title="Structured Data"
              description="Every document is parsed from MARCXML into structured fields: subjects, authors, agendas, voting records, and files."
            />
            <FeatureCard
              icon={RefreshCw}
              title="Nightly Updates"
              description="An automated pipeline syncs new and modified records from the UN Digital Library every night."
            />
          </div>
        </section>

        {/* API */}
        <section className="mx-auto max-w-5xl px-4 pb-12 sm:px-6">
          <h3 className="mb-6 text-center text-xs font-semibold tracking-widest text-gray-400 uppercase">
            REST API
          </h3>
          <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
            <div className="mb-4 flex items-center gap-2">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-un-blue/10">
                <Code2 className="h-5 w-5 text-un-blue" />
              </div>
              <div>
                <h4 className="text-sm font-semibold text-gray-900">
                  Programmatic Access
                </h4>
                <p className="text-sm text-gray-500">
                  Search, filter, and retrieve documents via a public JSON API
                </p>
              </div>
            </div>
            <div className="mb-4 rounded-lg bg-gray-50 p-4">
              <code className="block font-mono text-xs text-gray-700">
                curl https://digitallibrary.unfck.org/v1/search?q=climate+change
              </code>
            </div>
            <p className="mb-4 text-sm text-gray-500">
              Anonymous access is available at 10 req/min. Sign up for a free
              API key for higher limits — open to anyone.
            </p>
            <div className="flex gap-3">
              <Link
                href="/developer"
                className="inline-flex items-center gap-1 rounded-lg bg-un-blue px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-un-blue/90"
              >
                Get API Key
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
              <a
                href="/v1/docs"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
              >
                API Docs
              </a>
            </div>
          </div>
        </section>

        {/* How it works */}
        <section className="mx-auto max-w-5xl px-4 pb-16 sm:px-6">
          <h3 className="mb-6 text-center text-xs font-semibold tracking-widest text-gray-400 uppercase">
            How It Works
          </h3>
          <div className="grid gap-4 md:grid-cols-3">
            <div className="flex gap-3 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-un-blue text-xs font-bold text-white">
                1
              </div>
              <div>
                <h4 className="text-sm font-semibold text-gray-900">Harvest</h4>
                <p className="mt-1 text-sm text-gray-500">
                  MARCXML records are fetched from the UN search API using record
                  ID range slicing.
                </p>
              </div>
            </div>
            <div className="flex gap-3 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-un-blue text-xs font-bold text-white">
                2
              </div>
              <div>
                <h4 className="text-sm font-semibold text-gray-900">Parse</h4>
                <p className="mt-1 text-sm text-gray-500">
                  Each MARC21 record is parsed into 30+ structured fields and
                  stored in PostgreSQL.
                </p>
              </div>
            </div>
            <div className="flex gap-3 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-un-blue text-xs font-bold text-white">
                3
              </div>
              <div>
                <h4 className="text-sm font-semibold text-gray-900">Explore</h4>
                <p className="mt-1 text-sm text-gray-500">
                  Documents are searchable and viewable with metadata, JSON, and
                  raw MARCXML views.
                </p>
              </div>
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
