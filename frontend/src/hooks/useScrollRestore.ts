import { useEffect } from 'react'

export function useScrollRestore(key: string) {
  const storageKey = `scroll:${key}`

  useEffect(() => {
    const main = document.querySelector('main')
    if (!main) return

    const saved = sessionStorage.getItem(storageKey)
    if (saved != null) {
      const scrollTop = parseInt(saved, 10)
      requestAnimationFrame(() => {
        main.scrollTop = scrollTop
      })
    }

    return () => {
      sessionStorage.setItem(storageKey, String(main.scrollTop))
    }
  }, [storageKey])
}
