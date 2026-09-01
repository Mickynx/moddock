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

  const refresh = useCallback(() => {
    listMods(appid).then((r) => setMods(r.mods));
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
        {mods.length === 0 && (
          <PanelSectionRow>
            <div>No mods yet — upload one from the Inbox.</div>
          </PanelSectionRow>
        )}
        {mods.map((mod) => (
          <PanelSectionRow key={mod.name}>
            <ToggleField
              label={mod.name}
              description={
                mod.state === "partial"
                  ? "partial — some files missing"
                  : "takes effect on next launch"
              }
              checked={mod.state === "enabled"}
              onChange={async (value) => {
                const result = await setModEnabled(appid, mod.name, value);
                setError(result.ok ? null : result.error);
                refresh();
              }}
            />
          </PanelSectionRow>
        ))}
        {mods.map((mod) => (
          <PanelSectionRow key={`del-${mod.name}`}>
            <ButtonItem
              layout="below"
              onClick={async () => {
                if (confirmDelete !== mod.name) {
                  setConfirmDelete(mod.name);
                  return;
                }
                const result = await deleteMod(appid, mod.name);
                setError(result.ok ? null : result.error);
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
              await removeGame(appid);
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
