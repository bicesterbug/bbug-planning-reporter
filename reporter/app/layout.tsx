import type { ReactNode } from "react";

export const metadata = {
  title: "BBUG Planning Reporter",
  description: "Cherwell planning transport assessment on Claude Managed Agents",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en-GB">
      <body>{children}</body>
    </html>
  );
}
