# cogs/earthquake.py

import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Literal
import asyncio

# 設定ファイルを保存するディレクトリとファイルのパス
DATA_DIR = 'data'
CONFIG_FILE = os.path.join(DATA_DIR, 'channel_earthquake_notification_config.json')


class EarthquakeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        print("🔄 EarthquakeCog 初期化開始...")

        self.ensure_data_dir()
        self.config = self.load_config()
        self.last_quake_id = None
        self.session = None
        self.jst = timezone(timedelta(hours=+9), 'JST')

        print("✅ EarthquakeCog 初期化完了")

    async def setup_hook(self):
        """ボットの準備が整った後に実行される"""
        print("🔄 EarthquakeCog セットアップ開始...")
        self.session = aiohttp.ClientSession()
        self.check_eew.start()
        print("✅ EarthquakeCog セットアップ完了")

    async def cog_unload(self):
        print("🔄 EarthquakeCog アンロード中...")
        self.check_eew.cancel()
        if self.session and not self.session.closed:
            await self.session.close()
        print("✅ EarthquakeCog アンロード完了")

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
                    print("⚠️ 設定ファイルの読み込みに失敗しました。")
                    return {}
        return {}

    def save_config(self):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=4)

    # --- ヘルパー関数 ---
    def scale_to_japanese(self, scale_code):
        scale_map = {
            -1: "震度情報なし", 10: "震度1", 20: "震度2", 30: "震度3", 40: "震度4",
            45: "震度5弱", 50: "震度5強", 55: "震度6弱", 60: "震度6強", 70: "震度7"
        }
        return scale_map.get(scale_code, f"不明({scale_code})")

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

    # --- スラッシュコマンド (チャンネル設定) ---
    @app_commands.command(name="earthquake", description="【誰でも設定可】緊急地震速報の通知チャンネルを設定します。")
    @app_commands.describe(channel="通知を送信するチャンネル")
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        guild_id = str(interaction.guild.id)
        self.config[guild_id] = channel.id
        self.save_config()
        await interaction.response.send_message(f"✅ 緊急地震速報の通知チャンネルを {channel.mention} に設定しました。",
                                                ephemeral=True)

    # --- スラッシュコマンド (テスト通知) ---
    @app_commands.command(name="test_earthquake", description="緊急地震速報のテスト通知を送信します。")
    @app_commands.describe(max_scale="テストしたい最大震度を選択してください。")
    async def test_earthquake(self, interaction: discord.Interaction, max_scale: Literal["震度3", "震度5強", "震度7"]):
        await interaction.response.defer()

        guild_id = str(interaction.guild.id)
        target_channel = None
        is_configured_channel = False

        if guild_id in self.config:
            channel_id = self.config[guild_id]
            channel = interaction.guild.get_channel(channel_id)
            if channel:
                target_channel = channel
                is_configured_channel = True
            else:
                target_channel = interaction.channel
        else:
            target_channel = interaction.channel

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

    # --- ステータス確認コマンド ---
    @app_commands.command(name="earthquake_status", description="緊急地震速報システムの状態を確認します。")
    async def status_earthquake(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="🔧 緊急地震速報システム状態",
            color=discord.Color.blue(),
            timestamp=datetime.now(self.jst)
        )

        # 基本状態
        embed.add_field(name="監視状態", value="✅ 動作中" if self.check_eew.is_running() else "❌ 停止中", inline=True)
        embed.add_field(name="セッション状態", value="✅ 正常" if self.session and not self.session.closed else "❌ 無効",
                        inline=True)
        embed.add_field(name="最後の地震ID", value=f"`{self.last_quake_id}`" if self.last_quake_id else "`未取得`",
                        inline=True)

        # 通知チャンネル状態
        guild_id = str(interaction.guild.id)
        if guild_id in self.config:
            channel = interaction.guild.get_channel(self.config[guild_id])
            channel_status = f"✅ {channel.mention}" if channel else "❌ チャンネル削除済み"
        else:
            channel_status = "⚠️ 未設定"

        embed.add_field(name="通知チャンネル", value=channel_status, inline=False)
        embed.set_footer(text="システム診断")

        await interaction.followup.send(embed=embed)

    # --- バックグラウンドタスク ---
    @tasks.loop(seconds=5)
    async def check_eew(self):
        if not self.session or self.session.closed:
            print("⚠️ HTTPセッションを再作成中...")
            self.session = aiohttp.ClientSession()

        url = "https://api.p2pquake.net/v2/history?codes=551&limit=1"

        try:
            async with self.session.get(url, timeout=10) as response:
                if response.status != 200:
                    print(f"⚠️ API応答エラー: {response.status}")
                    return

                data = await response.json()
                if not data:
                    return

                latest_quake = data[0]
                quake_id = latest_quake['id']

                # 初回実行時の処理
                if self.last_quake_id is None:
                    self.last_quake_id = quake_id
                    print(f"🔄 初期地震ID設定: {quake_id}")
                    return

                # 新しい地震の検知
                if self.last_quake_id != quake_id:
                    print(f"🚨 新しい緊急地震速報を検知: {quake_id}")
                    self.last_quake_id = quake_id
                    await self.send_notification(latest_quake)

        except asyncio.TimeoutError:
            print("⚠️ API接続タイムアウト")
        except Exception as e:
            print(f"❌ 地震監視エラー: {e}")

    async def send_notification(self, quake_data):
        """地震通知の送信"""
        try:
            earthquake = quake_data['earthquake']
            hypocenter = earthquake['hypocenter']
            report_type = quake_data['issue']['type']
            max_scale_jp = self.scale_to_japanese(earthquake['maxScale'])

            # 時刻処理（シンプル版）
            time_str = earthquake['time']
            try:
                # "dd日HH時MM分SS秒" フォーマットを想定
                quake_time = datetime.strptime(time_str, "%d日%H時%M分%S秒")
                now = datetime.now(self.jst)
                quake_time = quake_time.replace(year=now.year, month=now.month, tzinfo=self.jst)
            except ValueError:
                # 解析に失敗した場合は現在時刻を使用
                print(f"⚠️ 時刻解析失敗: {time_str}")
                quake_time = datetime.now(self.jst)

            # Embed作成
            embed = discord.Embed(
                title=f"🚨 緊急地震速報 ({report_type})",
                description=f"**最大震度 {max_scale_jp}** の地震が検知されました。",
                color=self.get_embed_color(earthquake['maxScale']),
                timestamp=quake_time
            )
            embed.add_field(name="震源地", value=f"`{hypocenter['name']}`", inline=True)
            embed.add_field(name="マグニチュード", value=f"`M{earthquake['magnitude']}`", inline=True)
            embed.add_field(name="深さ", value=f"`{hypocenter['depth']}km`", inline=True)

            # 各地の震度情報
            points = quake_data.get('points', [])
            if points:
                areas_text = ""
                sorted_points = sorted(points, key=lambda p: p['scale'], reverse=True)
                for point in sorted_points[:5]:  # 上位5地点
                    areas_text += f"・`{self.scale_to_japanese(point['scale'])}` - {point['addr']}\n"
                if areas_text:
                    embed.add_field(name="各地の予測震度", value=areas_text, inline=False)

            embed.set_footer(text="Powered by P2P地震情報 API")
            embed.set_thumbnail(url="https://i.imgur.com/CDJVt0h.png")

            # 各サーバーの通知チャンネルに送信
            sent_count = 0
            for guild_id, channel_id in self.config.items():
                guild = self.bot.get_guild(int(guild_id))
                if guild:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        try:
                            await channel.send(embed=embed)
                            sent_count += 1
                        except discord.Forbidden:
                            print(f"❌ 送信権限なし: {guild.name}")
                        except Exception as e:
                            print(f"❌ 送信エラー: {e}")

            print(f"📤 通知送信完了: {sent_count}チャンネル")

        except Exception as e:
            print(f"❌ 通知処理エラー: {e}")

    @check_eew.before_loop
    async def before_check_eew(self):
        await self.bot.wait_until_ready()
        print("🔄 地震監視開始...")


async def setup(bot: commands.Bot):
    print("🔄 EarthquakeCog セットアップ関数開始...")
    cog = EarthquakeCog(bot)
    await bot.add_cog(cog)
    # ボットの準備完了後にセットアップを実行
    if hasattr(cog, 'setup_hook'):
        bot.loop.create_task(cog.setup_hook())
    print("✅ EarthquakeCog セットアップ関数完了")