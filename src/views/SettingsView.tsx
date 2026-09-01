import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  ToggleField,
} from "@decky/ui";
import { useEffect, useState } from "react";

import { getUploaderStatus, setUploader, UploaderStatus } from "../api";

export function SettingsView({ onBack }: { onBack: () => void }) {
  const [status, setStatus] = useState<UploaderStatus>({
    running: false,
    url: null,
    qr_svg: null,
  });
  // Transport-level failure, as opposed to status.error which the backend
  // reports when the server could not bind.
  const [callError, setCallError] = useState<string | null>(null);

  useEffect(() => {
    getUploaderStatus()
      .then(setStatus)
      .catch((e) => setCallError(`Could not read the status: ${String(e)}`));
  }, []);

  const error = callError ?? status.error;

  return (
    <PanelSection title="Web Upload">
      <PanelSectionRow>
        <ToggleField
          label="Upload service"
          description="Serves an upload page on your LAN"
          checked={status.running}
          onChange={async (value) => {
            try {
              setStatus(await setUploader(value));
              setCallError(null);
            } catch (e) {
              setCallError(
                `Could not ${value ? "start" : "stop"} the service: ${String(e)}`,
              );
            }
          }}
        />
      </PanelSectionRow>
      {error && (
        <PanelSectionRow>
          <div style={{ color: "#ff6a6a" }}>{error}</div>
        </PanelSectionRow>
      )}
      {status.running && status.url && (
        <>
          <PanelSectionRow>
            <div style={{ wordBreak: "break-all" }}>{status.url}</div>
          </PanelSectionRow>
          {status.qr_svg && (
            <PanelSectionRow>
              <div
                style={{ display: "flex", justifyContent: "center" }}
                dangerouslySetInnerHTML={{ __html: status.qr_svg }}
              />
            </PanelSectionRow>
          )}
        </>
      )}
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={onBack}>
          Back
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}
