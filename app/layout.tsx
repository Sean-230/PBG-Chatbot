import type { Metadata } from "next";
import "./globals.css";
import Providers from "./providers";

export const metadata: Metadata = {
  title: "PBG Assist — Customer Support",
  description:
    "Asisten cerdas berbasis AI untuk layanan Persetujuan Bangunan Gedung (PBG). Dapatkan informasi cepat, akurat, dan terpercaya mengenai proses pengajuan izin bangunan.",
  keywords: ["PBG", "Persetujuan Bangunan Gedung", "SIMBG", "izin bangunan", "chatbot AI"],
  openGraph: {
    title: "PBG Assist — Customer Support",
    description: "AI-powered assistant for government building permit (PBG) inquiries.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="id" className="h-full" suppressHydrationWarning>
      <body className="h-full antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
