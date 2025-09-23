# cogs/earthquake_tsunami.py

import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
import asyncio

# 設定ファイルを保存するディレクトリとファイルのパス
DATA_DIR = 'data'
CONFIG_FILE = os.path.join(DATA_DIR, 'earthquake_tsunami_notification_config.json')


class EarthquakeTsunamiCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        print("🔄 EarthquakeTsunamiCog 初期化開始...")

        self.ensure_data_dir()
        self.config = self.load_config()

        # 各情報タイプの最後のID追跡
        self.last_ids = {
            'eew': None,  # 緊急地震速報 (code: 551, 予報タイプ)
            'quake': None,  # 地震情報 (code: 551, 確定タイプ)
            'tsunami': None  # 津波予報 (code: 552)
        }

        # 処理済みID管理（重複処理防止）
        self.processed_ids = {
            'eew': set(),
            'quake': set(),
            'tsunami': set()
        }

        self.session = None
        self.jst = timezone(timedelta(hours=+9), 'JST')

        # API仕様
        self.api_base_url = "https://api.p2pquake.net/v2"
        self.request_headers = {
            'User-Agent': 'Discord-Bot-EarthquakeTsunami/1.0'
        }

        # 情報コード定義（完全版）
        self.info_codes = {
            'eew': 551,  # 緊急地震速報
            'quake': 551,  # 地震情報（EEWと同じコードだが内容で区別）
            'tsunami': [552, 551]  # 津波予報（複数コードから検索）
        }

        print("✅ EarthquakeTsunamiCog 初期化完了")

    async def setup_hook(self):
        """ボットの準備が整った後に実行される"""
        print("🔄 EarthquakeTsunamiCog セットアップ開始...")
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers=self.request_headers
        )
        self.check_earthquake_info.start()
        print("✅ EarthquakeTsunamiCog セットアップ完了")

    async def cog_unload(self):
        print("🔄 EarthquakeTsunamiCog アンロード中...")
        self.check_earthquake_info.cancel()
        if self.session and not self.session.closed:
            await self.session.close()
        print("✅ EarthquakeTsunamiCog アンロード完了")

    def ensure_data_dir(self):
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)
            print(f"'{DATA_DIR}' ディレクトリを作成しました。")

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                try:
                    config = json.load(f)
                    # 新しい設定形式に対応
                    for guild_id in config:
                        if isinstance(config[guild_id], int):
                            # 旧形式を新形式に変換
                            config[guild_id] = {
                                'eew': config[guild_id],
                                'quake': config[guild_id],
                                'tsunami': config[guild_id]
                            }
                    return config
                except json.JSONDecodeError:
                    print("⚠️ 設定ファイルの読み込みに失敗しました。")
                    return {}
        return {}

    def save_config(self):
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)

    # --- ヘルパー関数 ---
    def scale_to_japanese(self, scale_code):
        """震度コードを日本語表記に変換"""
        if scale_code is None or scale_code == -1:
            return "震度情報なし"

        scale_map = {
            10: "震度1", 20: "震度2", 30: "震度3", 40: "震度4",
            45: "震度5弱", 50: "震度5強", 55: "震度6弱", 60: "震度6強", 70: "震度7"
        }
        return scale_map.get(scale_code, f"不明({scale_code})")

    def get_embed_color(self, scale_code, info_type="quake"):
        """情報タイプと震度に応じたEmbed色を取得"""
        if info_type == "tsunami":
            return discord.Color.purple()  # 津波は紫色

        if scale_code is None or scale_code == -1:
            return discord.Color.light_grey()
        elif scale_code >= 55:  # 震度6弱以上
            return discord.Color.dark_red()
        elif scale_code >= 50:  # 震度5強
            return discord.Color.red()
        elif scale_code >= 40:  # 震度4
            return discord.Color.orange()
        elif scale_code >= 30:  # 震度3
            return discord.Color.gold()
        else:  # 震度2以下
            return discord.Color.blue()

    def parse_earthquake_time(self, time_str, announced_time=None):
        """地震時刻の解析"""
        try:
            if isinstance(time_str, str):
                # "2024年01月01日 12時34分頃" 形式
                if "年" in time_str and "月" in time_str and "日" in time_str:
                    time_str_clean = time_str.replace("年", "/").replace("月", "/").replace("日", " ").replace("時",
                                                                                                               ":").replace(
                        "分頃", ":00").replace("分", ":00")
                    parsed_time = datetime.strptime(time_str_clean, "%Y/%m/%d %H:%M:%S")
                    return parsed_time.replace(tzinfo=self.jst)

                # "01日12時34分" 形式
                elif "日" in time_str and "時" in time_str and "分" in time_str:
                    time_str_clean = time_str.replace("日", "日 ").replace("時", ":").replace("分", ":").replace("秒",
                                                                                                                 "")
                    if not time_str_clean.endswith(":"):
                        time_str_clean += "00"

                    now = datetime.now(self.jst)
                    try:
                        parsed_time = datetime.strptime(time_str_clean, "%d日 %H:%M:%S")
                        parsed_time = parsed_time.replace(year=now.year, month=now.month, tzinfo=self.jst)
                        return parsed_time
                    except ValueError:
                        parsed_time = datetime.strptime(time_str_clean.rstrip(":"), "%d日 %H:%M")
                        parsed_time = parsed_time.replace(year=now.year, month=now.month, tzinfo=self.jst)
                        return parsed_time

            # 解析に失敗した場合
            if announced_time:
                return datetime.fromisoformat(announced_time.replace('Z', '+00:00')).astimezone(self.jst)
            else:
                return datetime.now(self.jst)

        except Exception as e:
            print(f"❌ 時刻解析エラー: {time_str} - {e}")
            return datetime.now(self.jst)

    def format_magnitude(self, magnitude):
        """マグニチュードの整形"""
        if magnitude is None or magnitude == -1:
            return "不明"
        return f"M{magnitude:.1f}"

    def format_depth(self, depth):
        """震源の深さの整形"""
        if depth is None or depth == -1:
            return "不明"
        elif depth == 0:
            return "ごく浅い"
        else:
            return f"{depth}km"

    def get_tsunami_info(self, data):
        """津波情報の解析"""
        tsunami_info = {
            'has_tsunami': False,
            'warning_level': None,
            'areas': [],
            'description': ""
        }

        # 津波情報の確認
        tsunami = data.get('tsunami', {})
        if tsunami:
            tsunami_info['has_tsunami'] = True

            # 津波予報の種類
            forecast = tsunami.get('forecast', {})
            if forecast:
                # 大津波警報・津波警報・津波注意報の判定
                grade = forecast.get('grade', '')
                if grade:
                    if 'MajorWarning' in grade:
                        tsunami_info['warning_level'] = '大津波警報'
                    elif 'Warning' in grade:
                        tsunami_info['warning_level'] = '津波警報'
                    elif 'Watch' in grade:
                        tsunami_info['warning_level'] = '津波注意報'

                # 津波予報区域
                areas = forecast.get('areas', [])
                tsunami_areas = []
                for area in areas:
                    area_name = area.get('name', '')
                    grade = area.get('grade', '')
                    if area_name:
                        tsunami_areas.append({
                            'name': area_name,
                            'grade': grade
                        })
                tsunami_info['areas'] = tsunami_areas

            # 津波の説明文
            comment = tsunami.get('comment', '')
            if comment:
                tsunami_info['description'] = comment

        return tsunami_info

    # --- スラッシュコマンド ---
    @app_commands.command(name="earthquake_channel", description="地震・津波情報の通知チャンネルを設定します。")
    @app_commands.describe(
        channel="通知を送信するチャンネル",
        info_type="通知したい情報の種類"
    )
    async def set_channel(self, interaction: discord.Interaction,
                          channel: discord.TextChannel,
                          info_type: Literal["緊急地震速報", "地震情報", "津波予報", "すべて"]):
        guild_id = str(interaction.guild.id)

        # 設定の初期化
        if guild_id not in self.config:
            self.config[guild_id] = {}

        # 情報タイプに応じて設定
        if info_type == "緊急地震速報":
            self.config[guild_id]['eew'] = channel.id
        elif info_type == "地震情報":
            self.config[guild_id]['quake'] = channel.id
        elif info_type == "津波予報":
            self.config[guild_id]['tsunami'] = channel.id
        elif info_type == "すべて":
            self.config[guild_id]['eew'] = channel.id
            self.config[guild_id]['quake'] = channel.id
            self.config[guild_id]['tsunami'] = channel.id

        self.save_config()

        await interaction.response.send_message(
            f"✅ **{info_type}** の通知チャンネルを {channel.mention} に設定しました。",
            ephemeral=True
        )

    @app_commands.command(name="earthquake_test", description="地震・津波情報のテスト通知を送信します。")
    @app_commands.describe(
        info_type="テストしたい情報の種類",
        max_scale="テストしたい最大震度（地震情報の場合）",
        tsunami_level="テストしたい津波レベル（津波予報の場合）"
    )
    async def test_notification(self, interaction: discord.Interaction,
                                info_type: Literal["緊急地震速報", "地震情報", "津波予報"],
                                max_scale: Optional[Literal["震度3", "震度5強", "震度7"]] = "震度5強",
                                tsunami_level: Optional[Literal["津波注意報", "津波警報", "大津波警報"]] = "津波警報"):
        await interaction.response.defer()

        guild_id = str(interaction.guild.id)

        # 通知チャンネルの取得
        target_channel = None
        is_configured = False

        if guild_id in self.config:
            channel_mapping = {
                "緊急地震速報": 'eew',
                "地震情報": 'quake',
                "津波予報": 'tsunami'
            }

            config_key = channel_mapping.get(info_type)
            if config_key and config_key in self.config[guild_id]:
                channel_id = self.config[guild_id][config_key]
                channel = interaction.guild.get_channel(channel_id)
                if channel:
                    target_channel = channel
                    is_configured = True

        if not target_channel:
            target_channel = interaction.channel

        # テストデータの作成
        scale_map = {"震度3": 30, "震度5強": 50, "震度7": 70}
        scale_code = scale_map.get(max_scale, 50)

        if info_type == "津波予報":
            embed = await self.create_tsunami_test_embed(tsunami_level)
        else:
            embed = await self.create_earthquake_test_embed(info_type, max_scale, scale_code)

        # 送信
        try:
            await target_channel.send(embed=embed)
            if is_configured:
                await interaction.followup.send(
                    f"✅ 設定されたチャンネル {target_channel.mention} に **{info_type}** のテスト通知を送信しました。")
            else:
                await interaction.followup.send(
                    f"✅ このチャンネルに **{info_type}** のテスト通知を送信しました。\n"
                    f"ℹ️ 本番の通知は `/earthquake_channel` コマンドで設定したチャンネルに送信されます。")
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ {target_channel.mention} にメッセージを送信する権限がありません。")
        except Exception as e:
            await interaction.followup.send(f"❌ 通知の送信に失敗しました: {e}")

    async def create_earthquake_test_embed(self, info_type, max_scale, scale_code):
        """地震情報テストEmbedの作成"""
        if info_type == "緊急地震速報":
            title = f"🚨【テスト】緊急地震速報 (予報)"
            description = f"**最大震度 {max_scale}** の地震が検知されました。"
        else:
            title = f"📊【テスト】地震情報"
            description = f"**最大震度 {max_scale}** の地震が発生しました。"

        embed = discord.Embed(
            title=title,
            description=description,
            color=self.get_embed_color(scale_code),
            timestamp=datetime.now(self.jst)
        )

        embed.add_field(name="🌏 震源地", value="```テスト震源地```", inline=True)
        embed.add_field(name="📊 マグニチュード", value="```M7.0```", inline=True)
        embed.add_field(name="📏 深さ", value="```10km```", inline=True)

        areas_text = (
            f"🔴 **{max_scale}** - テスト県A市\n"
            f"🟠 **震度4** - テスト県B市\n"
            f"🟡 **震度3** - テスト県C市"
        )
        embed.add_field(name="📍 各地の震度", value=areas_text, inline=False)
        embed.set_footer(text="これはテスト通知です | Powered by P2P地震情報 API v2")
        embed.set_thumbnail(url="https://www.p2pquake.net/images/QuakeLogo_100x100.png")

        return embed

    async def create_tsunami_test_embed(self, tsunami_level):
        """津波予報テストEmbedの作成"""
        emoji_map = {
            "津波注意報": "🟡",
            "津波警報": "🟠",
            "大津波警報": "🔴"
        }

        embed = discord.Embed(
            title=f"{emoji_map.get(tsunami_level, '🌊')}【テスト】{tsunami_level}",
            description=f"**{tsunami_level}** が発表されました。",
            color=discord.Color.purple(),
            timestamp=datetime.now(self.jst)
        )

        embed.add_field(name="🌏 震源地", value="```テスト海域```", inline=True)
        embed.add_field(name="📊 マグニチュード", value="```M7.5```", inline=True)
        embed.add_field(name="📏 深さ", value="```10km```", inline=True)

        test_areas = f"🌊 **{tsunami_level}**\n・テスト県沿岸\n・テスト湾\n・テスト海岸"
        embed.add_field(name="🏖️ 予報区域", value=test_areas, inline=False)

        if tsunami_level == "大津波警報":
            warning_text = "⚠️ **直ちに避難してください** ⚠️"
        elif tsunami_level == "津波警報":
            warning_text = "⚠️ 直ちに海岸や川から離れ、高いところに避難してください。"
        else:
            warning_text = "⚠️ 海の中や海岸付近は危険です。海から上がって、海岸から離れてください。"

        embed.add_field(name="⚠️ 注意事項", value=warning_text, inline=False)
        embed.set_footer(text="これはテスト通知です | 気象庁")
        embed.set_thumbnail(url="https://www.p2pquake.net/images/QuakeLogo_100x100.png")

        return embed

    @app_commands.command(name="earthquake_status", description="地震・津波情報システムの状態を確認します。")
    async def status_system(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="🔧 地震・津波情報システム状態",
            color=discord.Color.blue(),
            timestamp=datetime.now(self.jst)
        )

        # 監視状態
        embed.add_field(name="🔄 監視状態", value="✅ 動作中" if self.check_earthquake_info.is_running() else "❌ 停止中",
                        inline=True)
        embed.add_field(name="🌐 セッション状態",
                        value="✅ 正常" if self.session and not self.session.closed else "❌ 無効", inline=True)

        # 最後のID状況
        id_status = ""
        for info_type, last_id in self.last_ids.items():
            type_names = {'eew': 'EEW', 'quake': 'QUAKE', 'tsunami': 'TSUNAMI'}
            processed_count = len(self.processed_ids.get(info_type, set()))
            id_status += f"**{type_names[info_type]}**: `{last_id or '未取得'}` (処理済み: {processed_count}件)\n"
        embed.add_field(name="🆔 最後のID", value=id_status, inline=False)

        # 通知チャンネル状況
        guild_id = str(interaction.guild.id)
        if guild_id in self.config:
            channel_status = ""
            for info_type in ['eew', 'quake', 'tsunami']:
                if info_type in self.config[guild_id]:
                    channel = interaction.guild.get_channel(self.config[guild_id][info_type])
                    status = f"✅ {channel.mention}" if channel else "❌ 削除済み"
                else:
                    status = "⚠️ 未設定"

                type_names = {'eew': '緊急地震速報', 'quake': '地震情報', 'tsunami': '津波予報'}
                channel_status += f"**{type_names[info_type]}**: {status}\n"
        else:
            channel_status = "⚠️ すべて未設定"

        embed.add_field(name="📢 通知チャンネル", value=channel_status, inline=False)
        embed.set_footer(text="システム診断完了 | P2P地震情報 API v2")

        await interaction.followup.send(embed=embed)

    # --- バックグラウンドタスク ---
    @tasks.loop(seconds=5)
    async def check_earthquake_info(self):
        """地震・津波情報の監視"""
        if not self.session or self.session.closed:
            print("⚠️ HTTPセッションを再作成中...")
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers=self.request_headers
            )

        # 地震情報監視（code: 551を両方のタイプで監視）
        await self.check_earthquake_data()  # EEWとQUAKE両方をチェック
        await self.check_tsunami_data()  # 津波情報を包括的にチェック

    async def check_info_type(self, info_type, code):
        """特定の情報タイプをチェック"""
        url = f"{self.api_base_url}/history?codes={code}&limit=1"

        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    print(f"⚠️ API応答エラー ({info_type}): {response.status}")
                    return

                data = await response.json()
                if not data:
                    return

                latest_info = data[0]
                info_id = latest_info['id']

                # 初回実行時の処理
                if self.last_ids[info_type] is None:
                    self.last_ids[info_type] = info_id
                    print(f"🔄 初期ID設定 ({info_type}): {info_id}")
                    return

                # 新しい情報の検知
                if self.last_ids[info_type] != info_id:
                    print(f"🆕 新しい{info_type}情報を検知: {info_id}")
                    self.last_ids[info_type] = info_id

                    # 情報タイプに応じた処理
                    if info_type == 'eew':
                        # 緊急地震速報と地震情報を区別
                        issue_type = latest_info.get('issue', {}).get('type', '')
                        if '予報' in issue_type or 'EEW' in issue_type or issue_type == '緊急地震速報':
                            await self.send_eew_notification(latest_info)
                        else:
                            # 確定情報として扱う
                            await self.send_quake_notification(latest_info)

                    elif info_type == 'tsunami':
                        # 津波情報があるかチェック
                        tsunami_info = self.get_tsunami_info(latest_info)
                        if tsunami_info['has_tsunami']:
                            await self.send_tsunami_notification(latest_info, tsunami_info)

        except asyncio.TimeoutError:
            print(f"⚠️ API接続タイムアウト ({info_type})")
        except Exception as e:
            print(f"❌ 情報監視エラー ({info_type}): {e}")

    async def send_eew_notification(self, data):
        """緊急地震速報の送信"""
        await self.send_notification(data, 'eew', "🚨 緊急地震速報")

    async def send_quake_notification(self, data):
        """地震情報の送信"""
        await self.send_notification(data, 'quake', "📊 地震情報")

    async def send_tsunami_notification(self, data, tsunami_info):
        """津波予報の送信"""
        # 津波情報のEmbed作成
        warning_level = tsunami_info.get('warning_level', '津波予報')

        emoji_map = {
            "大津波警報": "🔴",
            "津波警報": "🟠",
            "津波注意報": "🟡"
        }

        embed = discord.Embed(
            title=f"{emoji_map.get(warning_level, '🌊')} {warning_level}",
            description=f"**{warning_level}** が発表されました。",
            color=discord.Color.purple(),
            timestamp=datetime.now(self.jst)
        )

        # 基本の地震情報
        earthquake = data.get('earthquake', {})
        if earthquake:
            hypocenter = earthquake.get('hypocenter', {})
            embed.add_field(name="🌏 震源地", value=f"```{hypocenter.get('name', '不明')}```", inline=True)
            embed.add_field(name="📊 マグニチュード",
                            value=f"```{self.format_magnitude(earthquake.get('magnitude', -1))}```", inline=True)
            embed.add_field(name="📏 深さ", value=f"```{self.format_depth(hypocenter.get('depth', -1))}```", inline=True)

        # 津波予報区域
        if tsunami_info['areas']:
            area_text = ""
            for area in tsunami_info['areas'][:5]:  # 最大5件
                area_text += f"🌊 **{area.get('grade', warning_level)}** - {area['name']}\n"
            if area_text:
                embed.add_field(name="🏖️ 予報区域", value=area_text, inline=False)

        # 注意事項
        if warning_level == "大津波警報":
            warning_text = "⚠️ **直ちに避難してください** ⚠️\n高台や避難ビルなど安全な場所へ"
        elif warning_level == "津波警報":
            warning_text = "⚠️ **直ちに避難してください**\n海岸や川から離れ、高いところへ"
        else:
            warning_text = "⚠️ 海の中や海岸付近は危険です\n海から上がって、海岸から離れてください"

        embed.add_field(name="⚠️ 避難指示", value=warning_text, inline=False)

        if tsunami_info['description']:
            embed.add_field(name="ℹ️ 詳細情報", value=tsunami_info['description'][:500], inline=False)

        embed.set_footer(text="気象庁 | 津波から身を守るため直ちに避難を")
        embed.set_thumbnail(url="https://www.p2pquake.net/images/QuakeLogo_100x100.png")

        # 津波チャンネルに送信
        await self.send_embed_to_channels(embed, 'tsunami')

    async def send_notification(self, data, info_type, title_prefix):
        """通知の送信（共通処理）"""
        try:
            earthquake = data.get('earthquake', {})
            if not earthquake:
                return

            hypocenter = earthquake.get('hypocenter', {})
            issue_data = data.get('issue', {})
            report_type = issue_data.get('type', '情報')

            max_scale = earthquake.get('maxScale', -1)
            max_scale_jp = self.scale_to_japanese(max_scale)

            # 時刻解析
            time_str = earthquake.get('time', '')
            announced_time = issue_data.get('time', '')
            quake_time = self.parse_earthquake_time(time_str, announced_time)

            # Embed作成
            if info_type == 'eew':
                description = f"**最大震度 {max_scale_jp}** の地震が検知されました。"
            else:
                description = f"**最大震度 {max_scale_jp}** の地震が発生しました。"

            embed = discord.Embed(
                title=f"{title_prefix} ({report_type})",
                description=description,
                color=self.get_embed_color(max_scale, info_type),
                timestamp=quake_time
            )

            # 基本情報
            embed.add_field(name="🌏 震源地", value=f"```{hypocenter.get('name', '不明')}```", inline=True)
            embed.add_field(name="📊 マグニチュード",
                            value=f"```{self.format_magnitude(earthquake.get('magnitude', -1))}```", inline=True)
            embed.add_field(name="📏 深さ", value=f"```{self.format_depth(hypocenter.get('depth', -1))}```", inline=True)

            # 各地の震度情報
            points = data.get('points', [])
            if points:
                areas_text = ""
                sorted_points = sorted(points, key=lambda p: p.get('scale', 0), reverse=True)

                for point in sorted_points[:8]:  # 上位8地点
                    scale = point.get('scale', -1)
                    scale_jp = self.scale_to_japanese(scale)
                    addr = point.get('addr', '不明')

                    # 震度に応じた絵文字
                    if scale >= 55:
                        emoji = "🔴"
                    elif scale >= 50:
                        emoji = "🟠"
                    elif scale >= 40:
                        emoji = "🟡"
                    elif scale >= 30:
                        emoji = "🟢"
                    else:
                        emoji = "🔵"

                    areas_text += f"{emoji} **{scale_jp}** - {addr}\n"

                if areas_text:
                    embed.add_field(name="📍 各地の震度", value=areas_text[:1024], inline=False)

            # 津波情報があるかチェック
            tsunami_info = self.get_tsunami_info(data)
            if tsunami_info['has_tsunami'] and info_type == 'quake':
                tsunami_text = f"🌊 **{tsunami_info.get('warning_level', '津波予報')}** が発表されています"
                embed.add_field(name="🌊 津波情報", value=tsunami_text, inline=False)

            embed.set_footer(text="Powered by P2P地震情報 API v2 | 気象庁データ")
            embed.set_thumbnail(url="https://www.p2pquake.net/images/QuakeLogo_100x100.png")

            # 該当チャンネルに送信
            await self.send_embed_to_channels(embed, info_type)

        except Exception as e:
            print(f"❌ {info_type}通知処理エラー: {e}")

    async def send_embed_to_channels(self, embed, info_type):
        """指定された情報タイプのチャンネルにEmbedを送信"""
        if not self.config:
            print(f"⚠️ {info_type}通知チャンネルが設定されていません")
            return

        sent_count = 0
        failed_count = 0

        for guild_id, guild_config in self.config.items():
            if info_type not in guild_config:
                continue

            guild = self.bot.get_guild(int(guild_id))
            if guild:
                channel_id = guild_config[info_type]
                channel = guild.get_channel(channel_id)
                if channel:
                    try:
                        await channel.send(embed=embed)
                        sent_count += 1
                    except discord.Forbidden:
                        print(f"❌ 権限不足 ({info_type}): {guild.name} ({channel.name})")
                        failed_count += 1
                    except Exception as e:
                        print(f"❌ 送信失敗 ({info_type}): {guild.name} - {e}")
                        failed_count += 1
                else:
                    print(f"⚠️ チャンネル未発見 ({info_type}): {channel_id} (Guild: {guild.name})")
                    failed_count += 1
            else:
                print(f"⚠️ サーバー未発見: {guild_id}")
                failed_count += 1

        print(f"📤 {info_type}通知送信完了: 成功 {sent_count}件, 失敗 {failed_count}件")

    # --- 追加コマンド ---
    @app_commands.command(name="earthquake_latest", description="最新の地震・津波情報を表示します。")
    @app_commands.describe(info_type="表示したい情報の種類")
    async def latest_info(self, interaction: discord.Interaction,
                          info_type: Literal["緊急地震速報", "地震情報", "津波予報"] = "地震情報"):
        await interaction.response.defer()

        try:
            # 情報コードの決定
            code_mapping = {
                "緊急地震速報": 551,
                "地震情報": 551,  # 地震情報も551
                "津波予報": 552
            }

            code = code_mapping.get(info_type, 551)
            url = f"{self.api_base_url}/history?codes={code}&limit=20"  # 多めに取得して分類

            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data:
                        if info_type == "緊急地震速報":
                            # EEWタイプのデータを探す
                            eew_data = None
                            for item in data:
                                issue_type = item.get('issue', {}).get('type', '')
                                if self.is_eew_type(issue_type):
                                    eew_data = item
                                    break

                            if eew_data:
                                await self.send_info_to_user(interaction.followup, eew_data, info_type)
                            else:
                                await interaction.followup.send("⚠️ 最新の緊急地震速報が見つかりませんでした。")

                        elif info_type == "地震情報":
                            # 地震情報タイプのデータを探す
                            quake_data = None
                            for item in data:
                                issue_type = item.get('issue', {}).get('type', '')
                                if self.is_quake_type(issue_type):
                                    quake_data = item
                                    break

                            if quake_data:
                                await self.send_info_to_user(interaction.followup, quake_data, info_type)
                            else:
                                await interaction.followup.send("⚠️ 最新の地震情報が見つかりませんでした。")

                        elif info_type == "津波予報":
                            # 津波情報を複数コードから検索
                            tsunami_data = None
                            codes_to_search = [552, 551]

                            for search_code in codes_to_search:
                                search_url = f"{self.api_base_url}/history?codes={search_code}&limit=30"
                                async with self.session.get(search_url) as search_response:
                                    if search_response.status == 200:
                                        search_data = await search_response.json()
                                        if search_data:
                                            for item in search_data:
                                                tsunami_info = self.get_tsunami_info(item)
                                                if tsunami_info['has_tsunami']:
                                                    tsunami_data = item
                                                    print(f"🔍 津波情報発見 (code: {search_code}): {item['id']}")
                                                    break
                                        if tsunami_data:
                                            break

                            if tsunami_data:
                                tsunami_info = self.get_tsunami_info(tsunami_data)
                                await self.send_tsunami_info_to_user(interaction.followup, tsunami_data, tsunami_info)
                            else:
                                await interaction.followup.send("⚠️ 最新の津波予報情報が見つかりませんでした。")
                    else:
                        await interaction.followup.send(f"⚠️ 最新の{info_type}が見つかりませんでした。")
                else:
                    await interaction.followup.send(f"❌ API接続エラー: {response.status}")
        except Exception as e:
            await interaction.followup.send(f"❌ 情報の取得に失敗しました: {e}")

    async def send_info_to_user(self, followup, data, info_type):
        """ユーザーへの情報表示"""
        try:
            earthquake = data.get('earthquake', {})
            if not earthquake:
                await followup.send("⚠️ 地震データの解析に失敗しました。")
                return

            hypocenter = earthquake.get('hypocenter', {})
            issue_data = data.get('issue', {})
            report_type = issue_data.get('type', '情報')

            max_scale = earthquake.get('maxScale', -1)
            max_scale_jp = self.scale_to_japanese(max_scale)

            # 時刻解析
            time_str = earthquake.get('time', '')
            announced_time = issue_data.get('time', '')
            quake_time = self.parse_earthquake_time(time_str, announced_time)

            # タイトルの設定
            title_map = {
                "緊急地震速報": "🚨 最新の緊急地震速報",
                "地震情報": "📊 最新の地震情報"
            }

            title = f"{title_map.get(info_type, '📊 最新の地震情報')} ({report_type})"

            if info_type == "緊急地震速報":
                description = f"**最大震度 {max_scale_jp}** の地震が検知されました。"
            else:
                description = f"**最大震度 {max_scale_jp}** の地震が発生しました。"

            embed = discord.Embed(
                title=title,
                description=description,
                color=self.get_embed_color(max_scale),
                timestamp=quake_time
            )

            # 基本情報
            embed.add_field(name="🌏 震源地", value=f"```{hypocenter.get('name', '不明')}```", inline=True)
            embed.add_field(name="📊 マグニチュード",
                            value=f"```{self.format_magnitude(earthquake.get('magnitude', -1))}```", inline=True)
            embed.add_field(name="📏 深さ", value=f"```{self.format_depth(hypocenter.get('depth', -1))}```", inline=True)

            # 各地の震度情報
            points = data.get('points', [])
            if points:
                areas_text = ""
                sorted_points = sorted(points, key=lambda p: p.get('scale', 0), reverse=True)

                for point in sorted_points[:8]:  # 上位8地点
                    scale = point.get('scale', -1)
                    scale_jp = self.scale_to_japanese(scale)
                    addr = point.get('addr', '不明')

                    # 震度に応じた絵文字
                    if scale >= 55:
                        emoji = "🔴"
                    elif scale >= 50:
                        emoji = "🟠"
                    elif scale >= 40:
                        emoji = "🟡"
                    elif scale >= 30:
                        emoji = "🟢"
                    else:
                        emoji = "🔵"

                    areas_text += f"{emoji} **{scale_jp}** - {addr}\n"

                if areas_text:
                    embed.add_field(name="📍 各地の震度", value=areas_text[:1024], inline=False)

            # 津波情報の確認
            tsunami_info = self.get_tsunami_info(data)
            if tsunami_info['has_tsunami']:
                tsunami_text = f"🌊 **{tsunami_info.get('warning_level', '津波予報')}** が発表されています"
                if tsunami_info['areas']:
                    tsunami_text += f"\n対象: {', '.join([area['name'] for area in tsunami_info['areas'][:3]])}"
                embed.add_field(name="🌊 津波情報", value=tsunami_text, inline=False)

            embed.set_footer(text="Powered by P2P地震情報 API v2 | 気象庁データ")
            embed.set_thumbnail(url="https://www.p2pquake.net/images/QuakeLogo_100x100.png")

            await followup.send(embed=embed)

        except Exception as e:
            error_msg = f"❌ 地震情報の表示でエラー: {e}"
            print(error_msg)
            await followup.send(error_msg)

    async def send_tsunami_info_to_user(self, followup, data, tsunami_info):
        """津波情報のユーザー表示"""
        try:
            warning_level = tsunami_info.get('warning_level', '津波予報')

            emoji_map = {
                "大津波警報": "🔴",
                "津波警報": "🟠",
                "津波注意報": "🟡"
            }

            embed = discord.Embed(
                title=f"{emoji_map.get(warning_level, '🌊')} 最新の津波情報: {warning_level}",
                description=f"**{warning_level}** が発表されています。",
                color=discord.Color.purple(),
                timestamp=datetime.now(self.jst)
            )

            # 基本の地震情報
            earthquake = data.get('earthquake', {})
            if earthquake:
                hypocenter = earthquake.get('hypocenter', {})
                embed.add_field(name="🌏 震源地", value=f"```{hypocenter.get('name', '不明')}```", inline=True)
                embed.add_field(name="📊 マグニチュード",
                                value=f"```{self.format_magnitude(earthquake.get('magnitude', -1))}```", inline=True)
                embed.add_field(name="📏 深さ", value=f"```{self.format_depth(hypocenter.get('depth', -1))}```",
                                inline=True)

            # 津波予報区域
            if tsunami_info['areas']:
                area_text = ""
                for area in tsunami_info['areas'][:8]:  # 最大8件
                    area_text += f"🌊 **{area.get('grade', warning_level)}** - {area['name']}\n"
                if area_text:
                    embed.add_field(name="🏖️ 予報区域", value=area_text, inline=False)

            # 注意事項
            if warning_level == "大津波警報":
                warning_text = "🚨 **直ちに避難してください** 🚨\n高台や避難ビルなど安全な場所へ移動"
            elif warning_level == "津波警報":
                warning_text = "⚠️ **直ちに避難してください**\n海岸や川から離れ、高いところへ"
            else:
                warning_text = "⚠️ 海の中や海岸付近は危険です\n海から上がって、海岸から離れてください"

            embed.add_field(name="⚠️ 避難指示", value=warning_text, inline=False)

            if tsunami_info['description']:
                embed.add_field(name="ℹ️ 詳細情報", value=tsunami_info['description'][:500], inline=False)

            embed.set_footer(text="気象庁 | 津波から身を守るため直ちに避難を")
            embed.set_thumbnail(url="https://www.p2pquake.net/images/QuakeLogo_100x100.png")

            await followup.send(embed=embed)

        except Exception as e:
            error_msg = f"❌ 津波情報の表示でエラー: {e}"
            print(error_msg)
            await followup.send(error_msg)

    @app_commands.command(name="tsunami_search", description="津波情報を手動で検索します（デバッグ用）。")
    async def search_tsunami(self, interaction: discord.Interaction):
        """津波情報の手動検索（デバッグ用）"""
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="🔍 津波情報検索結果",
            color=discord.Color.purple(),
            timestamp=datetime.now(self.jst)
        )

        total_found = 0
        search_results = ""

        # 複数のコードで検索
        codes_to_search = [552, 551]

        for code in codes_to_search:
            try:
                url = f"{self.api_base_url}/history?codes={code}&limit=50"
                async with self.session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        code_found = 0

                        for item in data:
                            tsunami_info = self.get_tsunami_info(item)
                            if tsunami_info['has_tsunami']:
                                code_found += 1
                                total_found += 1

                                if code_found <= 3:  # 各コード上位3件表示
                                    issue_type = item.get('issue', {}).get('type', '不明')
                                    warning_level = tsunami_info.get('warning_level', '不明')
                                    search_results += f"**Code {code}**: {warning_level} - {issue_type}\n"

                        if code_found == 0:
                            search_results += f"**Code {code}**: 津波情報なし\n"
                        else:
                            search_results += f"**Code {code}**: {code_found}件発見\n"

                    else:
                        search_results += f"**Code {code}**: API エラー ({response.status})\n"

            except Exception as e:
                search_results += f"**Code {code}**: 検索エラー - {str(e)[:50]}\n"

        embed.add_field(name="📊 検索結果", value=search_results or "検索結果なし", inline=False)
        embed.add_field(name="📈 合計", value=f"津波情報: {total_found}件発見", inline=True)

        # 現在の津波監視状態
        status_text = f"最後のID: `{self.last_ids['tsunami'] or '未取得'}`\n"
        status_text += f"処理済み: {len(self.processed_ids['tsunami'])}件"
        embed.add_field(name="🔄 監視状態", value=status_text, inline=True)

        embed.set_footer(text="津波情報デバッグ検索")

        await interaction.followup.send(embed=embed)

    async def help_system(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📚 地震・津波情報システム ヘルプ",
            description="このボットは気象庁の地震・津波情報をリアルタイムで通知します。",
            color=discord.Color.green(),
            timestamp=datetime.now(self.jst)
        )

        # コマンド一覧
        commands_text = """
**🔧 設定コマンド**
`/earthquake_channel` - 通知チャンネルを設定
`/earthquake_test` - テスト通知を送信

**📊 情報表示コマンド**  
`/earthquake_latest` - 最新情報を表示
`/earthquake_status` - システム状態を確認
`/tsunami_search` - 津波情報を手動検索（デバッグ用）

**❓ その他**
`/earthquake_help` - このヘルプを表示
        """
        embed.add_field(name="🛠️ 利用可能なコマンド", value=commands_text.strip(), inline=False)

        # 通知される情報の種類
        info_types_text = """
**🚨 緊急地震速報** - 地震発生直後の速報
**📊 地震情報** - 確定した地震の詳細情報
**🌊 津波予報** - 津波注意報・警報・大津波警報
        """
        embed.add_field(name="📡 通知される情報", value=info_types_text.strip(), inline=False)

        # セットアップ手順
        setup_text = """
1. `/earthquake_channel` でチャンネルを設定
2. `/earthquake_test` で動作確認
3. `/earthquake_status` でシステム状態確認
        """
        embed.add_field(name="⚡ 初回セットアップ", value=setup_text.strip(), inline=False)

        embed.set_footer(text="データ提供: P2P地震情報 | 気象庁")
        embed.set_thumbnail(url="https://www.p2pquake.net/images/QuakeLogo_100x100.png")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @check_earthquake_info.before_loop
    async def before_check_earthquake_info(self):
        await self.bot.wait_until_ready()
        print("🔄 地震・津波情報監視開始 (P2P地震情報 API v2)")


async def setup(bot: commands.Bot):
    print("🔄 EarthquakeTsunamiCog セットアップ関数開始...")
    cog = EarthquakeTsunamiCog(bot)
    await bot.add_cog(cog)
    # ボットの準備完了後にセットアップを実行
    if hasattr(cog, 'setup_hook'):
        bot.loop.create_task(cog.setup_hook())
    print("✅ EarthquakeTsunamiCog セットアップ関数完了")