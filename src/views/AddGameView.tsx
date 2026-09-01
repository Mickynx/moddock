import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";

import { addGame, scanGames, ScannedGame } from "../api";

export function AddGameView({ onDone }: { onDone: () => void }) {
  const [scanning, setScanning] = useState(true);
  const [found, setFound] = useState<ScannedGame[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    scanGames()
      .then(setFound)
      .catch((e) => setError(`Scan failed: ${String(e)}`))
      // Always leave the scanning state, or the view pins on "Scanning…".
      .finally(() => setScanning(false));
  }, []);

  return (
    <PanelSection title={scanning ? "Scanning library…" : "Detected UE games"}>
      {error && (
        <PanelSectionRow>
          <div style={{ color: "#ff6a6a" }}>{error}</div>
        </PanelSectionRow>
      )}
      {!scanning && !error && found.length === 0 && (
        <PanelSectionRow>
          <div>No new Unreal Engine games found.</div>
        </PanelSectionRow>
      )}
      {found.map((g) => (
        <PanelSectionRow key={g.appid}>
          <ButtonItem
            layout="below"
            description={g.is_iostore ? "UE · IoStore" : "UE"}
            onClick={async () => {
              try {
                await addGame(g.appid, g.name, g.install_dir);
              } catch (e) {
                setError(`Could not add the game: ${String(e)}`);
                return;
              }
              onDone();
            }}
          >
            {g.name}
          </ButtonItem>
        </PanelSectionRow>
      ))}
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={onDone}>
          Back
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}
