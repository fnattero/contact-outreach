export const colors = {
  canvas: "#F4F4F1",
  surface: "#FFFFFF",
  surfaceSunken: "#EDEDE9",
  border: "#DEDEDA",
  borderStrong: "#C4C4BE",
  ink: "#16181C",
  inkSecondary: "#4A4F57",
  inkMuted: "#6E747E",
  accent: "#1E4B8F",
  accentHover: "#173C73",
  accentQuiet: "#E8EEF7",
  focus: "#3B7DD8",
  success: "#1F6E43",
  successBackground: "#E7F1EA",
  warning: "#8A5A00",
  warningBackground: "#F6EEDC",
  danger: "#A32B24",
  dangerBackground: "#F7E8E6",
  inactive: "#6E747E",
  inactiveBackground: "#ECECE8",
  tableRowHover: "#F7F7F4",
} as const;

export const typography = {
  display: { fontSize: 24, lineHeight: 30, fontWeight: 600 },
  title: { fontSize: 18, lineHeight: 24, fontWeight: 600 },
  body: { fontSize: 14, lineHeight: 20, fontWeight: 400 },
  bodyMedium: { fontSize: 14, lineHeight: 20, fontWeight: 500 },
  small: { fontSize: 13, lineHeight: 18, fontWeight: 400 },
  micro: { fontSize: 11, lineHeight: 14, fontWeight: 500, letterSpacing: "0.04em" },
  families: {
    ui: "var(--font-plex-sans), system-ui, sans-serif",
    data: "var(--font-plex-mono), monospace",
  },
} as const;

export const space = {
  1: 4,
  2: 8,
  3: 12,
  4: 16,
  6: 24,
  8: 32,
  12: 48,
} as const;

export const componentSpacing = {
  cardPaddingCompact: 16,
  cardPaddingDefault: 20,
  sectionGap: 24,
  formFieldGap: 16,
  formGroupGap: 32,
  pageGutterDesktop: 24,
  pageGutterTablet: 16,
  pageGutterMobile: 12,
} as const;

export const radii = {
  small: 3,
  medium: 6,
} as const;

export const elevation = {
  floating: "0 2px 8px rgba(22,24,28,0.08)",
  overlay: "0 8px 32px rgba(22,24,28,0.14)",
} as const;

export const density = {
  sidebarExpanded: 240,
  sidebarCollapsed: 64,
  topbar: 52,
  tableRow: 40,
  tableHeader: 36,
  contentMaxWidth: 1360,
  formMaxWidth: 640,
  readingMaxWidth: "68ch",
  tapTargetDesktop: 36,
  tapTargetTouch: 44,
} as const;

export const motion = {
  fast: 0.12,
  base: 0.18,
  slow: 0.24,
  ease: [0.2, 0, 0, 1] as const,
  cssEase: "cubic-bezier(0.2, 0, 0, 1)",
} as const;
