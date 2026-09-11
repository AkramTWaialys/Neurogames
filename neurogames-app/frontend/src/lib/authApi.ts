/**
 * Auth API — registration, login, session management.
 *
 * Tokens are stored in localStorage under `neuro_token`.
 * Player profiles are synced to `neuro_player` for game engines.
 */

import { CONFIG } from "./config";
import { getResponseErrorMessage } from "./apiErrors";
const API_BASE = CONFIG.API_BASE_URL;
const TOKEN_KEY = "neuro_token";
const PLAYER_KEY = "neuro_player";

// ── Types ────────────────────────────────────────────────────────────────────

export interface UserProfile {
  user_id: number;
  username: string;
  role: string;
  email?: string | null;
  phone?: string | null;
  display_name: string | null;
  avatar: string;
  age: number | null;
  age_group: string | null;
  cognitive_level: string;
  cluster: string;
  locale_pref: string;
  custom_school_name: string | null;
  conners_score: number | null;
}

export interface RegisterPayload {
  username: string;
  password: string;
  role?: string;
  email?: string;
  phone?: string;
  display_name?: string;
  avatar?: string;
  school_id?: number;
  age?: number;
  age_group?: string;
  cognitive_level?: string;
  cluster?: string;
  locale_pref?: string;
  custom_school_name?: string;
  conners_score?: number;
  conners_data?: string;
}

export interface LoginResult {
  ok: boolean;
  profile?: UserProfile;
  error?: string;
}

export interface RegisterResult {
  ok: boolean;
  user_id?: number;
  error?: string;
}

export interface DeveloperRegisterPayload {
  username: string;
  password: string;
  email: string;
  phone?: string;
}

// ── Token helpers ────────────────────────────────────────────────────────────

function storeToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function isLoggedIn(): boolean {
  return !!getToken();
}

export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function storePlayer(profile: UserProfile): void {
  // Sync with existing neuro_player format used by Sidebar, Profile, etc.
  const player = {
    id: profile.username,
    age: profile.age || 9,
    ageGroup: profile.age_group || "9-11",
    cognitiveLevel: profile.cognitive_level || "Medium",
    avatar: { emoji: profile.avatar || "🦁", name: "avatar.lion" },
  };
  localStorage.setItem(PLAYER_KEY, JSON.stringify(player));
}

// ── API calls ────────────────────────────────────────────────────────────────

export async function register(payload: RegisterPayload): Promise<RegisterResult> {
  try {
    const resp = await fetch(`${API_BASE}/api/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      return { ok: false, error: await getResponseErrorMessage(resp) };
    }
    const data = await resp.json();
    return { ok: true, user_id: data.user_id };
  } catch {
    return { ok: false, error: "Server unavailable" };
  }
}

export async function registerDeveloper(payload: DeveloperRegisterPayload): Promise<RegisterResult> {
  try {
    const resp = await fetch(`${API_BASE}/api/auth/developer-register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      return { ok: false, error: await getResponseErrorMessage(resp) };
    }
    const data = await resp.json();
    return { ok: true, user_id: data.user_id };
  } catch {
    return { ok: false, error: "Server unavailable" };
  }
}

export interface School {
  id: number;
  name: string;
}

export async function getSchools(): Promise<School[]> {
  try {
    const resp = await fetch(`${API_BASE}/api/auth/schools`);
    if (!resp.ok) return [];
    const data = await resp.json();
    return data.schools || [];
  } catch {
    return [];
  }
}

export async function login(
  username: string,
  password: string,
  rememberMe: boolean = false,
): Promise<LoginResult> {
  try {
    const resp = await fetch(`${API_BASE}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, remember_me: rememberMe }),
    });
    if (!resp.ok) {
      return { ok: false, error: await getResponseErrorMessage(resp) };
    }
    const data = await resp.json();
    storeToken(data.token);
    storePlayer(data.profile);
    return { ok: true, profile: data.profile };
  } catch {
    return { ok: false, error: "Server unavailable" };
  }
}

export async function getMe(): Promise<UserProfile | null> {
  const token = getToken();
  if (!token) return null;
  try {
    const resp = await fetch(`${API_BASE}/api/auth/me`, {
      headers: authHeaders(),
      signal: AbortSignal.timeout(5000),
    });
    if (!resp.ok) return null;
    const profile = (await resp.json()) as UserProfile;
    storePlayer(profile);
    return profile;
  } catch {
    return null;
  }
}

export function logout(): void {
  const token = getToken();
  if (token) {
    fetch(`${API_BASE}/api/auth/logout`, {
      method: "POST",
      headers: authHeaders(),
    }).catch(() => {});
  }
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(PLAYER_KEY);
}
