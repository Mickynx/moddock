import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  ToggleField,
} from "@decky/ui";
import { useCallback, useEffect, useState } from "react";

import {
  deleteMod,
  listMods,
  ModEntry,
  removeGame,
  setModEnabled,
} from "../api";

export function GameDetailView({
  appid,
  name,
  onBack,
}: {
  appid: string;
  name: string;
  onBack: () => void;
}) {
  const [mods, setMods] = useState<ModEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  // Assume installed until the backend says otherwise, so the hint row does
  // not flash on the way in.
  const [installed, setInstalled] = useState(true);

  const refresh = useCallback(() => {
    listMods(appid)
      .then((r) => {
        setMods(r.mods);
        setInstalled(r.installed);
      })
      .catch((e) => setError(`Could not load mods: ${String(e)}`));
  }, [appid]);

  useEffect(refresh, [refresh]);

  return (
    <>
      <PanelSection title={name}>
        {error && (
          <PanelSectionRow>
            <div style={{ color: "#ff6a6a" }}>{error}</div>
          </PanelSectionRow>
        )}
        {!installed && (
          <PanelSectionRow>
            <div>
              Game not detected as installed — mod files cannot be changed.
            </div>
          </PanelSectionRow>
        )}
        {installed && mods.length === 0 && (
          <PanelSectionRow>
            <div>No mods yet — upload one from the Inbox.</div>
          </PanelSectionRow>
        )}
        {mods.map((mod) => (
          <PanelSectionRow key={mod.name}>
            <ToggleField
              label={mod.name}
              disabled={!installed}
              description={
                mod.state === "partial"
                  ? "partial — some files missing"
                  : "takes effect on next launch"
              }
              checked={mod.state === "enabled"}
              onChange={async (value) => {
                try {
                  const result = await setModEnabled(appid, mod.name, value);
                  setError(result.ok ? null : result.error);
                } catch (e) {
                  setError(String(e));
                }
                refresh();
              }}
            />
          </PanelSectionRow>
        ))}
        {mods.map((mod) => (
          <PanelSectionRow key={`del-${mod.name}`}>
            <ButtonItem
              layout="below"
              disabled={!installed}
              onClick={async () => {
                if (confirmDelete !== mod.name) {
                  setConfirmDelete(mod.name);
                  return;
                }
                try {
                  const result = await deleteMod(appid, mod.name);
                  setError(result.ok ? null : result.error);
                } catch (e) {
                  setError(String(e));
                }
                setConfirmDelete(null);
                refresh();
              }}
            >
              {confirmDelete === mod.name
                ? `Confirm delete "${mod.name}"`
                : `Delete "${mod.name}"`}
            </ButtonItem>
          </PanelSectionRow>
        ))}
      </PanelSection>
      <PanelSection>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={async () => {
              try {
                await removeGame(appid);
              } catch (e) {
                // Stay on this view so the error stays visible.
                setError(`Could not remove the game: ${String(e)}`);
                return;
              }
              onBack();
            }}
          >
            Remove game from list
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={onBack}>
            Back
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}
