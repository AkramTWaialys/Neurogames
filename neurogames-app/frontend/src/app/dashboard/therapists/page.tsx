"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { isSuperAdmin } from "@/lib/adminAuth";
import { authHeaders } from "@/lib/authApi";
import { CONFIG } from "@/lib/config";
import { useI18n } from "@/i18n/I18nProvider";
import AdminSidebar from "@/components/admin/AdminSidebar";
import { Users, ExternalLink, Calendar, Shield } from "lucide-react";
import "../admin.css";

const API_BASE = CONFIG.API_BASE_URL;

interface AdminProfile {
  id: number;
  username: string;
  role: string;
  school_id: number | null;
  school_name?: string;
  schools: { id: number; name: string }[];
  created_at: string;
  last_login_at?: string;
}

export default function AdminsListPage() {
  const { t } = useI18n();
  const router = useRouter();
  const [admins, setAdmins] = useState<AdminProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isSuperAdmin()) {
      router.replace("/dashboard/overview");
      return;
    }
    fetchAdmins();
  }, [router]);

  async function fetchAdmins() {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/auth/admins`, {
        headers: authHeaders(),
      });
      if (res.ok) {
        const d = await res.json();
        setAdmins(d.admins || []);
      } else {
        setError("Failed to fetch administrators");
      }
    } catch (e) {
      setError("Network error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="adminShell">
      <AdminSidebar />
      <main className="adminMain">
        <header className="adminPageHeader">
          <h1>Administrators</h1>
          <p>Manage access and monitor activity for school-level staff</p>
        </header>

        {error && <div className="loginError">{error}</div>}

        {loading ? (
          <div className="loading">{t("common.loading")}</div>
        ) : (
          <div className="chartCard" style={{ padding: 0, overflow: "hidden" }}>
            <table className="dataTable">
              <thead>
                <tr>
                  <th>Administrator</th>
                  <th>Role</th>
                  <th>Assigned School</th>
                  <th>Last Seen</th>
                </tr>
              </thead>
              <tbody>
                {admins.map((a) => (
                  <tr 
                    key={a.id} 
                    className="hoverRow"
                    onClick={() => router.push(`/dashboard/therapists/${a.id}`)}
                  >
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                        <div style={{ width: "32px", height: "32px", borderRadius: "50%", background: "var(--admin-border)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--admin-primary)" }}>
                          <Users size={16} />
                        </div>
                        <span style={{ fontWeight: 600 }}>{a.username}</span>
                      </div>
                    </td>
                    <td>
                      <span style={{ 
                        fontSize: "11px", 
                        fontWeight: 700, 
                        padding: "2px 6px", 
                        borderRadius: "4px", 
                        background: a.role === "super_admin" ? "rgba(234, 179, 8, 0.1)" : "rgba(148, 163, 184, 0.1)",
                        color: a.role === "super_admin" ? "#EAB308" : "var(--admin-text-muted)",
                        textTransform: "uppercase"
                      }}>
                        {a.role}
                      </span>
                    </td>
                    <td>
                      {a.schools && a.schools.length > 0 ? (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                          {a.schools.map(s => (
                            <span
                              key={s.id}
                              style={{
                                background: "rgba(124, 58, 237, 0.1)",
                                color: "var(--admin-primary)",
                                padding: "3px 8px",
                                borderRadius: "4px",
                                fontSize: "11px",
                                fontWeight: 700,
                              }}
                            >
                              {s.name}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span style={{ opacity: 0.5, fontStyle: "italic" }}>No institution</span>
                      )}
                    </td>
                    <td style={{ fontSize: "12px", color: "var(--admin-text-muted)" }}>
                      {a.last_login_at ? new Date(a.last_login_at).toLocaleString() : "Never"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}
