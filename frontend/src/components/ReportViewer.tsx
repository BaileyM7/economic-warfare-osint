import { marked } from 'marked'

interface Props {
  markdown: string
}

export default function ReportViewer({ markdown }: Props) {
  const html = marked.parse(markdown) as string
  return (
    <div
      className="report-panel"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
