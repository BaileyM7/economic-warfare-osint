import { useState } from 'react'

interface Props {
  data: unknown
  label?: string
}

function syntaxHighlight(json: string): string {
  return json.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    (match) => {
      let cls = 'text-tertiary'
      if (/^"/.test(match)) {
        cls = /:$/.test(match) ? 'text-primary' : 'text-secondary'
      } else if (/true|false/.test(match)) {
        cls = 'text-tertiary'
      } else if (/null/.test(match)) {
        cls = 'text-outline'
      }
      return `<span class="${cls}">${match}</span>`
    }
  )
}

export default function DebugPanel({ data, label = 'Raw API Response' }: Props) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  const json = JSON.stringify(data, null, 2)

  function handleCopy() {
    navigator.clipboard.writeText(json).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <div className="mt-6 border-t border-outline-variant/10 pt-3">
      <div className="flex items-center gap-3">
        <button
          className="bg-surface-container border border-outline-variant/20 text-on-surface-variant text-xs px-3 py-1 rounded hover:bg-surface-bright transition-colors"
          onClick={() => setOpen((v) => !v)}
        >
          {open ? '\u25BE' : '\u25B8'} {label}
        </button>
        {open && (
          <button
            className="bg-surface-container border border-outline-variant/20 text-on-surface-variant text-xs px-3 py-1 rounded hover:bg-surface-bright transition-colors"
            onClick={handleCopy}
          >
            {copied ? '\u2713 Copied' : 'Copy JSON'}
          </button>
        )}
        {open && (
          <span className="text-[11px] text-outline">
            {(new TextEncoder().encode(json).length / 1024).toFixed(1)} KB
          </span>
        )}
      </div>

      {open && (
        <div className="mt-2 bg-surface-container-lowest border border-outline-variant/10 rounded-xl p-4 overflow-auto max-h-[500px]">
          <pre
            className="m-0 text-xs leading-relaxed font-mono"
            dangerouslySetInnerHTML={{ __html: syntaxHighlight(json) }}
          />
        </div>
      )}
    </div>
  )
}
