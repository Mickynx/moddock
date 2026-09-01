import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";

import { getManagedGames, listInbox, ManagedGame } from "../api";
import type { View } from "../index";

export function GamesView({
  setView,
  inboxTick,
}: {
  setView: (v: View) => void;
  inboxTick: number;
}) {
  const [games, setGames] = useState<ManagedGame[]>([]);
  const [inboxCount, setInboxCount] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getManagedGames()
      .then(setGames)
      .catch((e) => setError(`Could not load games: ${String(e)}`));
    listInbox()
      .then((entries) => setInboxCount(entries.length))
      .catch((e) => setError(`Could not read the inbox: ${String(e)}`));
  }, [inboxTick]);

  return (
    <>
      <PanelSection title="Games">
        {error && (
          <PanelSectionRow>
            <div style={{ color: "#ff6a6a" }}>{error}</div>
          </PanelSectionRow>
        )}
        {games.map((g) => (
          <PanelSectionRow key={g.appid}>
            {/* Kept enabled even when not installed, so the game stays
                reachable for removal from the detail view. */}
            <ButtonItem
              layout="below"
              description={
                g.installed
                  ? g.is_iostore
                    ? "UE · IoStore"
                    : "UE"
                  : "not detected as installed"
              }
              onClick={() =>
                setView({ kind: "detail", appid: g.appid, name: g.name })
              }
            >
              {g.name}
            </ButtonItem>
          </PanelSectionRow>
        ))}
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => setView({ kind: "add" })}>
            Add Game
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title="Import">
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => setView({ kind: "inbox" })}>
            {`Inbox (${inboxCount})`}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() => setView({ kind: "settings" })}
          >
            Upload Settings
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}
