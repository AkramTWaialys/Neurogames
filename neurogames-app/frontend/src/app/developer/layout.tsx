"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getMe, logout } from "@/lib/authApi";

export default function DeveloperLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    let active = true;
    getMe().then((profile) => {
      if (!active) return;
      if (!profile || profile.role !== "developer") {
        logout();
        router.replace("/");
        return;
      }
      setChecked(true);
    });
    return () => {
      active = false;
    };
  }, [router]);

  if (!checked) return <div className="loading">Loading...</div>;
  return <>{children}</>;
}
