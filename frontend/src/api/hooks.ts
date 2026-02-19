import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  fetchStats,
  fetchTree,
  fetchFileDetail,
  fetchChunkDetail,
  searchCode,
  fetchTasks,
  runOperation,
  runQuery,
  fetchStrategies,
  fetchQueryHistory,
  fetchQueryDetail,
  createTaskStream,
  fetchRepos,
  fetchRepo,
  createRepo,
  deleteRepo,
  checkRepoFreshness,
  syncRepo,
  type TaskInfo,
  type QueryCachedResponse,
  type RunResponse,
} from './client.ts'

export function useStats(repoId: number = 1) {
  return useQuery({ queryKey: ['stats', repoId], queryFn: () => fetchStats(repoId) })
}

export function useTree(path: string, repoId: number = 1) {
  return useQuery({ queryKey: ['tree', path, repoId], queryFn: () => fetchTree(path, repoId) })
}

export function useFileDetail(filePath: string, repoId: number = 1) {
  return useQuery({
    queryKey: ['file', filePath, repoId],
    queryFn: () => fetchFileDetail(filePath, repoId),
    enabled: !!filePath,
  })
}

export function useChunkDetail(chunkId: number, repoId: number = 1) {
  return useQuery({
    queryKey: ['chunk', chunkId, repoId],
    queryFn: () => fetchChunkDetail(chunkId, repoId),
    enabled: chunkId > 0,
  })
}

export function useSearch(q: string, mode: string, limit: number, repoId: number = 1) {
  return useQuery({
    queryKey: ['search', q, mode, limit, repoId],
    queryFn: () => searchCode(q, mode, limit, repoId),
    enabled: q.length > 0,
  })
}

export function useTasks() {
  return useQuery({
    queryKey: ['tasks'],
    queryFn: fetchTasks,
    refetchInterval: 3000,
  })
}

export function useRunOperation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body?: Record<string, unknown> }) =>
      runOperation(name, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })
}

export function useRunQuery() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ prompt, force, repoId, mode }: { prompt: string; force?: boolean; repoId?: number; mode?: string }) =>
      runQuery(prompt, force, repoId, mode),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })
}

export function useStrategies() {
  return useQuery({
    queryKey: ['strategies'],
    queryFn: fetchStrategies,
    staleTime: Infinity,
  })
}

export function useQueryHistory(repoId: number = 1) {
  return useQuery({
    queryKey: ['queryHistory', repoId],
    queryFn: () => fetchQueryHistory(repoId),
    refetchOnWindowFocus: true,
  })
}

export function useQueryDetail(id: number) {
  return useQuery({
    queryKey: ['queryDetail', id],
    queryFn: () => fetchQueryDetail(id),
    enabled: id > 0,
  })
}

export function useRepos() {
  return useQuery({ queryKey: ['repos'], queryFn: fetchRepos })
}

export function useRepo(repoId: number) {
  return useQuery({
    queryKey: ['repo', repoId],
    queryFn: () => fetchRepo(repoId),
    enabled: repoId > 0,
  })
}

export function useCreateRepo() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ name, url, shallow }: { name: string; url: string; shallow?: boolean }) =>
      createRepo(name, url, shallow),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['repos'] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })
}

export function useDeleteRepo() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (repoId: number) => deleteRepo(repoId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['repos'] })
    },
  })
}

export function useCheckFreshness() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (repoId: number) => checkRepoFreshness(repoId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['repos'] })
    },
  })
}

export function useSyncRepo() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (repoId: number) => syncRepo(repoId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['repos'] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })
}

export interface StreamEvent {
  type: string
  [key: string]: unknown
}

export function useTaskStream(taskId: string | null) {
  const [events, setEvents] = useState<StreamEvent[]>([])
  const [done, setDone] = useState(false)
  const esRef = useRef<EventSource | null>(null)

  const reset = useCallback(() => {
    setEvents([])
    setDone(false)
  }, [])

  useEffect(() => {
    if (!taskId) return

    reset()
    const es = createTaskStream(taskId)
    esRef.current = es

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data) as StreamEvent
        setEvents((prev) => [...prev, data])
        if (data.type === 'done' || data.type === 'error') {
          setDone(true)
          es.close()
        }
      } catch {
        // ignore parse errors
      }
    }

    es.onerror = () => {
      setDone(true)
      es.close()
    }

    return () => {
      es.close()
    }
  }, [taskId, reset])

  return { events, done }
}

// Re-export types for convenience
export type { TaskInfo, QueryCachedResponse, RunResponse }
