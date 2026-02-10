import { Footer } from "@/components/Footer";
import { Header } from "@/components/Header";
import { DocumentExplorer } from "@/components/DocumentExplorer";
import { getCurrentUser } from "@/features/auth/service";

export const dynamic = "force-dynamic";

export default async function Home() {
  const user = await getCurrentUser();

  return (
    <div className="flex min-h-screen flex-col">
      <Header user={user} entities={[]} hideAbout />
      <main className="flex-1 bg-background px-6 py-8">
        <div className="mx-auto max-w-6xl">
          <h2 className="mb-2 text-2xl font-bold text-foreground">
            Document Metadata Explorer
          </h2>
          <p className="mb-6 text-sm text-gray-600">
            Search documents and inspect all synchronized Dublin Core metadata.
          </p>
          <DocumentExplorer />
        </div>
      </main>
      <Footer />
    </div>
  );
}
