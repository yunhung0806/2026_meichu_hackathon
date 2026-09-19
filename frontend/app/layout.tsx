import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
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
  metadataBase: new URL("https://fridge-guardian-meichu.fun-crow-7756.chatgpt.site"),
  title: "冰友 ChillMate｜共享冰箱管家",
  description: "以本機 AI 管理共享冰箱、保存期限與物品紀錄。",
  openGraph: {
    title: "冰友 ChillMate｜共享冰箱管家",
    description: "讓本機 AI 幫你管理共享冰箱、食物期限與取放紀錄。",
    images: ["/og.png"],
  },
  twitter: {
    card: "summary_large_image",
    title: "冰友 ChillMate｜共享冰箱管家",
    description: "讓本機 AI 幫你管理共享冰箱、食物期限與取放紀錄。",
    images: ["/og.png"],
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-Hant">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
