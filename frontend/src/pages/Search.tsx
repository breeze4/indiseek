import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useSearch } from '../api/hooks.ts'

export default function SearchPage() {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState('hybrid')
  const [submitted, setSubmitted] = useState('')

  const { data, isLoading, error } = useSearch(submitted, mode, 20)

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitted(query)
  }

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Search Code</h2>

      <form onSubmit={handleSubmit} className="flex gap-2 mb-4">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search query..."
          className="flex-1 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
        />
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value)}
          className="bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white"
        >
          <option value="hybrid">Hybrid</option>
          <option value="lexical">Lexical</option>
          <option value="semantic">Semantic</option>
        </select>
        <button
          type="submit"
          className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm"
        >
          Search
        </button>
      </form>

      {isLoading && <p className="text-gray-400">Searching...</p>}
      {error && <p className="text-red-400">Error: {(error as Error).message}</p>}

      {data && (
        <div>
          <p className="text-sm text-gray-400 mb-3">
            {data.results.length} result(s) for &ldquo;{data.query}&rdquo; ({data.mode})
          </p>
          <div className="space-y-2">
            {data.results.map((r, i) => (
              <Link
                key={`${r.chunk_id}-${i}`}
                to={`/chunks/${r.chunk_id}`}
                className="block bg-gray-900 border border-gray-800 rounded-lg p-3 hover:border-gray-600 transition"
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-sm text-gray-300">{r.file_path}</span>
                  {r.symbol_name && (
                    <span className="text-sm font-mono text-blue-400">{r.symbol_name}</span>
                  )}
                  <span className="text-xs bg-gray-800 text-gray-400 px-1.5 py-0.5 rounded">
                    {r.chunk_type}
                  </span>
                  <span className="text-xs text-gray-500 ml-auto">
                    {r.match_type} &middot; {r.score.toFixed(4)}
                  </span>
                </div>
                <pre className="text-xs text-gray-400 overflow-hidden max-h-16 font-mono">
                  {r.content.slice(0, 300)}
                </pre>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
