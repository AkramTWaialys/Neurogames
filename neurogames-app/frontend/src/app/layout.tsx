import type { Metadata } from "next";
import { Suspense } from "react";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import AuthGuard from "@/components/AuthGuard";
import { I18nProvider } from "@/i18n/I18nProvider";
import FloatingTools from "@/components/FloatingTools";

export const metadata: Metadata = {
  title: "NeuroGames — Plateforme ADHD",
  description:
    "Plateforme de jeux cognitifs pour enfants avec TDAH — NeuroGames par ADN-Expertise",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="fr" dir="ltr" suppressHydrationWarning>
      <body>
        <I18nProvider>
          <Suspense fallback={null}>
            <Sidebar />
          </Suspense>
          <AuthGuard>
            <main>{children}</main>
          </AuthGuard>
          <FloatingTools />
        </I18nProvider>
      </body>
    </html>
  );
}

