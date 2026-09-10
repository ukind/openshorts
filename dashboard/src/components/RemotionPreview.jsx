import React, { useMemo, useRef, useEffect } from 'react';
import { Player } from '@remotion/player';
import { ShortVideo } from '../remotion/compositions/ShortVideo';

/**
 * Wraps Remotion's Player component for real-time preview in modals.
 * Supports external playerRef so parent can seek.
 */
export default function RemotionPreview({
    playerRef: externalRef = null,
    videoUrl,
    durationInSeconds = 30,
    subtitles = null,
    hook = null,
    effects = null,
    className = '',
    onTimeUpdate = null,
}) {
    const fps = 30;
    const durationInFrames = Math.max(1, Math.round(durationInSeconds * fps));
    const internalRef = useRef(null);
    const ref = externalRef || internalRef;

    const inputProps = useMemo(
        () => ({
            videoUrl,
            durationInFrames,
            fps,
            width: 1080,
            height: 1920,
            subtitles,
            hook,
            effects,
        }),
        [videoUrl, durationInFrames, subtitles, hook, effects]
    );

    useEffect(() => {
        if (!onTimeUpdate) return;
        let raf = 0;
        let lastMs = -1;
        let tries = 0;
        const tick = () => {
            try {
                const el = ref.current;
                if (el && typeof el.getCurrentFrame === 'function') {
                    const frame = el.getCurrentFrame();
                    const ms = Math.round((frame / fps) * 1000);
                    if (ms !== lastMs) {
                        lastMs = ms;
                        onTimeUpdate(ms);
                    }
                } else if (tries++ < 100) {
                    // Player not yet mounted — keep polling
                }
            } catch { /* preview frame polling is best-effort */ }
            raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(raf);
    }, [onTimeUpdate, fps, ref]);

    return (
        <div className={`w-full h-full ${className}`}>
            <Player
                ref={ref}
                component={ShortVideo}
                inputProps={inputProps}
                durationInFrames={durationInFrames}
                fps={fps}
                compositionWidth={1080}
                compositionHeight={1920}
                style={{ width: '100%', height: '100%' }}
                controls
                autoPlay
                loop
            />
        </div>
    );
}
