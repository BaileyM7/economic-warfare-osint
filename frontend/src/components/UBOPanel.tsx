import type { SayariUBOOwner } from '../types'

interface Props {
  targetName: string
  owners: SayariUBOOwner[]
  loading: boolean
}

export default function UBOPanel({ targetName, owners, loading }: Props) {
  if (!loading && owners.length === 0) return null

  return (
    <div className="mt-4">
      <div className="text-sm text-outline uppercase tracking-wider mb-2.5 pb-1.5 border-b border-outline-variant/10">
        Ultimate Beneficial Ownership
        {targetName && <span className="normal-case text-on-surface-variant font-medium"> — {targetName}</span>}
      </div>
      {loading && <div className="text-sm text-outline py-2">Loading UBO data...</div>}
      {!loading && owners.length > 0 && (
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr>
              {['Owner', 'Type', 'Country', 'Ownership', 'Depth', 'Flags'].map((h) => (
                <th key={h} className="text-left text-[11px] text-outline uppercase tracking-wider font-medium py-1.5 px-2.5 border-b border-outline-variant/10">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {owners.map((o, i) => (
              <tr key={o.entity_id || i} className="border-b border-outline-variant/5">
                <td className="py-2 px-2.5 text-on-surface-variant font-medium">{o.name}</td>
                <td className="py-2 px-2.5 text-on-surface-variant">{o.type || '—'}</td>
                <td className="py-2 px-2.5 text-on-surface-variant">{o.country || '—'}</td>
                <td className="py-2 px-2.5 text-on-surface-variant">
                  {o.ownership_percentage != null ? `${o.ownership_percentage.toFixed(1)}%` : '—'}
                </td>
                <td className="py-2 px-2.5 text-on-surface-variant">{o.path_length}</td>
                <td className="py-2 px-2.5">
                  <div className="flex gap-1.5">
                    {o.sanctioned && (
                      <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-error/15 text-error border border-error/30 uppercase">Sanctioned</span>
                    )}
                    {o.pep && (
                      <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-tertiary/15 text-tertiary border border-tertiary/30 uppercase">PEP</span>
                    )}
                    {!o.sanctioned && !o.pep && '—'}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
