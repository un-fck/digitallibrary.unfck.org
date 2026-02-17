import { getCurrentUser } from "@/features/auth/service";
import { Header } from "@/components/Header";
import { Footer } from "@/components/Footer";
import { DeveloperDashboard } from "@/components/DeveloperDashboard";

export const dynamic = "force-dynamic";

export default async function DeveloperPage() {
  const user = await getCurrentUser();

  return (
    <div className="flex min-h-screen flex-col bg-gray-50">
      <Header user={user} maxWidth="5xl" />
      <main className="flex-1 px-4 py-10 sm:px-6">
        <div className="mx-auto max-w-5xl">
          <DeveloperDashboard />
        </div>
      </main>
      <Footer />
    </div>
  );
}
