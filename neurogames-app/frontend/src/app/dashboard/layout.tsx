"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { validateAdminAccess } from "@/lib/adminAuth";
import { useI18n } from "@/i18n/I18nProvider";
import "./admin.css";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const pathname = usePathname();
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    let active = true;

    // Skip auth check on login page
    if (pathname === "/dashboard/login") {
      queueMicrotask(() => setChecked(true));
      return () => {
        active = false;
      };
    }

    queueMicrotask(() => {
      if (active) setChecked(false);
    });
    validateAdminAccess().then((allowed) => {
      if (!active) return;
      if (!allowed) {
        router.replace("/");
      } else {
        setChecked(true);
      }
    });

    return () => {
      active = false;
    };
  }, [pathname, router]);

  // Login page gets no shell
  if (pathname === "/dashboard/login") return <>{children}</>;

  // Wait for auth check
  if (!checked) return <div className="loading">{t("common.loading")}</div>;

  return <>{children}</>;
}
