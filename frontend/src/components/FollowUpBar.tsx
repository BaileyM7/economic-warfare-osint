import { useEffect, useRef, useState } from 'react'
import { fetchFollowUp } from '../api'

interface Message {
  role: 'user' | 'assistant'
  text: string
}

interface Props {
  contextType: 'company' | 'orchestrator' | 'vessel' | 'person' | 'sector' | 'coa'
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  context: Record<string, any>
  prefillQuestion?: string
}

export default function FollowUpBar({ contextType, context, prefillQuestion }: Props) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const threadRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (threadRef.current) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight
    }
  }, [messages, loading])

  useEffect(() => {
    setMessages([])
    setInput('')
  }, [context])

  useEffect(() => {
    if (prefillQuestion) {
      setInput(prefillQuestion)
      inputRef.current?.focus()
      inputRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [prefillQuestion])

  async function handleSubmit() {
    const question = input.trim()
    if (!question || loading) return

    const historySnapshot = messages.map((m) => ({ role: m.role, text: m.text }))

    setMessages((prev) => [...prev, { role: 'user', text: question }])
    setInput('')
    setLoading(true)

    try {
      const { answer } = await fetchFollowUp(question, contextType, context, historySnapshot)
      setMessages((prev) => [...prev, { role: 'assistant', text: answer }])
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', text: `Error: ${(e as Error).message}` },
      ])
    } finally {
      setLoading(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const hasMessages = messages.length > 0

  return (
    <div className="mt-8 border border-outline-variant/20 rounded-xl bg-surface-container-lowest overflow-hidden">
      {/* Header */}
      <div className="px-4 py-2.5 border-b border-outline-variant/10 flex items-center gap-2 bg-surface-container-low">
        <span className="material-symbols-outlined text-primary text-sm">chat_bubble</span>
        <span className="text-sm text-primary font-semibold">Ask a follow-up</span>
        <span className="text-[11px] text-outline">
          — Analyst has full access to all data from this analysis
        </span>
      </div>

      {/* Message thread */}
      {hasMessages && (
        <div ref={threadRef} className="px-4 py-3 flex flex-col gap-3.5 max-h-[420px] overflow-y-auto">
          {messages.map((msg, i) => (
            <div key={i} className="flex gap-2.5 items-start">
              <span className={`shrink-0 text-[10px] font-bold uppercase tracking-wider mt-0.5 px-1.5 py-0.5 rounded ${
                msg.role === 'user'
                  ? 'bg-primary/10 text-primary border border-primary/20'
                  : 'bg-secondary/10 text-secondary border border-secondary/20'
              }`}>
                {msg.role === 'user' ? 'You' : 'Analyst'}
              </span>
              <span className={`text-sm leading-relaxed whitespace-pre-wrap break-words ${
                msg.role === 'user' ? 'text-on-surface-variant' : 'text-on-surface'
              }`}>
                {msg.text}
              </span>
            </div>
          ))}

          {loading && (
            <div className="flex gap-2.5 items-center">
              <span className="shrink-0 text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-secondary/10 text-secondary border border-secondary/20">
                Analyst
              </span>
              <span className="text-xs text-outline italic">Thinking...</span>
            </div>
          )}
        </div>
      )}

      {/* Input row */}
      <div className={`px-4 py-2.5 flex gap-2.5 items-end ${hasMessages ? 'border-t border-outline-variant/10' : ''}`}>
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask anything about this analysis... (Enter to send, Shift+Enter for newline)"
          disabled={loading}
          rows={3}
          className="flex-1 bg-surface-container-low border border-outline-variant/20 rounded-lg text-on-surface text-sm px-3.5 py-2.5 resize-y min-h-[72px] max-h-[200px] overflow-y-auto font-body leading-relaxed focus:border-primary focus:ring-0 focus:outline-none transition-colors placeholder:text-outline"
        />
        <button
          onClick={handleSubmit}
          disabled={!input.trim() || loading}
          className="bg-primary-container text-on-primary-container rounded-lg text-sm font-semibold px-4 py-2.5 shrink-0 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity hover:shadow-lg hover:shadow-primary-container/20"
        >
          Send
        </button>
      </div>
    </div>
  )
}
