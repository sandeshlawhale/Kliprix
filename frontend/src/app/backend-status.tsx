"use client";

import { useEffect, useState } from "react";

import { ApiError, getHealth, type HealthResponse } from "@/lib/api";

type State =
  { kind: "loading" } | { kind: "ok"; health: HealthResponse } | { kind: "error"; message: string };

export function BackendStatus() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    getHealth()
      .then((health) => {
        if (active) setState({ kind: "ok", health });
      })
      .catch((error: unknown) => {
        const message = error instanceof ApiError ? error.message : "Unexpected error.";
        if (active) setState({ kind: "error", message });
      });
    return () => {
      active = false;
    };
  }, []);

  if (state.kind === "loading") {
    return <p role="status">Checking backend…</p>;
  }
  if (state.kind === "error") {
    return <p role="alert">Backend unavailable: {state.message}</p>;
  }
  return (
    <p role="status">
      Backend: {state.health.status} (version {state.health.version})
    </p>
  );
}
