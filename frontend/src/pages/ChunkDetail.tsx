import { useParams, Link } from 'react-router-dom'
import { useChunkDetail } from '../api/hooks.ts'
import { useCurrentRepo } from '../contexts/RepoContext.tsx'

export default function ChunkDetail() {
  const { id } = useParams<{ id: string }>()
  const { currentRepoId } = useCurrentRepo()
  const chunkId = parseInt(id ?? '0', 10)
  const { data, isLoading, error } = useChunkDetail(chunkId, currentRepoId)

  if (isLoading) return <p className="text-gray-400">Loading...</p>
  if (error) return <p className="text-red-400">Error: {(error as Error).message}</p>
  if (!data) return <p className="text-gray-400">Chunk not found</p>

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">
        Chunk #{data.id}
        {data.symbol_name && (
          <span className="text-blue-400 ml-2 font-mono">{data.symbol_name}</span>
        )}
      </h2>

      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 mb-4">
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div>
            <span className="text-gray-400">File:</span>{' '}
            <Link to={`/files/${data.file_path}`} className="text-blue-400 hover:underline">
              {data.file_path}
            </Link>
          </div>
          <div>
            <span className="text-gray-400">Type:</span>{' '}
            <span className="text-gray-200">{data.chunk_type}</span>
          </div>
          <div>
            <span className="text-gray-400">Lines:</span>{' '}
            <span className="text-gray-200">{data.start_line}-{data.end_line}</span>
          </div>
          <div>
            <span className="text-gray-400">Tokens:</span>{' '}
            <span className="text-gray-200">{data.token_estimate ?? 'N/A'}</span>
          </div>
          <div>
            <span className="text-gray-400">Embedded:</span>{' '}
            <span className={data.embedded ? 'text-green-400' : 'text-gray-500'}>
              {data.embedded ? 'Yes' : 'No'}
            </span>
          </div>
        </div>
      </div>

      <h3 className="text-lg font-semibold mb-2">Source</h3>
      <pre className="bg-gray-900 border border-gray-800 rounded-lg p-4 text-sm font-mono text-gray-300 overflow-x-auto whitespace-pre-wrap">
        {data.content}
      </pre>
    </div>
  )
}
