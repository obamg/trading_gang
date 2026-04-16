import { NumberDisplay } from "./NumberDisplay";

interface Props {
  value: number | null | undefined;
  decimals?: number;
  className?: string;
}

export function PercentChange({ value, decimals = 2, className }: Props) {
  return <NumberDisplay value={value} decimals={decimals} suffix="%" colored sign className={className} />;
}
