"use client";

/**
 * Contexto de sesión: expone el `MeOut` (usuario + tenant + flags del plan,
 * `GET /v1/me`) a toda la app y centraliza login/registro/logout para hosted.
 * En la app local no existe un login humano: el dueño vive en la base embebida
 * y este contexto obtiene JWT efímeros nuevos en cada apertura.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import * as api from "./api";
import { getAccessToken, hasSession, isDesktopApp } from "./tokens";
import type { MeOut } from "./types";

interface AuthContextValue {
  me: MeOut | null;
  loading: boolean;
  error: string | null;
  isAuthenticated: boolean;
  isLocalDesktop: boolean;
  refresh: () => Promise<void>;
  login: (email: string, password: string, totpCode?: string) => Promise<void>;
  register: (email: string, password: string, tenantName: string) => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<MeOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isLocalDesktop, setIsLocalDesktop] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const desktop = isDesktopApp();
      setIsLocalDesktop(desktop);
      // La app instalada representa a un único dueño. En cada proceso nuevo
      // obtiene una sesión fresca desde loopback; no pide ni conserva la
      // contraseña de una cuenta SaaS y no depende del Redis efímero de la
      // ejecución anterior.
      if (desktop && !getAccessToken()) {
        await api.openLocalDesktopSession();
      }
      if (!hasSession()) {
        setMe(null);
        setError(null);
        return;
      }
      const result = await api.getMe();
      setMe(result);
      setError(null);
    } catch (err) {
      setMe(null);
      setError(err instanceof Error ? err.message : "No se pudo cargar la sesión.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const login = useCallback(
    async (email: string, password: string, totpCode?: string) => {
      await api.login(email, password, totpCode);
      await refresh();
    },
    [refresh],
  );

  const register = useCallback(
    async (email: string, password: string, tenantName: string) => {
      await api.register(email, password, tenantName);
      await refresh();
    },
    [refresh],
  );

  const signOut = useCallback(() => {
    api.logout();
    setMe(null);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      me,
      loading,
      error,
      isAuthenticated: me !== null,
      isLocalDesktop,
      refresh,
      login,
      register,
      signOut,
    }),
    [me, loading, error, isLocalDesktop, refresh, login, register, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth debe usarse dentro de <AuthProvider>.");
  return ctx;
}
