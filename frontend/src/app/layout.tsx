import type { Metadata } from "next";

import "./globals.css";
import { Providers } from "@/app/providers";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { StorageBanner } from "@/components/layout/storage-banner";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";

export const metadata: Metadata = {
  title: "Kliprix",
  description: "Turn long videos into vertical shorts, locally.",
};

// Fixed 260 px sidebar, always visible (docs/03 "Global layout").
const SIDEBAR_STYLE = { "--sidebar-width": "260px" } as React.CSSProperties;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="font-sans antialiased">
        <Providers>
          <SidebarProvider style={SIDEBAR_STYLE}>
            <AppSidebar />
            <SidebarInset>
              <main className="mx-auto w-full max-w-[1100px] px-8 py-10">
                <StorageBanner />
                {children}
              </main>
            </SidebarInset>
          </SidebarProvider>
        </Providers>
      </body>
    </html>
  );
}
