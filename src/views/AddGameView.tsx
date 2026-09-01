import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";

import { addGame, scanGames, ScannedGame } from "../api";

export function AddGameView({ onDone }: { onDone: () => void }) {
  const [scanning, setScanning] = useState(true);
  const [found, setFound] = useState<ScannedGame[]>([]);

  useEffect(() => {
    scanGames().then((games) => {
      setFound(games);
      setScanning(false);
    });
  }, []);

  return (
    <PanelSection title={scanning ? "Scanning library…" : "Detected UE games"}>
      {!scanning && found.length === 0 && (
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
              await addGame(g.appid, g.name, g.install_dir);
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
