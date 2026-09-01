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

  useEffect(() => {
    getUploaderStatus().then(setStatus);
  }, []);

  return (
    <PanelSection title="Web Upload">
      <PanelSectionRow>
        <ToggleField
          label="Upload service"
          description="Serves an upload page on your LAN"
          checked={status.running}
          onChange={async (value) => setStatus(await setUploader(value))}
        />
      </PanelSectionRow>
      {status.error && (
        <PanelSectionRow>
          <div style={{ color: "#ff6a6a" }}>{status.error}</div>
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
