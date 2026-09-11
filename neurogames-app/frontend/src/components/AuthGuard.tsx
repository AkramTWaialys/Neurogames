"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { getMe, isLoggedIn } from "@/lib/authApi";

/**
 * AuthGuard — wraps protected pages.
 *
 * On mount, checks for a valid session token. If invalid or expired,
 * redirects to the login page. Public routes (/ and /register) are excluded.
 */

const PUBLIC_PATHS = ["/", "/register"];

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [checked, setChecked] = useState(false);
  const [authed, setAuthed] = useState(false);

  // Skip guard for public paths and dashboard paths (dashboard has its own auth)
  const isPublic =
    PUBLIC_PATHS.includes(pathname) ||
    pathname.startsWith("/dashboard") ||
    pathname.startsWith("/admin");

  useEffect(() => {
    if (isPublic) {
      queueMicrotask(() => {
        setChecked(true);
        setAuthed(true);
      });
      return;
    }

    // Quick client-side check first
    if (!isLoggedIn()) {
      router.replace("/");
      return;
    }

    // Validate with server
    getMe().then((profile) => {
      if (profile) {
        setAuthed(true);
      } else {
        // Token expired or invalid — redirect to login
        router.replace("/");
      }
      setChecked(true);
    });
  }, [pathname, isPublic, router]);

  // While checking, show nothing (prevents flash of protected content)
  if (!checked) return null;
  if (!authed) return null;

  return <>{children}</>;
}
