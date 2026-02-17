import { useState, useRef, useEffect } from 'react'
import { useRunQuery, useTaskStream } from '../api/hooks.ts'
import type { StreamEvent } from '../api/hooks.ts'
import type { QueryResult, QueryEvidence } from '../api/client.ts'

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

export default function Query() {
  const [prompt, setPrompt] = useState('')
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const [result, setResult] = useState<QueryResult | null>(null)
  const runQuery = useRunQuery()
  const { events, done } = useTaskStream(activeTaskId)

  const isRunning = activeTaskId !== null && !done

  // Extract result from done event
  useEffect(() => {
    if (!done) return
    const doneEvent = events.find((e) => e.type === 'done')
    if (doneEvent?.result) {
      setResult(doneEvent.result as QueryResult)
    }
  }, [done, events])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!prompt.trim() || isRunning) return

    setResult(null)
    setActiveTaskId(null)
    runQuery.mutate(prompt.trim(), {
      onSuccess: (data) => {
        setActiveTaskId(data.task_id)
      },
    })
  }

  const hasError = done && events.some((e) => e.type === 'error')
  const errorMessage = hasError
    ? String(events.find((e) => e.type === 'error')?.error ?? 'Unknown error')
    : null

  return (
    <div>
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

      {/* Progress log */}
      {activeTaskId && (
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
      {hasError && (
        <div className="mb-6 bg-red-950/50 border border-red-800 rounded p-4 text-red-300 text-sm">
          {errorMessage}
        </div>
      )}

      {/* Answer */}
      {result && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
          <h3 className="text-sm font-semibold text-gray-400 mb-3">Answer</h3>
          <div className="text-gray-200 text-sm whitespace-pre-wrap leading-relaxed">
            {result.answer}
          </div>
          <EvidenceTrail evidence={result.evidence} />
        </div>
      )}
    </div>
  )
}
