import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Header } from "@/components/Header";
import { Footer } from "@/components/Footer";

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col bg-gray-50">
      <Header maxWidth="5xl" />
      <main className="flex flex-1 items-center justify-center px-4 py-20 sm:px-6">
        <div className="text-center">
          <p className="mb-2 text-6xl font-bold tracking-tight text-gray-200">404</p>
          <h1 className="mb-2 text-xl font-semibold text-gray-900">Page not found</h1>
          <p className="mb-8 text-sm text-gray-500">
            The page you&apos;re looking for doesn&apos;t exist.
          </p>
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-lg bg-un-blue px-5 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-un-blue/90"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to Documents
          </Link>
        </div>
      </main>
      <Footer />
    </div>
  );
}
