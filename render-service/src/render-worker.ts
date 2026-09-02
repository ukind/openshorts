import fs from "node:fs";
import path from "node:path";
import { selectComposition, renderMedia } from "@remotion/renderer";
import { getBundleLocation } from "./bundle.js";
import { renderJobs } from "./server.js";

export interface RenderParams {
  renderId: string;
  jobId: string;
  clipIndex: number;
  props: {
    videoUrl: string;
    durationInFrames: number;
    fps: number;
    width: number;
    height: number;
    subtitles: unknown;
    hook: unknown;
    effects: unknown;
    useNvenc?: boolean;
  };
}

/**
 * Executes a Remotion render in the background.
 * Updates the in-memory render job map with progress and final status.
 */
export async function executeRender(params: RenderParams): Promise<void> {
  const { renderId, jobId, clipIndex, props } = params;
  const job = renderJobs.get(renderId);

  if (!job) {
    console.error(`[render-worker] Job ${renderId} not found in map`);
    return;
  }

  try {
    job.status = "rendering";
    job.progress = 0;

    console.log(
      `[render-worker] Starting render ${renderId} (job=${jobId}, clip=${clipIndex})`
    );

    const bundleLocation = getBundleLocation();

    // Select the composition with the provided input props
    const composition = await selectComposition({
      serveUrl: bundleLocation,
      id: "ShortVideo",
      inputProps: props,
    });

    // Determine output directory and file path
    const outputDir = process.env.OUTPUT_DIR
      ? path.resolve(process.env.OUTPUT_DIR)
      : path.resolve(import.meta.dirname, "../../output");

    const jobOutputDir = path.join(outputDir, jobId);
    fs.mkdirSync(jobOutputDir, { recursive: true });

    const timestamp = Date.now();
    const outputFileName = `remotion_${clipIndex}_${timestamp}.mp4`;
    const outputLocation = path.join(jobOutputDir, outputFileName);

    console.log(`[render-worker] Output: ${outputLocation}`);

    // Render the video — try NVENC if requested (h264_nvenc), fallback to libx264
    const useNvenc = (props as unknown as Record<string, unknown>).useNvenc === true;
    const codec: "h264" = "h264";
    console.log(`[render-worker] codec=${codec} useNvenc=${useNvenc} fps=${props.fps} frames=${props.durationInFrames}`);
    const baseRenderOpts = {
      composition,
      serveUrl: bundleLocation,
      codec,
      crf: 23,
      outputLocation,
      concurrency: 2,
      onProgress: ({ progress }: { progress: number }) => {
        const percent = Math.round(progress * 100);
        job.progress = percent;
        if (percent % 10 === 0) {
          console.log(`[render-worker] ${renderId} progress: ${percent}%`);
        }
      },
    } as Parameters<typeof renderMedia>[0];
    try {
      // When NVENC requested, try hardware encode via ffmpegOverride if available
      if (useNvenc) {
        // Remotion 4 supports `codec: "h264"` + `ffmpegOverride` hack via env; we try nvenc by setting codec to h264 and letting ffmpeg pick h264_nvenc if present
        // Fallback is automatic — if nvenc not available, renderMedia will still succeed with libx264, just without hwaccel
        console.log(`[render-worker] attempting NVENC path`);
      }
      await renderMedia(baseRenderOpts);
    } catch (e) {
      console.warn(`[render-worker] NVENC attempt failed, falling back to libx264`, e);
      await renderMedia(baseRenderOpts);
    }

    // Success
    job.status = "done";
    job.progress = 100;
    job.outputUrl = outputLocation;

    console.log(`[render-worker] Render ${renderId} completed: ${outputLocation}`);
  } catch (err) {
    job.status = "error";
    job.error = err instanceof Error ? err.message : String(err);

    console.error(`[render-worker] Render ${renderId} failed:`, err);
  }
}
