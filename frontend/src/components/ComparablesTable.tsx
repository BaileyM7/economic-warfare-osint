import type { Comparable } from '../types'

interface Props {
  comparables: Comparable[]
  hidden: Set<number>
  onToggle: (idx: number) => void
}

export default function ComparablesTable({ comparables, hidden, onToggle }: Props) {
  return (
    <table className="w-full text-left border-collapse">
      <thead>
        <tr className="border-b border-outline-variant/10">
          <th className="py-2 px-3 text-[10px] font-label uppercase tracking-widest text-outline w-8" />
          <th className="py-2 px-3 text-[10px] font-label uppercase tracking-widest text-outline">Company</th>
          <th className="py-2 px-3 text-[10px] font-label uppercase tracking-widest text-outline">Ticker</th>
          <th className="py-2 px-3 text-[10px] font-label uppercase tracking-widest text-outline">Sanction Date</th>
          <th className="py-2 px-3 text-[10px] font-label uppercase tracking-widest text-outline">Description</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-outline-variant/5">
        {comparables.map((c, i) => (
          <tr
            key={i}
            className={`cursor-pointer transition-colors hover:bg-surface-container-high ${
              hidden.has(i) ? 'opacity-30' : ''
            }`}
            onClick={() => onToggle(i)}
          >
            <td className="py-2 px-3">
              <span
                className="inline-block w-2.5 h-2.5 rounded-full"
                style={{ background: c.color }}
              />
            </td>
            <td className="py-2 px-3 text-xs font-medium text-on-surface">{c.name}</td>
            <td className="py-2 px-3 font-mono text-xs text-primary">{c.ticker}</td>
            <td className="py-2 px-3 text-xs text-on-surface-variant">{c.sanction_date}</td>
            <td className="py-2 px-3 text-xs text-outline">{c.description}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
