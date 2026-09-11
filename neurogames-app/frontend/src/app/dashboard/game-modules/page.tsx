"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { isSuperAdmin } from "@/lib/adminAuth";

export default function OldGameModulesPage() {
  const router = useRouter();

  useEffect(() => {
    queueMicrotask(() => {
      router.replace(isSuperAdmin() ? "/dashboard/game-requests" : "/dashboard/overview");
    });
  }, [router]);

  return <div className="loading">Redirecting...</div>;
}
