import asyncio
import logging
import discord
import sys
import warnings
from discord.ext import commands

from config import Config
import modules.shittim.error.ShittimError as error

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

class Shittim(commands.Bot):
    def __init__(self):
        try:
            with warnings.catch_warnings(record=True) as w:
                self.config = Config()
            for warning in w:
                logging.warning(f"Warning while loading config:\n{warning.message}" + "ShittimでのWarningは致命的なものなので起動できません。")
                sys.exit(1)
        except error.ShittimConfigDefaultNotFoundError as e:
            logging.error("Shittimのdefault.configファイルが見つかりませんでした。起動できません。再インストールするか、リポジトリから最新のdefault.configファイルを取得してください。")
            sys.exit(1)

        super().__init__(command_prefix=self.config.get('discord.prefix', '!'),
                         intents=discord.Intents.all())

        self.initial_extensions = [
            'modules.music_arona.music_arona',
            'modules.llmcord_plana.llmcord_plana',   
            'modules.shittim.shittim',
        ]

    async def setup_hook(self):
        for ext in self.initial_extensions:
            try:
                with warnings.catch_warnings(record=True) as w:
                    await self.load_extension(ext)
                for warning in w:
                    logging.warning(f"Warning while loading extension {ext}:\n{warning.message}")
                    self.unload_extension(ext)
            except Exception as e:
                logging.error(f"Failed to load extension {ext}:\n{e}")
            
        await self.tree.sync()
        logging.info("Synced application commands")

    def config_validate(self):
        if not self.config.get('discord.token') or self.config.get('discord.token') == "YOUR_DISCORD_TOKEN_HERE":
            logging.error("Discord token is not set in config. Please set it in config.yaml.")
            sys.exit(1)
        
        if not self.config.get('discord.prefix'):
            logging.error("Discord prefix is not set in config. Please set it in config.yaml.")
            sys.exit(1)

async def main():
    bot = Shittim()
    bot.config_validate()
    async with bot:
        await bot.start(bot.config.get('discord.token'))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Shittim stopped")
        sys.exit(0)
    except Exception as e:
        logging.error(f"Error in main: {e}")
        sys.exit(1)
