import {
  ButtonItem,
  Dropdown,
  PanelSection,
  PanelSectionRow,
} from "@decky/ui";
import { useCallback, useEffect, useState } from "react";

import {
  assignInboxEntry,
  deleteInboxEntry,
  getManagedGames,
  InboxEntry,
  listInbox,
  ManagedGame,
} from "../api";

export function InboxView({
  refreshKey,
  onBack,
}: {
  refreshKey: number;
  onBack: () => void;
}) {
  const [entries, setEntries] = useState<InboxEntry[]>([]);
  const [games, setGames] = useState<ManagedGame[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listInbox()
      .then(setEntries)
      .catch((e) => setError(`Could not read the inbox: ${String(e)}`));
    getManagedGames()
      .then(setGames)
      .catch((e) => setError(`Could not load games: ${String(e)}`));
  }, []);

  useEffect(refresh, [refresh, refreshKey]);

  const installedGames = games.filter((g) => g.installed);

  return (
    <PanelSection title="Inbox">
      {error && (
        <PanelSectionRow>
          <div style={{ color: "#ff6a6a" }}>{error}</div>
        </PanelSectionRow>
      )}
      {!error && entries.length === 0 && (
        <PanelSectionRow>
          <div>Empty. Enable the upload service and send a file.</div>
        </PanelSectionRow>
      )}
      {entries.map((entry) => (
        <PanelSectionRow key={entry.filename}>
          <div style={{ width: "100%" }}>
            <div>{entry.filename}</div>
            <div style={{ fontSize: "0.8em", opacity: 0.7 }}>
              {entry.status === "ready" ? entry.detail : `⚠ ${entry.detail}`}
            </div>
            {entry.status === "ready" && installedGames.length > 0 && (
              <Dropdown
                rgOptions={installedGames.map((g) => ({
                  data: g.appid,
                  label: `Install to ${g.name}`,
                }))}
                selectedOption={null}
                strDefaultLabel="Assign to game…"
                onChange={async (option) => {
                  try {
                    const result = await assignInboxEntry(
                      entry.filename,
                      option.data as string,
                      entry.filename.replace(/\.[^.]+$/, ""),
                    );
                    setError(result.ok ? null : result.error);
                  } catch (e) {
                    setError(`Install failed: ${String(e)}`);
                  }
                  refresh();
                }}
              />
            )}
            <ButtonItem
              layout="below"
              onClick={async () => {
                try {
                  await deleteInboxEntry(entry.filename);
                } catch (e) {
                  setError(`Delete failed: ${String(e)}`);
                }
                refresh();
              }}
            >
              Delete
            </ButtonItem>
          </div>
        </PanelSectionRow>
      ))}
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={onBack}>
          Back
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}
