import { useStats } from '../api/hooks.ts'

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

export default function Overview() {
  const { data: stats, isLoading, error } = useStats()

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
