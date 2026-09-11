"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import AdminSidebar from "@/components/admin/AdminSidebar";
import { AdminAPI, ParticipantRow } from "@/lib/adminApi";
import { isSuperAdmin } from "@/lib/adminAuth";
import { useI18n } from "@/i18n/I18nProvider";
import "../admin.css";

export default function ParticipantsPage() {
  const { t } = useI18n();
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<ParticipantRow[]>([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchData = useCallback(async (p: number, q: string) => {
    setLoading(true);
    setError("");
    try {
      const data = await AdminAPI.getParticipants(p, 20, q);
      if (data) {
        setRows(data.participants);
        setTotal(data.total);
        setTotalPages(data.total_pages);
      } else {
        setError(t("admin.loadError"));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("admin.loadError"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (isSuperAdmin()) {
      router.replace("/dashboard/schools");
      return;
    }
    queueMicrotask(() => fetchData(page, search));
  }, [page, search, fetchData, router]);

  // Debounced search
  const handleSearch = (val: string) => {
    setSearch(val);
    setPage(1);
  };

  return (
    <div className="adminShell">
      <AdminSidebar />
      <main className="adminMain">
        <div className="adminPageHeader">
          <h1>{t("common.participants")}</h1>
          <p>{t("admin.registeredParticipants", { count: total })}</p>
        </div>

        <input
          className="searchBar"
          type="text"
          placeholder={t("admin.searchParticipant")}
          value={search}
          onChange={(e) => handleSearch(e.target.value)}
        />

        {error && <div className="moduleAlert">{error}</div>}

        {loading ? (
          <div className="loading">{t("common.loading")}</div>
        ) : (
          <>
            <div className="chartCard" style={{ padding: 0, overflow: "hidden" }}>
              <table className="dataTable">
                <thead>
                  <tr>
                    <th>{t("common.participant")}</th>
                    <th>{t("common.sessions")}</th>
                    <th>{t("admin.lastActivity")}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr
                      key={r.participant_id}
                      onClick={() =>
                        router.push(`/dashboard/participants/${r.participant_id}`)
                      }
                    >
                      <td style={{ fontWeight: 700 }}>{r.participant_id}</td>
                      <td>{r.session_count}</td>
                      <td style={{ color: "#94A3B8" }}>
                        {r.last_active ? r.last_active.slice(0, 10) : "—"}
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={3} style={{ textAlign: "center", color: "#94A3B8" }}>
                        {t("admin.noParticipants")}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            <div className="pagination">
              <button
                className="pageBtn"
                disabled={page <= 1}
                onClick={() => setPage(page - 1)}
              >
                {t("admin.previous")}
              </button>
              <span className="pageInfo">
                {t("common.page", { page, total: totalPages })}
              </span>
              <button
                className="pageBtn"
                disabled={page >= totalPages}
                onClick={() => setPage(page + 1)}
              >
                {t("admin.next")}
              </button>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
