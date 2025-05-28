# music.py
import logging
import asyncio
import random
import contextlib

from typing import Optional

import discord
from discord.ext import commands
from services import ytdlp_wrapper as ytdl
from services.guild_player import GuildPlayer
from config import config

logger = logging.getLogger("arona.music")


class Music(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}

    def _get_player(self, guild: discord.Guild) -> Optional[GuildPlayer]:
        if guild.id in self.players:
            return self.players[guild.id]
        if guild.voice_client:
            gp = GuildPlayer(guild, guild.voice_client)
            self.players[guild.id] = gp
            return gp
        return None

    async def _ensure_voice(self, ctx: commands.Context) -> Optional[discord.VoiceClient]:
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.reply("ボイスチャンネルに入ってからコマンドを実行してください。", silent=True)
            return None

        vc = ctx.guild.voice_client
        if not vc:
            vc = await ctx.author.voice.channel.connect()
        elif vc.channel != ctx.author.voice.channel:
            await ctx.reply("既に別のボイスチャンネルで稼働中です。", silent=True)
            return None
        return vc

    @commands.hybrid_command(name="play", description="曲またはプレイリストを再生します")
    async def play(self, ctx: commands.Context, *, query: str):
        """YouTube / ニコ動 URL もしくは検索ワード."""

        if not await ctx.defer():
            pass

        vc = await self._ensure_voice(ctx)
        if not vc:
            return

        try:
            tracks = await ytdl.extract(query)
        except Exception as e:
            logger.error("extract 失敗", exc_info=True)
            await ctx.reply(f"取得に失敗しました: {e}", silent=True)
            return

        player = self._get_player(ctx.guild)
        if not player:
            await ctx.reply("プレイヤー初期化に失敗しました。", silent=True)
            return

        if isinstance(tracks, list):
            await ctx.reply(config.get_message("playlist_added", count=len(tracks)), silent=True)

            async def _feed():
                for t in tracks:
                    t.requester_id = ctx.author.id
                    await player.enqueue(t)
                    await asyncio.sleep(random.uniform(1, 3))
            asyncio.create_task(_feed(), name=f"enqueue:{ctx.guild.id}")
        else:
            tracks.requester_id = ctx.author.id
            await player.enqueue(tracks)
            await ctx.reply(config.get_message("added_to_queue", title=tracks.title), silent=True)

        player.start()

    @commands.hybrid_command(name="nowplaying", description="再生中の曲を表示します")
    async def nowplaying(self, ctx: commands.Context):
        await ctx.defer()
        player = self._get_player(ctx.guild)
        if not (player and player.current_track):
            await ctx.reply("いまは何も再生していません。", silent=True)
            return
        await ctx.reply(config.get_message("now_playing", title=player.current_track.title), silent=True)

    @commands.hybrid_command(name="pause", description="一時停止します")
    async def pause(self, ctx: commands.Context):
        await ctx.defer()
        player = self._get_player(ctx.guild)
        if player and player.is_playing():
            player.pause()
            await ctx.reply("⏸️ 一時停止しました。", silent=True)
        else:
            await ctx.reply("再生中の曲がありません。", silent=True)

    @commands.hybrid_command(name="resume", description="再開します")
    async def resume(self, ctx: commands.Context):
        await ctx.defer()
        player = self._get_player(ctx.guild)
        if player and player.is_paused():
            player.resume()
            await ctx.reply("▶️ 再開しました。", silent=True)
        else:
            await ctx.reply("一時停止中の曲がありません。", silent=True)

    @commands.hybrid_command(name="skip", description="次の曲へスキップ")
    async def skip(self, ctx: commands.Context):
        await ctx.defer()
        player = self._get_player(ctx.guild)
        if player and player.is_playing():
            player.skip()
            await ctx.reply("⏭️ スキップしました。", silent=True)
        else:
            await ctx.reply("再生中の曲がありません。", silent=True)

    @commands.hybrid_command(name="stop", description="停止して退出します")
    async def stop(self, ctx: commands.Context):
        await ctx.defer()
        player = self._get_player(ctx.guild)
        if player:
            await player.stop()
            self.players.pop(ctx.guild.id, None)
            await ctx.reply("⏹️ 再生を停止し、ボイスチャンネルから退出しました。", silent=True)
        else:
            await ctx.reply("再生していません。", silent=True)

    @commands.hybrid_command(name="queue", description="現在のキューを表示")
    async def queue(self, ctx: commands.Context):
        await ctx.defer()
        player = self._get_player(ctx.guild)
        if not player:
            await ctx.reply("再生していません。", silent=True)
            return

        lines = []
        if player.current_track:
            lines.append(f"🎶 **Now**: {player.current_track.title}")

        if not player.upcoming():
            lines.append("*(キューは空です)*")
        else:
            for i, t in enumerate(player.upcoming(), start=1):
                lines.append(f"{i}. {t.title}")

        await ctx.reply("\n".join(lines), silent=True)

    @commands.hybrid_command(name="shuffle", description="キューをシャッフル")
    async def shuffle(self, ctx: commands.Context):
        await ctx.defer()
        player = self._get_player(ctx.guild)
        if player and player.upcoming():
            player.shuffle()
            await ctx.reply("🔀 キューをシャッフルしました。", silent=True)
        else:
            await ctx.reply("シャッフルする曲がありません。", silent=True)

    @commands.hybrid_command(name="clear", description="キューを空にします")
    async def clear(self, ctx: commands.Context):
        await ctx.defer()
        player = self._get_player(ctx.guild)
        if player:
            player.clear()
            await ctx.reply("🗑️ キューを空にしました。", silent=True)
        else:
            await ctx.reply("再生していません。", silent=True)

    @commands.hybrid_command(name="remove", description="キューから指定番号を削除")
    async def remove(self, ctx: commands.Context, position: int):
        await ctx.defer()
        player = self._get_player(ctx.guild)
        if not player or not player.upcoming():
            await ctx.reply("キューが空です。", silent=True)
            return
        try:
            track = player.remove(position - 1)
            await ctx.reply(f"🗑️ `{track.title}` をキューから削除しました。", silent=True)
        except IndexError:
            await ctx.reply("指定番号がキュー範囲外です。", silent=True)

    @commands.hybrid_command(name="loop", description="現在の曲をループ切替")
    async def loop(self, ctx: commands.Context):
        await ctx.defer()
        player = self._get_player(ctx.guild)
        if not player or not player.current_track:
            await ctx.reply("再生中の曲がありません。", silent=True)
            return
        player.loop_current = not player.loop_current
        await ctx.reply(f"🔁 ループ **{'ON' if player.loop_current else 'OFF'}**", silent=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.id == self.bot.user.id and before.channel and not after.channel:
            player = self.players.pop(member.guild.id, None)
            if player:
                await player.stop()


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
