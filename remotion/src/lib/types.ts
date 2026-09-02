// --- Word-level caption ---
export interface CaptionWord {
  text: string;
  startMs: number;
  endMs: number;
}

// --- Subtitle config ---
export type SubtitleAnimation = "none" | "word-highlight" | "pop" | "karaoke";
export type SubtitlePosition = "top" | "middle" | "bottom";

export interface SubtitleStyle {
  fontFamily: string;
  fontSize: number;
  fontColor: string;
  highlightColor: string;
  borderColor: string;
  borderWidth: number;
  bgColor: string;
  bgOpacity: number;
  animation: SubtitleAnimation;
  // Karaoke look (kept optional for compat with dashboard)
  baseOpacity?: number;
  uppercase?: boolean;
  /** Vertical position 0-100: 0=top (~12% top), 50=middle (~45% top), 100=bottom (bottom 10%). */
  marginV?: number;
  /** Gap between words in px (4-16). Default 8. */
  wordGap?: number;
  /** Line height multiplier (0.9-1.4). Default 1.0. */
  lineHeight?: number;
  /** Letter spacing in px (-1 to 4). Default 0. */
  letterSpacing?: number;
}

export interface SubtitleConfig {
  captions: CaptionWord[];
  /** @deprecated use style.marginV (0-100) instead; kept for backwards-compat */
  position: SubtitlePosition;
  style: SubtitleStyle;
}

// --- Hook config ---
export type HookPosition = "top" | "center" | "bottom";
export type HookSize = "S" | "M" | "L";
export type HookEntrance = "spring" | "fade" | "slide-up" | "none";

export type HookStyle =
  | "classic"
  | "dark"
  | "yellow"
  | "red"
  | "outline"
  | "outline_yellow";

export interface HookConfig {
  text: string;
  position: HookPosition;
  size: HookSize;
  entranceAnimation: HookEntrance;
  displayDurationSec: number;
  style?: HookStyle;
}

// --- Effects config ---
export interface EffectSegment {
  startSec: number;
  endSec: number;
  zoom: number;
  zoomCenterX: number;
  zoomCenterY: number;
  brightness: number;
  contrast: number;
  saturate: number;
}

export interface EffectsConfig {
  segments: EffectSegment[];
}

// --- Main composition props ---
export interface ShortVideoProps {
  videoUrl: string;
  durationInFrames: number;
  fps: number;
  width: number;
  height: number;
  subtitles: SubtitleConfig | null;
  hook: HookConfig | null;
  effects: EffectsConfig | null;
}
