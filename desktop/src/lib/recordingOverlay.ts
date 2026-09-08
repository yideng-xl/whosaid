import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { manageAsyncListener } from "./recording";

export const setRecordingOverlayEnabled = (enabled: boolean) =>
  invoke<boolean>("set_recording_overlay_enabled", { enabled });

export function watchRecordingOverlay(onValue: (enabled: boolean) => void, onError: (error: unknown) => void): () => void {
  let disposed = false;
  let revision = 0;
  const dispose = manageAsyncListener(listen<boolean>("recording://overlay-enabled", ({ payload }) => {
    revision++;
    onValue(payload === true);
  }).then(unlisten => {
    const before = revision;
    void invoke<boolean>("get_recording_overlay_enabled").then(enabled => {
      if (!disposed && revision === before) onValue(enabled === true);
    }).catch(error => { if (!disposed) onError(error); });
    return unlisten;
  }), onError);
  return () => { disposed = true; dispose(); };
}
