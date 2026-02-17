import { useLocation, Link } from 'react-router-dom'
import { useFileDetail } from '../api/hooks.ts'

export default function FileDetail() {
  const location = useLocation()
  // Extract file path from URL: /files/path/to/file.ts
  const filePath = location.pathname.replace(/^\/files\//, '')
  const { data, isLoading, error } = useFileDetail(filePath)

  if (isLoading) return <p className="text-gray-400">Loading...</p>
  if (error) return <p className="text-red-400">Error: {(error as Error).message}</p>
  if (!data) return <p className="text-gray-400">No data</p>

  return (
    <div>
      <h2 className="text-xl font-bold mb-1">{filePath}</h2>

      {data.summary && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 mb-4">
          <p className="text-gray-300 text-sm">{data.summary.summary}</p>
          <p className="text-gray-500 text-xs mt-1">
            {data.summary.language} &middot; {data.summary.line_count} lines
          </p>
        </div>
      )}

      <h3 className="text-lg font-semibold mb-2">Chunks ({data.chunks.length})</h3>
      <div className="space-y-2 mb-6">
        {data.chunks.map((c) => (
          <Link
            key={c.id}
            to={`/chunks/${c.id}`}
            className="block bg-gray-900 border border-gray-800 rounded-lg p-3 hover:border-gray-600 transition"
          >
            <div className="flex items-center gap-2 mb-1">
              <span className="text-sm font-mono text-blue-400">
                {c.symbol_name || '(module)'}
              </span>
              <span className="text-xs bg-gray-800 text-gray-400 px-1.5 py-0.5 rounded">
                {c.chunk_type}
              </span>
              <span className="text-xs text-gray-500">
                L{c.start_line}-{c.end_line}
              </span>
              <span
                className={`text-xs px-1.5 py-0.5 rounded ml-auto ${
                  c.embedded ? 'bg-green-900 text-green-300' : 'bg-gray-800 text-gray-500'
                }`}
              >
                {c.embedded ? 'Embedded' : 'Not embedded'}
              </span>
            </div>
            <pre className="text-xs text-gray-400 overflow-hidden max-h-16 font-mono">
              {c.content.slice(0, 200)}
            </pre>
          </Link>
        ))}
        {data.chunks.length === 0 && (
          <p className="text-gray-500 text-sm">No chunks for this file.</p>
        )}
      </div>

      {data.symbols.length > 0 && (
        <>
          <h3 className="text-lg font-semibold mb-2">Symbols ({data.symbols.length})</h3>
          <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-gray-400">
                  <th className="text-left p-2">Name</th>
                  <th className="text-left p-2">Kind</th>
                  <th className="text-left p-2">Lines</th>
                  <th className="text-left p-2">Signature</th>
                </tr>
              </thead>
              <tbody>
                {data.symbols.map((s) => (
                  <tr key={s.id} className="border-b border-gray-800/50">
                    <td className="p-2 font-mono text-blue-400">{s.name}</td>
                    <td className="p-2 text-gray-400">{s.kind}</td>
                    <td className="p-2 text-gray-500">{s.start_line}-{s.end_line}</td>
                    <td className="p-2 text-gray-500 font-mono text-xs truncate max-w-md">
                      {s.signature}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
