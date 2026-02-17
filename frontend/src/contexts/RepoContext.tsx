import { createContext, useContext, useState, useEffect, type ReactNode } from 'react'

interface RepoContextType {
  currentRepoId: number
  setCurrentRepoId: (id: number) => void
}

const RepoContext = createContext<RepoContextType>({
  currentRepoId: 1,
  setCurrentRepoId: () => {},
})

const STORAGE_KEY = 'indiseek_repo_id'

export function RepoProvider({ children }: { children: ReactNode }) {
  const [currentRepoId, setCurrentRepoIdState] = useState<number>(() => {
    const stored = localStorage.getItem(STORAGE_KEY)
    return stored ? parseInt(stored, 10) || 1 : 1
  })

  function setCurrentRepoId(id: number) {
    setCurrentRepoIdState(id)
  }

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, String(currentRepoId))
  }, [currentRepoId])

  return (
    <RepoContext.Provider value={{ currentRepoId, setCurrentRepoId }}>
      {children}
    </RepoContext.Provider>
  )
}

export function useCurrentRepo() {
  return useContext(RepoContext)
}
