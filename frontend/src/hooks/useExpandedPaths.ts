import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

const PARAM = 'expanded'

export function useExpandedPaths() {
  const [searchParams, setSearchParams] = useSearchParams()

  const expandedPaths = useMemo(() => {
    const raw = searchParams.get(PARAM)
    if (!raw) return new Set<string>()
    return new Set(raw.split(',').filter(Boolean))
  }, [searchParams])

  const togglePath = useCallback(
    (path: string) => {
      setSearchParams(
        (prev) => {
          const raw = prev.get(PARAM)
          const current = raw ? new Set(raw.split(',').filter(Boolean)) : new Set<string>()

          if (current.has(path)) {
            current.delete(path)
          } else {
            current.add(path)
          }

          const next = new URLSearchParams(prev)
          if (current.size > 0) {
            next.set(PARAM, [...current].join(','))
          } else {
            next.delete(PARAM)
          }
          return next
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )

  return { expandedPaths, togglePath }
}
