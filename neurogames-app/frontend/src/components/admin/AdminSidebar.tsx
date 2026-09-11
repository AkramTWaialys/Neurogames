"use client";

import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { adminLogout, isSuperAdmin } from "@/lib/adminAuth";
import { BarChart3, Users, Brain, LogOut, Building2, ClipboardCheck } from "lucide-react";
import { useI18n } from "@/i18n/I18nProvider";
import { useEffect, useState } from "react";

export default function AdminSidebar() {
  const { t } = useI18n();
  const pathname = usePathname();
  const router = useRouter();
  const [isAdminSuper, setIsAdminSuper] = useState(false);

  useEffect(() => {
    queueMicrotask(() => setIsAdminSuper(isSuperAdmin()));
  }, []);

  const navGroups = isAdminSuper
    ? [
        {
          label: "Administration",
          items: [
            { href: "/dashboard/schools", icon: Building2, labelKey: "superadmin.schools", fallbackLabel: "Institutions" },
            { href: "/dashboard/therapists", icon: Users, labelKey: "superadmin.therapists", fallbackLabel: "Therapists" },
            { href: "/dashboard/game-requests", icon: ClipboardCheck, labelKey: "superadmin.gameRequests", fallbackLabel: "Game Requests" },
          ],
        },
      ]
    : [
        {
          label: "ADHD reference app",
          items: [
            { href: "/dashboard/overview", icon: BarChart3, labelKey: "admin.dashboard" },
            { href: "/dashboard/participants", icon: Users, labelKey: "common.participants" },
            { href: "/dashboard/analysis", icon: Brain, labelKey: "admin.analysis", fallbackLabel: "Analysis" },
          ],
        },
      ];

  const handleLogout = () => {
    // Import general logout to clear all tokens
    import("@/lib/authApi").then(({ logout }) => {
      logout();
      adminLogout();
      router.push("/");
    });
  };

  return (
    <aside className="adminSidebar">
      {/* Brand */}
      <Link href={isAdminSuper ? "/dashboard/schools" : "/dashboard/overview"} className="adminBrand">
        <span className="adminBrandIcon"><Brain size={24} /></span>
        <div className="adminBrandText">
          <h2>{t("common.appName")}</h2>
          <span>{isAdminSuper ? t("admin.administration") : "Therapist Dashboard"}</span>
        </div>
      </Link>

      {/* Navigation */}
      <nav className="adminNav">
        {navGroups.map((group) => (
          <div className="adminNavGroup" key={group.label}>
            <div className="adminNavSectionLabel">{group.label}</div>
            {group.items.map((item) => {
              const active =
                pathname === item.href || pathname.startsWith(item.href + "/");
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`adminNavItem ${active ? "adminNavActive" : ""}`}
                >
                  <span className="adminNavIcon"><Icon size={18} /></span>
                  {"fallbackLabel" in item && !t(item.labelKey) ? item.fallbackLabel : t(item.labelKey)}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="adminSpacer" />

      {/* Logout */}
      <button className="adminLogout" onClick={handleLogout}>
        <LogOut size={16} /> {t("actions.logout")}
      </button>
    </aside>
  );
}
