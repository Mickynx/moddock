import { definePlugin, addEventListener, removeEventListener } from "@decky/api";
import { staticClasses } from "@decky/ui";
import { useEffect, useState } from "react";

import { GamesView } from "./views/GamesView";
import { AddGameView } from "./views/AddGameView";
import { GameDetailView } from "./views/GameDetailView";
import { SettingsView } from "./views/SettingsView";

export type View =
  | { kind: "games" }
  | { kind: "add" }
  | { kind: "detail"; appid: string; name: string }
  | { kind: "settings" };

function Content() {
  const [view, setView] = useState<View>({ kind: "games" });
  // Bumped on every "moddock_upload" event so an open mod list refreshes
  // right after a web upload installs a mod.
  const [uploadTick, setUploadTick] = useState(0);

  useEffect(() => {
    const handler = () => setUploadTick((t) => t + 1);
    addEventListener("moddock_upload", handler);
    return () => removeEventListener("moddock_upload", handler);
  }, []);

  switch (view.kind) {
    case "add":
      return <AddGameView onDone={() => setView({ kind: "games" })} />;
    case "detail":
      return (
        <GameDetailView
          appid={view.appid}
          name={view.name}
          refreshKey={uploadTick}
          onBack={() => setView({ kind: "games" })}
        />
      );
    case "settings":
      return <SettingsView onBack={() => setView({ kind: "games" })} />;
    default:
      return <GamesView setView={setView} />;
  }
}

export default definePlugin(() => ({
  name: "ModDock",
  titleView: <div className={staticClasses.Title}>ModDock</div>,
  content: <Content />,
  icon: <span>⬒</span>,
}));
