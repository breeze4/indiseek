import { useState } from 'react'
import { useStats, useRepo, useCheckFreshness, useSyncRepo, useTaskStream } from '../api/hooks.ts'
import { useCurrentRepo } from '../contexts/RepoContext.tsx'
import type { FreshnessCheck } from '../api/client.ts'

function StatCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <h3 className="text-sm font-medium text-gray-400 mb-3">{title}</h3>
      {children}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex justify-between py-1">
      <span className="text-gray-400 text-sm">{label}</span>
      <span className="text-white font-mono text-sm">{value}</span>
    </div>
  )
}

function CoverageBar({ label, filled, total }: { label: string; filled: number; total: number }) {
  const pct = total > 0 ? Math.round((filled / total) * 100) : 0
  return (
    <div className="mb-2">
      <div className="flex justify-between text-sm mb-1">
        <span className="text-gray-400">{label}</span>
        <span className="text-gray-300">{filled}/{total} ({pct}%)</span>
      </div>
      <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-500 rounded-full transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

function FreshnessCard({ repoId }: { repoId: number }) {
  const { data: repo } = useRepo(repoId)
  const checkMut = useCheckFreshness()
  const syncMut = useSyncRepo()
  const [freshness, setFreshness] = useState<FreshnessCheck | null>(null)
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const { events, done } = useTaskStream(activeTaskId)

  if (!repo) return null

  const stale = freshness
    ? freshness.commits_behind > 0
    : repo.commits_behind > 0

  return (
    <div className={`bg-gray-900 border rounded-lg p-4 mb-6 ${stale ? 'border-yellow-800' : 'border-gray-800'}`}>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-medium text-gray-400">Freshness</h3>
        <div className="flex gap-2">
          <button
            onClick={() => {
              checkMut.mutate(repoId, {
                onSuccess: (data) => setFreshness(data),
              })
            }}
            disabled={checkMut.isPending}
            className={`px-3 py-1 rounded text-xs font-medium ${
              checkMut.isPending
                ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
                : 'bg-gray-700 hover:bg-gray-600 text-white'
            }`}
          >
            {checkMut.isPending ? 'Checking...' : 'Check'}
          </button>
          <button
            onClick={() => {
              syncMut.mutate(repoId, {
                onSuccess: (data) => {
                  if ('task_id' in data) setActiveTaskId(data.task_id)
                },
              })
            }}
            disabled={syncMut.isPending}
            className={`px-3 py-1 rounded text-xs font-medium ${
              syncMut.isPending
                ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
                : 'bg-blue-600 hover:bg-blue-700 text-white'
            }`}
          >
            {syncMut.isPending ? 'Syncing...' : 'Sync'}
          </button>
        </div>
      </div>

      {stale && (
        <div className="bg-yellow-900/30 border border-yellow-800/50 rounded px-3 py-2 text-yellow-300 text-xs mb-2">
          Index is behind remote. Run Sync to update.
        </div>
      )}

      <div className="text-sm text-gray-400 space-y-1">
        {repo.indexed_commit_sha && (
          <div>Indexed SHA: <code className="text-gray-300">{repo.indexed_commit_sha.slice(0, 7)}</code></div>
        )}
        {freshness && (
          <>
            <div>Current SHA: <code className="text-gray-300">{freshness.current_sha.slice(0, 7)}</code></div>
            <div>Commits behind: <span className="text-gray-300">{freshness.commits_behind === -1 ? 'unknown' : freshness.commits_behind}</span></div>
          </>
        )}
        {repo.last_indexed_at && (
          <div>Last indexed: <span className="text-gray-300">{new Date(repo.last_indexed_at).toLocaleString()}</span></div>
        )}
      </div>

      {activeTaskId && (
        <div className="mt-3 bg-gray-950 border border-gray-800 rounded p-2 max-h-32 overflow-y-auto font-mono text-xs text-gray-400">
          {events.map((e, i) => (
            <div key={i}>
              {e.type === 'progress' && <span>[{String(e.step)}] {String(e.status || e.current)}</span>}
              {e.type === 'done' && <span className="text-green-400">Done</span>}
              {e.type === 'error' && <span className="text-red-400">Error: {String(e.error)}</span>}
            </div>
          ))}
          {done && <div className="text-gray-600 mt-1">Stream ended</div>}
        </div>
      )}
    </div>
  )
}

export default function Overview() {
  const { currentRepoId } = useCurrentRepo()
  const { data: stats, isLoading, error } = useStats(currentRepoId)

  if (isLoading) return <p className="text-gray-400">Loading stats...</p>
  if (error) return <p className="text-red-400">Error: {(error as Error).message}</p>
  if (!stats) return null

  const sq = stats.sqlite
  const filesParsed = sq.files_parsed ?? 0
  const chunks = sq.chunks ?? 0
  const summaries = sq.file_summaries ?? 0
  const embeddedChunks = stats.lancedb.embedded_chunks ?? 0

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Pipeline Overview</h2>

      <FreshnessCard repoId={currentRepoId} />

      {/* Coverage bars */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 mb-6">
        <h3 className="text-sm font-medium text-gray-400 mb-3">Coverage</h3>
        <CoverageBar label="Summarized" filled={summaries} total={filesParsed} />
        <CoverageBar label="Embedded" filled={embeddedChunks} total={chunks} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard title="SQLite">
          {!sq.available ? (
            <p className="text-gray-500 text-sm">Not available</p>
          ) : sq.error ? (
            <p className="text-red-400 text-sm">{sq.error}</p>
          ) : (
            <>
              <Stat label="Files parsed" value={filesParsed} />
              <Stat label="Chunks" value={chunks} />
              <Stat label="Symbols" value={sq.symbols ?? 0} />
              <Stat label="SCIP symbols" value={sq.scip_symbols ?? 0} />
              <Stat label="SCIP occurrences" value={sq.scip_occurrences ?? 0} />
              <Stat label="File summaries" value={summaries} />
              <Stat label="Dir summaries" value={sq.directory_summaries ?? 0} />
            </>
          )}
        </StatCard>

        <StatCard title="LanceDB (Embeddings)">
          {!stats.lancedb.available ? (
            <p className="text-gray-500 text-sm">Not available</p>
          ) : (
            <Stat label="Embedded chunks" value={embeddedChunks} />
          )}
        </StatCard>

        <StatCard title="Tantivy (Lexical)">
          {!stats.tantivy.available ? (
            <p className="text-gray-500 text-sm">Not available</p>
          ) : (
            <Stat label="Indexed docs" value={stats.tantivy.indexed_docs ?? 0} />
          )}
        </StatCard>
      </div>
    </div>
  )
}
