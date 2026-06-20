import type { EntityType } from '../types'

const ICONS: Record<EntityType, string> = {
  company: '\u{1F3E2}',
  person: '\u{1F464}',
  vessel: '\u{1F6A2}',
  sector: '\u{1F3ED}',
}

export default function EntityTypeBadge({ entityType }: { entityType: EntityType }) {
  return (
    <span className={`entity-type-badge ${entityType}`}>
      {ICONS[entityType] || ''} {entityType}
    </span>
  )
}
