import Link from "next/link";

export function Footer() {
  return (
    <footer className="border-t border-gray-200 bg-white">
      <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-2 px-4 py-5 sm:flex-row sm:px-6">
        <p className="text-xs text-gray-400">
          Data sourced from the{" "}
          <a
            href="https://digitallibrary.un.org"
            target="_blank"
            rel="noopener noreferrer"
            className="text-gray-500 underline decoration-gray-300 underline-offset-2 transition-colors hover:text-un-blue"
          >
            UN Digital Library
          </a>
        </p>
        <div className="flex items-center gap-4 text-xs text-gray-400">
          <Link
            href="/about"
            className="transition-colors hover:text-gray-600"
          >
            About
          </Link>
          <a
            href="https://github.com/unfck-org/digitallibrary"
            target="_blank"
            rel="noopener noreferrer"
            className="transition-colors hover:text-gray-600"
          >
            GitHub
          </a>
        </div>
      </div>
    </footer>
  );
}
