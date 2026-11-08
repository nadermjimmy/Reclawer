/* Chats, messages, per-chat suggestions, single-chat sync and search (docs/ux-revamp.md §4.3).
   Listen-only: nothing here sends anything to WhatsApp. */
import { get, post, request } from "./client";
import type { Proposal, ProposalState } from "./proposals";

export type ChatFilter = "all" | "inventory" | "suggestions" | "groups" | "private";

export interface Chat {
  jid: string;
  name: string;
  kind: "group" | "private";
  in_scope: boolean;
  count: number;
  media: number;
  last_ts: number | null;
  preview: string;
  preview_sender: string | null;
  media_preview: boolean;
  open_suggestions: number;
  last_sync_at: number | null;
  /** Messages that arrived (webhook) since the last Sync now of this chat; null when never synced. */
  new_since_sync: number | null;
}

export interface ChatList {
  chats: Chat[];
  counts: { all: number; inventory: number; suggestions: number };
}

export interface StoredFile {
  id: number;
  kind: string;
  file_name: string;
  mimetype: string | null;
  size: number;
  parse_status: string;
  url: string;
  download_url: string;
}

export interface ChatMessage {
  id: string;
  ts: number | null;
  sender: string | null;
  text: string;
  kind: "text" | "image" | "video" | "workbook" | "csv" | "pdf" | "document" | "other" | string;
  file_name: string | null;
  mimetype: string | null;
  file: StoredFile | null;
  media: "ok" | "failed" | "pending" | "none";
  suggestion_ids: number[];
}

export interface MessagePage {
  chat: { jid: string; name: string; in_scope: boolean; kind: "group" | "private" };
  messages: ChatMessage[];
  has_more: boolean;
  has_newer: boolean;
  around: string | null;
}

export interface MessageHit {
  wa_id: string;
  jid: string;
  chat_name: string;
  ts: number | null;
  sender: string;
  snippet: string;
  match_ranges: [number, number][];
  field: "text" | "file" | "sender";
  file_name: string | null;
}

export interface ChatHit {
  jid: string;
  name: string;
  kind: "group" | "private";
  in_scope: boolean;
  last_ts: number | null;
  match_ranges: [number, number][];
}

export interface SearchResults {
  q: string;
  chats: ChatHit[];
  messages: MessageHit[];
  has_more: boolean;
  took_ms: number;
}

export interface ChatSuggestions {
  items: Proposal[];
  counts: Record<"proposed" | "approved" | "rejected", number>;
  has_master: boolean;
  sheets: string[];
}

/** RESULT line of a chat sync job. */
export interface SyncResult {
  messages: number;
  files: number;
  files_failed: number;
  suggestions: number;
  failed_batches: number;
  skipped?: "no_master";
  error?: "bridge_unreachable";
}

const enc = encodeURIComponent;

export const listChats = (filter: ChatFilter = "all", q = "") => get<ChatList>("/chats", { filter, q });
export const setChatScope = (jid: string, in_scope: boolean) =>
  request<{ jid: string; in_scope: boolean }>(`/chats/${enc(jid)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ in_scope }),
  });
export const chatMessages = (jid: string, q: { before?: string; after?: string; around?: string; limit?: number } = {}) =>
  get<MessagePage>(`/chats/${enc(jid)}/messages`, q);
export const chatSuggestions = (jid: string, state?: ProposalState) =>
  get<ChatSuggestions>(`/chats/${enc(jid)}/suggestions`, { state });
/** 202 {job_id}; 409 busy (offer "Sync when free"); 422 not_in_scope (confirm, then call with addToScope). */
export const syncChat = (jid: string, addToScope = false) =>
  post<{ job_id: number; jid: string }>(`/chats/${enc(jid)}/sync`, { add_to_scope: addToScope });
export const search = (q: string, chat?: string, limit = 50) => get<SearchResults>("/search", { q, chat, limit });
