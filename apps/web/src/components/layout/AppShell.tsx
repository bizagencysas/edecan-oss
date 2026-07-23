"use client";

import { useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";

import { LogOutIcon, MenuIcon, XIcon } from "@/components/icons";
import { DesktopUpdateNotifier } from "@/components/DesktopUpdateNotifier";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useAuth } from "@/lib/auth-context";

import { BrandMark, NavList, Sidebar } from "./Sidebar";

export function AppShell({ children }: { children: ReactNode }) {
  const { me, signOut, isLocalDesktop } = useAuth();
  const pathname = usePathname();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const isChatRoute = pathname === "/app" || pathname === "/app/";
  const isWorkspaceRoute = pathname === "/app/studio" || Boolean(pathname?.startsWith("/app/studio/"));
  const isFullHeightRoute = isChatRoute || isWorkspaceRoute;

  return (
    <div className="flex h-dvh min-h-0 min-w-0 max-w-full overflow-hidden bg-slate-50 dark:bg-slate-950">
      <DesktopUpdateNotifier />
      <Sidebar />

      {drawerOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button
            aria-label="Cerrar menú"
            className="absolute inset-0 bg-slate-900/50"
            onClick={() => setDrawerOpen(false)}
          />
          <div className="relative flex h-full min-h-0 w-64 flex-col overflow-hidden bg-white shadow-xl dark:bg-slate-900">
            <div className="flex items-center justify-between pr-2">
              <BrandMark />
              <button
                aria-label="Cerrar menú"
                className="rounded-md p-2 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                onClick={() => setDrawerOpen(false)}
              >
                <XIcon className="h-5 w-5" />
              </button>
            </div>
            <NavList onNavigate={() => setDrawerOpen(false)} />
          </div>
        </div>
      )}

      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4 dark:border-slate-800 dark:bg-slate-900 md:px-6">
          <button
            aria-label="Abrir menú"
            className="rounded-md p-2 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 md:hidden"
            onClick={() => setDrawerOpen(true)}
          >
            <MenuIcon className="h-5 w-5" />
          </button>
          <div className="hidden text-sm text-slate-500 dark:text-slate-400 md:block">Tu asistente</div>
          <div className="flex items-center gap-3">
            <span className="hidden text-sm text-slate-600 dark:text-slate-300 sm:inline">
              {isLocalDesktop ? "Tu Edecán" : me?.user.email}
            </span>
            <ThemeToggle />
            {!isLocalDesktop && (
              <button
                onClick={signOut}
                title="Cerrar sesión"
                className="rounded-md p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-800 dark:hover:text-white"
              >
                <LogOutIcon className="h-4 w-4" />
              </button>
            )}
          </div>
        </header>
        <main
          className={
            isFullHeightRoute
              ? `flex min-h-0 min-w-0 max-w-full flex-1 flex-col overflow-hidden ${isWorkspaceRoute ? "p-2" : "p-2 sm:p-3 lg:p-4"}`
              : "flex min-h-0 min-w-0 max-w-full flex-1 flex-col overflow-x-hidden overflow-y-auto p-4 md:p-6"
          }
        >
          <div className={isFullHeightRoute ? "h-full min-h-0 min-w-0 max-w-full" : "min-w-0 max-w-full"}>
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
