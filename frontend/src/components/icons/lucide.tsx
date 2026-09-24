import {
  Bell,
  Check,
  ChevronDown,
  ChevronLeft,
  Clock,
  CreditCard,
  Home,
  HelpCircle,
  Info,
  LoaderCircle,
  LogOut,
  Plus,
  ShieldCheck,
  X,
} from 'lucide-react'
import type { IconProps } from './IconProps'

/**
 * Tab-bar icons: `filled` switches the stock lucide glyph between its outline
 * and a solid state. The stroke has to change colour too, not just the fill —
 * stroking in `currentColor` over a `currentColor` fill hides the glyph's inner
 * lines and leaves a featureless shape. lucide spreads extra props onto the
 * `<svg>` after its own `fill`/`stroke`, so these override.
 */
function fillProps(filled: boolean | undefined, strokeWidth: number) {
  return filled
    ? { fill: 'var(--ink)', stroke: 'var(--surface)', strokeWidth }
    : { fill: 'none', strokeWidth }
}

export function ShieldIcon({ size = 28, strokeWidth = 1.8, filled }: IconProps) {
  return <ShieldCheck size={size} {...fillProps(filled, strokeWidth)} aria-hidden="true" />
}

export function HomeIcon({ size = 22, strokeWidth = 1.8, filled }: IconProps) {
  return <Home size={size} {...fillProps(filled, strokeWidth)} aria-hidden="true" />
}

export function AccountsIcon({ size = 22, strokeWidth = 1.8, filled }: IconProps) {
  return <CreditCard size={size} {...fillProps(filled, strokeWidth)} aria-hidden="true" />
}

export function BellIcon({ size = 22, strokeWidth = 1.8, filled }: IconProps) {
  return <Bell size={size} {...fillProps(filled, strokeWidth)} aria-hidden="true" />
}

export function BackChevronIcon({ size = 20, strokeWidth = 2 }: IconProps) {
  return <ChevronLeft size={size} strokeWidth={strokeWidth} aria-hidden="true" />
}

export function ChevronDownIcon({ size = 20, strokeWidth = 2 }: IconProps) {
  return <ChevronDown size={size} strokeWidth={strokeWidth} aria-hidden="true" />
}

export function CheckIcon({ size = 18, strokeWidth = 2.4 }: IconProps) {
  return <Check size={size} strokeWidth={strokeWidth} aria-hidden="true" />
}

export function CrossIcon({ size = 18, strokeWidth = 2.4 }: IconProps) {
  return <X size={size} strokeWidth={strokeWidth} aria-hidden="true" />
}

export function ClockIcon({ size = 20, strokeWidth = 2.2 }: IconProps) {
  return <Clock size={size} strokeWidth={strokeWidth} aria-hidden="true" />
}

export function HelpCircleIcon({ size = 20, strokeWidth = 2.2 }: IconProps) {
  return <HelpCircle size={size} strokeWidth={strokeWidth} aria-hidden="true" />
}

export function InfoIcon({ size = 20, strokeWidth = 2.2 }: IconProps) {
  return <Info size={size} strokeWidth={strokeWidth} aria-hidden="true" />
}

export function LogOutIcon({ size = 20, strokeWidth = 1.8 }: IconProps) {
  return <LogOut size={size} strokeWidth={strokeWidth} aria-hidden="true" />
}

export function PlusIcon({ size = 16, strokeWidth = 2.4 }: IconProps) {
  return <Plus size={size} strokeWidth={strokeWidth} aria-hidden="true" />
}

export function SpinnerIcon({ size = 28, strokeWidth = 2.5 }: IconProps) {
  return (
    <LoaderCircle size={size} strokeWidth={strokeWidth} aria-hidden="true" className="animate-spin" />
  )
}

export type { IconProps } from './IconProps'
