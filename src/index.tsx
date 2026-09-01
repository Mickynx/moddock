import { definePlugin, addEventListener, removeEventListener } from "@decky/api";
import { staticClasses } from "@decky/ui";
import { useEffect, useState } from "react";

import { GamesView } from "./views/GamesView";
import { AddGameView } from "./views/AddGameView";
import { GameDetailView } from "./views/GameDetailView";
import { InboxView } from "./views/InboxView";
import { SettingsView } from "./views/SettingsView";

export type View =
  | { kind: "games" }
  | { kind: "add" }
  | { kind: "detail"; appid: string; name: string }
  | { kind: "inbox" }
  | { kind: "settings" };

function Content() {
  const [view, setView] = useState<View>({ kind: "games" });
  const [inboxTick, setInboxTick] = useState(0);

  useEffect(() => {
    const handler = () => setInboxTick((t) => t + 1);
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
          onBack={() => setView({ kind: "games" })}
        />
      );
    case "inbox":
      return (
        <InboxView
          refreshKey={inboxTick}
          onBack={() => setView({ kind: "games" })}
        />
      );
    case "settings":
      return <SettingsView onBack={() => setView({ kind: "games" })} />;
    default:
      return <GamesView setView={setView} inboxTick={inboxTick} />;
  }
}

export default definePlugin(() => ({
  name: "ModDock",
  titleView: <div className={staticClasses.Title}>ModDock</div>,
  content: <Content />,
  icon: <span>⬒</span>,
}));
