/* JSON for More > Sources and source previews. Loosely typed on purpose. */
import { get } from "./client";
import type { ContextMessage } from "./proposals";

export type Row = Record<string, unknown> & { id?: number };

export const listSources = () => get<{ files: Row[]; failed: Row[] }>("/sources");
export const sourceMessage = (waId: string) =>
  get<{ chat: { jid: string; name: string }; message: ContextMessage; messages: ContextMessage[] }>(
    `/sources/messages/${encodeURIComponent(waId)}`,
  );

/** Original file as stored (same session cookie). */
export const fileUrl = (id: number, download = false) => `/sources/file/${id}${download ? "?download=1" : ""}`;
