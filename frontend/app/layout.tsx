import "./globals.css";
import type { ReactNode } from "react";
import { Gochi_Hand, Inter, Pixelify_Sans, Space_Mono } from "next/font/google";
import WebMcp from "./WebMcp";

const sans = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = Space_Mono({ subsets: ["latin"], weight: ["400", "700"], variable: "--font-mono", display: "swap" });
const pixel = Pixelify_Sans({ subsets: ["latin"], weight: ["700"], variable: "--font-pixel", display: "swap" });
const hand = Gochi_Hand({ subsets: ["latin"], weight: "400", variable: "--font-hand", display: "swap" });

export const metadata = { title: "PlanLab", description: "AI-run pricing experiments and a Save Agent for PayPal merchants" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} ${pixel.variable} ${hand.variable}`}>
      <body>{children}<WebMcp /></body>
    </html>
  );
}
