interface Props {
  narrative: string | undefined
}

export default function NarrativeCard({ narrative }: Props) {
  if (!narrative) return null
  return (
    <div className="bg-surface-container rounded-xl p-6 mb-6 border-l-2 border-primary">
      <div className="flex items-center gap-2 mb-3">
        <span className="material-symbols-outlined text-primary text-lg">description</span>
        <h3 className="text-sm font-headline font-bold uppercase tracking-wider">
          Analyst Assessment
        </h3>
      </div>
      <p className="text-sm leading-relaxed text-on-surface-variant">{narrative}</p>
    </div>
  )
}
