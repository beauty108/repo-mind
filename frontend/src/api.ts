export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8080';

const BASE_URL = `${API_BASE_URL}/api`;

// ── Shared types ──────────────────────────────────────────────────────────────

export interface RepoResponse {
  id: string;
  github_url: string;
  owner: string;
  repo_name: string;
  status: 'pending' | 'indexing' | 'ready' | 'failed';
  error_message?: string;
  indexed_file_count: number;
  skipped_file_count: number;
  indexed_commit_sha?: string;
  embedding_model_name?: string;
  is_private: boolean;
  created_at: string;
  updated_at: string;
}

export interface FileResponse {
  id: string;
  path: string;
  language: string;
  size_bytes: number;
}

export interface FileListResponse {
  items: FileResponse[];
  total: number;
  page: number;
  page_size: number;
}

export interface Citation {
  file_path: string;
  start_line: number;
  end_line: number;
  content?: string;
  symbol_name?: string;
  symbol_type?: string;
  language?: string;
  similarity?: number;
}

export interface MessageResponse {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
  created_at: string;
}

export interface ConversationMessagesResponse {
  conversation_id: string;
  repository_id: string;
  messages: MessageResponse[];
}

// ── Auth token management ─────────────────────────────────────────────────────

export function getStoredToken(): string | null {
  return localStorage.getItem('repomind_token');
}

export function setStoredToken(token: string): void {
  localStorage.setItem('repomind_token', token);
}

export function clearStoredToken(): void {
  localStorage.removeItem('repomind_token');
  localStorage.removeItem('repomind_user');
}

function authHeaders(): Record<string, string> {
  const token = getStoredToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ── Auth endpoints ────────────────────────────────────────────────────────────

export interface AuthPayload {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user_id: string;
  email: string;
}

export async function register(payload: AuthPayload): Promise<TokenResponse> {
  const res = await fetch(`${BASE_URL}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Registration failed' }));
    throw new Error(err.detail || 'Registration failed');
  }
  return res.json();
}

export async function login(payload: AuthPayload): Promise<TokenResponse> {
  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Login failed' }));
    throw new Error(err.detail || 'Login failed');
  }
  return res.json();
}

// ── Repository endpoints ──────────────────────────────────────────────────────

export interface SubmitRepoPayload {
  github_url: string;
  github_token?: string;
}

export async function submitRepository(payload: SubmitRepoPayload): Promise<RepoResponse> {
  const res = await fetch(`${BASE_URL}/repos`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to submit repository' }));
    throw new Error(err.detail || 'Failed to submit repository');
  }
  return res.json();
}

export async function getRepository(repoId: string): Promise<RepoResponse> {
  const res = await fetch(`${BASE_URL}/repos/${repoId}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error('Repository not found');
  return res.json();
}

export async function getRepositoryFiles(repoId: string, page = 1, pageSize = 50): Promise<FileListResponse> {
  const res = await fetch(`${BASE_URL}/repos/${repoId}/files?page=${page}&page_size=${pageSize}`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new Error(data?.detail || 'Failed to fetch repository files');
  }
  return res.json();
}

export async function getConversationHistory(repoId: string, convId: string): Promise<ConversationMessagesResponse> {
  const res = await fetch(`${BASE_URL}/repos/${repoId}/conversations/${convId}/messages`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new Error(data?.detail || 'Failed to fetch conversation history');
  }
  return res.json();
}

// ── Chat streaming ────────────────────────────────────────────────────────────

export async function cancelStream(repoId: string, streamId: string): Promise<void> {
  try {
    await fetch(`${BASE_URL}/repos/${repoId}/chat/stream/${streamId}`, {
      method: 'DELETE',
      headers: authHeaders(),
    });
  } catch {
    // Best-effort — don't throw on cancel failure
  }
}
