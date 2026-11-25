import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Admin spike (D01)",
  description: "Next.js + Appwrite spike for ADR 0004",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="env">Spike · not production</div>
        {children}
      </body>
    </html>
  );
}
