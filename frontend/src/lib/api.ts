// The ONLY module that calls the backend (docs/06-architecture.md, boundary rule 3).
// Requests go to same-origin /api/*, which Next.js rewrites to the FastAPI backend.
import type { components } from "@/lib/api-types";

type Schemas = components["schemas"];
export type HealthResponse = Schemas["HealthResponse"];
export type SystemInfo = Schemas["SystemInfo"];
export type Settings = Schemas["Settings"];
export type SettingsUpdate = Schemas["SettingsUpdate"];
export type StyleOverrides = Schemas["StyleOverrides"];
export type StyleOverridesUpdate = Schemas["StyleOverridesUpdate"];
export type ProviderInfo = Schemas["ProviderInfo"];
export type ProviderId = ProviderInfo["id"];
export type SessionStatus = Schemas["SessionStatus"];
export type ModelEntry = Schemas["ModelEntry"];
export type TestResponse = Schemas["TestResponse"];
export type InspectResult = Schemas["InspectResult"];
export type VideoMeta = Schemas["VideoMeta"];
export type Batch = Schemas["Batch"];
export type BatchPage = Schemas["BatchPage"];
export type BatchSummary = Schemas["BatchSummary"];
export type Video = Schemas["Video"];
export type Checkpoint = Schemas["Checkpoint"];
export type ClipSummary = Schemas["ClipSummary"];
export type Clip = Schemas["Clip"];
export type ClipPatch = Schemas["ClipPatch"];
export type Word = Schemas["WordOut"];
export type BlurBox = Schemas["BlurBox"];
export type DownloaderUpdate = Schemas["DownloaderUpdate"];

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

export const BACKEND_UNREACHABLE = "BACKEND_UNREACHABLE";

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = (value as { error: unknown }).error;
  return typeof error === "object" && error !== null && "code" in error && "message" in error;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { cache: "no-store", ...init });
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
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

/** A message for any thrown value, using the backend's text when there is one. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Something went wrong.";
}

// ----- system and settings -----
export const getHealth = () => request<HealthResponse>("/health");
export const getSystem = () => request<SystemInfo>("/system");
export const updateDownloader = () =>
  request<DownloaderUpdate>("/system/update-downloader", { method: "POST" });
export const getSettings = () => request<Settings>("/settings");
export const putSettings = (update: SettingsUpdate) =>
  request<Settings>("/settings", json("PUT", update));

// ----- LLM session -----
export interface LlmConfig {
  provider: ProviderId;
  model: string;
  api_key: string;
  base_url?: string | null;
}

function llmBody(config: LlmConfig): Record<string, unknown> {
  const body: Record<string, unknown> = {
    provider: config.provider,
    model: config.model,
    api_key: config.api_key,
  };
  if (config.base_url) body.base_url = config.base_url;
  return body;
}

export const getProviders = () => request<ProviderInfo[]>("/llm/providers");
export const getLlmSession = () => request<SessionStatus>("/llm/session");
export const putLlmSession = (config: LlmConfig) =>
  request<Schemas["SessionConfigured"]>("/llm/session", json("PUT", llmBody(config)));
export const deleteLlmSession = () => request<void>("/llm/session", { method: "DELETE" });
/** Saves provider, model and key (encrypted) in the backend's database (D-053). */
export const putLlmSaved = (config: LlmConfig) =>
  request<Schemas["SessionConfigured"]>("/llm/saved", json("PUT", llmBody(config)));
export const deleteLlmSaved = () => request<void>("/llm/saved", { method: "DELETE" });
export const listModels = (provider: ProviderId, apiKey: string, baseUrl?: string | null) =>
  request<Schemas["ModelsResponse"]>(
    "/llm/models",
    json(
      "POST",
      baseUrl ? { provider, api_key: apiKey, base_url: baseUrl } : { provider, api_key: apiKey },
    ),
  );
export const testLlm = (config: LlmConfig) =>
  request<TestResponse>("/llm/test", json("POST", llmBody(config)));

// ----- videos and batches -----
export const inspectVideos = (urls: string[]) =>
  request<Schemas["InspectResponse"]>("/videos/inspect", json("POST", { urls }));
export const createBatch = (urls: string[], style?: StyleOverridesUpdate) =>
  request<Schemas["CreatedBatch"]>("/batches", json("POST", style ? { urls, style } : { urls }));
export const listBatches = (cursor?: string | null) =>
  request<BatchPage>(`/batches${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`);
export const getBatch = (id: string) => request<Batch>(`/batches/${encodeURIComponent(id)}`);
export const cancelBatch = (id: string) =>
  request<Batch>(`/batches/${encodeURIComponent(id)}/cancel`, { method: "POST" });
/** Hold a running batch (D-049): the running step stops, finished steps are kept. */
export const pauseBatch = (id: string) =>
  request<Batch>(`/batches/${encodeURIComponent(id)}/pause`, { method: "POST" });
/** Continue a paused batch; the stopped step starts again. */
export const resumeBatch = (id: string) =>
  request<Batch>(`/batches/${encodeURIComponent(id)}/resume`, { method: "POST" });
export const deleteBatch = (id: string, deleteFiles: boolean) =>
  request<void>(`/batches/${encodeURIComponent(id)}?delete_files=${deleteFiles}`, {
    method: "DELETE",
  });
/** Up to `count` more clips from a video's saved download (D-034); returns the batch. */
/** Resumes a failed video from the step that failed (P-001). */
export const retryVideo = (videoId: string) =>
  request<Batch>(`/videos/${encodeURIComponent(videoId)}/retry`, { method: "POST" });
export const moreClips = (videoId: string, count: number) =>
  request<Batch>(`/videos/${encodeURIComponent(videoId)}/more-clips`, json("POST", { count }));
/** One clip from a range picked by hand (D-061), source seconds. */
export const customClip = (videoId: string, start_s: number, end_s: number) =>
  request<Batch>(
    `/videos/${encodeURIComponent(videoId)}/custom-clip`,
    json("POST", {
      start_s: Math.round(start_s * 100) / 100,
      end_s: Math.round(end_s * 100) / 100,
    }),
  );
/** The saved download, for picking a custom clip's range (D-061). */
export const videoSourceUrl = (videoId: string) =>
  `/api/videos/${encodeURIComponent(videoId)}/source`;
/** URL of the batch's Server-Sent Events stream (opened by the SSE hook). */
export const batchEventsUrl = (id: string) => `/api/batches/${encodeURIComponent(id)}/events`;

// ----- clips -----
export const getClip = (id: string) => request<Clip>(`/clips/${encodeURIComponent(id)}`);
export const patchClip = (id: string, patch: ClipPatch) =>
  request<Clip>(`/clips/${encodeURIComponent(id)}`, json("PATCH", patch));
export const previewClip = (id: string) =>
  request<Schemas["Accepted"]>(`/clips/${encodeURIComponent(id)}/preview`, { method: "POST" });
export const exportClip = (id: string) =>
  request<Schemas["Accepted"]>(`/clips/${encodeURIComponent(id)}/render`, { method: "POST" });
export const retryClip = (id: string) =>
  request<Schemas["Accepted"]>(`/clips/${encodeURIComponent(id)}/retry`, { method: "POST" });
export const clipMediaUrl = (
  id: string,
  kind: "preview" | "export" | "base" | "thumbnail",
  version = "",
) =>
  `/api/clips/${encodeURIComponent(id)}/media/${kind}${version ? `?v=${encodeURIComponent(version)}` : ""}`;
export const clipDownloadUrl = (id: string) => `/api/clips/${encodeURIComponent(id)}/download`;

// ----- storage -----
export type StorageInfo = Schemas["StorageInfo"];
export type CleanupScope = Schemas["CleanupRequest"]["scope"];
export const getStorage = () => request<StorageInfo>("/storage");
export const cleanupStorage = (scope: CleanupScope, batchId?: string) =>
  request<Schemas["CleanupResult"]>(
    "/storage/cleanup",
    json("POST", batchId ? { scope, batch_id: batchId } : { scope }),
  );
