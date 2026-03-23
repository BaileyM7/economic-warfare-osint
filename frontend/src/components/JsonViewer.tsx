interface Props {
  data: Record<string, unknown>
}

export default function JsonViewer({ data }: Props) {
  return (
    <div className="json-panel">
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </div>
  )
}
