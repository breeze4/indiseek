import { Link, useSearchParams } from 'react-router-dom'
import { ChevronRight, ChevronDown, Folder, FileText } from 'lucide-react'
import { useTree } from '../api/hooks.ts'
import { useExpandedPaths } from '../hooks/useExpandedPaths.ts'
import { useScrollRestore } from '../hooks/useScrollRestore.ts'
import type { TreeChild } from '../api/client.ts'

function Badge({ ok, label }: { ok: boolean | number; label: string }) {
  const isTrue = typeof ok === 'number' ? ok > 0 : ok
  return (
    <span
      className={`text-xs px-1.5 py-0.5 rounded ${
        isTrue ? 'bg-green-900 text-green-300' : 'bg-gray-800 text-gray-500'
      }`}
    >
      {label}
    </span>
  )
}

function DirStats({ child }: { child: TreeChild }) {
  const total = child.total_files ?? 0
  const parsed = typeof child.parsed === 'number' ? child.parsed : 0
  const summarized = typeof child.summarized === 'number' ? child.summarized : 0
  const embedded = typeof child.embedded === 'number' ? child.embedded : 0
  return (
    <span className="text-xs text-gray-500 ml-2">
      {parsed}/{total} parsed, {summarized} summ, {embedded} embed
    </span>
  )
}

interface TreeNodeProps {
  child: TreeChild
  parentPath: string
  isExpanded: boolean
  expandedPaths: Set<string>
  onToggle: (path: string) => void
}

function TreeNode({ child, parentPath, isExpanded, expandedPaths, onToggle }: TreeNodeProps) {
  const fullPath = parentPath ? `${parentPath}/${child.name}` : child.name

  if (child.type === 'file') {
    return (
      <Link
        to={`/files/${fullPath}`}
        className="flex items-center gap-2 py-1 px-2 hover:bg-gray-800 rounded text-sm group"
      >
        <FileText size={14} className="text-gray-500 shrink-0" />
        <span className="text-gray-300 group-hover:text-white shrink-0">{child.name}</span>
        {child.summary && (
          <span className="flex-1 min-w-0 truncate text-xs text-gray-500" title={child.summary}>
            {child.summary}
          </span>
        )}
        <span className="flex gap-1 ml-auto shrink-0">
          <Badge ok={child.parsed ?? false} label="P" />
          <Badge ok={child.summarized ?? false} label="S" />
          <Badge ok={child.embedded ?? false} label="E" />
        </span>
      </Link>
    )
  }

  return (
    <div>
      <button
        onClick={() => onToggle(fullPath)}
        className="flex items-center gap-1 py-1 px-2 hover:bg-gray-800 rounded text-sm w-full text-left"
      >
        {isExpanded ? <ChevronDown size={14} className="shrink-0" /> : <ChevronRight size={14} className="shrink-0" />}
        <Folder size={14} className="text-yellow-500 shrink-0" />
        <span className="text-gray-200 shrink-0">{child.name}/</span>
        {child.summary && (
          <span className="flex-1 min-w-0 truncate text-xs text-gray-500" title={child.summary}>
            {child.summary}
          </span>
        )}
        <DirStats child={child} />
      </button>
      {isExpanded && (
        <div className="ml-4 border-l border-gray-800 pl-2">
          <TreeLevel path={fullPath} expandedPaths={expandedPaths} onToggle={onToggle} />
        </div>
      )}
    </div>
  )
}

interface TreeLevelProps {
  path: string
  expandedPaths: Set<string>
  onToggle: (path: string) => void
}

function TreeLevel({ path, expandedPaths, onToggle }: TreeLevelProps) {
  const { data, isLoading, error } = useTree(path)

  if (isLoading) return <p className="text-gray-500 text-sm py-1 px-2">Loading...</p>
  if (error) return <p className="text-red-400 text-sm py-1 px-2">Error loading</p>
  if (!data?.children.length) return <p className="text-gray-500 text-sm py-1 px-2">Empty</p>

  return (
    <div>
      {data.children.map((child) => {
        const fullPath = path ? `${path}/${child.name}` : child.name
        return (
          <TreeNode
            key={child.name}
            child={child}
            parentPath={path}
            isExpanded={expandedPaths.has(fullPath)}
            expandedPaths={expandedPaths}
            onToggle={onToggle}
          />
        )
      })}
    </div>
  )
}

export default function FileTree() {
  const [searchParams] = useSearchParams()
  const startPath = searchParams.get('path') ?? ''
  const { expandedPaths, togglePath } = useExpandedPaths()
  useScrollRestore('files-tree')

  return (
    <div>
      <h2 className="text-xl font-bold mb-2">File Tree</h2>
      <p className="text-sm text-gray-400 mb-4">
        P=Parsed S=Summarized E=Embedded. Click directories to expand, files to view details.
      </p>
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-3">
        <TreeLevel path={startPath} expandedPaths={expandedPaths} onToggle={togglePath} />
      </div>
    </div>
  )
}
