import { Routes, Route, NavLink } from 'react-router-dom'
import { Database, FolderTree, Search, Play, MessageSquare, GitBranch } from 'lucide-react'
import Overview from './pages/Overview.tsx'
import FileTree from './pages/FileTree.tsx'
import FileDetail from './pages/FileDetail.tsx'
import ChunkDetail from './pages/ChunkDetail.tsx'
import SearchPage from './pages/Search.tsx'
import Operations from './pages/Operations.tsx'
import Query from './pages/Query.tsx'
import Repos from './pages/Repos.tsx'

const navItems = [
  { to: '/repos', icon: GitBranch, label: 'Repos' },
  { to: '/', icon: Database, label: 'Overview' },
  { to: '/files', icon: FolderTree, label: 'Files' },
  { to: '/search', icon: Search, label: 'Search' },
  { to: '/query', icon: MessageSquare, label: 'Query' },
  { to: '/operations', icon: Play, label: 'Operations' },
]

function App() {
  return (
    <div className="flex min-h-screen">
      <nav className="w-48 bg-gray-900 border-r border-gray-800 p-4 flex flex-col gap-1">
        <h1 className="text-lg font-bold mb-4 text-white">Indiseek</h1>
        {navItems.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-2 px-3 py-2 rounded text-sm ${
                isActive
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'
              }`
            }
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}
      </nav>
      <main className="flex-1 p-6 overflow-auto">
        <Routes>
          <Route path="/repos" element={<Repos />} />
          <Route path="/" element={<Overview />} />
          <Route path="/files" element={<FileTree />} />
          <Route path="/files/*" element={<FileDetail />} />
          <Route path="/chunks/:id" element={<ChunkDetail />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/query" element={<Query />} />
          <Route path="/operations" element={<Operations />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
