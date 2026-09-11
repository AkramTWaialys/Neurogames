"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { isSuperAdmin } from "@/lib/adminAuth";
import { authHeaders } from "@/lib/authApi";
import { getResponseErrorMessage } from "@/lib/apiErrors";
import { CONFIG } from "@/lib/config";
import { useI18n } from "@/i18n/I18nProvider";
import AdminSidebar from "@/components/admin/AdminSidebar";
import { Trash2, X, Lock } from "lucide-react";
import "../admin.css";

const API_BASE = CONFIG.API_BASE_URL;
const PROTECTED_SCHOOL_ID = 1; // "Others" institution

interface School {
  id: number;
  name: string;
}

interface AdminSchool {
  id: number;
  name: string;
}

interface AdminProfile {
  id: number;
  username: string;
  role: string;
  school_id: number | null;
  school_name?: string;
  schools: AdminSchool[];
}

export default function SchoolsPanel() {
  const { t } = useI18n();
  const router = useRouter();

  const [schools, setSchools] = useState<School[]>([]);
  const [admins, setAdmins] = useState<AdminProfile[]>([]);

  const [newSchoolName, setNewSchoolName] = useState("");
  const [newAdminUsername, setNewAdminUsername] = useState("");
  const [newAdminPassword, setNewAdminPassword] = useState("");
  const [selectedSchoolIds, setSelectedSchoolIds] = useState<number[]>([]);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isSuperAdmin()) {
      router.replace("/dashboard/overview");
      return;
    }
    fetchData();
  }, [router]);

  async function fetchData() {
    try {
      const hdrs = authHeaders();
      const [resS, resA] = await Promise.all([
        fetch(`${API_BASE}/api/auth/schools`, { headers: hdrs }),
        fetch(`${API_BASE}/api/auth/admins`, { headers: hdrs }),
      ]);
      if (resS.ok) {
        const d = await resS.json();
        setSchools(d.schools || []);
      }
      if (resA.ok) {
        const d = await resA.json();
        const filteredAdmins = (d.admins || []).filter((a: AdminProfile) => a.role !== "super_admin");
        setAdmins(filteredAdmins);
      }
    } catch (e) {
      console.error(e);
    }
  }

  async function handleCreateSchool() {
    if (!newSchoolName.trim()) return;
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/auth/schools`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ name: newSchoolName })
      });
      if (res.ok) {
        setNewSchoolName("");
        await fetchData();
      } else {
        setError(await getResponseErrorMessage(res, "Error creating institution"));
      }
    } catch {
      setError("Network error");
    }
    setLoading(false);
  }

  async function handleDeleteSchool(id: number) {
    if (id === PROTECTED_SCHOOL_ID) return;
    if (!confirm("Are you sure you want to delete this institution? Assigned administrators will lose access to it.")) return;
    try {
      const res = await fetch(`${API_BASE}/api/auth/schools/${id}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (res.ok) {
        await fetchData();
      } else {
        setError(await getResponseErrorMessage(res, "Failed to delete institution"));
      }
    } catch (e) {
      console.error(e);
    }
  }

  async function handleCreateAdmin() {
    if (!newAdminUsername.trim() || !newAdminPassword) return;
    if (selectedSchoolIds.length === 0) {
      setError("Please select at least one institution.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      // Create admin first with the primary school, then add remaining school assignments.
      const res = await fetch(`${API_BASE}/api/auth/admin-register`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          username: newAdminUsername,
          password: newAdminPassword,
          school_id: selectedSchoolIds[0] ?? null
        })
      });
      if (res.ok) {
        const created = await res.json();
        const adminId = created.user_id;
        // Assign the selected schools via the pivot endpoint
        await Promise.all(
          selectedSchoolIds.map(sid =>
            fetch(`${API_BASE}/api/auth/admins/${adminId}/schools/${sid}`, {
              method: "POST",
              headers: authHeaders(),
            })
          )
        );
        setNewAdminUsername("");
        setNewAdminPassword("");
        setSelectedSchoolIds([]);
        await fetchData();
      } else {
        setError(await getResponseErrorMessage(res, "Error creating administrator"));
      }
    } catch {
      setError("Network error");
    }
    setLoading(false);
  }

  async function handleDeleteAdmin(id: number) {
    if (!confirm("Are you sure you want to delete this administrator account?")) return;
    try {
      const res = await fetch(`${API_BASE}/api/auth/admins/${id}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (res.ok) {
        await fetchData();
      } else {
        setError(await getResponseErrorMessage(res, "Failed to delete administrator"));
      }
    } catch (e) {
      console.error(e);
    }
  }

  async function handleAssignSchool(adminId: number, schoolId: number) {
    try {
      await fetch(`${API_BASE}/api/auth/admins/${adminId}/schools/${schoolId}`, {
        method: "POST",
        headers: authHeaders(),
      });
      await fetchData();
    } catch (e) {
      console.error(e);
    }
  }

  async function handleUnassignSchool(adminId: number, schoolId: number) {
    try {
      await fetch(`${API_BASE}/api/auth/admins/${adminId}/schools/${schoolId}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      await fetchData();
    } catch (e) {
      console.error(e);
    }
  }

  function toggleSchoolSelection(schoolId: number) {
    setSelectedSchoolIds(prev =>
      prev.includes(schoolId) ? prev.filter(id => id !== schoolId) : [...prev, schoolId]
    );
  }

  if (loading && schools.length === 0) {
    return <div className="loading">{t("common.loading")}</div>;
  }

  // All schools (including "Others") are available for assignment
  const assignableSchools = schools;

  return (
    <div className="adminShell">
      <AdminSidebar />
      <main className="adminMain">
        <header className="adminPageHeader">
          <h1>Institutions & Administrators</h1>
          <p>Set up isolation boundaries and manage administrator access</p>
        </header>

        {error && (
          <div className="loginError" style={{ marginBottom: "20px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            {error}
            <X size={16} style={{ cursor: "pointer" }} onClick={() => setError("")} />
          </div>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.2fr)", gap: "32px" }}>

          {/* Institutions Column */}
          <div>
            <div className="chartCard">
              <h2 style={{ marginBottom: "20px" }}>Create New Institution</h2>
              <div style={{ display: "flex", gap: "12px" }}>
                <input
                  type="text"
                  placeholder="Institution Name"
                  value={newSchoolName}
                  onChange={(e) => setNewSchoolName(e.target.value)}
                  className="searchBar"
                  style={{ marginBottom: 0, flex: 1 }}
                />
                <button
                  className="loginBtn"
                  style={{ width: "auto", padding: "0 24px" }}
                  onClick={handleCreateSchool}
                  disabled={loading || !newSchoolName.trim()}
                >
                  Create
                </button>
              </div>
            </div>

            <div className="chartCard" style={{ marginTop: "24px", padding: 0, overflow: "hidden" }}>
              <div style={{ padding: "20px 20px 0" }}>
                <h2 style={{ margin: 0 }}>Existing Institutions</h2>
              </div>
              <table className="dataTable" style={{ marginTop: "12px" }}>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Name</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {schools.map(s => (
                    <tr key={s.id}>
                      <td style={{ color: "var(--admin-text-muted)", fontFamily: "monospace" }}>#{s.id}</td>
                      <td style={{ fontWeight: 600 }}>
                        {s.name}
                        {s.id === PROTECTED_SCHOOL_ID && (
                          <span style={{ marginLeft: "8px", fontSize: "10px", fontWeight: 700, padding: "2px 6px", borderRadius: "4px", background: "rgba(234, 179, 8, 0.1)", color: "#EAB308", textTransform: "uppercase" }}>
                            Protected
                          </span>
                        )}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        {s.id === PROTECTED_SCHOOL_ID ? (
                          <span title="This institution is protected">
                            <Lock size={14} style={{ color: "var(--admin-text-muted)", opacity: 0.5 }} />
                          </span>
                        ) : (
                          <button
                            onClick={() => handleDeleteSchool(s.id)}
                            style={{ background: "none", border: "none", color: "#EF4444", cursor: "pointer", padding: "4px", opacity: 0.7 }}
                            title="Delete Institution"
                          >
                            <Trash2 size={16} />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                  {schools.length === 0 && (
                    <tr>
                      <td colSpan={3} style={{ textAlign: "center", opacity: 0.5, padding: "32px" }}>No institutions found</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Administrators Column */}
          <div>
            <div className="chartCard">
              <h2 style={{ marginBottom: "20px" }}>Create New Administrator</h2>
              <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                  <div>
                    <label style={{ fontSize: "11px", color: "var(--admin-text-muted)", marginBottom: "4px", display: "block", textTransform: "uppercase", fontWeight: 700 }}>Username</label>
                    <input
                      type="text"
                      placeholder="teacher_john"
                      value={newAdminUsername}
                      onChange={(e) => setNewAdminUsername(e.target.value)}
                      className="searchBar"
                      style={{ marginBottom: 0 }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: "11px", color: "var(--admin-text-muted)", marginBottom: "4px", display: "block", textTransform: "uppercase", fontWeight: 700 }}>Password</label>
                    <input
                      type="password"
                      placeholder="••••••••"
                      value={newAdminPassword}
                      onChange={(e) => setNewAdminPassword(e.target.value)}
                      className="searchBar"
                      style={{ marginBottom: 0 }}
                    />
                  </div>
                </div>
                <div>
                  <label style={{ fontSize: "11px", color: "var(--admin-text-muted)", marginBottom: "8px", display: "block", textTransform: "uppercase", fontWeight: 700 }}>
                    Assign to Institutions (select multiple)
                  </label>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
                    {assignableSchools.map(s => (
                      <button
                        key={s.id}
                        onClick={() => toggleSchoolSelection(s.id)}
                        style={{
                          padding: "6px 12px",
                          borderRadius: "6px",
                          border: selectedSchoolIds.includes(s.id) ? "2px solid var(--admin-primary)" : "2px solid var(--admin-border)",
                          background: selectedSchoolIds.includes(s.id) ? "rgba(124, 58, 237, 0.15)" : "transparent",
                          color: selectedSchoolIds.includes(s.id) ? "var(--admin-primary)" : "var(--admin-text-muted)",
                          cursor: "pointer",
                          fontWeight: 600,
                          fontSize: "13px",
                          transition: "all 0.15s ease",
                        }}
                      >
                        {s.name}
                      </button>
                    ))}
                    {assignableSchools.length === 0 && (
                      <span style={{ color: "var(--admin-text-muted)", fontSize: "13px" }}>No institutions available — create one first</span>
                    )}
                  </div>
                </div>
                <button
                  className="loginBtn"
                  onClick={handleCreateAdmin}
                  disabled={loading || !newAdminUsername.trim() || !newAdminPassword || selectedSchoolIds.length === 0}
                  style={{ marginTop: "8px" }}
                >
                  Create Administrator
                </button>
              </div>
            </div>

            <div className="chartCard" style={{ marginTop: "24px", padding: 0, overflow: "hidden" }}>
              <div style={{ padding: "20px 20px 0" }}>
                <h2 style={{ margin: 0 }}>Existing Administrators</h2>
              </div>
              <table className="dataTable" style={{ marginTop: "12px" }}>
                <thead>
                  <tr>
                    <th>Username</th>
                    <th>Assigned Institutions</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {admins.map(a => (
                    <tr key={a.id}>
                      <td style={{ fontWeight: 600 }}>{a.username}</td>
                      <td>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px", maxWidth: "240px" }}>
                          {a.schools && a.schools.length > 0 ? (
                            a.schools.map(s => (
                              <span
                                key={s.id}
                                style={{
                                  background: "rgba(124, 58, 237, 0.1)",
                                  color: "var(--admin-primary)",
                                  padding: "3px 8px",
                                  borderRadius: "4px",
                                  fontSize: "11px",
                                  fontWeight: 700,
                                  display: "flex",
                                  alignItems: "center",
                                  gap: "4px",
                                }}
                              >
                                {s.name}
                                <span 
                                  style={{ cursor: "pointer", opacity: 0.7, display: "flex", alignItems: "center" }}
                                  onClick={() => handleUnassignSchool(a.id, s.id)}
                                  title={`Remove from ${s.name}`}
                                >
                                  <X size={10} />
                                </span>
                              </span>
                            ))
                          ) : (
                            <span style={{ opacity: 0.5, fontStyle: "italic", fontSize: "12px" }}>No institution assigned</span>
                          )}
                          {/* Add institution dropdown */}
                          <select
                            defaultValue=""
                            onChange={(e) => {
                              if (e.target.value) {
                                handleAssignSchool(a.id, Number(e.target.value));
                                e.target.value = "";
                              }
                            }}
                            className="adminSelect"
                            style={{ fontSize: "11px", padding: "2px 6px", margin: 0, height: "auto", border: "1px dashed var(--admin-border)", background: "transparent" }}
                          >
                            <option value="">+ Add</option>
                            {assignableSchools
                              .filter(s => !a.schools?.some(as => as.id === s.id))
                              .map(s => <option key={s.id} value={s.id}>{s.name}</option>)
                            }
                          </select>
                        </div>
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <button
                          onClick={() => handleDeleteAdmin(a.id)}
                          style={{ background: "none", border: "none", color: "#EF4444", cursor: "pointer", opacity: 0.7 }}
                          title="Delete Administrator"
                        >
                          <Trash2 size={16} />
                        </button>
                      </td>
                    </tr>
                  ))}
                  {admins.length === 0 && (
                    <tr>
                      <td colSpan={3} style={{ textAlign: "center", opacity: 0.5, padding: "32px" }}>No administrators found</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

      </main>
    </div>
  );
}
