"use client";

// Server data through TanStack Query (docs/12). Query keys live here so invalidation is
// consistent across pages.
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import * as api from "@/lib/api";
import { loadLlmConfig } from "@/lib/session";

export const keys = {
  system: ["system"] as const,
  downloaderRelease: ["downloader-release"] as const,
  settings: ["settings"] as const,
  providers: ["providers"] as const,
  llmSession: ["llm-session"] as const,
  storage: ["storage"] as const,
  batches: ["batches"] as const,
  batch: (id: string) => ["batch", id] as const,
  clip: (id: string) => ["clip", id] as const,
};

export const useSystem = () => useQuery({ queryKey: keys.system, queryFn: api.getSystem });
/** Newest yt-dlp on PyPI vs the installed one; only used while Settings is open (D-068). */
export const useDownloaderRelease = () =>
  useQuery({
    queryKey: keys.downloaderRelease,
    queryFn: api.getDownloaderRelease,
    staleTime: 60 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: api.getSettings });
export const useProviders = () =>
  useQuery({ queryKey: keys.providers, queryFn: api.getProviders, staleTime: Infinity });

/** The backend's LLM session; re-sends the tab's saved config if the backend restarted. */
export function useLlmSession() {
  return useQuery({
    queryKey: keys.llmSession,
    queryFn: async () => {
      const status = await api.getLlmSession();
      if (status.configured) return status;
      const saved = loadLlmConfig();
      if (!saved) return status;
      const restored = await api.putLlmSession(saved);
      return {
        configured: true,
        provider: restored.provider,
        model: restored.model,
        saved: restored.saved ?? null,
      };
    },
    refetchInterval: 30_000,
  });
}

export function useUpdateSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.putSettings,
    onSuccess: (settings) => client.setQueryData(keys.settings, settings),
  });
}

export function useBatchHistory() {
  return useInfiniteQuery({
    queryKey: keys.batches,
    queryFn: ({ pageParam }) => api.listBatches(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    // Faster while a batch works, so its sidebar marker clears soon after it finishes.
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) => page.items.some((item) => item.working))
        ? 3_000
        : 10_000,
  });
}

export const useBatch = (id: string) =>
  useQuery({ queryKey: keys.batch(id), queryFn: () => api.getBatch(id) });

export const useClip = (id: string) =>
  useQuery({ queryKey: keys.clip(id), queryFn: () => api.getClip(id) });

export function invalidateBatch(client: QueryClient, batchId: string): void {
  void client.invalidateQueries({ queryKey: keys.batch(batchId) });
  void client.invalidateQueries({ queryKey: keys.batches });
}

export const useStorage = () =>
  useQuery({ queryKey: keys.storage, queryFn: api.getStorage, refetchInterval: 60_000 });
