import { definePlugin } from "@decky/api";
import { staticClasses } from "@decky/ui";

function Content() {
  return <div>ModDock</div>;
}

export default definePlugin(() => ({
  name: "ModDock",
  titleView: <div className={staticClasses.Title}>ModDock</div>,
  content: <Content />,
  icon: <span>M</span>,
}));
