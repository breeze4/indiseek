const API_BASE = '/dashboard/api'

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

export interface QueryHistoryDetail extends QueryHistoryItem {
  answer: string | null
  evidence: QueryEvidence[] | null
  error: string | null
  completed_at: string | null
}

// API functions

export const fetchStats = () => apiFetch<Stats>('/stats')

export const fetchTree = (path: string) =>
  apiFetch<TreeResponse>(`/tree?path=${encodeURIComponent(path)}`)

export const fetchFileDetail = (filePath: string) =>
  apiFetch<FileDetailResponse>(`/files/${filePath}`)

export const fetchChunkDetail = (chunkId: number) =>
  apiFetch<ChunkData>(`/chunks/${chunkId}`)

export const searchCode = (q: string, mode: string, limit: number) =>
  apiFetch<SearchResponse>(
    `/search?q=${encodeURIComponent(q)}&mode=${encodeURIComponent(mode)}&limit=${limit}`
  )

export const fetchTasks = () => apiFetch<TaskInfo[]>('/tasks')

export const runOperation = (name: string, body?: Record<string, unknown>) =>
  apiFetch<RunResponse>(`/run/${name}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })

export const runQuery = (prompt: string, force?: boolean) =>
  apiFetch<RunResponse | QueryCachedResponse>('/run/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, ...(force ? { force: true } : {}) }),
  })

export const fetchQueryHistory = () =>
  apiFetch<QueryHistoryItem[]>('/queries')

export const fetchQueryDetail = (id: number) =>
  apiFetch<QueryHistoryDetail>(`/queries/${id}`)

export function createTaskStream(taskId: string): EventSource {
  return new EventSource(`${API_BASE}/tasks/${taskId}/stream`)
}
