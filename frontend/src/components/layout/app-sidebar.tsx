"use client";

import { MoreHorizontal, Plus } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { AccountMenu } from "@/components/layout/account-menu";
import { DeleteBatchDialog } from "@/components/layout/delete-batch-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { Skeleton } from "@/components/ui/skeleton";
import { useBatchHistory } from "@/hooks/queries";
import type { BatchSummary } from "@/lib/api";
import { errorMessage } from "@/lib/api";
import { historyGroup, plural } from "@/lib/format";

const GROUPS = ["Today", "Yesterday", "Earlier"] as const;

export function AppSidebar() {
  const pathname = usePathname();
  const history = useBatchHistory();
  const [toDelete, setToDelete] = useState<BatchSummary | null>(null);
  const items = history.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    // Sticky so it stays put while the main area scrolls.
    <Sidebar collapsible="none" className="sticky top-0 h-svh self-start border-r">
      <SidebarHeader className="gap-3 p-4">
        <Link
          href="/"
          className="self-center text-lg font-semibold tracking-tight"
          aria-label="Kliprix home"
        >
          <span className="text-brand">K</span>liprix
        </Link>
        <Button asChild className="w-full justify-start" variant="outline">
          <Link href="/">
            <Plus aria-hidden /> New clips
          </Link>
        </Button>
      </SidebarHeader>
      <SidebarContent>
        {history.isPending && (
          <SidebarGroup>
            <SidebarMenu>
              {[0, 1, 2].map((n) => (
                <SidebarMenuItem key={n}>
                  <Skeleton className="h-8 w-full" />
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
        )}
        {history.isError && (
          <p className="text-destructive px-4 py-2 text-sm" role="alert">
            {errorMessage(history.error)}
          </p>
        )}
        {history.isSuccess && items.length === 0 && (
          <p className="text-muted-foreground px-4 py-2 text-sm">No generations yet</p>
        )}
        {GROUPS.map((group) => {
          const groupItems = items.filter((item) => historyGroup(item.created_at) === group);
          if (groupItems.length === 0) return null;
          return (
            <SidebarGroup key={group}>
              <SidebarGroupLabel>{group}</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {groupItems.map((item) => {
                    const href = `/history/${item.id}`;
                    const more = item.video_count > 1 ? ` +${item.video_count - 1} more` : "";
                    return (
                      <SidebarMenuItem key={item.id}>
                        <SidebarMenuButton
                          asChild
                          isActive={pathname.startsWith(href)}
                          className="h-auto flex-col items-start gap-0.5 py-2"
                        >
                          <Link href={href}>
                            <span className="w-full truncate">
                              {item.title}
                              {more}
                            </span>
                            <span className="text-muted-foreground text-xs">
                              {plural(item.video_count, "video")} ·{" "}
                              {plural(item.clip_count, "clip")}
                            </span>
                          </Link>
                        </SidebarMenuButton>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <SidebarMenuAction showOnHover aria-label={`Actions for ${item.title}`}>
                              <MoreHorizontal />
                            </SidebarMenuAction>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent side="right" align="start">
                            <DropdownMenuItem
                              variant="destructive"
                              onSelect={() => setToDelete(item)}
                            >
                              Delete
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </SidebarMenuItem>
                    );
                  })}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          );
        })}
        {history.hasNextPage && (
          <div className="px-4 pb-4">
            <Button
              variant="ghost"
              size="sm"
              className="w-full"
              disabled={history.isFetchingNextPage}
              onClick={() => void history.fetchNextPage()}
            >
              {history.isFetchingNextPage ? "Loading…" : "Show older"}
            </Button>
          </div>
        )}
      </SidebarContent>
      <SidebarFooter className="p-2">
        <AccountMenu />
      </SidebarFooter>
      <DeleteBatchDialog batch={toDelete} onClose={() => setToDelete(null)} />
    </Sidebar>
  );
}
