"""Engine adapter contract.

An adapter converts a game install directory into an engine-specific
info object that must expose at least `mods_dir: Path` — the directory
whose contents the engine loads as mods. v1 ships only the Unreal
adapter; new engines add a module here and a branch in main.py's
detection call.
"""
