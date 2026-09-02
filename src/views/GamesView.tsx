import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";

import { getManagedGames, ManagedGame } from "../api";
import type { View } from "../index";

export function GamesView({ setView }: { setView: (v: View) => void }) {
  const [games, setGames] = useState<ManagedGame[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getManagedGames()
      .then((result) => {
        setGames(result);
        setError(null);
      })
      .catch((e) => setError(`Could not load games: ${String(e)}`));
  }, []);

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
      <PanelSection title="Upload">
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            description="Upload mods from your phone — they install directly"
            onClick={() => setView({ kind: "settings" })}
          >
            Upload Settings
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}
