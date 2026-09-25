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
  TriangleAlert,
  X,
} from 'lucide-react'
import type { IconProps } from './IconProps'

export function ShieldIcon({ size = 28, strokeWidth = 1.8, filled }: IconProps) {
  void filled
  return <ShieldCheck size={size} fill="none" stroke="var(--ink)" strokeWidth={strokeWidth} aria-hidden="true" />
}

export function HomeIcon({ size = 22, strokeWidth = 1.8, filled }: IconProps) {
  void filled
  return <Home size={size} fill="none" stroke="var(--ink)" strokeWidth={strokeWidth} aria-hidden="true" />
}

export function AccountsIcon({ size = 22, strokeWidth = 1.8, filled }: IconProps) {
  void filled
  return <CreditCard size={size} fill="none" stroke="var(--ink)" strokeWidth={strokeWidth} aria-hidden="true" />
}

export function BellIcon({ size = 22, strokeWidth = 1.8, filled }: IconProps) {
  void filled
  return <Bell size={size} fill="none" stroke="var(--ink)" strokeWidth={strokeWidth} aria-hidden="true" />
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

export function WarningIcon({ size = 20, strokeWidth = 2.2 }: IconProps) {
  return <TriangleAlert size={size} strokeWidth={strokeWidth} aria-hidden="true" />
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
