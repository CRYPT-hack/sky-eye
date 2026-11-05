import { useEffect, type ReactNode } from "react";
import { href, useRoute, type Route } from "./router";

type Section = "live" | "trucks" | "status";

function NavIcon({ children }: { children: ReactNode }) {
  return (
    <svg
      className="nav-icon"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  );
}

const SECTIONS: { section: Section; label: string; icon: ReactNode }[] = [
  {
    section: "live",
    label: "Live",
    icon: (
      <NavIcon>
        <circle cx="12" cy="12" r="8" />
        <circle cx="12" cy="12" r="3" fill="currentColor" stroke="none" />
      </NavIcon>
    ),
  },
  {
    section: "trucks",
    label: "Trucks",
    icon: (
      <NavIcon>
        <path d="M2.5 6.5h11v9h-11z" />
        <path d="M13.5 9.5h4l3 3.2v2.8h-7" />
        <circle cx="7" cy="17.6" r="1.8" />
        <circle cx="17" cy="17.6" r="1.8" />
      </NavIcon>
    ),
  },
  {
    section: "status",
    label: "Status",
    icon: (
      <NavIcon>
        <path d="M4 16.5a8 8 0 1 1 16 0" />
        <path d="M12 16.5l3.6-4.6" />
        <path d="M4 20h16" />
      </NavIcon>
    ),
  },
];

/** Which nav entry a route belongs to: a truck's detail page sits under Trucks. */
function sectionOf(route: Route): Section {
  return route.page === "truck" ? "trucks" : route.page;
}

function pageTitle(route: Route): string {
  switch (route.page) {
    case "live":
      return "Live";
    case "trucks":
      return "Trucks";
    case "truck":
      return `Truck #${route.id}`;
    case "status":
      return "Status";
  }
}

// Stand-ins until the real pages land (Live: Task 5, Trucks: Task 6, Status: Task 7).
const PLACEHOLDER: Record<Route["page"], string> = {
  live: "The live drone video, truck counts and the latest trucks will show here.",
  trucks: "Every saved truck will show here, with a date filter.",
  truck: "The photos, the clip and the details of this truck will show here.",
  status: "Engine and detector details and the detection settings will show here.",
};

function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <circle cx="16" cy="16" r="8.5" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <path
        d="M16 3v6M16 23v6M3 16h6M23 16h6"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
      <circle className="brand-dot" cx="16" cy="16" r="3.5" />
    </svg>
  );
}

export default function App() {
  const route = useRoute();
  const title = pageTitle(route);
  const current = sectionOf(route);

  useEffect(() => {
    document.title = `${title} – sky-eye`;
  }, [title]);

  return (
    <>
      <header className="app-header">
        <a className="brand" href={href({ page: "live" })}>
          <BrandMark />
          <span>sky-eye</span>
        </a>
        <nav className="app-nav" aria-label="Main">
          {SECTIONS.map(({ section, label, icon }) => {
            const active = section === current;
            return (
              <a
                key={section}
                className="nav-link"
                href={href({ page: section })}
                aria-current={active ? (route.page === section ? "page" : "true") : undefined}
              >
                {icon}
                <span>{label}</span>
              </a>
            );
          })}
        </nav>
      </header>
      <main className="app-main">
        <div className="page">
          <div className="page-head">
            <h1 className="page-title">{title}</h1>
          </div>
          <div className="empty">
            <p>{PLACEHOLDER[route.page]}</p>
          </div>
        </div>
      </main>
    </>
  );
}
