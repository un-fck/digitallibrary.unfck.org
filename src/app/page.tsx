import { Footer } from "@/components/Footer";
import { Header } from "@/components/Header";
import { DocumentExplorer } from "@/components/DocumentExplorer";
import { getCurrentUser } from "@/features/auth/service";

export const dynamic = "force-dynamic";

export default async function Home() {
  const user = await getCurrentUser();

  return (
    <div className="flex min-h-screen flex-col bg-gray-50">
      <Header user={user} entities={[]} maxWidth="5xl" activePage="home" />
      <main className="flex-1 px-4 py-10 sm:px-6">
        <div className="mx-auto max-w-5xl">
          <DocumentExplorer />
        </div>
      </main>
      <Footer />
    </div>
  );
}
