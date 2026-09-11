"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { isSuperAdmin } from "@/lib/adminAuth";
import { authHeaders } from "@/lib/authApi";
import { CONFIG } from "@/lib/config";
import { useI18n } from "@/i18n/I18nProvider";
import AdminSidebar from "@/components/admin/AdminSidebar";
import { 
  Building2, Users, Calendar, Shield, 
  ArrowLeft, Mail, Clock, MapPin, Activity 
} from "lucide-react";
import "../../admin.css";

const API_BASE = CONFIG.API_BASE_URL;

interface AdminDetail {
  id: number;
  username: string;
  role: string;
  school_id: number | null;
  school_name?: string;
  schools: { id: number; name: string; student_count: number }[];
  created_at: string;
  last_login_at?: string;
  student_count: number;
}

export default function AdminDetailPage() {
  const { id } = useParams();
  const { t } = useI18n();
  const router = useRouter();
  const [admin, setAdmin] = useState<AdminDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isSuperAdmin()) {
      router.replace("/dashboard/overview");
      return;
    }
    fetchDetail();
  }, [id, router]);

  async function fetchDetail() {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/auth/admins/${id}`, {
        headers: authHeaders(),
      });
      if (res.ok) {
        const d = await res.json();
        setAdmin(d);
      } else {
        setError("Admin not found or access denied");
      }
    } catch (e) {
      setError("Network error");
    } finally {
      setLoading(false);
    }
  }

  if (loading) return (
    <div className="adminShell">
      <AdminSidebar />
      <main className="adminMain"><div className="loading">{t("common.loading")}</div></main>
    </div>
  );

  if (error || !admin) return (
    <div className="adminShell">
      <AdminSidebar />
      <main className="adminMain"><div className="loginError">{error || "Admin not found"}</div></main>
    </div>
  );

  return (
    <div className="adminShell">
      <AdminSidebar />
      <main className="adminMain">
        <button 
          onClick={() => router.back()} 
          style={{ background: "none", border: "none", color: "var(--admin-text-muted)", display: "flex", alignItems: "center", gap: "8px", cursor: "pointer", marginBottom: "24px", fontWeight: 600 }}
        >
          <ArrowLeft size={16} /> Back to Administrators
        </button>

        <div className="adminProfileHeader">
          <div className="adminAvatarLarge">
            <Users size={48} />
          </div>
          <div className="adminHeaderInfo">
            <h1>{admin.username}</h1>
            <div className="adminBadgeRow">
              <span className={`roleBadge ${admin.role}`}>
                <Shield size={12} /> {admin.role}
              </span>
              {admin.school_name && (
                <span className="schoolBadge">
                  <Building2 size={12} /> {admin.school_name}
                </span>
              )}
            </div>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1.5fr", gap: "24px", marginTop: "32px" }}>
          
          <div className="chartCard">
            <h2>Administrator Details</h2>
            <div className="adminDetailList">
              <div className="detailItem">
                <Clock size={16} />
                <div className="detailContent">
                  <label>Joined On</label>
                  <span>{new Date(admin.created_at).toLocaleDateString(undefined, { dateStyle: "long" })}</span>
                </div>
              </div>
              <div className="detailItem">
                <Activity size={16} />
                <div className="detailContent">
                  <label>Last Activity</label>
                  <span>{admin.last_login_at ? new Date(admin.last_login_at).toLocaleString() : "No login history"}</span>
                </div>
              </div>
              <div className="detailItem">
                <Mail size={16} />
                <div className="detailContent">
                  <label>System Identifier</label>
                  <span>#{admin.id}</span>
                </div>
              </div>
            </div>
          </div>

          <div className="chartCard">
            <h2>Assigned Institutions</h2>
            {admin.schools && admin.schools.length > 0 ? (
              <div className="adminDetailList" style={{ marginTop: "16px" }}>
                {admin.schools.map(s => (
                  <div key={s.id} className="kpiCard" style={{ background: "var(--admin-border)", border: "none", padding: "16px", borderRadius: "8px", display: "flex", alignItems: "center", gap: "16px" }}>
                    <span className="kpiIcon"><Building2 size={20} /></span>
                    <div style={{ flex: 1 }}>
                      <h3 style={{ margin: 0, fontSize: "14px", fontWeight: 700 }}>{s.name}</h3>
                      <p style={{ margin: "4px 0 0", fontSize: "12px", color: "var(--admin-text-muted)" }}>
                        <Users size={12} style={{ display: "inline", marginRight: "4px" }} />
                        {s.student_count} student{s.student_count !== 1 ? "s" : ""}
                      </p>
                    </div>
                  </div>
                ))}
                <div style={{ marginTop: "12px", padding: "12px", background: "rgba(124, 58, 237, 0.05)", borderRadius: "8px", textAlign: "center" }}>
                  <span style={{ fontWeight: 700, color: "var(--admin-primary)" }}>{admin.student_count}</span>
                  <span style={{ color: "var(--admin-text-muted)", fontSize: "13px" }}> total students across all institutions</span>
                </div>
              </div>
            ) : (
              <p style={{ color: "var(--admin-text-muted)", fontStyle: "italic", marginTop: "16px" }}>No institutions assigned</p>
            )}
          </div>
        </div>
      </main>

      <style jsx>{`
        .adminProfileHeader {
          display: flex;
          align-items: center;
          gap: 24px;
        }
        .adminAvatarLarge {
          width: 96px;
          height: 96px;
          border-radius: 50%;
          background: var(--admin-border);
          display: flex;
          align-items: center;
          justify-content: center;
          color: var(--admin-primary);
        }
        .adminHeaderInfo h1 {
          margin: 0;
          font-size: 2.2rem;
          color: #fff;
        }
        .adminBadgeRow {
          display: flex;
          gap: 12px;
          margin-top: 8px;
        }
        .roleBadge, .schoolBadge {
          display: flex;
          align-items: center;
          gap: 6px;
          padding: 4px 10px;
          border-radius: 6px;
          font-size: 12px;
          font-weight: 700;
          text-transform: uppercase;
        }
        .roleBadge.super_admin {
          background: rgba(234, 179, 8, 0.1);
          color: #EAB308;
        }
        .roleBadge.admin {
          background: rgba(148, 163, 184, 0.1);
          color: var(--admin-text-muted);
        }
        .schoolBadge {
          background: rgba(124, 58, 237, 0.1);
          color: var(--admin-primary);
        }
        .adminDetailList {
          display: flex;
          flex-direction: column;
          gap: 20px;
          margin-top: 16px;
        }
        .detailItem {
          display: flex;
          align-items: flex-start;
          gap: 14px;
          color: var(--admin-text-muted);
        }
        .detailContent label {
          display: block;
          font-size: 11px;
          font-weight: 700;
          text-transform: uppercase;
          margin-bottom: 2px;
        }
        .detailContent span {
          color: #E2E8F0;
          font-weight: 500;
        }
      `}</style>
    </div>
  );
}
