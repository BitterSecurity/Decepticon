import type { Metadata } from "next";
import { connection } from "next/server";
import { browserConfig } from "@/lib/browser-config";
import { BrowserConfigProvider } from "@/lib/browser-config-context";
import { Geist, Geist_Mono } from "next/font/google";
import { TooltipProvider } from "@/components/ui/tooltip";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Decepticon — Autonomous Red Team Platform",
  description:
    "AI-powered autonomous red team testing framework with real-time attack monitoring and vulnerability management.",
  icons: {
    icon: "/logo.png",
  },
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>): Promise<React.ReactNode> {
  await connection();
  const config = browserConfig(process.env);

  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} dark h-full antialiased`}
    >
      <body className="min-h-full bg-background text-foreground">
        <BrowserConfigProvider config={config}>
          <TooltipProvider>{children}</TooltipProvider>
        </BrowserConfigProvider>
      </body>
    </html>
  );
}
