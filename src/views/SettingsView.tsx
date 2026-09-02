import {
  ButtonItem,
  Field,
  PanelSection,
  PanelSectionRow,
  TextField,
  ToggleField,
} from "@decky/ui";
import { useCallback, useEffect, useState } from "react";

import {
  deleteRecipe,
  getUploaderStatus,
  listRecipes,
  RecipeSummary,
  setUploader,
  setUploadPort,
  UploaderStatus,
} from "../api";

const DEFAULT_PORT = 8765;

export function SettingsView({ onBack }: { onBack: () => void }) {
  const [status, setStatus] = useState<UploaderStatus>({
    running: false,
    url: null,
    qr_svg: null,
  });
  // Transport-level failure, as opposed to status.error which the backend
  // reports when the server could not bind or refused a port.
  const [callError, setCallError] = useState<string | null>(null);
  // The port field is edited freely and only applied on demand, so it keeps
  // its own draft value instead of following `status` on every keystroke.
  const [portDraft, setPortDraft] = useState<string>(String(DEFAULT_PORT));
  const [recipes, setRecipes] = useState<RecipeSummary[]>([]);

  const apply = (next: UploaderStatus) => {
    setStatus(next);
    if (next.port !== undefined) setPortDraft(String(next.port));
    setCallError(null);
  };

  const refreshRecipes = useCallback(() => {
    listRecipes()
      .then(setRecipes)
      .catch((e) =>
        setCallError(`Could not load install methods: ${String(e)}`),
      );
  }, []);

  useEffect(() => {
    getUploaderStatus()
      .then(apply)
      .catch((e) => setCallError(`Could not read the status: ${String(e)}`));
  }, []);

  useEffect(refreshRecipes, [refreshRecipes]);

  const error = callError ?? status.error;

  return (
    <>
      <PanelSection title="Web Upload">
        <PanelSectionRow>
          <ToggleField
            label="Upload service"
            description="Serves an upload page on your LAN"
            checked={status.running}
            onChange={async (value) => {
              try {
                apply(await setUploader(value));
              } catch (e) {
                setCallError(
                  `Could not ${value ? "start" : "stop"} the service: ${String(e)}`,
                );
              }
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <TextField
            label="Port"
            description="1024–65535; applying restarts a running service"
            value={portDraft}
            mustBeNumeric
            rangeMin={1024}
            rangeMax={65535}
            onChange={(e) => setPortDraft(e.target.value)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={portDraft === String(status.port ?? DEFAULT_PORT)}
            onClick={async () => {
              const port = Number.parseInt(portDraft, 10);
              if (!Number.isFinite(port)) {
                setCallError("Port must be a number");
                return;
              }
              try {
                apply(await setUploadPort(port));
              } catch (e) {
                setCallError(`Could not change the port: ${String(e)}`);
              }
            }}
          >
            Apply port
          </ButtonItem>
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
      </PanelSection>
      <PanelSection title="Install methods">
        {recipes.length === 0 && (
          <PanelSectionRow>
            <div>No install methods yet.</div>
          </PanelSectionRow>
        )}
        {recipes.map((recipe) =>
          recipe.builtin ? (
            <PanelSectionRow key={recipe.id}>
              <Field label={recipe.name} description="built-in" />
            </PanelSectionRow>
          ) : (
            <PanelSectionRow key={recipe.id}>
              {/* Custom methods are the only deletable ones; the backend
                  refuses built-ins anyway and its message lands in the
                  error row above. */}
              <ButtonItem
                layout="below"
                label={recipe.name}
                description={`custom · ${recipe.rules} ${
                  recipe.rules === 1 ? "rule" : "rules"
                }`}
                onClick={async () => {
                  try {
                    const result = await deleteRecipe(recipe.id);
                    setCallError(result.ok ? null : result.error);
                  } catch (e) {
                    setCallError(String(e));
                  }
                  refreshRecipes();
                }}
              >
                Delete
              </ButtonItem>
            </PanelSectionRow>
          ),
        )}
        <PanelSectionRow>
          <div>New methods are created on the upload page.</div>
        </PanelSectionRow>
      </PanelSection>
      <PanelSection>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={onBack}>
            Back
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}
