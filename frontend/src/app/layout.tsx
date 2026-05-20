import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "InvoiceFlow — AI Invoice Platform",
  description: "AI-powered billing and invoice management platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
