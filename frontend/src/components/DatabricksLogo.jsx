export default function DatabricksLogo({ size = 40, withBackground = true }) {
  if (withBackground) {
    return (
      <svg width={size} height={size} viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect width="40" height="40" rx="9" fill="#FF3621"/>
        {/* Databricks spark — isometric box */}
        <path d="M20 6 L33 13.5 L20 21 L7 13.5 Z" fill="white"/>
        <path d="M7 13.5 L20 21 L20 34 L7 26.5 Z" fill="rgba(255,255,255,0.55)"/>
        <path d="M33 13.5 L33 26.5 L20 34 L20 21 Z" fill="rgba(255,255,255,0.8)"/>
      </svg>
    )
  }

  return (
    <svg width={size} height={size} viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M20 2 L38 11.5 L20 21 L2 11.5 Z" fill="#FF3621"/>
      <path d="M2 11.5 L20 21 L20 38 L2 28.5 Z" fill="#C9260F"/>
      <path d="M38 11.5 L38 28.5 L20 38 L20 21 Z" fill="#E82D1A"/>
    </svg>
  )
}
