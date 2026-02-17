import { useState, useRef, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useRunQuery, useTaskStream, useQueryHistory, useQueryDetail } from '../api/hooks.ts'
import type { StreamEvent } from '../api/hooks.ts'
import type { QueryResult, QueryEvidence, QueryCachedResponse } from '../api/client.ts'

function ProgressLog({ events }: { events: StreamEvent[] }) {
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events.length])

  const progressEvents = events.filter((e) => e.type === 'progress')
  if (progressEvents.length === 0) return null

  return (
    <div className="bg-gray-950 border border-gray-800 rounded p-3 max-h-64 overflow-y-auto font-mono text-xs text-gray-400">
      {progressEvents.map((e, i) => (
        <div key={i} className="py-0.5">
          <span className="text-gray-600">[{String(e.iteration)}]</span>{' '}
          <span className="text-blue-400">{String(e.tool)}</span>
          <span className="text-gray-500">
            ({Object.entries((e.args ?? {}) as Record<string, unknown>)
              .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
              .join(', ')})
          </span>
          {e.summary != null && (
            <div className="ml-4 text-gray-500">{String(e.summary)}</div>
          )}
        </div>
      ))}
      <div ref={endRef} />
    </div>
  )
}

function EvidenceTrail({ evidence }: { evidence: QueryEvidence[] }) {
  const [open, setOpen] = useState(false)

  if (evidence.length === 0) return null

  return (
    <div className="mt-4">
      <button
        onClick={() => setOpen(!open)}
        className="text-sm text-gray-400 hover:text-gray-200 flex items-center gap-1"
      >
        <span className={`transition-transform ${open ? 'rotate-90' : ''}`}>&#9654;</span>
        Evidence trail ({evidence.length} tool calls)
      </button>
      {open && (
        <div className="mt-2 bg-gray-950 border border-gray-800 rounded p-3 space-y-2 font-mono text-xs max-h-96 overflow-y-auto">
          {evidence.map((e, i) => (
            <div key={i} className="border-b border-gray-800/50 pb-2 last:border-0">
              <div>
                <span className="text-blue-400">{e.tool}</span>
                <span className="text-gray-500">
                  ({Object.entries(e.args)
                    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
                    .join(', ')})
                </span>
              </div>
              <div className="text-gray-500 mt-0.5 whitespace-pre-wrap">{e.summary}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === 'completed'
      ? 'bg-green-900/50 text-green-400'
      : status === 'failed'
        ? 'bg-red-900/50 text-red-400'
        : status === 'cached'
          ? 'bg-purple-900/50 text-purple-400'
          : 'bg-blue-900/50 text-blue-400'
  return <span className={`text-[10px] px-1.5 py-0.5 rounded ${cls}`}>{status}</span>
}

export default function Query() {
  const [prompt, setPrompt] = useState('')
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const [result, setResult] = useState<QueryResult | null>(null)
  const [selectedQueryId, setSelectedQueryId] = useState<number>(0)
  const [isCachedResult, setIsCachedResult] = useState(false)
  const runQuery = useRunQuery()
  const { events, done } = useTaskStream(activeTaskId)
  const queryClient = useQueryClient()
  const { data: history } = useQueryHistory()
  const { data: historyDetail } = useQueryDetail(selectedQueryId)

  const isRunning = activeTaskId !== null && !done

  // Extract result from done event
  useEffect(() => {
    if (!done) return
    const doneEvent = events.find((e) => e.type === 'done')
    if (doneEvent?.result) {
      setResult(doneEvent.result as QueryResult)
    }
    // Refresh history list when a query completes
    queryClient.invalidateQueries({ queryKey: ['queryHistory'] })
  }, [done, events, queryClient])

  function handleSubmit(e: React.FormEvent, force?: boolean) {
    e.preventDefault()
    if (!prompt.trim() || isRunning) return

    setResult(null)
    setActiveTaskId(null)
    setSelectedQueryId(0)
    setIsCachedResult(false)
    runQuery.mutate({ prompt: prompt.trim(), force }, {
      onSuccess: (data) => {
        if ('cached' in data && data.cached) {
          const cached = data as QueryCachedResponse
          setResult({ answer: cached.answer, evidence: cached.evidence })
          setIsCachedResult(true)
          queryClient.invalidateQueries({ queryKey: ['queryHistory'] })
        } else {
          setActiveTaskId((data as { task_id: string }).task_id)
          queryClient.invalidateQueries({ queryKey: ['queryHistory'] })
        }
      },
    })
  }

  function handleSelectHistory(id: number) {
    // Clear active task state, load from history
    setActiveTaskId(null)
    setResult(null)
    setSelectedQueryId(id)
    setIsCachedResult(false)
    // Set prompt from history so Re-run works
    const item = history?.find((q) => q.id === id)
    if (item) setPrompt(item.prompt)
  }

  // When history detail loads, show it as the result
  const displayResult = selectedQueryId > 0 && historyDetail
    ? historyDetail.answer
      ? { answer: historyDetail.answer, evidence: historyDetail.evidence ?? [] }
      : null
    : result

  const displayError = selectedQueryId > 0 && historyDetail?.status === 'failed'
    ? historyDetail.error ?? 'Unknown error'
    : null

  const showCached = isCachedResult ||
    (selectedQueryId > 0 && historyDetail?.status === 'cached')

  const hasError = selectedQueryId === 0
    ? done && events.some((e) => e.type === 'error')
    : displayError !== null
  const errorMessage = selectedQueryId === 0
    ? hasError
      ? String(events.find((e) => e.type === 'error')?.error ?? 'Unknown error')
      : null
    : displayError

  return (
    <div className="flex gap-6">
      {/* History sidebar */}
      <div className="w-64 shrink-0">
        <h3 className="text-sm font-semibold text-gray-400 mb-3">History</h3>
        <div className="space-y-1 max-h-[calc(100vh-12rem)] overflow-y-auto">
          {history && history.length > 0 ? (
            history.map((q) => (
              <button
                key={q.id}
                onClick={() => handleSelectHistory(q.id)}
                className={`w-full text-left px-3 py-2 rounded text-sm transition-colors ${
                  selectedQueryId === q.id
                    ? 'bg-gray-800 text-gray-200'
                    : 'text-gray-400 hover:bg-gray-800/50 hover:text-gray-300'
                }`}
              >
                <div className="truncate text-xs">{q.prompt}</div>
                <div className="flex items-center gap-2 mt-1">
                  <StatusBadge status={q.status} />
                  <span className="text-[10px] text-gray-600">{timeAgo(q.created_at)}</span>
                  {q.duration_secs != null && (
                    <span className="text-[10px] text-gray-600">{q.duration_secs.toFixed(1)}s</span>
                  )}
                </div>
              </button>
            ))
          ) : (
            <p className="text-xs text-gray-600 px-3">No queries yet</p>
          )}
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 min-w-0">
        <h2 className="text-xl font-bold mb-4">Query</h2>

        <form onSubmit={handleSubmit} className="mb-6">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Ask a question about the codebase..."
            disabled={isRunning}
            rows={3}
            className="w-full bg-gray-950 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 placeholder-gray-600 resize-y disabled:opacity-50"
          />
          <div className="flex items-center gap-3 mt-2">
            <button
              type="submit"
              disabled={!prompt.trim() || isRunning}
              className={`px-4 py-1.5 rounded text-sm font-medium ${
                !prompt.trim() || isRunning
                  ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
                  : 'bg-blue-600 hover:bg-blue-700 text-white'
              }`}
            >
              {isRunning ? 'Running...' : 'Submit'}
            </button>
            {isRunning && (
              <span className="inline-block w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
            )}
            {runQuery.isError && (
              <span className="text-red-400 text-sm">
                {(runQuery.error as Error).message}
              </span>
            )}
          </div>
        </form>

        {/* Progress log (only for active task, not history) */}
        {activeTaskId && selectedQueryId === 0 && (
          <div className="mb-6">
            <div className="flex items-center gap-2 mb-2">
              <h3 className="text-sm font-semibold text-gray-400">Progress</h3>
              {isRunning && (
                <span className="inline-block w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
              )}
              {done && !hasError && (
                <span className="text-green-400 text-xs">Complete</span>
              )}
              {hasError && (
                <span className="text-red-400 text-xs">Failed</span>
              )}
            </div>
            <ProgressLog events={events} />
          </div>
        )}

        {/* Error */}
        {hasError && errorMessage && (
          <div className="mb-6 bg-red-950/50 border border-red-800 rounded p-4 text-red-300 text-sm">
            {errorMessage}
          </div>
        )}

        {/* Answer */}
        {displayResult && (
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
            <div className="flex items-center gap-3 mb-3">
              <h3 className="text-sm font-semibold text-gray-400">Answer</h3>
              {showCached && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-900/50 text-purple-400">
                  cached
                </span>
              )}
            </div>
            <div className="text-gray-200 text-sm whitespace-pre-wrap leading-relaxed">
              {displayResult.answer}
            </div>
            <EvidenceTrail evidence={displayResult.evidence} />
            {showCached && (
              <button
                onClick={(e) => handleSubmit(e, true)}
                disabled={isRunning}
                className="mt-4 px-3 py-1 rounded text-xs font-medium bg-gray-800 hover:bg-gray-700 text-gray-300 disabled:opacity-50"
              >
                Re-run without cache
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
