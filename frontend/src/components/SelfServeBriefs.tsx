import { useState, FormEvent } from 'react'
import { subscribeBriefs, sendBriefNow } from '../api'
import type { SendBriefNowResponse } from '../types'

/**
 * SelfServeBriefs — subscribe an email (+ optional phone) for scheduled intel
 * briefs, and trigger an on-demand send. Phase 3 contract: POST /api/subscribe,
 * POST /api/brief/send-now (synchronous, a few seconds). Surfaces the real send
 * status / error, no swallowing.
 *
 * Brand style: navy block, sharp corners, hairline dividers, red CTA for the
 * primary action, calm blue for the secondary subscribe.
 */

const SEND_STATUS_COPY: Record<SendBriefNowResponse['status'], { label: string; tone: 'ok' | 'warn' | 'err' }> = {
  sent: { label: 'Brief sent', tone: 'ok' },
  failed: { label: 'Send failed', tone: 'err' },
  skipped_kill_switch: { label: 'Skipped — notifications disabled', tone: 'warn' },
  skipped_disabled: { label: 'Skipped — email not enabled', tone: 'warn' },
  skipped_allowlist: { label: 'Skipped — not on allowlist', tone: 'warn' },
}

const TONE_CLASS: Record<'ok' | 'warn' | 'err', string> = {
  ok: 'text-secondary border-secondary/30 bg-secondary/10',
  warn: 'text-tertiary border-tertiary/30 bg-tertiary/10',
  err: 'text-error border-error/30 bg-error/10',
}

export default function SelfServeBriefs() {
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [subscribing, setSubscribing] = useState(false)
  const [subscribed, setSubscribed] = useState<string | null>(null)
  const [subError, setSubError] = useState<string | null>(null)

  const [sending, setSending] = useState(false)
  const [sendResult, setSendResult] = useState<{ msg: string; tone: 'ok' | 'warn' | 'err' } | null>(null)

  async function handleSubscribe(e: FormEvent) {
    e.preventDefault()
    if (!email.trim()) return
    setSubscribing(true)
    setSubError(null)
    setSubscribed(null)
    try {
      const res = await subscribeBriefs(email.trim(), phone.trim() || undefined)
      setSubscribed(res.email)
    } catch (err) {
      setSubError(err instanceof Error ? err.message : 'Subscription failed')
    } finally {
      setSubscribing(false)
    }
  }

  async function handleSendNow() {
    setSending(true)
    setSendResult(null)
    try {
      const res = await sendBriefNow()
      const copy = SEND_STATUS_COPY[res.status] ?? { label: res.status, tone: 'warn' as const }
      setSendResult({
        msg: res.error ? `${copy.label} — ${res.error}` : copy.label,
        tone: copy.tone,
      })
    } catch (err) {
      setSendResult({
        msg: err instanceof Error ? err.message : 'Send failed',
        tone: 'err',
      })
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="bg-surface-container-low border border-outline-variant/10">
      <div className="px-5 py-4 border-b border-outline-variant/10">
        <h3 className="font-headline text-sm font-bold uppercase tracking-widest text-on-surface">
          <span className="font-bold">Self-Serve</span>{' '}
          <span className="font-normal">Briefs</span>
        </h3>
        <p className="text-[11px] text-on-surface-variant mt-1">
          Subscribe to receive scheduled intel briefs, or send one to your inbox now.
        </p>
      </div>

      <form onSubmit={handleSubscribe} className="px-5 py-4 space-y-3">
        <div>
          <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
            Email
          </label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="analyst@agency.gov"
            className="w-full bg-surface-container-lowest border border-outline-variant/20 px-3 py-2.5 text-sm text-on-surface placeholder:text-outline focus:outline-none focus:border-primary/50"
          />
        </div>
        <div>
          <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
            Phone <span className="text-on-surface-variant font-normal normal-case tracking-normal">(optional)</span>
          </label>
          <input
            type="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+1 202 555 0123"
            className="w-full bg-surface-container-lowest border border-outline-variant/20 px-3 py-2.5 text-sm text-on-surface placeholder:text-outline focus:outline-none focus:border-primary/50"
          />
        </div>

        <div className="flex items-center gap-3 pt-1">
          <button
            type="submit"
            disabled={subscribing || !email.trim()}
            className="border border-primary text-primary px-5 h-10 font-label text-xs font-bold uppercase tracking-wide flex items-center gap-2 hover:bg-primary/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {subscribing ? (
              <span className="material-symbols-outlined text-sm animate-spin">progress_activity</span>
            ) : (
              <span className="material-symbols-outlined text-sm">mark_email_read</span>
            )}
            Subscribe
          </button>
          <button
            type="button"
            onClick={handleSendNow}
            disabled={sending}
            className="bg-accent text-white px-5 h-10 font-label text-xs font-bold uppercase tracking-wide flex items-center gap-2 hover:bg-accent-hover transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {sending ? (
              <span className="material-symbols-outlined text-sm animate-spin">progress_activity</span>
            ) : (
              <span className="material-symbols-outlined text-sm">send</span>
            )}
            Send Brief Now
          </button>
        </div>

        {subscribed && (
          <div className="text-[11px] text-secondary border border-secondary/30 bg-secondary/10 px-3 py-2">
            Subscribed — briefs will go to {subscribed}
          </div>
        )}
        {subError && (
          <div className="text-[11px] text-error border border-error/30 bg-error/10 px-3 py-2">{subError}</div>
        )}
        {sendResult && (
          <div className={`text-[11px] border px-3 py-2 ${TONE_CLASS[sendResult.tone]}`}>{sendResult.msg}</div>
        )}
      </form>
    </div>
  )
}
