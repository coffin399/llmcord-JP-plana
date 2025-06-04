from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional
import warnings

import discord
from discord.ext import commands
from modules.music_arona.services import ytdlp_wrapper as ytdl
from modules.music_arona.services.guild_player import GuildPlayer
from modules.music_arona.config import Config
import modules.music_arona.error.AronaError as error

logger = logging.getLogger("arona.music")

def fmt_dur(sec: int) -> str:
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h:02}:{m:02}:{s:02}" if h else f"{m:02}:{s:02}"

async def _display_name(bot: commands.Bot, guild: Optional[discord.Guild], user_id: int) -> str:
    """Return a *display name* (nickname if possible, else username)."""
    if guild:
        m = guild.get_member(user_id) or await guild.fetch_member(user_id, default=None)
        if m:
            return m.display_name
    try:
        u = await bot.fetch_user(user_id)
        if u:
            return u.display_name
    except discord.HTTPException:
        pass
    return "Unknown"

class Music(commands.Cog):
    """A trimmed, readable Arona music cog with back-ported goodies."""

    def __init__(self, bot: commands.Bot, config: Config):
        self.bot = bot
        self.config = config
        self.players: dict[int, GuildPlayer] = {}

    def _player(self, guild: discord.Guild) -> Optional[GuildPlayer]:
        if guild.id in self.players:
            return self.players[guild.id]
        if guild.voice_client:  # connect hook
            gp = GuildPlayer(guild, guild.voice_client)
            self.players[guild.id] = gp
            return gp
        return None

    async def _ensure_voice(self, ctx: commands.Context) -> Optional[discord.VoiceClient]:
        """Join the author's voice channel if necessary, or make sure we're in the same one."""
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
        await ctx.defer()
        vc = await self._ensure_voice(ctx)
        if not vc:
            return

        try:
            tracks = await ytdl.extract(query)
        except Exception as e:
            logger.error("extract 失敗", exc_info=True)
            await ctx.reply(f"取得に失敗しました: {e}", silent=True)
            return

        player = self._player(ctx.guild)
        if not player:
            await ctx.reply("プレイヤー初期化に失敗しました。", silent=True)
            return

        if isinstance(tracks, list):
            await ctx.reply(self.config.get_message("playlist_added", count=len(tracks)), silent=True)

            async def _feed():
                for t in tracks:
                    t.requester_id = ctx.author.id
                    await player.enqueue(t)
                    await asyncio.sleep(random.uniform(1, 3))

            asyncio.create_task(_feed(), name=f"enqueue:{ctx.guild.id}")
        else:
            tracks.requester_id = ctx.author.id
            await player.enqueue(tracks)
            print(tracks)
            await ctx.reply(self.config.get_message("added_to_queue", title=tracks.title, duration=fmt_dur(tracks.duration), requester_display_name=await _display_name(self.bot, ctx.guild, tracks.requester_id)), silent=True)

        player.start()

    @commands.hybrid_command(name="nowplaying", description="再生中の曲を表示します")
    async def nowplaying(self, ctx: commands.Context):
        await ctx.defer()
        player = self._player(ctx.guild)
        if not (player and player.current_track):
            await ctx.reply("いまは何も再生していません。", silent=True)
            return

        track = player.current_track
        requester = await _display_name(self.bot, ctx.guild, track.requester_id) if track.requester_id else "Unknown"
        await ctx.reply(
            self.config.get_message(
                "now_playing",
                title=track.title,
                requester_display_name=requester,
                duration=fmt_dur(track.duration),
            ),
            silent=True,
        )

    @commands.hybrid_command(name="pause", description="一時停止します")
    async def pause(self, ctx: commands.Context):
        await ctx.defer()
        p = self._player(ctx.guild)
        if p and p.is_playing():
            p.pause()
            await ctx.reply("⏸️ 一時停止しました。", silent=True)
        else:
            await ctx.reply("再生中の曲がありません。", silent=True)

    @commands.hybrid_command(name="resume", description="再開します")
    async def resume(self, ctx: commands.Context):
        await ctx.defer()
        p = self._player(ctx.guild)
        if p and p.is_paused():
            p.resume()
            await ctx.reply("▶️ 再開しました。", silent=True)
        else:
            await ctx.reply("一時停止中の曲がありません。", silent=True)

    @commands.hybrid_command(name="skip", description="次の曲へスキップ")
    async def skip(self, ctx: commands.Context):
        await ctx.defer()
        p = self._player(ctx.guild)
        if p and (p.is_playing() or p.is_paused()):
            p.skip()
            await ctx.reply("⏭️ スキップしました。", silent=True)
        else:
            await ctx.reply("再生中の曲がありません。", silent=True)

    @commands.hybrid_command(name="stop", description="停止して退出します")
    async def stop(self, ctx: commands.Context):
        await ctx.defer()
        p = self._player(ctx.guild)
        if p:
            await p.stop()
            self.players.pop(ctx.guild.id, None)
            await ctx.reply("⏹️ 再生を停止し、ボイスチャンネルから退出しました。", silent=True)
        else:
            await ctx.reply("再生していません。", silent=True)

    @commands.hybrid_command(name="queue", description="現在のキューを表示")
    async def queue(self, ctx: commands.Context):
        await ctx.defer()
        p = self._player(ctx.guild)
        if not p:
            await ctx.reply("再生していません。", silent=True)
            return

        lines = []
        if p.current_track:
            req = await _display_name(self.bot, ctx.guild, p.current_track.requester_id) if p.current_track.requester_id else "Unknown"
            lines.append(f"🎶 **Now**: {p.current_track.title} (req. **{req}**)")

        up_next = p.upcoming()
        if not up_next:
            lines.append("*(キューは空です)*")
        else:
            for i, t in enumerate(up_next, start=1):
                dn = await _display_name(self.bot, ctx.guild, t.requester_id) if t.requester_id else "Unknown"
                lines.append(f"{i}. {t.title} (req. **{dn}**)")

        await ctx.reply("\n".join(lines), silent=True)

    @commands.hybrid_command(name="shuffle", description="キューをシャッフル")
    async def shuffle(self, ctx: commands.Context):
        await ctx.defer()
        p = self._player(ctx.guild)
        if p and p.upcoming():
            p.shuffle()
            await ctx.reply("🔀 キューをシャッフルしました。", silent=True)
        else:
            await ctx.reply("シャッフルする曲がありません。", silent=True)

    @commands.hybrid_command(name="clear", description="キューを空にします")
    async def clear(self, ctx: commands.Context):
        await ctx.defer()
        p = self._player(ctx.guild)
        if p:
            p.clear()
            await ctx.reply("🗑️ キューを空にしました。", silent=True)
        else:
            await ctx.reply("再生していません。", silent=True)

    @commands.hybrid_command(name="remove", description="キューから指定番号を削除")
    async def remove(self, ctx: commands.Context, position: int):
        await ctx.defer()
        p = self._player(ctx.guild)
        if not p or not p.upcoming():
            await ctx.reply("キューが空です。", silent=True)
            return
        try:
            track = p.remove(position - 1)
            await ctx.reply(f"🗑️ `{track.title}` をキューから削除しました。", silent=True)
        except IndexError:
            await ctx.reply("指定番号がキュー範囲外です。", silent=True)

    @commands.hybrid_command(name="loop", description="現在の曲をループ切替")
    async def loop(self, ctx: commands.Context):
        await ctx.defer()
        p = self._player(ctx.guild)
        if not p or not p.current_track:
            await ctx.reply("再生中の曲がありません。", silent=True)
            return
        p.loop_current = not p.loop_current
        await ctx.reply(f"🔁 ループ **{'ON' if p.loop_current else 'OFF'}**", silent=True)

    @commands.hybrid_command(name="volume", description="音量を 0-200 % で設定／表示します")
    async def volume(self, ctx: commands.Context, level: Optional[int] = None):
        await ctx.defer()
        p = self._player(ctx.guild)
        if not p:
            await ctx.reply("再生していません。", silent=True)
            return

        if level is None:
            await ctx.reply(f"🔊 現在の音量: **{round(p.volume * 100)}%**", silent=True)
            return

        if not (0 <= level <= 200):
            await ctx.reply("音量は 0-200 で指定してください。", silent=True)
            return

        p.set_volume(level / 100)
        await ctx.reply(f"🔊 音量を **{level}%** に設定しました。", silent=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.id == self.bot.user.id and before.channel and not after.channel:
            player = self.players.pop(member.guild.id, None)
            if player:
                await player.stop()

async def setup(bot: commands.Bot):
    config = Config()
    await bot.add_cog(Music(bot, config))
