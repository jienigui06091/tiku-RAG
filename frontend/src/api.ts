export interface Library {
  id: string;
  name: string;
  subject: string | null;
  description: string | null;
  document_count: number;
  question_count: number;
  chunk_count: number;
}

export interface DocumentRecord {
  id: string;
  filename: string;
  status: string;
  processing_stage: string;
  progress: number;
  page_count: number | null;
  error_message: string | null;
  question_count: number;
  chunk_count: number;
}

export interface Question {
  id: string;
  sequence: number | null;
  stem: string;
  options: string[] | null;
  answer: string | null;
  analysis: string | null;
  chapter: string | null;
  source_page: number | null;
  document_name: string | null;
}

export interface QuestionPage {
  items: Question[];
  total: number;
  page: number;
  page_size: number;
}

export interface DocumentChunk {
  id: string;
  sequence: number;
  content: string;
  chapter: string | null;
  source_page_start: number | null;
  source_page_end: number | null;
  char_start: number;
  char_end: number;
  document_name: string | null;
}

export interface DocumentChunkSummary {
  id: string;
  sequence: number;
  content_preview: string;
  chapter: string | null;
  source_page_start: number | null;
  source_page_end: number | null;
  document_name: string | null;
}

export interface DocumentChunkPage {
  items: DocumentChunkSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface Citation {
  id: string;
  document_name: string | null;
  chunk_sequence: number;
  source_page_start: number | null;
  source_page_end: number | null;
  chapter: string | null;
  score: number;
}

export interface ChatSession {
  id: string;
  title: string;
  library_id: string | null;
  library_name: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  created_at: string | null;
}

export interface ChatExchange {
  user_message: ChatMessage;
  assistant_message: ChatMessage;
  retrieval_mode: string;
}

export interface ChatStreamHandlers {
  onDelta: (content: string) => void;
  onDone: (exchange: ChatExchange) => void;
}

export interface ChunkingConfig {
  chunk_model: "interface" | "structured" | "fixed" | "delimiter";
  chunk_size: number;
  chunk_overlap: number;
  retain_context: boolean;
  split_by_page: boolean;
  custom_delimiter: string;
}

export interface ReindexResult {
  documents: number;
  chunks: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || "请求失败");
  }
  return response.json() as Promise<T>;
}

async function deleteRequest(path: string): Promise<void> {
  const response = await fetch(`/api${path}`, { method: "DELETE" });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || "删除失败");
  }
}

async function streamChatMessage(chatId: string, query: string, handlers: ChatStreamHandlers): Promise<void> {
  const response = await fetch(`/api/chats/${chatId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ query }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || "发送消息失败。");
  }
  if (!response.body) {
    throw new Error("浏览器不支持流式响应。");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const event = parseSseEvent(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        if (event) {
          if (event.type === "delta") {
            handlers.onDelta(event.data.content as string);
          } else if (event.type === "done") {
            handlers.onDone(event.data as ChatExchange);
          } else if (event.type === "error") {
            throw new Error(String(event.data.detail || "发送消息失败。"));
          }
        }
        boundary = buffer.indexOf("\n\n");
      }
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
}

function parseSseEvent(rawEvent: string): { type: string; data: Record<string, unknown> } | null {
  const lines = rawEvent.split(/\r?\n/);
  const type = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
  const data = lines.find((line) => line.startsWith("data:"))?.slice(5).trim();
  if (!type || !data) return null;
  return { type, data: JSON.parse(data) as Record<string, unknown> };
}

export const api = {
  listLibraries: () => request<Library[]>("/libraries"),
  createLibrary: (payload: { name: string; subject?: string; description?: string }) =>
    request<Library>("/libraries", { method: "POST", body: JSON.stringify(payload) }),
  deleteLibrary: (libraryId: string) => deleteRequest(`/libraries/${libraryId}`),
  listDocuments: (libraryId: string) => request<DocumentRecord[]>(`/libraries/${libraryId}/documents`),
  listChunks: (libraryId: string, page: number, keyword = "") => {
    const params = new URLSearchParams({ page: String(page), page_size: "12" });
    if (keyword) params.set("keyword", keyword);
    return request<DocumentChunkPage>(`/libraries/${libraryId}/chunks?${params}`);
  },
  getChunk: (libraryId: string, chunkId: string) =>
    request<DocumentChunk>(`/libraries/${libraryId}/chunks/${chunkId}`),
  upload: async (libraryId: string, file: File, chunking: ChunkingConfig) => {
    const body = new FormData();
    body.append("file", file);
    body.append("chunk_model", chunking.chunk_model);
    body.append("chunk_size", String(chunking.chunk_size));
    body.append("chunk_overlap", String(chunking.chunk_overlap));
    body.append("retain_context", String(chunking.retain_context));
    body.append("split_by_page", String(chunking.split_by_page));
    body.append("custom_delimiter", chunking.custom_delimiter);
    const response = await fetch(`/api/libraries/${libraryId}/documents`, { method: "POST", body });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new Error(payload?.detail || "上传失败");
    }
    return response.json() as Promise<DocumentRecord>;
  },
  reindex: (libraryId: string, chunking: ChunkingConfig) =>
    request<ReindexResult>(`/libraries/${libraryId}/reindex`, {
      method: "POST",
      body: JSON.stringify(chunking),
    }),
  listChats: () => request<ChatSession[]>("/chats"),
  createChat: (payload: { library_id?: string }) =>
    request<ChatSession>("/chats", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateChat: (chatId: string, payload: { title?: string; library_id?: string | null }) =>
    request<ChatSession>(`/chats/${chatId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteChat: (chatId: string) => deleteRequest(`/chats/${chatId}`),
  listChatMessages: (chatId: string) => request<ChatMessage[]>(`/chats/${chatId}/messages`),
  streamChatMessage,
};
