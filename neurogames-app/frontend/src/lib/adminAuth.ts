/**
 * Dashboard authentication helpers.
 * Simple password-based auth using localStorage.
 */

const ADMIN_KEY = "neuro_admin";

function isDashboardRole(role?: string | null): boolean {
  return role === "therapist" || role === "admin" || role === "super_admin";
}

export function clearAdminSession(): void {
  localStorage.removeItem(ADMIN_KEY);
}

export interface AdminLoginResult {
  authenticated: boolean;
  error?: string;
}

export async function adminLogin(password: string): Promise<AdminLoginResult> {
  const { login } = await import("./authApi");
  // Password-only fallback for the legacy dashboard login screen.
  let res = await login("therapist", password);
  let authed = false;
  if (res.ok && isDashboardRole(res.profile?.role)) {
    authed = true;
  } else {
    res = await login("admin", password);
    if (res.ok && isDashboardRole(res.profile?.role)) {
      authed = true;
    }
  }

  if (!authed) {
    // try superadmin
    res = await login("superadmin", password);
    if (res.ok && isDashboardRole(res.profile?.role)) {
      authed = true;
    }
  }

  if (authed) {
    localStorage.setItem(ADMIN_KEY, JSON.stringify({ role: res.profile?.role, ts: Date.now() }));
    return { authenticated: true };
  }
  return { authenticated: false, error: "Mot de passe incorrect" };
}

export function hasAdminAccess(): boolean {
  try {
    const token = localStorage.getItem("neuro_token");
    if (!token) {
      clearAdminSession();
      return false;
    }
    const raw = localStorage.getItem(ADMIN_KEY);
    if (!raw) return false;
    const data = JSON.parse(raw);
    return isDashboardRole(data?.role);
  } catch {
    return false;
  }
}

export function isSuperAdmin(): boolean {
  try {
    if (!localStorage.getItem("neuro_token")) return false;
    const raw = localStorage.getItem(ADMIN_KEY);
    if (!raw) return false;
    const data = JSON.parse(raw);
    return data?.role === "super_admin";
  } catch {
    return false;
  }
}

export async function validateAdminAccess(): Promise<boolean> {
  if (!hasAdminAccess()) return false;
  try {
    const { getMe, logout } = await import("./authApi");
    const profile = await getMe();
    if (profile && isDashboardRole(profile.role)) {
      localStorage.setItem(ADMIN_KEY, JSON.stringify({ role: profile.role, ts: Date.now() }));
      return true;
    }
    logout();
    clearAdminSession();
    return false;
  } catch {
    clearAdminSession();
    return false;
  }
}

export async function adminLogout(): Promise<void> {
  try {
    const { logout } = await import("./authApi");
    logout();
  } catch { /* ignore */ }
  clearAdminSession();
}
