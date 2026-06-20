import { useState, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { login, setToken } from '../api'

export default function LoginPage() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const res = await login(username, password)
      setToken(res.access_token)
      navigate('/search', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface-dim p-6">
      <div className="w-full max-w-md bg-surface-container rounded-xl p-8 border border-outline-variant/15">
        <div className="text-center mb-8">
          <p className="font-headline text-xs uppercase tracking-[0.2em] text-on-surface-variant mb-3">
            <span className="font-bold">Agile</span> <span className="font-normal">Defense</span>
          </p>
          <h1 className="text-3xl font-headline font-bold uppercase tracking-widest text-on-surface">Emissary</h1>
          <p className="text-sm text-on-surface-variant mt-2">Sign in to continue</p>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">Username</label>
            <input
              type="text"
              autoFocus
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-sm text-on-surface focus:outline-none focus:border-primary/50"
            />
          </div>
          <div>
            <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">Password</label>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-sm text-on-surface focus:outline-none focus:border-primary/50"
            />
          </div>
          {error && (
            <div className="bg-error-container/20 border border-error/30 rounded-lg p-3 text-sm text-error">
              {error}
            </div>
          )}
          <button
            type="submit"
            disabled={loading || !username || !password}
            className="w-full bg-accent text-white px-4 py-2.5 rounded-lg text-sm font-bold hover:bg-accent-hover transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {loading ? (
              <><span className="material-symbols-outlined text-base animate-spin">progress_activity</span>Signing in...</>
            ) : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  )
}
