/* Part 2 (Chats): typed stubs for the endpoints in docs/ux-revamp.md §4.3. The backend routes do not exist yet;
   implement them in receiver/api.py and fill these in. Nothing here is called by part 1 screens. */
import { get, post, request } from "./client";
import type { Proposal, ProposalState } from "./proposals";

export interface Chat {
  jid: string;
  name: string;
  kind: "group" | "private";
  in_scope: boolean;
  count: number;
  media: number;
  last_ts: number | null;
  preview: string;
  media_preview: boolean;
  open_suggestions: number;
  last_sync_at: number | null;
}

export interface ChatMessage {
  id: string;
  ts: number;
  sender: string | null;
  text: string;
  kind: "text" | "image" | "video" | "workbook" | "csv" | "pdf" | "document" | string;
  file_name: string | null;
  mimetype: string | null;
  file: { id: number; kind: string; file_name: string; mimetype: string; size: number } | null;
  media: "ok" | "failed" | "pending" | "none";
  suggestion_ids: number[];
}

export interface SearchResults {
  chats: { jid: string; name: string; hits: number }[];
  messages: { jid: string; chat_name: string; wa_id: string; ts: number; sender: string; snippet: string; match_ranges: [number, number][] }[];
}

export const listChats = (scope: "all" | "ticked" = "all", has_suggestions?: boolean) =>
  get<Chat[]>("/chats", { scope, has_suggestions });
export const setChatScope = (jid: string, in_scope: boolean) =>
  request<{ jid: string; in_scope: boolean }>(`/chats/${encodeURIComponent(jid)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ in_scope }),
  });
export const chatMessages = (jid: string, q: { before?: number; around?: string; q?: string; kind?: string; limit?: number } = {}) =>
  get<{ messages: ChatMessage[]; has_more: boolean; has_newer: boolean }>(`/chats/${encodeURIComponent(jid)}/messages`, q);
export const chatProposals = (jid: string, state: ProposalState = "proposed") =>
  get<{ items: Proposal[] }>(`/chats/${encodeURIComponent(jid)}/proposals`, { state });
/** 202 {job_id}; 409 busy (offer "Sync when free"); 422 not_in_scope (confirm, then call with addToScope). */
export const syncChat = (jid: string, addToScope = false) =>
  post<{ job_id: number }>(`/chats/${encodeURIComponent(jid)}/sync`, { add_to_scope: addToScope });
export const search = (q: string, jid?: string, limit = 50) => get<SearchResults>("/search", { q, jid, limit });
