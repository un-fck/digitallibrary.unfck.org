import Link from "next/link";
import { ArrowRight, Code2, Key, Zap, Lock, ExternalLink } from "lucide-react";
import { getCurrentUser } from "@/features/auth/service";
import { Header } from "@/components/Header";
import { Footer } from "@/components/Footer";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "API Docs — UN Digital Library",
  description:
    "REST API reference for the UN Digital Library. Search and retrieve 767,000+ UN documents programmatically.",
};

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mb-6 text-center text-xs font-semibold tracking-widest text-gray-400 uppercase">
      {children}
    </h2>
  );
}

export default async function DocsPage() {
  const user = await getCurrentUser();

  return (
    <div className="flex min-h-screen flex-col bg-gray-50">
      <Header user={user} maxWidth="5xl" activePage="docs" />
      <main className="flex-1">
        {/* Hero */}
        <section className="bg-white border-b border-gray-200">
          <div className="mx-auto max-w-5xl px-4 py-14 text-center sm:px-6">
            <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-un-blue/10">
              <Code2 className="h-7 w-7 text-un-blue" />
            </div>
            <h1 className="mb-3 text-3xl font-bold tracking-tight text-gray-900">
              REST API
            </h1>
            <p className="mx-auto max-w-2xl text-base leading-relaxed text-gray-500">
              Programmatic access to 767,000+ UN documents. Anonymous access
              available at 10 req/min — sign in for a free key with higher limits.
            </p>
            <div className="mt-6">
              <a
                href="/v1/docs"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 rounded-lg bg-un-blue px-5 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-un-blue/90"
              >
                API Reference (Swagger)
                <ExternalLink className="h-4 w-4" />
              </a>
            </div>
          </div>
        </section>

        <div className="mx-auto max-w-5xl px-4 py-12 sm:px-6 space-y-14">
          {/* Authentication */}
          <section>
            <SectionHeading>Authentication</SectionHeading>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
                <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-gray-100">
                  <Zap className="h-4 w-4 text-gray-500" />
                </div>
                <h3 className="mb-1 text-sm font-semibold text-gray-900">Anonymous</h3>
                <p className="text-sm text-gray-500 mb-3">
                  Make requests without credentials. Lower rate limits apply.
                </p>
                <pre className="rounded-lg bg-gray-50 p-3 overflow-x-auto">
                  <code className="font-mono text-xs text-gray-600 whitespace-pre-wrap break-all">
                    {`curl "https://digitallibrary.unfck.org/v1/search?q=A/RES"`}
                  </code>
                </pre>
              </div>
              <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
                <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-un-blue/10">
                  <Lock className="h-4 w-4 text-un-blue" />
                </div>
                <h3 className="mb-1 text-sm font-semibold text-gray-900">API Key</h3>
                <p className="text-sm text-gray-500 mb-3">
                  Pass your key via <code className="font-mono text-xs bg-gray-100 px-1 rounded">Authorization</code> header or <code className="font-mono text-xs bg-gray-100 px-1 rounded">?api_key=</code> param.
                </p>
                <pre className="rounded-lg bg-gray-50 p-3 overflow-x-auto">
                  <code className="font-mono text-xs text-gray-600 whitespace-pre-wrap break-all">
                    {`curl -H "Authorization: Bearer undl_live_..." \\\n  "https://digitallibrary.unfck.org/v1/search?q=A/RES"`}
                  </code>
                </pre>
              </div>
            </div>
          </section>

          {/* Rate Limits */}
          <section>
            <SectionHeading>Rate Limits</SectionHeading>
            <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50">
                    <th className="px-5 py-3 text-left text-xs font-semibold text-gray-500">Tier</th>
                    <th className="px-5 py-3 text-left text-xs font-semibold text-gray-500">Rate</th>
                    <th className="px-5 py-3 text-left text-xs font-semibold text-gray-500">Daily</th>
                    <th className="px-5 py-3 text-left text-xs font-semibold text-gray-500">Access</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  <tr>
                    <td className="px-5 py-3 font-medium text-gray-700">Anonymous</td>
                    <td className="px-5 py-3 text-gray-500">10 req/min</td>
                    <td className="px-5 py-3 text-gray-500">100</td>
                    <td className="px-5 py-3 text-gray-500">No key needed</td>
                  </tr>
                  <tr>
                    <td className="px-5 py-3 font-medium text-gray-700">Free</td>
                    <td className="px-5 py-3 text-gray-500">60 req/min</td>
                    <td className="px-5 py-3 text-gray-500">10,000</td>
                    <td className="px-5 py-3 text-gray-500">Free API key</td>
                  </tr>
                  <tr>
                    <td className="px-5 py-3 font-medium text-gray-700">Research</td>
                    <td className="px-5 py-3 text-gray-500">300 req/min</td>
                    <td className="px-5 py-3 text-gray-500">100,000</td>
                    <td className="px-5 py-3 text-gray-500">Research institutions</td>
                  </tr>
                  <tr>
                    <td className="px-5 py-3 font-medium text-gray-700">Institutional</td>
                    <td className="px-5 py-3 text-gray-500">1,000 req/min</td>
                    <td className="px-5 py-3 text-gray-500">Unlimited</td>
                    <td className="px-5 py-3 text-gray-500">UN agencies &amp; partners</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          {/* Quick Start */}
          <section>
            <SectionHeading>Quick Start</SectionHeading>
            <div className="grid gap-4 md:grid-cols-3">
              <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
                <p className="mb-3 text-xs font-semibold text-gray-500">curl</p>
                <pre className="rounded-lg bg-gray-50 p-3 overflow-x-auto">
                  <code className="font-mono text-xs text-gray-700 whitespace-pre">{`# Search
curl "https://digitallibrary.unfck.org\\
  /v1/search?q=climate"

# With API key
curl \\
  -H "Authorization: Bearer KEY" \\
  "https://digitallibrary.unfck.org\\
  /v1/search?q=climate"`}</code>
                </pre>
              </div>

              <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
                <p className="mb-3 text-xs font-semibold text-gray-500">Python</p>
                <pre className="rounded-lg bg-gray-50 p-3 overflow-x-auto">
                  <code className="font-mono text-xs text-gray-700 whitespace-pre">{`import httpx

BASE = "https://digitallibrary.unfck.org"

r = httpx.get(
  f"{BASE}/v1/search",
  params={"q": "climate"},
  headers={
    "Authorization": "Bearer KEY"
  },
)
docs = r.json()["results"]`}</code>
                </pre>
              </div>

              <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
                <p className="mb-3 text-xs font-semibold text-gray-500">JavaScript</p>
                <pre className="rounded-lg bg-gray-50 p-3 overflow-x-auto">
                  <code className="font-mono text-xs text-gray-700 whitespace-pre">{`const BASE =
  "https://digitallibrary.unfck.org";

const res = await fetch(
  \`\${BASE}/v1/search?q=climate\`,
  {
    headers: {
      Authorization: \`Bearer \${KEY}\`,
    },
  }
);
const { results } = await res.json();`}</code>
                </pre>
              </div>
            </div>
          </section>

          {/* API Reference link */}
          <section className="rounded-xl border border-gray-200 bg-white p-8 shadow-sm text-center">
            <h3 className="mb-2 text-base font-semibold text-gray-900">
              Full endpoint reference
            </h3>
            <p className="mb-5 text-sm text-gray-500">
              Browse all endpoints, parameters, and response schemas in the interactive Swagger UI.
            </p>
            <a
              href="/v1/docs"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-5 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
            >
              Open API Reference
              <ExternalLink className="h-4 w-4" />
            </a>
          </section>

          {/* CTA */}
          <section className="rounded-xl border border-gray-200 bg-white p-8 shadow-sm text-center">
            <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-lg bg-un-blue/10">
              <Key className="h-5 w-5 text-un-blue" />
            </div>
            <h3 className="mb-2 text-base font-semibold text-gray-900">Get your API key</h3>
            <p className="mb-5 text-sm text-gray-500">
              Free keys are available. Sign in to generate your key and start building.
            </p>
            <Link
              href={user ? "/developer" : "/login?next=/developer"}
              className="inline-flex items-center gap-2 rounded-lg bg-un-blue px-5 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-un-blue/90"
            >
              {user ? "Go to Developer Dashboard" : "Sign in to get a key"}
              <ArrowRight className="h-4 w-4" />
            </Link>
          </section>
        </div>
      </main>
      <Footer />
    </div>
  );
}
