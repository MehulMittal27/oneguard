export interface IconProps {
  size?: number
  // DESIGN.md's icon stroke width varies by context (1.8–2.6px depending on
  // where the icon appears), so it's a prop with a per-icon default, not fixed.
  strokeWidth?: number
  // Tab-bar icons only — draws the selected tab's icon solid. See `fillProps`.
  filled?: boolean
}
