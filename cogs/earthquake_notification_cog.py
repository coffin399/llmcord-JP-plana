# cogs/earthquake.py

import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Literal

# 設定ファイルを保存するディレクトリとファイルのパス
DATA_DIR = 'data'
CONFIG_FILE = os.path.join(DATA_DIR, 'channel_earthquake_notification_config.json')


class EarthquakeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ensure_data_dir()
        self.config = self.load_config()
        self.last_quake_id = None
        self.session = aiohttp.ClientSession()
        self.jst = timezone(timedelta(hours=+9), 'JST')
        self.check_eew.start()

    def cog_unload(self):
        self.check_eew.cancel()
        if self.session:
            self.bot.loop.create_task(self.session.close())

    def ensure_data_dir(self):
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)
            print(f"'{DATA_DIR}' ディレクトリを作成しました。")

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return {}
        return {}

    def save_config(self):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=4)

    # --- ヘルパー関数 (変更なし) ---
    def scale_to_japanese(self, scale_code):
        scale_map = {
            -1: "震度情報なし", 10: "震度1", 20: "震度2", 30: "震度3", 40: "震度4",
            45: "震度5弱", 50: "震度5強", 55: "震度6弱", 60: "震度6強", 70: "震度7"
        }
        return scale_map.get(scale_code, "不明")

    def get_embed_color(self, scale_code):
        if scale_code >= 55:
            return discord.Color.dark_red()
        elif scale_code >= 50:
            return discord.Color.red()
        elif scale_code >= 40:
            return discord.Color.orange()
        elif scale_code >= 30:
            return discord.Color.gold()
        else:
            return discord.Color.blue()

    # --- スラッシュコマンド (チャンネル設定) (変更なし) ---
    @app_commands.command(name="earthquake", description="【誰でも設定可】緊急地震速報の通知チャンネルを設定します。")
    @app_commands.describe(channel="通知を送信するチャンネル")
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        guild_id = str(interaction.guild.id)
        self.config[guild_id] = channel.id
        self.save_config()
        await interaction.response.send_message(f"✅ 緊急地震速報の通知チャンネルを {channel.mention} に設定しました。",
                                                ephemeral=True)

    @set_channel.error
    async def set_channel_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        print(f"set_channelコマンドでエラーが発生しました: {error}")
        await interaction.response.send_message(
            f"⚠️ コマンドの実行中にエラーが発生しました。ボットに必要な権限（メッセージ送信など）があるか確認してください。",
            ephemeral=True)

    # --- スラッシュコマンド (テスト通知) (修正箇所) ---
    @app_commands.command(name="test_earthquake", description="緊急地震速報のテスト通知を送信します。")
    @app_commands.describe(max_scale="テストしたい最大震度を選択してください。")
    async def test_earthquake(self, interaction: discord.Interaction, max_scale: Literal["震度3", "震度5強", "震度7"]):
        # 誰でも応答が見えるように ephemeral=False (デフォルト) で応答を保留
        await interaction.response.defer()

        guild_id = str(interaction.guild.id)
        target_channel = None  # 送信先チャンネル
        is_configured_channel = False

        # 通知チャンネルが設定されているか確認
        if guild_id in self.config:
            channel_id = self.config[guild_id]
            channel = interaction.guild.get_channel(channel_id)
            if channel:
                target_channel = channel
                is_configured_channel = True
            else:
                # 設定はあるがチャンネルが見つからない場合、コマンド実行チャンネルにフォールバック
                target_channel = interaction.channel
        else:
            # 通知チャンネルが未設定の場合、コマンドが実行されたチャンネルを使用
            target_channel = interaction.channel

        # ダミーデータの作成
        scale_map = {"震度3": 30, "震度5強": 50, "震度7": 70}
        scale_code = scale_map.get(max_scale, 30)

        embed = discord.Embed(
            title=f"🚨【テスト】緊急地震速報 (予報)",
            description=f"**最大震度 {max_scale}** の地震が検知されました。",
            color=self.get_embed_color(scale_code),
            timestamp=datetime.now(self.jst)
        )
        embed.add_field(name="震源地", value="`テスト震源`", inline=True)
        embed.add_field(name="マグニチュード", value="`M7.0`", inline=True)
        embed.add_field(name="深さ", value="`10km`", inline=True)
        areas_text = (
            f"・`{max_scale}` - テスト県A市\n"
            f"・`震度4` - テスト県B市\n"
            f"・`震度3` - テスト県C市\n"
        )
        embed.add_field(name="各地の予測震度", value=areas_text, inline=False)
        embed.set_footer(text="これはテスト通知です | Powered by P2P地震情報 API")
        embed.set_thumbnail(url="https://i.imgur.com/CDJVt0h.png")

        # 決定した送信先チャンネルにメッセージを送信
        try:
            await target_channel.send(embed=embed)
            if is_configured_channel:
                await interaction.followup.send(
                    f"✅ 設定されたチャンネル {target_channel.mention} にテスト通知を送信しました。")
            else:
                await interaction.followup.send(
                    f"✅ このチャンネルにテスト通知を送信しました。\nℹ️ 本番の通知は `/earthquake` コマンドで設定したチャンネルに送信されます。")
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ {target_channel.mention} にメッセージを送信する権限がありません。ボットの権限を確認してください。")
        except Exception as e:
            await interaction.followup.send(f"❌ 未知のエラーにより、通知の送信に失敗しました: {e}")

    # --- バックグラウンドタスク (変更なし) ---
    @tasks.loop(seconds=2)
    async def check_eew(self):
        url = "https://api.p2pquake.net/v2/history?codes=551&limit=1"
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    print(f"API Error: Status code {response.status}")
                    return

                data = await response.json()
                if not data: return

                latest_quake = data[0]
                quake_id = latest_quake['id']

                if self.last_quake_id != quake_id:
                    if self.last_quake_id is None:
                        self.last_quake_id = quake_id
                        return

                    self.last_quake_id = quake_id

                    earthquake = latest_quake['earthquake']
                    hypocenter = earthquake['hypocenter']
                    report_type = latest_quake['issue']['type']
                    max_scale_jp = self.scale_to_japanese(earthquake['maxScale'])

                    time_str = earthquake['time']
                    quake_time_utc = datetime.strptime(time_str, "%d日%H時%M分%S秒")
                    now = datetime.now(self.jst)
                    quake_time_utc = quake_time_utc.replace(year=now.year, month=now.month)
                    quake_time_jst = quake_time_utc.replace(tzinfo=timezone.utc).astimezone(self.jst)

                    embed = discord.Embed(
                        title=f"🚨 緊急地震速報 ({report_type})",
                        description=f"**最大震度 {max_scale_jp}** の地震が検知されました。",
                        color=self.get_embed_color(earthquake['maxScale']),
                        timestamp=quake_time_jst
                    )
                    embed.add_field(name="震源地", value=f"`{hypocenter['name']}`", inline=True)
                    embed.add_field(name="マグニチュード", value=f"`M{earthquake['magnitude']}`", inline=True)
                    embed.add_field(name="深さ", value=f"`{hypocenter['depth']}km`", inline=True)

                    points = latest_quake.get('points', [])
                    if points:
                        areas_text = ""
                        sorted_points = sorted(points, key=lambda p: p['scale'], reverse=True)
                        for point in sorted_points[:5]:
                            areas_text += f"・`{self.scale_to_japanese(point['scale'])}` - {point['addr']}\n"
                        if areas_text:
                            embed.add_field(name="各地の予測震度", value=areas_text, inline=False)

                    embed.set_footer(text="Powered by P2P地震情報 API")
                    embed.set_thumbnail(url="https://i.imgur.com/CDJVt0h.png")

                    for guild_id, channel_id in self.config.items():
                        guild = self.bot.get_guild(int(guild_id))
                        if guild:
                            channel = guild.get_channel(channel_id)
                            if channel:
                                try:
                                    await channel.send(embed=embed)
                                except discord.Forbidden:
                                    print(f"Error: チャンネル {channel.name} ({channel_id}) への送信権限がありません。")
                                except Exception as e:
                                    print(f"Error sending message to {channel_id}: {e}")

        except aiohttp.ClientError as e:
            print(f"AIOHttp Error: {e}")
        except Exception as e:
            print(f"An unexpected error occurred in check_eew: {e}")

    @check_eew.before_loop
    async def before_check_eew(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(EarthquakeCog(bot))