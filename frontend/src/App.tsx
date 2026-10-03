/**
 * Root application component.
 *
 * Top-level state holds only live availability. Browser URLs select
 * the list, workshop detail, account, tickets, and admin views; an
 * unknown path falls through to a 404 view.
 *
 * Real-time updates from the SSE stream land in `spotOverrides`, a
 * `workshop_id -> available_spots` map that overlays the values
 * returned by the workshop list hook so a fresh spot count is rendered
 * without a re-fetch. The most recent event is forwarded to
 * the detail view as `liveEvent` so an open detail card can refresh
 * itself on a relevant update.
 *
 * Authentication is owned by `AuthProvider`; this component only
 * checks the resolved user when protecting the signed-in routes. While
 * the auth context is still rehydrating (`isRestoring`) the whole
 * shell renders a skeleton so the list view never flashes its
 * "Sign in" button for a user who is actually signed in. The admin
 * route is additionally restricted to `user.is_admin`; everyone
 * else is redirected home.
 */
import { useCallback, useEffect, useState } from "react";

import { useRoute } from "./app/useRoute";
import { AccountView } from "./components/AccountView";
import { AdminView } from "./components/AdminView";
import { NotFoundView } from "./components/NotFoundView";
import { OrganizerView } from "./components/OrganizerView";
import { TicketsView } from "./components/TicketsView";
import { WorkshopDetailView } from "./components/WorkshopDetailView";
import { WorkshopListView } from "./components/WorkshopListView";
import { useAuth } from "./features/auth/AuthContext";
import { useEventSource } from "./useEventSource";
import type { SSEEvent } from "./types";

const PAGE_TITLES: Record<string, string> = {
  home: "Workshop Reservations",
  account: "Your account - Workshop Reservations",
  tickets: "My tickets - Workshop Reservations",
  admin: "Admin - Workshop Reservations",
  organizer: "Organizer dashboard - Workshop Reservations",
  workshop: "Workshop details - Workshop Reservations",
};

export function App() {
  const [route, navigate] = useRoute();
  const { user, isRestoring } = useAuth();
  const [spotOverrides, setSpotOverrides] = useState<Record<string, number>>({});
  const [liveEvent, setLiveEvent] = useState<SSEEvent | null>(null);
  const [catalogRevision, setCatalogRevision] = useState(0);

  const handleEvent = useCallback((event: SSEEvent) => {
    if (event.type === "workshop_created" || event.type === "workshop_deleted") {
      setCatalogRevision((value) => value + 1);
    } else if (event.available_spots !== undefined) {
      setSpotOverrides((previous) => ({
        ...previous,
        [event.workshop_id]: event.available_spots ?? previous[event.workshop_id] ?? 0,
      }));
    }
    setLiveEvent(event);
  }, []);
  useEventSource(handleEvent);

  const handleSpotsChanged = useCallback((id: string, spots: number) => {
    setSpotOverrides((previous) => ({ ...previous, [id]: spots }));
  }, []);
  const navigateHome = useCallback(() => navigate("/"), [navigate]);

  // Route guards. Signed-in routes send anonymous visitors
  // back to the list with the auth modal opened; the admin
  // surface additionally requires the super-admin, so a
  // signed-in non-admin is redirected home. The organizer
  // dashboard requires the organizer platform role (or the
  // super-admin's `admin` role); a signed-in attendee is
  // redirected home.
  useEffect(() => {
    if (isRestoring) return;
    if (route.name === "account" || route.name === "tickets") {
      if (!user) navigate("/?auth=sign-in");
      return;
    }
    if (route.name === "admin") {
      if (!user) {
        navigate("/?auth=sign-in");
      } else if (!user.is_admin) {
        navigate("/");
      }
    }
    if (route.name === "organizer") {
      if (!user) {
        navigate("/?auth=sign-in");
      } else if (user.role !== "organizer" && user.role !== "admin") {
        navigate("/");
      }
    }
  }, [isRestoring, navigate, route.name, user]);

  // Update the document title to match the current route. The
  // fallback "Page not found - Workshop Reservations" lets screen-reader
  // users hear the 404 state without inspecting the URL.
  useEffect(() => {
    if (route.name === "notFound") {
      document.title = "Page not found - Workshop Reservations";
      return;
    }
    document.title = PAGE_TITLES[route.name] ?? PAGE_TITLES.home ?? "Workshop Reservations";
  }, [route.name]);

  if (isRestoring) {
    return (
      <div
        id="app-restoring"
        className="paper-grid flex min-h-screen items-center justify-center"
        aria-label="Loading your account"
      >
        <div className="flex items-center gap-3 text-ink/70">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-coral" />
          <span className="text-sm font-semibold uppercase tracking-[.2em]">Workshop</span>
        </div>
      </div>
    );
  }

  if (route.name === "notFound") {
    return <NotFoundView onHome={navigateHome} />;
  }

  if (route.name === "account") {
    if (!user) return null;
    return <AccountView onClose={navigateHome} />;
  }

  if (route.name === "tickets") {
    if (!user) return null;
    return <TicketsView onBack={navigateHome} />;
  }

  if (route.name === "admin") {
    if (!user?.is_admin) return null;
    return <AdminView onClose={navigateHome} />;
  }

  if (route.name === "organizer") {
    if (!user || (user.role !== "organizer" && user.role !== "admin")) return null;
    return <OrganizerView onClose={navigateHome} />;
  }

  if (route.name === "workshop") {
    return (
      <WorkshopDetailView
        workshopId={route.workshopId}
        availableSpots={spotOverrides[route.workshopId] ?? null}
        liveEvent={liveEvent}
        onBack={navigateHome}
        onSpotsChanged={handleSpotsChanged}
        onDeleted={navigateHome}
      />
    );
  }

  return (
    <main id="app-main" className="paper-grid min-h-screen">
      <WorkshopListView
        spotOverrides={spotOverrides}
        onSelect={(id) => navigate(`/workshops/${encodeURIComponent(id)}`)}
        onOpenAccount={() => navigate("/account")}
        onOpenTickets={() => navigate("/tickets")}
        onOpenAdmin={() => navigate("/admin")}
        onOpenOrganizer={() => navigate("/organizer")}
        authRequested={route.authRequested}
        refreshKey={catalogRevision}
      />
    </main>
  );
}
