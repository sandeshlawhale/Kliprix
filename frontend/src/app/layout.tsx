import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Kliprix",
  description: "Turn long videos into vertical shorts, locally.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
