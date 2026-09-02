import React from "react";
import {
  AbsoluteFill,
  Sequence,
  useCurrentFrame,
  useVideoConfig,
  spring,
  interpolate,
} from "remotion";
import type { SubtitleConfig } from "../lib/types";
import { groupCaptionsIntoBlocks, getActiveWordIndex } from "../lib/captions";
import { getFontStack, antonFontFace, notoSerifFontFace } from "../lib/fonts";

function isEmojiWord(text: string): boolean {
  const t = text.trim();
  if (!t) return false;
  const stripped = t.replace(/\uFE0F|\u200D|[\u{1F3FB}-\u{1F3FF}]/gu, '');
  const withoutEmoji = stripped.replace(/[\u{1F300}-\u{1FAFF}\u2600-\u27BF\u2300-\u23FF\u2B50\u2764]/gu, '').trim();
  return withoutEmoji.length === 0 && /[\u{1F300}-\u{1FAFF}\u2600-\u27BF\u2300-\u23FF\u2B50\u2764]/u.test(stripped);
}

interface SubtitlesProps {
  config: SubtitleConfig;
}

const POSITION_MAP: Record<string, React.CSSProperties> = {
  top: { top: "12%", bottom: "auto" },
  middle: { top: "45%", bottom: "auto" },
  bottom: { bottom: "10%", top: "auto" },
};

/** Dynamic vertical from style.marginV (0-100): 0→12% top, 50→45% top, 100→bottom 10%. Falls back to discrete POSITION_MAP for legacy. */
function getPositionStyle(
  style: SubtitleConfig["style"],
  position: SubtitleConfig["position"]
): React.CSSProperties {
  const mv = (style as unknown as { marginV?: number }).marginV;
  if (typeof mv === "number" && !Number.isNaN(mv)) {
    const clamped = Math.max(0, Math.min(100, mv));
    if (clamped >= 98) return { bottom: "10%", top: "auto" };
    let topPct: number;
    if (clamped <= 50) topPct = 12 + (clamped / 50) * 33;
    else topPct = 45 + ((clamped - 50) / 50) * 45;
    return { top: `${topPct}%`, bottom: "auto" };
  }
  return POSITION_MAP[position] ?? POSITION_MAP.bottom;
}

export const Subtitles: React.FC<SubtitlesProps> = ({ config }) => {
  const { fps } = useVideoConfig();
  const maxChars = (config as any).maxChars ?? 20;
  const maxDuration = (config as any).maxDuration ?? 2000;
  const blocks = groupCaptionsIntoBlocks(config.captions, maxChars, maxDuration);

  return (
    <AbsoluteFill>
      <style>{antonFontFace}</style>
      <style>{notoSerifFontFace}</style>
      {blocks.map((block, i) => {
        const startFrame = Math.round((block.startMs / 1000) * fps);
        const durationFrames = Math.max(
          1,
          Math.round(((block.endMs - block.startMs) / 1000) * fps)
        );

        return (
          <Sequence
            key={i}
            from={startFrame}
            durationInFrames={durationFrames}
            layout="none"
          >
            <SubtitleBlock
              block={block}
              config={config}
              blockStartMs={block.startMs}
            />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

interface SubtitleBlockProps {
  block: ReturnType<typeof groupCaptionsIntoBlocks>[number];
  config: SubtitleConfig;
  blockStartMs: number;
}

const SubtitleBlock: React.FC<SubtitleBlockProps> = ({
  block,
  config,
  blockStartMs,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { style, position } = config;

  // Current time relative to composition start (sequence-relative frame)
  const currentTimeMs = blockStartMs + (frame / fps) * 1000;
  const activeIndex = getActiveWordIndex(block.words, currentTimeMs);

  const positionStyle = getPositionStyle(style, position);
  const fontStack = getFontStack(style.fontFamily);

  // Background box style
  const hasBg = style.bgOpacity > 0;
  const bgStyle: React.CSSProperties = hasBg
    ? {
        backgroundColor: `${style.bgColor}${Math.round(style.bgOpacity * 255)
          .toString(16)
          .padStart(2, "0")}`,
        borderRadius: 8,
        padding: "8px 16px",
      }
    : {};

  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        display: "flex",
        justifyContent: "center",
        ...positionStyle,
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "center",
          alignItems: "center",
          lineHeight: (style as unknown as { lineHeight?: number }).lineHeight ?? 1.0,
          gap: `${(style as unknown as { wordGap?: number }).wordGap ?? 8}px`,
          maxWidth: "95%",
          ...bgStyle,
        }}
      >
        {block.words.map((word, i) => (
          <WordSpan
            key={i}
            word={word.text}
            isActive={i === activeIndex}
            style={style}
            fontStack={fontStack}
            animation={style.animation}
            frame={frame}
            fps={fps}
            wordStartMs={word.startMs}
            blockStartMs={blockStartMs}
          />
        ))}
      </div>
    </div>
  );
};

interface WordSpanProps {
  word: string;
  isActive: boolean;
  style: SubtitleConfig["style"];
  fontStack: string;
  animation: SubtitleConfig["style"]["animation"];
  frame: number;
  fps: number;
  wordStartMs: number;
  blockStartMs: number;
}

const WordSpan: React.FC<WordSpanProps> = ({
  word,
  isActive,
  style,
  fontStack,
  animation,
  frame,
  fps,
  wordStartMs,
  blockStartMs,
}) => {
  const wordStartFrame = Math.round(
    ((wordStartMs - blockStartMs) / 1000) * fps
  );

  const displayWord = (style as any).uppercase ? word.toUpperCase() : word;

  let transform = "";
  let color = style.fontColor;
  let extraStyle: React.CSSProperties = {};

  if (isActive) {
    color = style.highlightColor;

    switch (animation) {
      case "pop": {
        const scale = spring({
          frame: frame - wordStartFrame,
          fps,
          config: { mass: 0.5, stiffness: 300, damping: 12 },
          durationInFrames: 10,
        });
        const scaleValue = interpolate(scale, [0, 1], [1, 1.25]);
        transform = `scale(${scaleValue})`;
        break;
      }
      case "karaoke": {
        extraStyle = {
          backgroundColor: style.highlightColor,
          color: style.bgColor || "#000000",
          borderRadius: 4,
          padding: "2px 6px",
        };
        break;
      }
      case "word-highlight": {
        extraStyle = {
          textShadow: `0 0 12px ${style.highlightColor}, 0 0 24px ${style.highlightColor}40`,
        };
        break;
      }
      default:
        break;
    }
  }

  // Text stroke via textShadow (CSS paint-order not reliable in Remotion)
  const strokeShadow =
    style.borderWidth > 0
      ? [
          `${style.borderWidth}px 0 0 ${style.borderColor}`,
          `-${style.borderWidth}px 0 0 ${style.borderColor}`,
          `0 ${style.borderWidth}px 0 ${style.borderColor}`,
          `0 -${style.borderWidth}px 0 ${style.borderColor}`,
        ].join(", ")
      : "none";

  // Animated emoji: render via Remotion's AnimatedEmoji (Google animated, Android color) when the word is an emoji.
  // Emoji are intentionally larger than text (1.8×) so they pop, and text keeps its exact fontSize
  // — previously text appeared smaller when emoji were present because the flex wrap with
  // emoji spans compressed the line. Now emoji are 1.8× and vertically centered.
  if (isEmojiWord(word)) {
    const baseSize = Math.round(style.fontSize * 0.6 * 1920 / 288);
    const size = Math.round(baseSize * 1.5);
    return (
      <span
        style={{
          fontFamily: '"Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif',
          fontSize: size,
          lineHeight: (style as unknown as { lineHeight?: number }).lineHeight ?? 1.0,
          display: "inline-block",
          transform,
          verticalAlign: "middle",
          margin: "-2px 1px",
          ...extraStyle,
        }}
      >
        {displayWord}
      </span>
    );
  }

  return (
    <span
      style={{
        fontFamily: fontStack,
        fontSize: Math.round(style.fontSize * 0.6 * 1920 / 288),
        fontWeight: 700,
        color: animation === "karaoke" && isActive ? undefined : color,
        textShadow:
          animation !== "karaoke"
            ? [strokeShadow, extraStyle.textShadow].filter(Boolean).join(", ")
            : strokeShadow,
        transform,
        display: "inline-block",
        transition: "none",
        letterSpacing: `${(style as unknown as { letterSpacing?: number }).letterSpacing ?? 0}px`,
        lineHeight: (style as unknown as { lineHeight?: number }).lineHeight ?? 1.0,
        ...extraStyle,
      }}
    >
      {displayWord}
    </span>
  );
};
