import Image from "next/image";
import Link from "next/link";
import { UserMenu } from "./UserMenu";
import type { EntityOption } from "./EntityCombobox";

interface Props {
  user?: { email: string; entity?: string | null } | null;
  children?: React.ReactNode;
  entities?: EntityOption[];
  maxWidth?: "5xl" | "6xl" | "7xl";
  activePage?: "home" | "about" | "developer";
}

export const SITE_TITLE = "UN Digital Library";
export const SITE_SUBTITLE = "Open access to UN documents";

export function Header({
  user,
  children,
  entities = [],
  maxWidth = "7xl",
  activePage,
}: Props) {
  const isLoggedIn = !!user;
  const widthClass =
    maxWidth === "5xl"
      ? "max-w-5xl"
      : maxWidth === "6xl"
        ? "max-w-6xl"
        : "max-w-7xl";

  return (
    <header className="border-b border-gray-200 bg-white">
      <div
        className={`mx-auto flex ${widthClass} items-center justify-between px-4 py-3 sm:px-6`}
      >
        <Link
          href="/"
          className="flex items-center gap-3 transition-opacity hover:opacity-90"
        >
          <Image
            src="/images/UN_Logo_Stacked_Colour_English.svg"
            alt="UN Logo"
            width={44}
            height={44}
            priority
            className="h-11 w-auto select-none"
            draggable={false}
          />
          <div>
            <h1 className="text-lg font-bold tracking-tight text-gray-900">
              {SITE_TITLE}
            </h1>
            <p className="text-xs text-gray-400">{SITE_SUBTITLE}</p>
          </div>
        </Link>
        <nav className="flex items-center gap-3">
          {activePage === "about" ? (
            <Link
              href="/"
              className="rounded-lg px-3 py-1.5 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-900"
            >
              Documents
            </Link>
          ) : (
            <Link
              href="/about"
              className="rounded-lg px-3 py-1.5 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-900"
            >
              About
            </Link>
          )}
          <Link
            href="/developer"
            className="rounded-lg px-3 py-1.5 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-900"
          >
            API
          </Link>
          {isLoggedIn ? (
            <UserMenu
              email={user.email}
              entity={user.entity}
              entities={entities}
            />
          ) : (
            <Link
              href="/login"
              className="rounded-lg bg-un-blue px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-un-blue/90"
            >
              Sign In
            </Link>
          )}
          {children}
        </nav>
      </div>
    </header>
  );
}
