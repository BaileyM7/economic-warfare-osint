import { useEffect, useState } from 'react'
import { fetchMe, getToken } from '../api'

interface AuthState {
  username: string | null
  isAdmin: boolean
  loading: boolean
}

export function useAuth(): AuthState {
  const [state, setState] = useState<AuthState>({
    username: null,
    isAdmin: false,
    loading: true,
  })

  useEffect(() => {
    if (!getToken()) {
      setState({ username: null, isAdmin: false, loading: false })
      return
    }
    let cancelled = false
    fetchMe()
      .then((me) => {
        if (!cancelled) setState({ username: me.username, isAdmin: me.is_admin, loading: false })
      })
      .catch(() => {
        if (!cancelled) setState({ username: null, isAdmin: false, loading: false })
      })
    return () => {
      cancelled = true
    }
  }, [])

  return state
}
