import { Github } from "lucide-react";
import { Header } from "@/components/Header";
import { Footer } from "@/components/Footer";
import { getCurrentUser } from "@/features/auth/service";
import { fetchEntities } from "@/lib/data/entities";
import { SearchDemo } from "@/components/SearchDemo";

export const dynamic = "force-dynamic";

export default async function Home() {
  const [user, entities] = await Promise.all([
    getCurrentUser(),
    fetchEntities(),
  ]);

  return (
    <div className="flex min-h-screen flex-col">
      <Header user={user} entities={entities} />
      <main className="flex-1 bg-background px-6 py-8">
        <div className="mx-auto max-w-4xl">
          {/* GitHub Link */}
          <a
            href="https://github.com/kleinlennart/un-website-boilerplate"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 text-un-blue hover:underline"
          >
            <Github className="h-5 w-5" />
            <span>View original template on GitHub</span>
          </a>

          {/* Entity and Document Search */}
          <section className="mt-10">
            <h2 className="mb-6 text-2xl font-bold text-foreground">
              Entity and Document Search
            </h2>
            <SearchDemo entities={entities} />
          </section>

          {/* Theme Colors Showcase */}
          <section className="mt-12">
            <h2 className="mb-6 text-2xl font-bold text-foreground">
              Theme Colors
            </h2>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              {[
                { name: "UN Blue", color: "bg-un-blue", hex: "#009EDB" },
                { name: "Faded Jade", color: "bg-faded-jade", hex: "#4A7C7E" },
                {
                  name: "Camouflage Green",
                  color: "bg-camouflage-green",
                  hex: "#7D8471",
                },
                {
                  name: "Pale Oyster",
                  color: "bg-pale-oyster",
                  hex: "#9B8B7A",
                },
                { name: "Au Chico", color: "bg-au-chico", hex: "#A0665C" },
                { name: "Smoky", color: "bg-smoky", hex: "#6C5B7B" },
                {
                  name: "Shuttle Gray",
                  color: "bg-shuttle-gray",
                  hex: "#5A6C7D",
                },
                { name: "Trout", color: "bg-trout", hex: "#495057" },
                { name: "Dusty Gray", color: "bg-dusty-gray", hex: "#969696" },
              ].map((c) => (
                <div key={c.name} className="flex flex-col gap-2">
                  <div className={`h-20 rounded-lg ${c.color}`} />
                  <div>
                    <p className="text-sm font-medium text-foreground">
                      {c.name}
                    </p>
                    <p className="text-xs text-gray-600">{c.hex}</p>
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* Grayscale Showcase */}
          <section className="mt-12">
            <h2 className="mb-2 text-2xl font-bold text-foreground">
              Grayscale
            </h2>
            <a
              href="https://tailwindcss.com/docs/colors"
              target="_blank"
              rel="noopener noreferrer"
              className="mb-6 inline-block text-sm text-un-blue hover:underline"
            >
              from Tailwind CSS
            </a>
            <div className="space-y-0 overflow-hidden rounded-lg">
              {[
                { name: "black", bg: "bg-black", text: "text-white" },
                { name: "gray-950", bg: "bg-gray-950", text: "text-white" },
                { name: "gray-900", bg: "bg-gray-900", text: "text-white" },
                { name: "gray-800", bg: "bg-gray-800", text: "text-white" },
                { name: "gray-700", bg: "bg-gray-700", text: "text-white" },
                { name: "gray-600", bg: "bg-gray-600", text: "text-white" },
                { name: "gray-500", bg: "bg-gray-500", text: "text-white" },
                { name: "gray-400", bg: "bg-gray-400", text: "text-black" },
                { name: "gray-300", bg: "bg-gray-300", text: "text-black" },
                { name: "gray-200", bg: "bg-gray-200", text: "text-black" },
                { name: "gray-100", bg: "bg-gray-100", text: "text-black" },
                { name: "gray-50", bg: "bg-gray-50", text: "text-black" },
                { name: "white", bg: "bg-white", text: "text-black" },
              ].map((g) => (
                <div key={g.name} className={`${g.bg} p-6`}>
                  <p className={`font-bold ${g.text}`}>{g.name}</p>
                </div>
              ))}
            </div>
          </section>

          {/* Typography Showcase */}
          <section className="mt-12 mb-12">
            <h2 className="mb-2 text-2xl font-bold text-foreground">
              Typography Scale
            </h2>
            <p className="mb-6 text-sm text-gray-600">Roboto Font Family</p>
            <div className="space-y-4">
              {[
                { label: "text-xs · 0.75rem (12px)", className: "text-xs" },
                { label: "text-sm · 0.875rem (14px)", className: "text-sm" },
                { label: "text-base · 1rem (16px)", className: "text-base" },
                { label: "text-lg · 1.125rem (18px)", className: "text-lg" },
                { label: "text-xl · 1.25rem (20px)", className: "text-xl" },
                { label: "text-2xl · 1.5rem (24px)", className: "text-2xl" },
                { label: "text-3xl · 1.875rem (30px)", className: "text-3xl" },
                { label: "text-4xl · 2.25rem (36px)", className: "text-4xl" },
              ].map((t, i, arr) => (
                <div
                  key={t.label}
                  className={
                    i < arr.length - 1 ? "border-b border-gray-200 pb-4" : ""
                  }
                >
                  <p className="mb-2 text-xs text-gray-600">{t.label}</p>
                  <p className={`${t.className} text-foreground`}>
                    Lorem ipsum dolor sit amet, consectetur adipiscing elit.
                  </p>
                </div>
              ))}
            </div>
          </section>
        </div>
      </main>
      <Footer />
    </div>
  );
}
