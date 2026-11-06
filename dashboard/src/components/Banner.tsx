import type { ReactNode } from "react";

export type BannerTone = "info" | "warn" | "error";

/** A message strip. Errors are announced at once (alert); info and warn politely (status). */
export default function Banner({ tone, children }: { tone: BannerTone; children: ReactNode }) {
  return (
    <div className={`banner banner--${tone}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}
