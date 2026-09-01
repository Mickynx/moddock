import decky


class Plugin:
    async def _main(self):
        decky.logger.info("ModDock loaded")

    async def _unload(self):
        decky.logger.info("ModDock unloaded")
