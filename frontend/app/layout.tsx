import "./globals.css";
import type { ReactNode } from "react";
import WebMcp from "./WebMcp";

export const metadata = { title: "PlanLab", description: "AI-run pricing experiments for PayPal merchants" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}<WebMcp /></body>
    </html>
  );
}
