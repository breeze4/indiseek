import { useState } from 'react'
import {
  useRepos,
  useCreateRepo,
  useDeleteRepo,
  useCheckFreshness,
  useSyncRepo,
  useTaskStream,
} from '../api/hooks.ts'
import type { Repo, FreshnessCheck } from '../api/client.ts'

function StatusBadge({ status }: { status: string }) {
  const colors =
    status === 'active'
      ? 'bg-green-900 text-green-300'
      : 'bg-gray-800 text-gray-400'
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded ${colors}`}>{status}</span>
  )
}

function FreshnessBadge({ repo, freshness }: { repo: Repo; freshness?: FreshnessCheck | null }) {
  if (freshness) {
    if (freshness.commits_behind === 0) {
      return <span className="text-xs px-1.5 py-0.5 rounded bg-green-900 text-green-300">up to date</span>
    }
    const label = freshness.commits_behind === -1
      ? 'unknown commits behind'
      : `${freshness.commits_behind} behind`
    return <span className="text-xs px-1.5 py-0.5 rounded bg-yellow-900 text-yellow-300">{label}</span>
  }
  if (repo.commits_behind > 0) {
    return <span className="text-xs px-1.5 py-0.5 rounded bg-yellow-900 text-yellow-300">{repo.commits_behind} behind</span>
  }
  if (!repo.indexed_commit_sha) {
    return <span className="text-xs px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">not indexed</span>
  }
  return null
}

function RepoCard({
  repo,
  onDelete,
  onCheck,
  onSync,
  checking,
  syncing,
  freshness,
  disabled,
}: {
  repo: Repo
  onDelete: (id: number) => void
  onCheck: (id: number) => void
  onSync: (id: number) => void
  checking: boolean
  syncing: boolean
  freshness?: FreshnessCheck | null
  disabled: boolean
}) {
  const [confirmDelete, setConfirmDelete] = useState(false)

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <div className="flex items-start justify-between mb-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-1">
            <h3 className="font-semibold text-white truncate">{repo.name}</h3>
            <StatusBadge status={repo.status} />
            <FreshnessBadge repo={repo} freshness={freshness} />
          </div>
          {repo.url && (
            <p className="text-xs text-gray-500 truncate">{repo.url}</p>
          )}
          <p className="text-xs text-gray-600 truncate">{repo.local_path}</p>
        </div>
      </div>

      <div className="flex items-center gap-4 text-xs text-gray-500 mb-3">
        {repo.indexed_commit_sha && (
          <span>SHA: <code className="text-gray-400">{repo.indexed_commit_sha.slice(0, 7)}</code></span>
        )}
        {repo.last_indexed_at && (
          <span>Indexed: {new Date(repo.last_indexed_at).toLocaleDateString()}</span>
        )}
      </div>

      {freshness && freshness.changed_files.length > 0 && (
        <div className="mb-3 bg-gray-950 border border-gray-800 rounded p-2 max-h-32 overflow-y-auto">
          <p className="text-xs text-gray-400 mb-1">{freshness.changed_files.length} changed files:</p>
          {freshness.changed_files.map((f) => (
            <div key={f} className="text-xs text-gray-500 font-mono truncate">{f}</div>
          ))}
        </div>
      )}

      <div className="flex gap-2">
        <button
          onClick={() => onCheck(repo.id)}
          disabled={disabled || checking}
          className={`px-3 py-1.5 rounded text-sm font-medium ${
            disabled || checking
              ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
              : 'bg-gray-700 hover:bg-gray-600 text-white'
          }`}
        >
          {checking ? 'Checking...' : 'Check'}
        </button>
        <button
          onClick={() => onSync(repo.id)}
          disabled={disabled || syncing}
          className={`px-3 py-1.5 rounded text-sm font-medium ${
            disabled || syncing
              ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
              : 'bg-blue-600 hover:bg-blue-700 text-white'
          }`}
        >
          {syncing ? 'Syncing...' : 'Sync'}
        </button>
        <a
          href={`/dashboard/files`}
          className="px-3 py-1.5 rounded text-sm font-medium bg-gray-700 hover:bg-gray-600 text-white"
        >
          View
        </a>
        <div className="flex-1" />
        {!confirmDelete ? (
          <button
            onClick={() => setConfirmDelete(true)}
            className="px-3 py-1.5 rounded text-sm font-medium text-red-400 hover:bg-red-900/30"
          >
            Delete
          </button>
        ) : (
          <div className="flex gap-1">
            <button
              onClick={() => { onDelete(repo.id); setConfirmDelete(false) }}
              className="px-3 py-1.5 rounded text-sm font-medium bg-red-700 hover:bg-red-600 text-white"
            >
              Confirm
            </button>
            <button
              onClick={() => setConfirmDelete(false)}
              className="px-3 py-1.5 rounded text-sm font-medium text-gray-400 hover:text-white"
            >
              Cancel
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

function AddRepoForm({ onSubmit, disabled }: { onSubmit: (name: string, url: string) => void; disabled: boolean }) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim() || !url.trim()) return
    onSubmit(name.trim(), url.trim())
    setName('')
    setUrl('')
  }

  return (
    <form onSubmit={handleSubmit} className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <h3 className="font-semibold text-white mb-3">Add Repository</h3>
      <div className="flex gap-2">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name"
          className="bg-gray-950 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-300 placeholder-gray-600 w-40"
        />
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="Git URL (https://...)"
          className="flex-1 bg-gray-950 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-300 placeholder-gray-600"
        />
        <button
          type="submit"
          disabled={disabled || !name.trim() || !url.trim()}
          className={`px-4 py-1.5 rounded text-sm font-medium ${
            disabled || !name.trim() || !url.trim()
              ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
              : 'bg-blue-600 hover:bg-blue-700 text-white'
          }`}
        >
          Clone
        </button>
      </div>
    </form>
  )
}

export default function Repos() {
  const { data: repos, isLoading, error } = useRepos()
  const createMut = useCreateRepo()
  const deleteMut = useDeleteRepo()
  const checkMut = useCheckFreshness()
  const syncMut = useSyncRepo()
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const { events, done } = useTaskStream(activeTaskId)
  const [freshnessResults, setFreshnessResults] = useState<Record<number, FreshnessCheck>>({})
  const [checkingId, setCheckingId] = useState<number | null>(null)
  const [syncingId, setSyncingId] = useState<number | null>(null)

  function handleCreate(name: string, url: string) {
    createMut.mutate(
      { name, url },
      {
        onSuccess: (data) => {
          if ('task_id' in data) {
            setActiveTaskId(data.task_id)
          }
        },
      },
    )
  }

  function handleDelete(repoId: number) {
    deleteMut.mutate(repoId)
  }

  function handleCheck(repoId: number) {
    setCheckingId(repoId)
    checkMut.mutate(repoId, {
      onSuccess: (data) => {
        setFreshnessResults((prev) => ({ ...prev, [repoId]: data }))
        setCheckingId(null)
      },
      onSettled: () => setCheckingId(null),
    })
  }

  function handleSync(repoId: number) {
    setSyncingId(repoId)
    syncMut.mutate(repoId, {
      onSuccess: (data) => {
        if ('task_id' in data) {
          setActiveTaskId(data.task_id)
        }
        setSyncingId(null)
      },
      onSettled: () => setSyncingId(null),
    })
  }

  if (isLoading) return <p className="text-gray-400">Loading...</p>
  if (error) return <p className="text-red-400">Error: {(error as Error).message}</p>

  const anyMutating = createMut.isPending || syncMut.isPending

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Repositories</h2>

      <div className="mb-4">
        <AddRepoForm onSubmit={handleCreate} disabled={anyMutating} />
      </div>

      {createMut.isError && (
        <p className="text-red-400 text-sm mb-4">
          Clone failed: {(createMut.error as Error).message}
        </p>
      )}

      {deleteMut.isError && (
        <p className="text-red-400 text-sm mb-4">
          Delete failed: {(deleteMut.error as Error).message}
        </p>
      )}

      {checkMut.isError && (
        <p className="text-red-400 text-sm mb-4">
          Check failed: {(checkMut.error as Error).message}
        </p>
      )}

      {activeTaskId && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 mb-4">
          <div className="flex items-center gap-2 mb-2">
            <h3 className="font-semibold text-white">Progress</h3>
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
          <div className="bg-gray-950 border border-gray-800 rounded p-3 max-h-48 overflow-y-auto font-mono text-xs text-gray-400">
            {events.map((e, i) => (
              <div key={i}>
                {e.type === 'progress' && <span>[{String(e.step)}] {String(e.status || e.current)}</span>}
                {e.type === 'done' && <span className="text-green-400">Done: {JSON.stringify(e.result)}</span>}
                {e.type === 'error' && <span className="text-red-400">Error: {String(e.error)}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-3">
        {repos && repos.length === 0 && (
          <p className="text-gray-500 text-sm">No repositories yet. Add one above.</p>
        )}
        {repos?.map((repo) => (
          <RepoCard
            key={repo.id}
            repo={repo}
            onDelete={handleDelete}
            onCheck={handleCheck}
            onSync={handleSync}
            checking={checkingId === repo.id}
            syncing={syncingId === repo.id}
            freshness={freshnessResults[repo.id]}
            disabled={anyMutating}
          />
        ))}
      </div>
    </div>
  )
}
