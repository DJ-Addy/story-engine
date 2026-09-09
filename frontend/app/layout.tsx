import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Suspense } from "react";
import Tour from "@/components/tour/Tour";
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
  title: "Story Engine",
  description: "Screenplay-to-audiobook and previz production tool",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        {children}
        {/* The guided tour crosses pages, including ones that mount no nav bar
            (the dashboard), so it lives here rather than in AppNav. It reads
            the URL, hence its own Suspense boundary. */}
        <Suspense fallback={null}>
          <Tour />
        </Suspense>
      </body>
    </html>
  );
}
