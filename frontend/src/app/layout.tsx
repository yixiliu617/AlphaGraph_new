import type { Metadata } from "next";
import "./globals.css";
import TopNav from "@/components/layout/TopNav";
import ContextSidebar from "@/components/layout/ContextSidebar";

export const metadata: Metadata = {
  title: "AlphaGraph | Institutional Financial Research",
  description: "AI-driven Financial Research Platform for long/short portfolio managers",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="font-sans antialiased text-slate-900 bg-slate-50 h-screen flex flex-col overflow-hidden">
        {/* Top navigation bar */}
        <TopNav />

        {/* Below the nav: context sidebar + page content side by side */}
        <div className="flex flex-1 overflow-hidden">
          <ContextSidebar />

          {/* Each page is responsible for its own padding, scroll, and height */}
          <main className="flex-1 overflow-hidden bg-slate-50">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
