// The ONLY module that calls the backend (docs/06-architecture.md, boundary rule 3).
// Requests go to same-origin /api/*, which Next.js rewrites to the FastAPI backend.
import type { components } from "@/lib/api-types";

export type HealthResponse = components["schemas"]["HealthResponse"];

/** Error shape for every non-2xx response (docs/07-api-contract.md). */
export interface ApiErrorBody {
  error: { code: string; message: string; details: Record<string, unknown> };
}

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const BACKEND_UNREACHABLE = "BACKEND_UNREACHABLE";

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = (value as { error: unknown }).error;
  return typeof error === "object" && error !== null && "code" in error && "message" in error;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, init);
  } catch {
    throw new ApiError(BACKEND_UNREACHABLE, "Cannot reach the Kliprix backend.", 0);
  }
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    if (isApiErrorBody(body)) {
      throw new ApiError(body.error.code, body.error.message, response.status);
    }
    // The Next.js proxy answers 5xx without our error shape when the backend is down.
    throw new ApiError(
      BACKEND_UNREACHABLE,
      `Cannot reach the Kliprix backend (HTTP ${response.status}). Is it running?`,
      response.status,
    );
  }
  return (await response.json()) as T;
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health", { cache: "no-store" });
}
