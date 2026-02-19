const API_BASE = '/api'

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init)
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status}: ${body}`)
  }
  return res.json()
}

// Types

export interface Stats {
  sqlite: {
    available: boolean
    files_parsed?: number
    chunks?: number
    symbols?: number
    scip_symbols?: number
    scip_occurrences?: number
    file_summaries?: number
    error?: string
  }
  lancedb: {
    available: boolean
    embedded_chunks?: number
  }
  tantivy: {
    available: boolean
    indexed_docs?: number
  }
}

export interface TreeChild {
  name: string
  type: 'file' | 'directory'
  summary?: string | null
  // file fields
  parsed?: boolean
  summarized?: boolean
  embedded?: boolean
  // directory fields
  total_files?: number
}

export interface TreeResponse {
  path: string
  children: TreeChild[]
}

export interface ChunkData {
  id: number
  file_path: string
  symbol_name: string | null
  chunk_type: string
  start_line: number
  end_line: number
  content: string
  token_estimate: number | null
  embedded: boolean
}

export interface FileDetailResponse {
  file_path: string
  summary: { summary: string; language: string | null; line_count: number | null } | null
  chunks: ChunkData[]
  symbols: Array<{
    id: number
    name: string
    kind: string
    start_line: number
    end_line: number
    signature: string | null
  }>
}

export interface SearchResult {
  chunk_id: number
  file_path: string
  symbol_name: string | null
  chunk_type: string
  content: string
  score: number
  match_type: string
}

export interface SearchResponse {
  query: string
  mode: string
  results: SearchResult[]
}

export interface TaskInfo {
  id: string
  name: string
  status: 'running' | 'completed' | 'failed'
  result?: Record<string, unknown>
  error?: string
}

export interface RunResponse {
  task_id: string
  name: string
  status: string
}

export interface QueryEvidence {
  tool: string
  args: Record<string, unknown>
  summary: string
}

export interface QueryResult {
  answer: string
  evidence: QueryEvidence[]
}

export interface QueryCachedResponse {
  cached: true
  query_id: number
  source_query_id: number
  answer: string
  evidence: QueryEvidence[]
}

export interface QueryHistoryItem {
  id: number
  prompt: string
  status: 'running' | 'completed' | 'failed' | 'cached'
  created_at: string
  duration_secs: number | null
}

export interface Repo {
  id: number
  name: string
  url: string | null
  local_path: string
  created_at: string
  last_indexed_at: string | null
  indexed_commit_sha: string | null
  current_commit_sha: string | null
  commits_behind: number
  status: string
}

export interface FreshnessCheck {
  indexed_sha: string | null
  current_sha: string
  commits_behind: number
  changed_files: string[]
}

export interface QueryHistoryDetail extends QueryHistoryItem {
  answer: string | null
  evidence: QueryEvidence[] | null
  error: string | null
  completed_at: string | null
}

// API functions

export const fetchStats = (repoId: number = 1) =>
  apiFetch<Stats>(`/stats?repo_id=${repoId}`)

export const fetchTree = (path: string, repoId: number = 1) =>
  apiFetch<TreeResponse>(`/tree?path=${encodeURIComponent(path)}&repo_id=${repoId}`)

export const fetchFileDetail = (filePath: string, repoId: number = 1) =>
  apiFetch<FileDetailResponse>(`/files/${filePath}?repo_id=${repoId}`)

export const fetchChunkDetail = (chunkId: number, repoId: number = 1) =>
  apiFetch<ChunkData>(`/chunks/${chunkId}?repo_id=${repoId}`)

export const searchCode = (q: string, mode: string, limit: number, repoId: number = 1) =>
  apiFetch<SearchResponse>(
    `/search?q=${encodeURIComponent(q)}&mode=${encodeURIComponent(mode)}&limit=${limit}&repo_id=${repoId}`
  )

export const fetchTasks = () => apiFetch<TaskInfo[]>('/tasks')

export const runOperation = (name: string, body?: Record<string, unknown>) =>
  apiFetch<RunResponse>(`/run/${name}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })

export const fetchStrategies = () =>
  apiFetch<{ strategies: string[] }>('/strategies').then((r) => r.strategies)

export const runQuery = (prompt: string, force?: boolean, repoId: number = 1, mode: string = 'auto') =>
  apiFetch<RunResponse | QueryCachedResponse>('/run/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, repo_id: repoId, mode, ...(force ? { force: true } : {}) }),
  })

export const fetchQueryHistory = (repoId: number = 1) =>
  apiFetch<QueryHistoryItem[]>(`/queries?repo_id=${repoId}`)

export const fetchQueryDetail = (id: number) =>
  apiFetch<QueryHistoryDetail>(`/queries/${id}`)

export const fetchRepos = () => apiFetch<Repo[]>('/repos')

export const fetchRepo = (repoId: number) => apiFetch<Repo>(`/repos/${repoId}`)

export const createRepo = (name: string, url: string, shallow = true) =>
  apiFetch<RunResponse>('/repos', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, url, shallow }),
  })

export const deleteRepo = (repoId: number) =>
  apiFetch<{ deleted: boolean; repo_id: number }>(`/repos/${repoId}`, {
    method: 'DELETE',
  })

export const checkRepoFreshness = (repoId: number) =>
  apiFetch<FreshnessCheck>(`/repos/${repoId}/check`, {
    method: 'POST',
  })

export const syncRepo = (repoId: number) =>
  apiFetch<RunResponse>(`/repos/${repoId}/sync`, {
    method: 'POST',
  })

export function createTaskStream(taskId: string): EventSource {
  return new EventSource(`${API_BASE}/tasks/${taskId}/stream`)
}
