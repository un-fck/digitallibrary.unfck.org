import Image from "next/image";
import Link from "next/link";
import { UserMenu } from "./UserMenu";
import type { EntityOption } from "./EntityCombobox";

interface Props {
  user?: { email: string; entity?: string | null } | null;
  children?: React.ReactNode;
  entities?: EntityOption[];
  maxWidth?: "5xl" | "6xl" | "7xl";
  activePage?: "home" | "docs" | "developer";
}

export const SITE_TITLE = "UN Digital Library";
export const SITE_SUBTITLE = "Open access to UN documents";

function NavLink({
  href,
  active,
  children,
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
        active
          ? "bg-gray-100 text-gray-900"
          : "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
      }`}
    >
      {children}
    </Link>
  );
}

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
        <nav className="flex items-center gap-1">
          <NavLink href="/" active={activePage === "home"}>
            Documents
          </NavLink>
          <NavLink href="/docs" active={activePage === "docs"}>
            API Docs
          </NavLink>
          {isLoggedIn && (
            <NavLink href="/developer" active={activePage === "developer"}>
              Developer
            </NavLink>
          )}
          <div className="ml-2">
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
          </div>
          {children}
        </nav>
      </div>
    </header>
  );
}
