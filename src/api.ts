import { callable } from "@decky/api";

export interface ScannedGame {
  appid: string;
  name: string;
  install_dir: string;
  is_iostore: boolean;
}

export interface ManagedGame {
  appid: string;
  name: string;
  installed: boolean;
  is_iostore: boolean;
}

export interface ModEntry {
  name: string;
  state: "enabled" | "disabled" | "partial";
  // Name of the install method that placed the mod; "legacy" for mods stored
  // before methods existed.
  recipe_name: string;
}

export interface ModsResponse {
  installed: boolean;
  running_hint: boolean;
  mods: ModEntry[];
}

export interface OpResult {
  ok: boolean;
  error: string | null;
}

export interface UploaderStatus {
  running: boolean;
  url: string | null;
  qr_svg: string | null;
  // The configured LAN port; the backend always reports it, running or not.
  port?: number;
  // Set when the upload server failed to start (e.g. port already in use) or
  // when a requested port was rejected.
  error?: string | null;
}

export interface RecipeSummary {
  id: string;
  name: string;
  builtin: boolean;
  // A rule count, not the rules themselves: enough to hint at how elaborate
  // a method is.
  rules: number;
}

export const scanGames = callable<[], ScannedGame[]>("scan_games");
export const getManagedGames = callable<[], ManagedGame[]>("get_managed_games");
export const addGame =
  callable<[appid: string, name: string, install_dir: string], void>("add_game");
export const removeGame = callable<[appid: string], void>("remove_game");
export const listMods = callable<[appid: string], ModsResponse>("list_mods");
export const setModEnabled =
  callable<[appid: string, mod_name: string, enabled: boolean], OpResult>(
    "set_mod_enabled",
  );
export const deleteMod =
  callable<[appid: string, mod_name: string], OpResult>("delete_mod");
export const setUploader =
  callable<[enabled: boolean], UploaderStatus>("set_uploader");
export const getUploaderStatus =
  callable<[], UploaderStatus>("get_uploader_status");
export const setUploadPort =
  callable<[port: number], UploaderStatus>("set_upload_port");
export const listRecipes = callable<[], RecipeSummary[]>("list_recipes");
export const deleteRecipe =
  callable<[recipe_id: string], OpResult>("delete_recipe");
