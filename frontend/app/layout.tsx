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
        <a href="#main-content" className="skip-link">
          Skip to main content
        </a>
        <Nav />
        <main className="page" id="main-content">
          {children}
        </main>
      </body>
    </html>
  );
}
