import { useCallback, useEffect, useState } from "react";

export type AppRoute =
  | { name: "home"; authRequested: boolean }
  | { name: "account" }
  | { name: "tickets" }
  | { name: "admin" }
  | { name: "organizer" }
  | { name: "workshop"; workshopId: string }
  | { name: "notFound" };

function readRoute(): AppRoute {
  const { pathname, search } = window.location;
  const workshopMatch = /^\/workshops\/([^/]+)\/?$/.exec(pathname);

  if (workshopMatch) {
    return { name: "workshop", workshopId: decodeURIComponent(workshopMatch[1] ?? "") };
  }
  if (pathname === "/account" || pathname === "/account/") {
    return { name: "account" };
  }
  if (pathname === "/tickets" || pathname === "/tickets/") {
    return { name: "tickets" };
  }
  if (pathname === "/admin" || pathname === "/admin/") {
    return { name: "admin" };
  }
  if (pathname === "/organizer" || pathname === "/organizer/") {
    return { name: "organizer" };
  }
  if (pathname === "/" || pathname === "") {
    return {
      name: "home",
      authRequested: new URLSearchParams(search).get("auth") === "sign-in",
    };
  }
  return { name: "notFound" };
}

/** Keep application navigation in the browser URL without another dependency. */
export function useRoute(): readonly [AppRoute, (path: string) => void] {
  const [route, setRoute] = useState(readRoute);

  useEffect(() => {
    const onPopState = () => setRoute(readRoute());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = useCallback((path: string) => {
    window.history.pushState({}, "", path);
    setRoute(readRoute());
  }, []);

  return [route, navigate] as const;
}
