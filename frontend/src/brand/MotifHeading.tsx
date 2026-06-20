/**
 * Agile Defense two-word headline motif: the first word is bold, the second
 * light. A deliberate brand motif — use it for page titles and hero headings
 * where a two-word framing fits ("Risk Feed", "Course of Action").
 *
 * See "Agile Defense Design System" readme — "the two-word headline motif".
 */
interface MotifHeadingProps {
  bold: string;
  light: string;
  className?: string;
}

export default function MotifHeading({ bold, light, className = '' }: MotifHeadingProps) {
  return (
    <span className={`font-headline ${className}`}>
      <span className="font-bold">{bold}</span>{' '}
      <span className="font-normal">{light}</span>
    </span>
  );
}
