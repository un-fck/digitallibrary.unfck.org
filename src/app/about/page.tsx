import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { getCurrentUser } from "@/features/auth/service";
import { Header, SITE_TITLE, SITE_SUBTITLE } from "@/components/Header";
import { Footer } from "@/components/Footer";

export const dynamic = "force-dynamic";

export default async function AboutPage() {
  const user = await getCurrentUser();
  const isLoggedIn = !!user;
  const ctaHref = isLoggedIn ? "/" : "/login";

  return (
    <div className="flex min-h-screen flex-col">
      <Header user={user} maxWidth="6xl" hideAbout />
      <main className="flex-1 bg-gradient-to-b from-gray-50 to-white">
        <section className="mx-auto max-w-6xl px-4 py-16 text-center">
          <h2 className="mb-4 text-4xl font-bold tracking-tight text-gray-900">
            {SITE_TITLE}
          </h2>
          <p className="mx-auto mb-8 max-w-2xl text-lg text-gray-600">
            {SITE_SUBTITLE}
          </p>
          <div className="flex items-center justify-center gap-4">
            <Link
              href={ctaHref}
              className="inline-flex items-center gap-2 rounded-lg bg-un-blue px-6 py-3 font-medium text-white transition-colors hover:bg-un-blue/90"
            >
              {isLoggedIn ? "Go to Dashboard" : "Get Started"}
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </section>

        <section className="mx-auto max-w-4xl px-4 pb-16">
          <h3 className="mb-8 text-center text-sm font-semibold tracking-wider text-gray-400 uppercase">
            How It Works
          </h3>
          <div className="grid gap-8 md:grid-cols-3">
            <div className="text-center">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-un-blue text-lg font-bold text-white">
                1
              </div>
              <h4 className="mb-2 font-semibold text-gray-900">Sign In</h4>
              <p className="text-sm text-gray-600">
                Enter your email and click the magic link sent to your inbox.
              </p>
            </div>
            <div className="text-center">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-un-blue text-lg font-bold text-white">
                2
              </div>
              <h4 className="mb-2 font-semibold text-gray-900">
                Select Entity
              </h4>
              <p className="text-sm text-gray-600">
                Choose your organisational entity on first sign-in.
              </p>
            </div>
            <div className="text-center">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-un-blue text-lg font-bold text-white">
                3
              </div>
              <h4 className="mb-2 font-semibold text-gray-900">Get Started</h4>
              <p className="text-sm text-gray-600">
                Access the dashboard and start using the application.
              </p>
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
