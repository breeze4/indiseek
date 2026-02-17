import { useState, useRef, useEffect } from 'react'
import { useTasks, useRunOperation, useTaskStream } from '../api/hooks.ts'
import type { StreamEvent } from '../api/hooks.ts'

const OPERATIONS = [
  {
    name: 'treesitter',
    label: 'Tree-sitter Parse',
    description: 'Parse .ts/.tsx files and extract symbols/chunks.',
    hasFilter: true,
  },
  {
    name: 'scip',
    label: 'SCIP Cross-refs',
    description: 'Load SCIP index for go-to-definition and references.',
    hasFilter: false,
  },
  {
    name: 'embed',
    label: 'Embed Chunks',
    description: 'Generate semantic embeddings via Gemini API.',
    hasFilter: true,
  },
  {
    name: 'summarize',
    label: 'Summarize Files',
    description: 'LLM-summarize each file for the directory map.',
    hasFilter: true,
  },
  {
    name: 'lexical',
    label: 'Lexical Index',
    description: 'Build Tantivy BM25 full-text search index.',
    hasFilter: false,
  },
]

function ProgressLog({ events }: { events: StreamEvent[] }) {
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events.length])

  if (events.length === 0) return null

  return (
    <div className="mt-3 bg-gray-950 border border-gray-800 rounded p-3 max-h-48 overflow-y-auto font-mono text-xs text-gray-400">
      {events.map((e, i) => (
        <div key={i}>
          {e.type === 'progress' && (
            <span>
              [{String(e.step)}] {String(e.current)}/{String(e.total)}
              {e.file ? ` ${String(e.file)}` : ''}
              {e.batch ? ` batch ${String(e.batch)}/${String(e.total_batches)}` : ''}
            </span>
          )}
          {e.type === 'done' && (
            <span className="text-green-400">
              Done: {JSON.stringify(e.result)}
            </span>
          )}
          {e.type === 'error' && (
            <span className="text-red-400">
              Error: {String(e.error)}
            </span>
          )}
        </div>
      ))}
      <div ref={endRef} />
    </div>
  )
}

function OperationCard({
  name,
  label,
  description,
  hasFilter,
  disabled,
  onRun,
}: {
  name: string
  label: string
  description: string
  hasFilter: boolean
  disabled: boolean
  onRun: (name: string, pathFilter?: string) => void
}) {
  const [filter, setFilter] = useState('')

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <div className="flex items-start justify-between mb-2">
        <div>
          <h3 className="font-semibold text-white">{label}</h3>
          <p className="text-sm text-gray-400">{description}</p>
        </div>
        <button
          onClick={() => onRun(name, filter || undefined)}
          disabled={disabled}
          className={`px-3 py-1.5 rounded text-sm font-medium ${
            disabled
              ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
              : 'bg-blue-600 hover:bg-blue-700 text-white'
          }`}
        >
          Run
        </button>
      </div>
      {hasFilter && (
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Path filter (optional, e.g. packages/vite/src)"
          className="w-full bg-gray-950 border border-gray-700 rounded px-2 py-1 text-sm text-gray-300 placeholder-gray-600 mt-1"
        />
      )}
    </div>
  )
}

export default function Operations() {
  const { data: tasks } = useTasks()
  const runOp = useRunOperation()
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const { events, done } = useTaskStream(activeTaskId)

  const anyRunning = tasks?.some((t) => t.status === 'running') ?? false

  function handleRun(name: string, pathFilter?: string) {
    const body: Record<string, unknown> = {}
    if (pathFilter) {
      body.path_filter = pathFilter
    }
    runOp.mutate(
      { name, body },
      {
        onSuccess: (data) => {
          setActiveTaskId(data.task_id)
        },
      },
    )
  }

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Indexing Operations</h2>

      <div className="space-y-3 mb-6">
        {OPERATIONS.map((op) => (
          <OperationCard
            key={op.name}
            {...op}
            disabled={anyRunning || runOp.isPending}
            onRun={handleRun}
          />
        ))}
      </div>

      {runOp.isError && (
        <p className="text-red-400 text-sm mb-4">
          Failed to start: {(runOp.error as Error).message}
        </p>
      )}

      {activeTaskId && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <h3 className="font-semibold text-white">Live Progress</h3>
            {!done && (
              <span className="inline-block w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
            )}
            {done && events.some((e) => e.type === 'done') && (
              <span className="text-green-400 text-sm">Completed</span>
            )}
            {done && events.some((e) => e.type === 'error') && (
              <span className="text-red-400 text-sm">Failed</span>
            )}
          </div>
          <ProgressLog events={events} />
        </div>
      )}

      {tasks && tasks.length > 0 && (
        <div className="mt-6">
          <h3 className="text-lg font-semibold mb-2">Task History</h3>
          <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-gray-400">
                  <th className="text-left p-2">Name</th>
                  <th className="text-left p-2">Status</th>
                  <th className="text-left p-2">Result</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((t) => (
                  <tr key={t.id} className="border-b border-gray-800/50">
                    <td className="p-2 text-gray-300">{t.name}</td>
                    <td className="p-2">
                      <span
                        className={`text-xs px-1.5 py-0.5 rounded ${
                          t.status === 'completed'
                            ? 'bg-green-900 text-green-300'
                            : t.status === 'running'
                              ? 'bg-blue-900 text-blue-300'
                              : 'bg-red-900 text-red-300'
                        }`}
                      >
                        {t.status}
                      </span>
                    </td>
                    <td className="p-2 text-gray-500 text-xs font-mono truncate max-w-md">
                      {t.result ? JSON.stringify(t.result) : t.error ? t.error.slice(0, 100) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
