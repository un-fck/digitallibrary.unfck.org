import { Suspense } from "react";
import { Header } from "@/components/Header";
import { VerifyForm } from "@/features/auth/ui/VerifyForm";
import { fetchEntities } from "@/lib/data/entities";

export const dynamic = "force-dynamic";

export default async function VerifyPage() {
  const entities = await fetchEntities();

  return (
    <div className="flex min-h-screen flex-col bg-white">
      <Header maxWidth="6xl" />
      <main className="flex flex-1 items-center justify-center px-4">
        <div className="w-full max-w-sm">
          <h2 className="mb-2 text-xl font-semibold text-foreground">
            Complete Sign-In
          </h2>
          <p className="mb-6 text-sm text-gray-500">
            Select your entity to continue
          </p>
          <Suspense fallback={<p className="text-gray-500">Loading...</p>}>
            <VerifyForm entities={entities} />
          </Suspense>
        </div>
      </main>
    </div>
  );
}
