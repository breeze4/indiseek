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
  createTaskStream,
  type TaskInfo,
} from './client.ts'

export function useStats() {
  return useQuery({ queryKey: ['stats'], queryFn: fetchStats })
}

export function useTree(path: string) {
  return useQuery({ queryKey: ['tree', path], queryFn: () => fetchTree(path) })
}

export function useFileDetail(filePath: string) {
  return useQuery({
    queryKey: ['file', filePath],
    queryFn: () => fetchFileDetail(filePath),
    enabled: !!filePath,
  })
}

export function useChunkDetail(chunkId: number) {
  return useQuery({
    queryKey: ['chunk', chunkId],
    queryFn: () => fetchChunkDetail(chunkId),
    enabled: chunkId > 0,
  })
}

export function useSearch(q: string, mode: string, limit: number) {
  return useQuery({
    queryKey: ['search', q, mode, limit],
    queryFn: () => searchCode(q, mode, limit),
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
export type { TaskInfo }
