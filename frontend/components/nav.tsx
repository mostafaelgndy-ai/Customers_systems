"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearTokens, loadTokens } from "../lib/api";
import { useAuthGuard } from "../lib/hooks";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/datasets", label: "Datasets" },
  { href: "/runs", label: "Runs & jobs" },
  { href: "/customers", label: "Churn risks" },
  { href: "/segments", label: "Segments" },
  { href: "/exports", label: "Exports" },
];

export default function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const { profile, loading } = useAuthGuard();

  if (pathname === "/login") return null;

  const signedIn = !!loadTokens().access_token;

  return (
    <header className="topbar">
      <div className="topbar-inner">
        <Link href="/" className="brand">
          CustomerIQ
        </Link>
        <nav className="nav-links">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={pathname === link.href ? "nav-link active" : "nav-link"}
            >
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="nav-user">
          {loading || !signedIn ? null : (
            <>
              <span className="nav-email">{profile?.user.email}</span>
              <button
                type="button"
                className="btn btn-secondary btn-small"
                onClick={() => {
                  clearTokens();
                  router.replace("/login");
                }}
              >
                Sign out
              </button>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
