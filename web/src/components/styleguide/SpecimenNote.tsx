// 样本注脚：每个 specimen 下面的 token/class 注解行（styleguide 全节共用）。
import { useI18n } from "../../i18n";

interface SpecimenNoteProps {
  zh: string;
  en: string;
}

export function SpecimenNote({ zh, en }: SpecimenNoteProps) {
  const { text } = useI18n();
  return <figcaption className="sg-caption">{text(zh, en)}</figcaption>;
}
