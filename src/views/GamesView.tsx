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

  useEffect(() => {
    getManagedGames().then(setGames);
    listInbox().then((entries) => setInboxCount(entries.length));
  }, [inboxTick]);

  return (
    <>
      <PanelSection title="Games">
        {games.map((g) => (
          <PanelSectionRow key={g.appid}>
            <ButtonItem
              layout="below"
              disabled={!g.installed}
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
