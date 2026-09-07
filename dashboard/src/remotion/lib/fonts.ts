import { staticFile } from "remotion";

/**
 * CSS @font-face declaration for NotoSerif-Bold (bundled locally).
 * Use in components via: <style>{notoSerifFontFace}</style>
 */
export const NOTO_SERIF_FONT_FAMILY = "NotoSerif-Bold";

export const notoSerifFontFace = `
@font-face {
  font-family: '${NOTO_SERIF_FONT_FAMILY}';
  src: url('${staticFile("fonts/NotoSerif-Bold.ttf")}') format('truetype');
  font-weight: 700;
  font-style: normal;
}
`;

/**
 * Anton — bundled so Remotion (Chromium) matches FFmpeg/libass (fontmap: Impact→Anton).
 * Without this, getFontStack('Anton') fell back to system sans-serif (smaller/lighter)
 * when emoji switched the burn from FFmpeg (libass Anton) to Remotion (animated).
 */
export const ANTON_FONT_FAMILY = "Anton";

export const antonFontFace = `
@font-face {
  font-family: '${ANTON_FONT_FAMILY}';
  src: url('${staticFile("fonts/Anton-Regular.ttf")}') format('truetype');
  font-weight: 400;
  font-style: normal;
}
`;

/**
 * Map of subtitle font families to CSS stacks.
 * Must match fonts/openshorts-fontmap.conf (libass aliases) so FFmpeg vs Remotion
 * render the same: Verdana/Arial/Helvetica → Liberation Sans, Georgia → Liberation Serif,
 * Impact → Anton. Missing entries fell back to system sans-serif in Remotion (Chromium)
 * and appeared smaller/lighter than FFmpeg's Liberation/Anton when emoji triggered Remotion.
 */
export const SUBTITLE_FONTS: Record<string, string> = {
  Anton: "'Anton', Impact, sans-serif",
  Verdana: "'Liberation Sans', Verdana, Geneva, sans-serif",
  Arial: "'Liberation Sans', Arial, Helvetica, sans-serif",
  Impact: "'Anton', Impact, Haettenschweiler, sans-serif",
  Helvetica: "'Liberation Sans', Helvetica, Arial, sans-serif",
  Georgia: "'Liberation Serif', Georgia, 'Times New Roman', serif",
  "Courier New": "'Courier New', Courier, monospace",
};

export function getFontStack(fontFamily: string): string {
  return SUBTITLE_FONTS[fontFamily] ?? fontFamily;
}
