import type { Metadata } from "next";
import Nav from "../components/nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "CustomerIQ",
  description: "Customer churn prediction & segmentation for e-commerce.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <Nav />
        <main className="page">{children}</main>
      </body>
    </html>
  );
}
