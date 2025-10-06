# PLANA/notifications/earthquake_notification_cog.py

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal, Optional, Dict, Set, Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from PLANA.notifications.error.earthquake_errors import (
    EarthquakeTsunamiExceptionHandler,
    APIError,
    DataParsingError,
    ConfigError,
    NotificationError
)

DATA_DIR = 'data'
CONFIG_FILE = os.path.join(DATA_DIR, 'earthquake_tsunami_notification_config.json')

logger = logging.getLogger('EarthquakeTsunamiCog')


class InfoType(Enum):
    """情報タイプの定義"""
    EEW = "eew"
    QUAKE = "quake"
    TSUNAMI = "tsunami"
    UNKNOWN = "unknown"


class EarthquakeTsunamiCog(commands.Cog, name="EarthquakeNotifications"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("🔄 EarthquakeTsunamiCog 初期化開始...")

        self.ensure_data_dir()
        self.config = self.load_config()

        self.last_ids: Dict[str, Optional[str]] = {
            InfoType.EEW.value: None, InfoType.QUAKE.value: None, InfoType.TSUNAMI.value: None
        }
        self.processed_ids: Dict[str, Set[str]] = {
            InfoType.EEW.value: set(), InfoType.QUAKE.value: set(), InfoType.TSUNAMI.value: set()
        }
        self.max_processed_ids = 1000

        # WebSocket関連
        self.ws_session = None
        self.ws_connection = None
        self.ws_reconnect_delay = 5
        self.ws_max_reconnect_delay = 300
        self.ws_running = False

        self.http_session = None
        self.jst = timezone(timedelta(hours=+9), 'JST')
        self.api_base_url = "https://api.p2pquake.net/v2"
        self.ws_url = "wss://api.p2pquake.net/v2/ws"
        self.request_headers = {'User-Agent': 'Discord-Bot-EarthquakeTsunami/3.0', 'Accept': 'application/json'}

        self.error_stats = {'api_errors': 0, 'parsing_errors': 0, 'network_errors': 0, 'ws_disconnects': 0,
                            'last_error_time': None}
        self.processing_stats = {'eew_processed': 0, 'quake_processed': 0, 'tsunami_processed': 0, 'unknown_skipped': 0,
                                 'last_stats_output': datetime.now(self.jst)}
        self.stats_interval = 3600

        self.exception_handler = EarthquakeTsunamiExceptionHandler(self)
        logger.info("✅ EarthquakeTsunamiCog 初期化完了")

    async def cog_load(self):
        logger.info("🔄 EarthquakeTsunamiCog セットアップ開始...")
        try:
            await self.recreate_http_session()
            logger.info("🔄 最新情報のIDを初期化中...")
            await self.initialize_processed_ids()

            # WebSocket接続を開始
            self.ws_running = True
            asyncio.create_task(self.websocket_listener())

            # 統計出力タスクを開始
            self.output_stats_task.start()

            logger.info("✅ EarthquakeTsunamiCog セットアップ完了")
        except Exception as e:
            self.exception_handler.log_generic_error(e, "Cogのセットアップ")
            logger.critical(f"❌ セットアップに失敗しました: {e}")

    async def cog_unload(self):
        logger.info("🔄 EarthquakeTsunamiCog アンロード中...")

        # WebSocket接続を停止
        self.ws_running = False
        if self.ws_connection and not self.ws_connection.closed:
            await self.ws_connection.close()
        if self.ws_session and not self.ws_session.closed:
            await self.ws_session.close()

        # HTTP接続を停止
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()

        # タスクを停止
        if hasattr(self, 'output_stats_task'):
            self.output_stats_task.cancel()

        logger.info("✅ EarthquakeTsunamiCog アンロード完了")

    async def websocket_listener(self):
        """WebSocketで地震情報をリアルタイム受信"""
        reconnect_delay = self.ws_reconnect_delay

        while self.ws_running:
            try:
                logger.info(f"🔌 WebSocket接続開始: {self.ws_url}")

                if not self.ws_session or self.ws_session.closed:
                    self.ws_session = aiohttp.ClientSession(headers=self.request_headers)

                async with self.ws_session.ws_connect(self.ws_url) as ws:
                    self.ws_connection = ws
                    logger.info("✅ WebSocket接続成功")
                    reconnect_delay = self.ws_reconnect_delay

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                await self.process_websocket_message(data)
                            except json.JSONDecodeError as e:
                                logger.error(f"WebSocketメッセージのJSON解析エラー: {e}")
                                self.error_stats['parsing_errors'] += 1
                            except Exception as e:
                                self.exception_handler.log_generic_error(e, "WebSocketメッセージ処理")

                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logger.error(f"WebSocketエラー: {ws.exception()}")
                            break

            except aiohttp.ClientError as e:
                logger.error(f"WebSocket接続エラー: {e}")
                self.error_stats['network_errors'] += 1
                self.error_stats['ws_disconnects'] += 1
            except Exception as e:
                self.exception_handler.log_generic_error(e, "WebSocket接続")

            if self.ws_running:
                logger.warning(f"⚠️ WebSocket切断。{reconnect_delay}秒後に再接続...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, self.ws_max_reconnect_delay)

    async def process_websocket_message(self, data: Dict[str, Any]):
        """WebSocketから受信したメッセージを処理"""
        try:
            # code フィールドで情報タイプを判定
            code = data.get('code', 0)

            if code not in [551, 552]:
                return

            info_id = self.extract_id_safe(data)
            if not info_id:
                return

            info_type = self.classify_info_type(data)

            if info_type == InfoType.UNKNOWN:
                self.processing_stats['unknown_skipped'] += 1
                logger.debug(f"UNKNOWN情報をスキップ: ID {info_id}")
                return

            if info_id in self.processed_ids[info_type.value]:
                return

            logger.info(f"🆕 WebSocketで新しい{info_type.value}情報を受信: ID {info_id}")

            if info_type == InfoType.EEW:
                await self.send_eew_notification(data)
            elif info_type == InfoType.QUAKE:
                await self.send_quake_notification(data)
            elif info_type == InfoType.TSUNAMI:
                tsunami_info = self.get_tsunami_info(data)
                if tsunami_info.get('has_tsunami', False):
                    await self.send_tsunami_notification(data, tsunami_info)

            self.processing_stats[f'{info_type.value}_processed'] += 1
            self.processed_ids[info_type.value].add(info_id)
            self.last_ids[info_type.value] = info_id
            self.manage_processed_ids(info_type.value)

        except NotificationError as e:
            logger.error(f"通知エラー: {e}", exc_info=True)
        except Exception as e:
            self.exception_handler.log_generic_error(e, "WebSocketメッセージ処理")

    async def recreate_http_session(self):
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        self.http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers=self.request_headers,
            connector=aiohttp.TCPConnector(limit=10)
        )
        logger.info("HTTPセッションを再作成しました")

    async def safe_api_request(self, url: str, timeout: int = 15) -> Optional[Dict[str, Any]]:
        try:
            if not self.http_session or self.http_session.closed:
                await self.recreate_http_session()
            async with self.http_session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                if response.status == 200:
                    try:
                        return await response.json()
                    except json.JSONDecodeError as e:
                        self.error_stats['last_error_time'] = datetime.now(self.jst)
                        raise self.exception_handler.handle_json_decode_error(e, url)
                else:
                    self.error_stats['last_error_time'] = datetime.now(self.jst)
                    raise self.exception_handler.handle_api_response_error(response.status, url)
        except Exception as e:
            if isinstance(e, (APIError, DataParsingError)):
                raise e
            self.error_stats['last_error_time'] = datetime.now(self.jst)
            raise self.exception_handler.handle_api_error(e, url)

    def manage_processed_ids(self, info_type: str):
        if len(self.processed_ids[info_type]) > self.max_processed_ids:
            self.processed_ids[info_type] = set(list(self.processed_ids[info_type])[-self.max_processed_ids:])
            logger.info(f"{info_type}: 処理済みID数を{self.max_processed_ids}に制限")

    async def initialize_processed_ids(self):
        logger.info("🔍 最新情報のIDを初期化中...")
        for code in [551, 552]:
            try:
                url = f"{self.api_base_url}/history?codes={code}&limit=100"
                data = await self.safe_api_request(url)
                if not (data and isinstance(data, list)):
                    continue

                latest_ids = {it.value: None for it in InfoType if it != InfoType.UNKNOWN}

                for item in data:
                    item_id = self.extract_id_safe(item)
                    if not item_id:
                        continue

                    info_type = self.classify_info_type(item)
                    if info_type != InfoType.UNKNOWN:
                        self.processed_ids[info_type.value].add(item_id)
                        if latest_ids[info_type.value] is None:
                            latest_ids[info_type.value] = item_id

                for it, lid in latest_ids.items():
                    if lid:
                        self.last_ids[it] = lid

            except (APIError, DataParsingError) as e:
                logger.error(f"❌ Code {code} のID初期化に失敗: {e}")
            except Exception as e:
                self.exception_handler.log_generic_error(e, f"Code {code} のID初期化")

        logger.info("🔍 ID初期化結果:")
        for it, lid in self.last_ids.items():
            count = len(self.processed_ids.get(it, set()))
            logger.info(f"  {it.upper()}: {lid[:8] if lid else '未取得'} (処理済み: {count}件)")

    def extract_id_safe(self, item: Dict[str, Any]) -> Optional[str]:
        return str(item.get('id')) if item.get('id') is not None else None

    @tasks.loop(seconds=3600)
    async def output_stats_task(self):
        """統計情報を定期的に出力"""
        error_total = sum(v for k, v in self.error_stats.items() if k.endswith('_errors') or k == 'ws_disconnects')
        stats_msg = (
            f"[統計] EEW:{self.processing_stats['eew_processed']} "
            f"QUAKE:{self.processing_stats['quake_processed']} "
            f"TSUNAMI:{self.processing_stats['tsunami_processed']} "
            f"UNKNOWN:{self.processing_stats['unknown_skipped']} "
            f"エラー:{error_total} WS切断:{self.error_stats['ws_disconnects']}"
        )
        logger.info(stats_msg)

    def classify_info_type(self, item: Dict[str, Any]) -> InfoType:
        try:
            issue_type = item.get('issue', {}).get('type', '').lower()
            code = item.get('code', 0)

            if code == 552 or self.get_tsunami_info(item).get('has_tsunami', False):
                return InfoType.TSUNAMI

            if code == 551:
                earthquake_data = item.get('earthquake', {})
                if issue_type in ['foreign', 'eew'] or 'eew' in issue_type:
                    return InfoType.EEW
                if issue_type == 'scaleprompt':
                    domestic_tsunami = earthquake_data.get('domesticTsunami', '')
                    if domestic_tsunami in ['Unknown', '']:
                        return InfoType.EEW
                if issue_type in ['detailscale', 'destination', 'scaleanddetail', 'scaleprompt']:
                    return InfoType.QUAKE
                if earthquake_data and issue_type:
                    return InfoType.QUAKE

            logger.debug(f"UNKNOWN情報: code={code}, issue.type={issue_type}")
            return InfoType.UNKNOWN
        except Exception as e:
            logger.warning(f"情報分類エラー: {e}", exc_info=True)
            return InfoType.UNKNOWN

    def ensure_data_dir(self):
        try:
            if not os.path.exists(DATA_DIR):
                os.makedirs(DATA_DIR)
        except OSError as e:
            raise ConfigError(f"データディレクトリの作成に失敗: {e}")

    def load_config(self) -> Dict[str, Any]:
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    for guild_id, value in list(config.items()):
                        if isinstance(value, int):
                            config[guild_id] = {it.value: value for it in InfoType if it != InfoType.UNKNOWN}
                    return config
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning(f"設定ファイル読み込みエラー: {e}")
        return {}

    def save_config(self):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            raise ConfigError(f"設定ファイルの保存に失敗: {e}")

    def scale_to_japanese(self, scale_code):
        if scale_code is None or scale_code == -1:
            return "震度情報なし"
        scale_map = {
            10: "震度1", 20: "震度2", 30: "震度3", 40: "震度4",
            45: "震度5弱", 50: "震度5強", 55: "震度6弱", 60: "震度6強", 70: "震度7"
        }
        return scale_map.get(scale_code, f"不明({scale_code})")

    def get_embed_color(self, scale_code, info_type="quake"):
        if info_type == "tsunami":
            return discord.Color.purple()
        if scale_code is None or scale_code == -1:
            return discord.Color.light_grey()
        if scale_code >= 55:
            return discord.Color.dark_red()
        if scale_code >= 50:
            return discord.Color.red()
        if scale_code >= 40:
            return discord.Color.orange()
        if scale_code >= 30:
            return discord.Color.gold()
        return discord.Color.blue()

    def parse_earthquake_time(self, time_str, announced_time=None):
        try:
            if isinstance(time_str, str) and time_str.strip():
                try:
                    return datetime.strptime(time_str, "%Y/%m/%d %H:%M:%S").replace(tzinfo=self.jst)
                except ValueError:
                    pass
            if announced_time and isinstance(announced_time, str):
                try:
                    return datetime.strptime(announced_time, "%Y/%m/%d %H:%M:%S").replace(tzinfo=self.jst)
                except ValueError:
                    pass
            return datetime.now(self.jst)
        except Exception:
            return datetime.now(self.jst)

    def format_magnitude(self, magnitude):
        try:
            if magnitude is None or magnitude == -1 or magnitude == "-1":
                return "不明"
            mag_value = float(magnitude)
            if mag_value == -1:
                return "不明"
            return f"M{mag_value:.1f}"
        except (ValueError, TypeError):
            return "不明"

    def format_depth(self, depth):
        try:
            if depth is None or depth == -1 or depth == "-1":
                return "不明"
            if isinstance(depth, str):
                if not depth.replace('km', '').replace('m', '').strip().isdigit():
                    return depth
                depth_value = int(depth.replace('km', '').strip())
            else:
                depth_value = int(depth)

            if depth_value == -1:
                return "不明"
            return "ごく浅い" if depth_value == 0 else f"{depth_value}km"
        except (ValueError, TypeError):
            return "不明"

    def get_tsunami_info(self, data):
        info = {'has_tsunami': False, 'warning_level': None, 'areas': [], 'description': ""}
        try:
            tsunami_data = data.get('tsunami')
            if not tsunami_data or tsunami_data.get('domesticTsunami') in ['None', None]:
                return info
            info['has_tsunami'] = True
            grades = {'MajorWarning': '大津波警報', 'Warning': '津波警報', 'Watch': '津波注意報'}
            highest_level = 0
            level_text = '津波予報'
            areas_data = tsunami_data.get('areas', [])
            for area in areas_data if isinstance(areas_data, list) else []:
                if not isinstance(area, dict):
                    continue
                grade = area.get('grade')
                if grade == 'MajorWarning' and highest_level < 3:
                    highest_level, level_text = 3, grades[grade]
                elif grade == 'Warning' and highest_level < 2:
                    highest_level, level_text = 2, grades[grade]
                elif grade == 'Watch' and highest_level < 1:
                    highest_level, level_text = 1, grades[grade]
                if area.get('name'):
                    info['areas'].append({'name': area['name'], 'grade': grades.get(grade, '情報')})
            info['warning_level'] = level_text
        except Exception:
            pass
        return info

    async def send_eew_notification(self, data):
        await self.send_notification(data, InfoType.EEW.value, "🚨 緊急地震速報")

    async def send_quake_notification(self, data):
        await self.send_notification(data, InfoType.QUAKE.value, "📊 地震情報")

    async def send_notification(self, data, info_type, title_prefix):
        try:
            earthquake = data.get('earthquake', {})
            if not earthquake:
                logger.warning(f"{info_type}: earthquake データが存在しません")
                return

            hypocenter = earthquake.get('hypocenter', {})
            issue_data = data.get('issue', {})
            report_type = issue_data.get('type', '情報')
            max_scale = earthquake.get('maxScale', -1)
            quake_time = self.parse_earthquake_time(earthquake.get('time', ''), issue_data.get('time', ''))

            magnitude = hypocenter.get('magnitude', -1)
            depth = hypocenter.get('depth', -1)

            if info_type == InfoType.EEW.value:
                description = f"強い揺れに警戒してください。" if max_scale == -1 else f"**最大震度 {self.scale_to_japanese(max_scale)}** 程度の揺れが予想されます。"
                description += "\n⚠️ **これは速報です。情報が更新される可能性があります。**"
            else:
                description = f"**最大震度 {self.scale_to_japanese(max_scale)}** の地震が発生しました。"

            embed = discord.Embed(
                title=f"{title_prefix} ({report_type})",
                description=description,
                color=self.get_embed_color(max_scale, info_type),
                timestamp=quake_time
            )
            hypocenter_name = hypocenter.get('name', '不明')
            embed.add_field(name="🌏 震源地", value=f"```{hypocenter_name or '調査中'}```", inline=True)
            mag_prefix = "推定 " if info_type == InfoType.EEW.value else ""
            embed.add_field(name="📊 マグニチュード", value=f"```{mag_prefix}{self.format_magnitude(magnitude)}```",
                            inline=True)
            embed.add_field(name="📏 深さ", value=f"```{self.format_depth(depth)}```", inline=True)

            points = data.get('points', [])
            if points and isinstance(points, list):
                areas_text = ""
                field_name = "📍 予測震度" if info_type == InfoType.EEW.value else "📍 各地の震度"
                for point in sorted(points, key=lambda p: p.get('scale', 0), reverse=True)[:8]:
                    scale, addr = point.get('scale', -1), point.get('addr', '不明')
                    emoji = "🔴" if scale >= 55 else "🟠" if scale >= 50 else "🟡" if scale >= 40 else "🟢" if scale >= 30 else "🔵"
                    scale_suffix = " 程度" if info_type == InfoType.EEW.value else ""
                    areas_text += f"{emoji} **{self.scale_to_japanese(scale)}{scale_suffix}** - {addr}\n"
                if areas_text:
                    embed.add_field(name=field_name, value=areas_text[:1024], inline=False)
            elif info_type == InfoType.EEW.value:
                embed.add_field(name="📍 震度情報", value="詳細な震度情報は確定情報をお待ちください", inline=False)

            tsunami_info = self.get_tsunami_info(data)
            if tsunami_info['has_tsunami'] and info_type == InfoType.QUAKE.value:
                embed.add_field(name="🌊 津波情報",
                                value=f"🌊 **{tsunami_info.get('warning_level', '津波予報')}** が発表されています",
                                inline=False)
            if info_type == InfoType.EEW.value:
                embed.add_field(name="⚠️ 注意",
                                value="この情報は速報です。揺れが予想される地域の方は、身の安全を確保してください。",
                                inline=False)

            embed.set_footer(text="Powered by P2P地震情報 WebSocket API | PLANA by coffin299")
            embed.set_thumbnail(url="https://www.p2pquake.net/images/QuakeLogo_100x100.png")

            await self.send_embed_to_channels(embed, info_type)
        except Exception as e:
            raise NotificationError(f"{info_type}通知処理エラー: {e}")

    async def send_tsunami_notification(self, data, tsunami_info):
        try:
            warning_level = tsunami_info.get('warning_level', '津波予報')
            emoji_map = {"大津波警報": "🔴", "津波警報": "🟠", "津波注意報": "🟡"}
            embed = discord.Embed(
                title=f"{emoji_map.get(warning_level, '🌊')} {warning_level}",
                description=f"**{warning_level}** が発表されました。",
                color=discord.Color.purple(),
                timestamp=datetime.now(self.jst)
            )
            earthquake = data.get('earthquake', {})
            if earthquake and isinstance(earthquake, dict):
                hypocenter = earthquake.get('hypocenter', {})
                magnitude = hypocenter.get('magnitude', -1)
                depth = hypocenter.get('depth', -1)
                embed.add_field(name="🌏 震源地", value=f"```{hypocenter.get('name', '不明')}```", inline=True)
                embed.add_field(name="📊 マグニチュード", value=f"```{self.format_magnitude(magnitude)}```", inline=True)
                embed.add_field(name="📏 深さ", value=f"```{self.format_depth(depth)}```", inline=True)

            areas = tsunami_info.get('areas', [])
            if areas and isinstance(areas, list):
                area_text = "".join(
                    f"🌊 **{area.get('grade', warning_level)}** - {area.get('name', '不明')}\n"
                    for area in areas[:5] if isinstance(area, dict)
                )
                if area_text:
                    embed.add_field(name="🏖️ 予報区域", value=area_text, inline=False)

            warning_text = (
                "⚠️ **直ちに避難してください** ⚠️\n高台や避難ビルなど安全な場所へ" if warning_level == "大津波警報"
                else "⚠️ **直ちに避難してください**\n海岸や川から離れ、高いところへ" if warning_level == "津波警報"
                else "⚠️ 海の中や海岸付近は危険です\n海から上がって、海岸から離れてください"
            )
            embed.add_field(name="⚠️ 避難指示", value=warning_text, inline=False)
            if tsunami_info.get('description'):
                embed.add_field(name="ℹ️ 詳細情報", value=tsunami_info['description'][:500], inline=False)

            embed.set_footer(text="気象庁 | 津波から身を守るため直ちに避難を | PLANA by coffin299")
            embed.set_thumbnail(url="https://www.p2pquake.net/images/QuakeLogo_100x100.png")
            await self.send_embed_to_channels(embed, InfoType.TSUNAMI.value)
        except Exception as e:
            raise NotificationError(f"津波通知処理エラー: {e}")

    async def send_embed_to_channels(self, embed, info_type):
        if not self.config:
            logger.warning(f"通知送信スキップ ({info_type}): config が空です")
            return

        logger.info(f"📤 {info_type}通知送信開始 - 設定ギルド数: {len(self.config)}")
        sent_count, failed_count, skipped_count = 0, 0, 0

        for guild_id, guild_config in self.config.copy().items():
            try:
                if not isinstance(guild_config, dict):
                    logger.warning(f"送信スキップ ({info_type}): ギルド {guild_id} の設定が辞書型ではありません")
                    skipped_count += 1
                    continue

                channel_id = guild_config.get(info_type)
                if not channel_id:
                    skipped_count += 1
                    continue

                guild = self.bot.get_guild(int(guild_id))
                if not guild:
                    logger.warning(f"送信スキップ ({info_type}): ギルド {guild_id} が見つかりません")
                    failed_count += 1
                    continue

                channel = guild.get_channel(channel_id)
                if not channel:
                    logger.warning(f"送信スキップ ({info_type}): チャンネル {channel_id} が見つかりません")
                    failed_count += 1
                    continue

                permissions = channel.permissions_for(guild.me)
                if not permissions.send_messages or not permissions.embed_links:
                    logger.error(f"送信失敗 ({info_type}): チャンネル '{channel.name}' への権限が不足")
                    failed_count += 1
                    continue

                await channel.send(embed=embed)
                sent_count += 1
                logger.info(f"✅ 送信成功: '{guild.name}' の '{channel.name}'")

            except discord.Forbidden:
                logger.error(f"送信失敗 ({info_type}): 権限不足 - ギルド {guild_id}")
                failed_count += 1
            except discord.HTTPException as e:
                logger.error(f"送信失敗 ({info_type}): Discord APIエラー - {e.status}")
                failed_count += 1
            except Exception as e:
                logger.error(f"予期せぬ送信失敗 ({info_type}): ギルド {guild_id}", exc_info=True)
                failed_count += 1

        logger.info(
            f"📊 {info_type}通知送信完了: 成功 {sent_count}件, 失敗 {failed_count}件, スキップ {skipped_count}件")

        if sent_count == 0 and (failed_count > 0 or skipped_count > 0):
            logger.warning(f"⚠️ {info_type}の通知が1件も送信されませんでした")

    # ========== コマンド群 ==========

    @app_commands.command(name="earthquake_channel", description="地震・津波情報の通知チャンネルを設定します")
    @app_commands.describe(channel="通知を送信するチャンネル", info_type="通知したい情報の種類")
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel,
                          info_type: Literal["緊急地震速報", "地震情報", "津波予報", "すべて"]):
        try:
            guild_id = str(interaction.guild.id)
            if guild_id not in self.config:
                self.config[guild_id] = {}

            types_to_set = (
                [InfoType.EEW.value, InfoType.QUAKE.value, InfoType.TSUNAMI.value]
                if info_type == "すべて"
                else [{"緊急地震速報": InfoType.EEW.value, "地震情報": InfoType.QUAKE.value,
                       "津波予報": InfoType.TSUNAMI.value}[info_type]]
            )

            for t in types_to_set:
                self.config[guild_id][t] = channel.id

            self.save_config()
            await interaction.response.send_message(
                f"✅ **{info_type}** の通知チャンネルを {channel.mention} に設定しました。")
        except Exception as e:
            self.exception_handler.log_generic_error(e, "チャンネル設定コマンド")
            await interaction.response.send_message(self.exception_handler.get_user_friendly_message(e), ephemeral=False)

    @app_commands.command(name="earthquake_status", description="地震・津波情報システムの状態を確認します")
    async def status_system(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=False)
            embed = discord.Embed(
                title="🔧 地震・津波情報システム状態",
                color=discord.Color.blue(),
                timestamp=datetime.now(self.jst)
            )

            # WebSocket接続状態
            ws_status = "✅ 接続中" if self.ws_connection and not self.ws_connection.closed else "❌ 切断中"
            embed.add_field(name="🔌 WebSocket状態", value=ws_status, inline=True)

            embed.add_field(
                name="🌐 HTTPセッション",
                value="✅ 正常" if self.http_session and not self.http_session.closed else "❌ 無効",
                inline=True
            )

            # 最後のID
            id_status = ""
            for it, lid in self.last_ids.items():
                count = len(self.processed_ids.get(it, set()))
                id_status += f"**{it.upper()}**: `{lid[:8] if lid else '未取得'}` ({count}件)\n"
            embed.add_field(name="🆔 最後のID", value=id_status, inline=False)

            # 通知チャンネル設定
            guild_id = str(interaction.guild.id)
            if guild_id in self.config:
                channel_status = ""
                type_map = {
                    InfoType.EEW.value: '緊急地震速報',
                    InfoType.QUAKE.value: '地震情報',
                    InfoType.TSUNAMI.value: '津波予報'
                }
                for it, name in type_map.items():
                    if it in self.config[guild_id]:
                        channel = interaction.guild.get_channel(self.config[guild_id][it])
                        status = f"✅ {channel.mention}" if channel else "❌ 削除済み"
                    else:
                        status = "⚠️ 未設定"
                    channel_status += f"**{name}**: {status}\n"
            else:
                channel_status = "⚠️ すべて未設定"

            embed.add_field(name="📢 通知チャンネル", value=channel_status, inline=False)

            # エラー統計
            if self.error_stats['last_error_time']:
                embed.add_field(
                    name="🕐 最後のエラー",
                    value=self.error_stats['last_error_time'].strftime('%m/%d %H:%M:%S'),
                    inline=True
                )

            error_summary = (
                f"API: {self.error_stats['api_errors']} | "
                f"解析: {self.error_stats['parsing_errors']} | "
                f"WS切断: {self.error_stats['ws_disconnects']}"
            )
            embed.add_field(name="📊 エラー統計", value=error_summary, inline=False)

            embed.set_footer(text="システム診断完了 | P2P地震情報 WebSocket API | PLANA by coffin299")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            self.exception_handler.log_generic_error(e, "ステータスコマンド")
            msg = self.exception_handler.get_user_friendly_message(e)
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=False)
            else:
                await interaction.followup.send(msg)

    @app_commands.command(name="earthquake_test", description="地震・津波情報のテスト通知を送信します")
    @app_commands.describe(
        info_type="テストしたい情報の種類",
        max_scale="テストしたい最大震度",
        tsunami_level="テストしたい津波レベル"
    )
    async def test_notification(
            self,
            interaction: discord.Interaction,
            info_type: Literal["緊急地震速報", "地震情報", "津波予報"],
            max_scale: Optional[Literal["震度3", "震度5強", "震度7"]] = "震度5強",
            tsunami_level: Optional[Literal["津波注意報", "津波警報", "大津波警報"]] = "津波警報"
    ):
        try:
            await interaction.response.defer(ephemeral=False)
            target_channel, is_configured = interaction.channel, False
            guild_id = str(interaction.guild.id)

            if guild_id in self.config:
                type_map = {
                    "緊急地震速報": InfoType.EEW.value,
                    "地震情報": InfoType.QUAKE.value,
                    "津波予報": InfoType.TSUNAMI.value
                }
                config_key = type_map.get(info_type)
                if config_key and config_key in self.config[guild_id]:
                    channel = interaction.guild.get_channel(self.config[guild_id][config_key])
                    if channel:
                        target_channel, is_configured = channel, True

            embed = (
                await self.create_tsunami_test_embed(tsunami_level)
                if info_type == "津波予報"
                else await self.create_earthquake_test_embed(
                    info_type,
                    max_scale,
                    {"震度3": 30, "震度5強": 50, "震度7": 70}.get(max_scale, 50)
                )
            )

            await target_channel.send(embed=embed)

            msg = (
                f"✅ 設定されたチャンネル {target_channel.mention} に **{info_type}** のテスト通知を送信しました。"
                if is_configured
                else f"✅ このチャンネルに **{info_type}** のテスト通知を送信しました。\nℹ️ 本番の通知は `/earthquake_channel` コマンドで設定したチャンネルに送信されます。"
            )
            await interaction.followup.send(msg)
        except discord.Forbidden:
            await interaction.followup.send(f"❌ {target_channel.mention} にメッセージを送信する権限がありません。")
        except Exception as e:
            self.exception_handler.log_generic_error(e, "テスト通知コマンド")
            await interaction.followup.send(self.exception_handler.get_user_friendly_message(e))

    async def create_earthquake_test_embed(self, info_type, max_scale, scale_code):
        title = (
            f"🚨【テスト】緊急地震速報 (予報)"
            if info_type == "緊急地震速報"
            else f"📊【テスト】地震情報"
        )
        description = f"**最大震度 {max_scale}** の地震が{'検知されました' if info_type == '緊急地震速報' else '発生しました'}。"

        embed = discord.Embed(
            title=title,
            description=description,
            color=self.get_embed_color(scale_code),
            timestamp=datetime.now(self.jst)
        )
        embed.add_field(name="🌏 震源地", value="```テスト震源地```", inline=True)
        embed.add_field(name="📊 マグニチュード", value="```M7.0```", inline=True)
        embed.add_field(name="📏 深さ", value="```10km```", inline=True)
        embed.add_field(
            name="📍 各地の震度",
            value=f"🔴 **{max_scale}** - テスト県A市\n🟠 **震度4** - テスト県B市\n🟡 **震度3** - テスト県C市",
            inline=False
        )
        embed.set_footer(text="これはテスト通知です | Powered by P2P地震情報 WebSocket API | PLANA by coffin299")
        embed.set_thumbnail(url="https://www.p2pquake.net/images/QuakeLogo_100x100.png")
        return embed

    async def create_tsunami_test_embed(self, tsunami_level):
        emoji_map = {"津波注意報": "🟡", "津波警報": "🟠", "大津波警報": "🔴"}
        embed = discord.Embed(
            title=f"{emoji_map.get(tsunami_level, '🌊')}【テスト】{tsunami_level}",
            description=f"**{tsunami_level}** が発表されました。",
            color=discord.Color.purple(),
            timestamp=datetime.now(self.jst)
        )
        embed.add_field(name="🌏 震源地", value="```テスト海域```", inline=True)
        embed.add_field(name="📊 マグニチュード", value="```M7.5```", inline=True)
        embed.add_field(name="📏 深さ", value="```10km```", inline=True)
        embed.add_field(
            name="🏖️ 予報区域",
            value=f"🌊 **{tsunami_level}**\n・テスト県沿岸\n・テスト湾\n・テスト海岸",
            inline=False
        )
        warning_text = (
            "⚠️ **直ちに避難してください** ⚠️"
            if tsunami_level == "大津波警報"
            else "⚠️ 直ちに海岸や川から離れ、高いところに避難してください。"
            if tsunami_level == "津波警報"
            else "⚠️ 海の中や海岸付近は危険です。海から上がって、海岸から離れてください。"
        )
        embed.add_field(name="⚠️ 注意事項", value=warning_text, inline=False)
        embed.set_footer(text="これはテスト通知です | 気象庁 | PLANA by coffin299")
        embed.set_thumbnail(url="https://www.p2pquake.net/images/QuakeLogo_100x100.png")
        return embed

    @app_commands.command(name="earthquake_help", description="このシステムのヘルプを表示します")
    async def help_system(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📚 地震・津波情報システム ヘルプ",
            description="このボットは気象庁の地震・津波情報をリアルタイムで通知します（WebSocket接続）。",
            color=discord.Color.green(),
            timestamp=datetime.now(self.jst)
        )
        embed.add_field(
            name="🛠️ 利用可能なコマンド",
            value=(
                "**🔧 設定コマンド**\n"
                "`/earthquake_channel` - 通知チャンネルを設定\n"
                "`/earthquake_test` - テスト通知を送信\n\n"
                "**📊 情報表示コマンド**\n"
                "`/earthquake_status` - システム状態を確認\n"
                "`/earthquake_debug` - 詳細診断情報を表示\n\n"
                "**❓ その他**\n"
                "`/earthquake_help` - このヘルプを表示"
            ),
            inline=False
        )
        embed.add_field(
            name="📡 通知される情報",
            value=(
                "**🚨 緊急地震速報（EEW）** - 地震発生直後の速報（WebSocketでリアルタイム受信）\n"
                "**📊 地震情報** - 確定した地震の詳細情報\n"
                "**🌊 津波予報** - 津波注意報・警報・大津波警報"
            ),
            inline=False
        )
        embed.add_field(
            name="⚡ 初回セットアップ",
            value=(
                "1. `/earthquake_channel` でチャンネルを設定\n"
                "2. `/earthquake_test` で動作確認\n"
                "3. `/earthquake_status` でシステム状態確認"
            ),
            inline=False
        )
        embed.add_field(
            name="🔌 WebSocket接続について",
            value=(
                "このBotはP2P地震情報のWebSocket APIに常時接続し、\n"
                "リアルタイムで地震情報を受信します。\n"
                "接続が切れた場合は自動的に再接続を試みます。"
            ),
            inline=False
        )
        embed.set_footer(text="データ提供: P2P地震情報 | 気象庁 | PLANA by coffin299")
        embed.set_thumbnail(url="https://www.p2pquake.net/images/QuakeLogo_100x100.png")
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(name="earthquake_debug", description="通知設定の詳細診断")
    async def debug_config(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=False)

            guild_id = str(interaction.guild.id)
            embed = discord.Embed(
                title="🔍 通知設定診断",
                color=discord.Color.blue(),
                timestamp=datetime.now(self.jst)
            )

            embed.add_field(
                name="📁 設定ファイル",
                value=f"```json\n{json.dumps(self.config, indent=2, ensure_ascii=False)[:500]}```",
                inline=False
            )

            if guild_id in self.config:
                guild_config = self.config[guild_id]
                config_text = ""
                for info_type, channel_id in guild_config.items():
                    channel = interaction.guild.get_channel(channel_id)
                    if channel:
                        perms = channel.permissions_for(interaction.guild.me)
                        config_text += f"**{info_type}**:\n"
                        config_text += f"  チャンネル: {channel.mention} (ID: {channel_id})\n"
                        config_text += f"  メッセージ送信: {'✅' if perms.send_messages else '❌'}\n"
                        config_text += f"  埋め込みリンク: {'✅' if perms.embed_links else '❌'}\n"
                    else:
                        config_text += f"**{info_type}**: ❌ チャンネル {channel_id} が見つかりません\n"

                embed.add_field(name="⚙️ このサーバーの設定", value=config_text or "設定なし", inline=False)
            else:
                embed.add_field(name="⚙️ このサーバーの設定", value="❌ 未設定", inline=False)

            ws_info = "✅ 接続中" if self.ws_connection and not self.ws_connection.closed else "❌ 切断中"
            embed.add_field(
                name="🤖 Bot状態",
                value=(
                    f"ギルド数: {len(self.bot.guilds)}\n"
                    f"WebSocket: {ws_info}\n"
                    f"HTTPセッション: {'✅' if self.http_session and not self.http_session.closed else '❌'}\n"
                    f"WS切断回数: {self.error_stats['ws_disconnects']}"
                ),
                inline=False
            )

            await interaction.followup.send(embed=embed, ephemeral=False)

        except Exception as e:
            logger.error(f"診断コマンドエラー: {e}", exc_info=True)
            await interaction.followup.send(f"❌ エラーが発生しました: {e}", ephemeral=False)


async def setup(bot: commands.Bot):
    try:
        await bot.add_cog(EarthquakeTsunamiCog(bot))
    except Exception as e:
        logger.critical(f"Cogセットアップエラー: {e}", exc_info=True)
        raise