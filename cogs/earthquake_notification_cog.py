# cogs/earthquake_tsunami.py

import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional, Dict, Set, Any
import asyncio
import logging
from enum import Enum

# 設定ファイルを保存するディレクトリとファイルのパス
DATA_DIR = 'data'
CONFIG_FILE = os.path.join(DATA_DIR, 'earthquake_tsunami_notification_config.json')

# ロギング設定
logger = logging.getLogger('EarthquakeTsunamiCog')


class InfoType(Enum):
    """情報タイプの定義"""
    EEW = "eew"
    QUAKE = "quake"
    TSUNAMI = "tsunami"
    UNKNOWN = "unknown"


class APIError(Exception):
    """API関連のエラー"""
    pass


class DataParsingError(Exception):
    """データ解析エラー"""
    pass


class EarthquakeTsunamiCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        print("🔄 EarthquakeTsunamiCog 初期化開始...")

        # エラー処理の初期化
        self.setup_error_handling()

        self.ensure_data_dir()
        self.config = self.load_config()

        # 各情報タイプの最後のID追跡（文字列で管理）
        self.last_ids: Dict[str, Optional[str]] = {
            InfoType.EEW.value: None,
            InfoType.QUAKE.value: None,
            InfoType.TSUNAMI.value: None
        }

        # 処理済みID管理（重複処理防止）- セットで管理し、サイズ制限を設ける
        self.processed_ids: Dict[str, Set[str]] = {
            InfoType.EEW.value: set(),
            InfoType.QUAKE.value: set(),
            InfoType.TSUNAMI.value: set()
        }

        # 処理済みIDの最大保持数（メモリ使用量制御）
        self.max_processed_ids = 1000

        self.session = None
        self.jst = timezone(timedelta(hours=+9), 'JST')

        # API仕様（修正版）
        self.api_base_url = "https://api.p2pquake.net/v2"
        self.request_headers = {
            'User-Agent': 'Discord-Bot-EarthquakeTsunami/2.0',
            'Accept': 'application/json'
        }

        # 情報コード定義（P2P地震情報API v2準拠）
        self.info_codes = {
            InfoType.EEW.value: 551,  # 緊急地震速報
            InfoType.QUAKE.value: 551,  # 地震情報（EEWと同じコードだが内容で区別）
            InfoType.TSUNAMI.value: 552  # 津波予報
        }

        # エラー統計
        self.error_stats = {
            'api_errors': 0,
            'parsing_errors': 0,
            'network_errors': 0,
            'last_error_time': None
        }

        # [修正] 処理統計用の変数を初期化
        self.processing_stats = {
            'eew_processed': 0,
            'quake_processed': 0,
            'tsunami_processed': 0,
            'unknown_skipped': 0,
            'last_stats_output': datetime.now(self.jst)
        }
        self.stats_interval = 3600  # 統計出力間隔（秒）、例: 1時間

        print("✅ EarthquakeTsunamiCog 初期化完了")

    # [追加] Cogが読み込まれたときに自動で実行されるメソッド
    async def cog_load(self):
        print("🔄 EarthquakeTsunamiCog セットアップ開始...")
        try:
            await self.recreate_session()
            print("🔄 最新情報のIDを初期化中...")
            await self.initialize_processed_ids()
            self.check_earthquake_info.start()
            print("✅ EarthquakeTsunamiCog セットアップ完了")
        except Exception as e:
            logger.error(f"セットアップエラー: {e}")
            print(f"❌ セットアップに失敗しました: {e}")

    def setup_error_handling(self):
        """エラー処理の初期設定"""
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setLevel(logging.WARNING)
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.WARNING)

    def setup_fallback_ids(self):
        """フォールバック: 未取得情報タイプに対する対策"""
        now = datetime.now(self.jst)
        fallback_base = f"fallback_{now.strftime('%Y%m%d_%H%M%S')}"

        fallback_count = 0
        for info_type in [InfoType.EEW.value, InfoType.TSUNAMI.value]:
            if not self.last_ids[info_type]:
                if self.last_ids[InfoType.QUAKE.value]:
                    self.last_ids[info_type] = self.last_ids[InfoType.QUAKE.value]
                    print(f"  ⚙️ {info_type}にフォールバックID設定: {self.last_ids[info_type][:8]}...")
                    fallback_count += 1

        if fallback_count > 0:
            print(f"  ✅ {fallback_count}個の情報タイプにフォールバックID設定完了")

    async def safe_api_request(self, url: str, timeout: int = 15) -> Optional[Dict[str, Any]]:
        """安全なAPI リクエスト処理"""
        try:
            if not self.session or self.session.closed:
                await self.recreate_session()

            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                if response.status == 200:
                    try:
                        data = await response.json()
                        logger.debug(f"API成功: {url} - データ件数: {len(data) if isinstance(data, list) else 1}")
                        return data
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON解析エラー: {url} - {e}")
                        self.error_stats['parsing_errors'] += 1
                        raise DataParsingError(f"JSON解析失敗: {e}")
                elif response.status == 400:  # [修正] 400 Bad Request をログに残す
                    logger.error(f"APIリクエストエラー (Bad Request): {url} - ステータス: {response.status}")
                    self.error_stats['api_errors'] += 1
                    raise APIError(f"不正なリクエスト: {response.status}")
                elif response.status == 429:
                    logger.warning(f"API レート制限: {url}")
                    self.error_stats['api_errors'] += 1
                    raise APIError(f"レート制限: {response.status}")
                else:
                    logger.error(f"API エラー: {url} - ステータス: {response.status}")
                    self.error_stats['api_errors'] += 1
                    raise APIError(f"APIエラー: {response.status}")

        except asyncio.TimeoutError:
            logger.error(f"タイムアウト: {url}")
            self.error_stats['network_errors'] += 1
            raise APIError("リクエストタイムアウト")
        except aiohttp.ClientError as e:
            logger.error(f"ネットワークエラー: {url} - {e}")
            self.error_stats['network_errors'] += 1
            raise APIError(f"ネットワークエラー: {e}")
        finally:
            self.error_stats['last_error_time'] = datetime.now(self.jst)

    async def recreate_session(self):
        """HTTPセッションの再作成"""
        try:
            if self.session and not self.session.closed:
                await self.session.close()
        except Exception as e:
            logger.warning(f"セッションクローズエラー: {e}")

        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers=self.request_headers,
            connector=aiohttp.TCPConnector(limit=10)
        )
        logger.info("HTTPセッションを再作成しました")

    def manage_processed_ids(self, info_type: str):
        """処理済みIDの管理（メモリ使用量制御）"""
        if len(self.processed_ids[info_type]) > self.max_processed_ids:
            ids_list = list(self.processed_ids[info_type])
            self.processed_ids[info_type] = set(ids_list[-self.max_processed_ids:])
            logger.info(f"{info_type}: 処理済みID数を{self.max_processed_ids}に制限")

    async def initialize_processed_ids(self):
        """起動時に最新のIDを取得して、過去の通知を防ぐ（改良版）"""
        codes_to_check = [551, 552]

        for code in codes_to_check:
            try:
                url = f"{self.api_base_url}/history?codes={code}&limit=50"
                data = await self.safe_api_request(url)

                print(f"🔍 Code {code} APIレスポンス: {len(data) if data else 0}件")

                if data and isinstance(data, list) and len(data) > 0:
                    latest_ids = {
                        InfoType.EEW.value: None,
                        InfoType.QUAKE.value: None,
                        InfoType.TSUNAMI.value: None
                    }
                    processed_counts = {
                        InfoType.EEW.value: 0,
                        InfoType.QUAKE.value: 0,
                        InfoType.TSUNAMI.value: 0
                    }

                    for item in data:
                        item_id = self.extract_id_safe(item)
                        if item_id:
                            info_type = self.classify_info_type(item)
                            if info_type != InfoType.UNKNOWN:
                                self.processed_ids[info_type.value].add(item_id)
                                processed_counts[info_type.value] += 1
                                if latest_ids[info_type.value] is None:
                                    latest_ids[info_type.value] = item_id

                    for info_type, latest_id in latest_ids.items():
                        if latest_id:
                            self.last_ids[info_type] = latest_id

                    print(
                        f"  分類結果: EEW={processed_counts[InfoType.EEW.value]}, QUAKE={processed_counts[InfoType.QUAKE.value]}, TSUNAMI={processed_counts[InfoType.TSUNAMI.value]}")
                else:
                    print(f"  データなし or 空の配列")

            except (APIError, DataParsingError) as e:
                print(f"❌ Code {code} のID初期化に失敗: {e}")
            except Exception as e:
                print(f"❌ Code {code} で予期しないエラー: {e}")

        await self.search_historical_eew_tsunami()

        print("🔍 ID初期化結果:")
        type_names = {InfoType.EEW.value: 'EEW', InfoType.QUAKE.value: 'QUAKE', InfoType.TSUNAMI.value: 'TSUNAMI'}
        for info_type, last_id in self.last_ids.items():
            processed_count = len(self.processed_ids.get(info_type, set()))
            id_display = last_id[:8] + "..." if last_id else "未取得"
            print(f"  {type_names[info_type]}: {id_display} ({processed_count}件)")

        self.setup_fallback_ids()

    async def search_historical_eew_tsunami(self):
        """過去のEEW・TSUNAMI情報の検索（IDの確実な初期化のため）"""
        try:
            print("🔍 過去のEEW・TSUNAMI情報を検索中...")

            for code in [551, 552]:
                # [修正] APIの仕様上、limitの最大値は100です。200を指定すると400エラーになるため修正。
                limit = 100
                url = f"{self.api_base_url}/history?codes={code}&limit={limit}"
                data = await self.safe_api_request(url)

                if data and isinstance(data, list):
                    eew_found, tsunami_found = 0, 0
                    for item in data:
                        item_id = self.extract_id_safe(item)
                        if not item_id: continue

                        info_type = self.classify_info_type(item)
                        if info_type == InfoType.EEW and not self.last_ids[InfoType.EEW.value]:
                            self.last_ids[InfoType.EEW.value] = item_id
                            self.processed_ids[InfoType.EEW.value].add(item_id)
                            eew_found += 1
                        elif info_type == InfoType.TSUNAMI and not self.last_ids[InfoType.TSUNAMI.value]:
                            self.last_ids[InfoType.TSUNAMI.value] = item_id
                            self.processed_ids[InfoType.TSUNAMI.value].add(item_id)
                            tsunami_found += 1

                    if eew_found > 0 or tsunami_found > 0:
                        print(f"  Code {code} (limit={limit}): EEW={eew_found}, TSUNAMI={tsunami_found}")

                # 両方のIDが見つかったら検索を終了
                if self.last_ids[InfoType.EEW.value] and self.last_ids[InfoType.TSUNAMI.value]:
                    break
        except (APIError, DataParsingError) as e:
            print(f"⚠️ 過去情報検索中にAPIエラーが発生しました: {e}")
        except Exception as e:
            print(f"⚠️ 過去情報検索エラー: {e}")

    def extract_id_safe(self, item: Dict[str, Any]) -> Optional[str]:
        """アイテムからIDを安全に抽出"""
        try:
            item_id = item.get('id')
            return str(item_id) if item_id is not None else None
        except Exception as e:
            logger.warning(f"ID抽出エラー: {e} - データ: {item}")
            return None

    async def output_stats_if_needed(self):
        """統計情報の定期出力"""
        try:
            now = datetime.now(self.jst)
            time_since_last = (now - self.processing_stats['last_stats_output']).total_seconds()

            if time_since_last >= self.stats_interval:
                # [修正] datetimeオブジェクトを含む可能性のある .values() の直接sum()を避ける
                error_total = self.error_stats['api_errors'] + self.error_stats['parsing_errors'] + self.error_stats[
                    'network_errors']

                stats_msg = (
                    f"[統計] EEW:{self.processing_stats['eew_processed']} "
                    f"QUAKE:{self.processing_stats['quake_processed']} "
                    f"TSUNAMI:{self.processing_stats['tsunami_processed']} "
                    f"UNKNOWN_SKIP:{self.processing_stats['unknown_skipped']} "
                    f"エラー合計:{error_total}"
                )
                logger.warning(stats_msg)  # 重要な情報なのでWARNINGレベルで出力
                print(stats_msg)
                self.processing_stats['last_stats_output'] = now
        except Exception as e:
            logger.debug(f"統計出力エラー: {e}")

    def classify_info_type(self, item: Dict[str, Any]) -> InfoType:
        """情報タイプの分類（P2P地震情報API仕様準拠・デバッグ強化版）"""
        try:
            issue = item.get('issue', {})
            issue_type = issue.get('type', '')
            code = item.get('code', 0)

            if code == 552:
                return InfoType.TSUNAMI

            if code == 551:
                tsunami_info = self.get_tsunami_info(item)
                if tsunami_info.get('has_tsunami', False):
                    return InfoType.TSUNAMI

                eew_keywords = ['緊急地震速報', 'EEW']
                forecast_indicators = ['予報', 'forecast', 'warning']
                quake_keywords = [
                    '震度速報', '震源速報', '震源・震度情報', '各地の震度', '震度・震源情報',
                    'DetailScale', 'ScalePrompt', 'Destination', 'ScaleAndDestination'
                ]

                # issue_typeを小文字にして判定を安定させる
                issue_type_lower = issue_type.lower()
                if any(keyword.lower() in issue_type_lower for keyword in eew_keywords) or \
                        any(keyword.lower() in issue_type_lower for keyword in forecast_indicators):
                    return InfoType.EEW

                if any(keyword.lower() in issue_type_lower for keyword in quake_keywords):
                    return InfoType.QUAKE

                if item.get('earthquake'):
                    return InfoType.QUAKE

            return InfoType.UNKNOWN
        except Exception:
            return InfoType.UNKNOWN

    async def cog_unload(self):
        print("🔄 EarthquakeTsunamiCog アンロード中...")
        if hasattr(self, 'check_earthquake_info'):
            self.check_earthquake_info.cancel()
        if self.session and not self.session.closed:
            try:
                await self.session.close()
            except Exception as e:
                logger.warning(f"セッション終了エラー: {e}")
        print("✅ EarthquakeTsunamiCog アンロード完了")

    def ensure_data_dir(self):
        """データディレクトリの確保"""
        try:
            if not os.path.exists(DATA_DIR):
                os.makedirs(DATA_DIR)
                print(f"'{DATA_DIR}' ディレクトリを作成しました。")
        except OSError as e:
            logger.error(f"ディレクトリ作成エラー: {e}")

    def load_config(self) -> Dict[str, Any]:
        """設定ファイルの読み込み"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 新しい設定形式に対応
                    for guild_id in list(config.keys()):
                        if isinstance(config[guild_id], int):
                            old_channel_id = config[guild_id]
                            config[guild_id] = {
                                InfoType.EEW.value: old_channel_id,
                                InfoType.QUAKE.value: old_channel_id,
                                InfoType.TSUNAMI.value: old_channel_id
                            }
                    return config
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning(f"設定ファイル読み込みエラー: {e}")
        except Exception as e:
            logger.error(f"予期しない設定読み込みエラー: {e}")
        return {}

    def save_config(self):
        """設定ファイルの保存"""
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"設定ファイル保存エラー: {e}")

    # (以下、ヘルパー関数群は変更なし)
    # --- ヘルパー関数群 ---

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
            return discord.Color.purple()

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
        """地震時刻の解析（エラーハンドリング強化・フォーマット修正）"""
        try:
            if isinstance(time_str, str) and time_str.strip():
                # "2025/09/21 10:04:00" 形式（P2P地震情報APIの実際フォーマット）
                if "/" in time_str and ":" in time_str:
                    try:
                        parsed_time = datetime.strptime(time_str, "%Y/%m/%d %H:%M:%S")
                        return parsed_time.replace(tzinfo=self.jst)
                    except ValueError:
                        pass

                # "2024年01月01日 12時34分頃" 形式
                if "年" in time_str and "月" in time_str and "日" in time_str:
                    time_str_clean = time_str.replace("年", "/").replace("月", "/").replace("日", " ").replace("時",
                                                                                                               ":").replace(
                        "分頃", ":00").replace("分", ":00")
                    parsed_time = datetime.strptime(time_str_clean, "%Y/%m/%d %H:%M:%S")
                    return parsed_time.replace(tzinfo=self.jst)

                # "01日12時34分" 形式
                elif "日" in time_str and "時" in time_str:
                    time_str_clean = time_str.replace("日", "日 ").replace("時", ":").replace("分", ":")
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

            # 発表時刻を使用（P2P地震情報APIフォーマットに対応）
            if announced_time:
                if isinstance(announced_time, str):
                    try:
                        # "2025/09/21 10:07:12" 形式
                        if "/" in announced_time and ":" in announced_time:
                            parsed_time = datetime.strptime(announced_time, "%Y/%m/%d %H:%M:%S")
                            return parsed_time.replace(tzinfo=self.jst)
                        # ISO形式も試行
                        return datetime.fromisoformat(announced_time.replace('Z', '+00:00')).astimezone(self.jst)
                    except ValueError:
                        pass

            # 全て失敗した場合は現在時刻
            return datetime.now(self.jst)

        except Exception as e:
            return datetime.now(self.jst)

    def format_magnitude(self, magnitude):
        """マグニチュードの整形"""
        try:
            if magnitude is None or magnitude == -1:
                return "不明"
            return f"M{float(magnitude):.1f}"
        except (ValueError, TypeError):
            return "不明"

    def format_depth(self, depth):
        """震源の深さの整形"""
        try:
            if depth is None or depth == -1:
                return "不明"
            elif depth == 0:
                return "ごく浅い"
            else:
                return f"{int(depth)}km"
        except (ValueError, TypeError):
            return "不明"

    def get_tsunami_info(self, data):
        """津波情報の解析（エラーハンドリング強化・デバッグ版）"""
        tsunami_info = {
            'has_tsunami': False,
            'warning_level': None,
            'areas': [],
            'description': ""
        }

        try:
            # 津波情報の確認
            tsunami_data = data.get('tsunami')
            if not tsunami_data:
                return tsunami_info

            domestic_tsunami = tsunami_data.get('domesticTsunami')
            if domestic_tsunami == 'None' or domestic_tsunami is None:
                return tsunami_info

            tsunami_info['has_tsunami'] = True

            # 大津波警報・津波警報・津波注意報の判定
            grades = {
                'MajorWarning': '大津波警報',
                'Warning': '津波警報',
                'Watch': '津波注意報',
            }

            # 複数の津波情報エリアから最も高いレベルを特定
            highest_grade_level = 0
            warning_level_text = '津波予報'

            areas_data = tsunami_data.get('areas', [])
            if not isinstance(areas_data, list):
                areas_data = []

            for area in areas_data:
                if not isinstance(area, dict):
                    continue

                grade = area.get('grade')
                if grade == 'MajorWarning' and highest_grade_level < 3:
                    highest_grade_level = 3
                    warning_level_text = grades[grade]
                elif grade == 'Warning' and highest_grade_level < 2:
                    highest_grade_level = 2
                    warning_level_text = grades[grade]
                elif grade == 'Watch' and highest_grade_level < 1:
                    highest_grade_level = 1
                    warning_level_text = grades[grade]

            tsunami_info['warning_level'] = warning_level_text

            # 津波予報区域
            tsunami_areas = []
            for area in areas_data:
                if isinstance(area, dict):
                    area_name = area.get('name', '')
                    grade_text = grades.get(area.get('grade'), '情報')
                    if area_name:
                        tsunami_areas.append({
                            'name': area_name,
                            'grade': grade_text
                        })
            tsunami_info['areas'] = tsunami_areas

            # デバッグ出力（津波情報が見つかった場合のみ）
            if tsunami_info['has_tsunami']:
                print(f"🌊 津波情報検出: {warning_level_text}, エリア数: {len(tsunami_areas)}")

        except Exception as e:
            # 津波解析エラーは静かに処理
            pass

        return tsunami_info

    # --- メインの監視処理（修正版） ---

    async def check_earthquake_data(self):
        """地震情報(EEW, Quake)をチェックし、関連する津波情報も処理する"""
        try:
            url = f"{self.api_base_url}/history?codes=551&limit=20"
            data = await self.safe_api_request(url)

            if not data or not isinstance(data, list):
                return

            for info in reversed(data):
                try:
                    await self.process_single_info(info)
                except Exception as e:
                    logger.error(f"情報処理エラー: {e}")

        except (APIError, DataParsingError) as e:
            logger.warning(f"地震情報監視エラー: {e}")
        except Exception as e:
            logger.error(f"予期しない地震情報監視エラー: {e}")

    async def check_tsunami_data(self):
        """津波情報(code:552)を専門にチェックする"""
        try:
            url = f"{self.api_base_url}/history?codes=552&limit=20"
            data = await self.safe_api_request(url)

            if not data or not isinstance(data, list):
                return

            for info in reversed(data):
                try:
                    await self.process_single_info(info)
                except Exception as e:
                    logger.error(f"津波情報処理エラー: {e}")

        except (APIError, DataParsingError) as e:
            logger.warning(f"津波情報監視エラー: {e}")
        except Exception as e:
            logger.error(f"予期しない津波情報監視エラー: {e}")

    async def process_single_info(self, info: Dict[str, Any]):
        """単一の情報アイテムを処理"""
        info_id = self.extract_id_safe(info)
        if not info_id:
            return

        info_type = self.classify_info_type(info)
        if info_type == InfoType.UNKNOWN:
            self.processing_stats['unknown_skipped'] += 1  # [追加] スキップをカウント
            return

        if info_id in self.processed_ids[info_type.value]:
            return

        print(f"🆕 新しい{info_type.value}情報を検知: {info_id}")

        try:
            if info_type == InfoType.EEW:
                await self.send_eew_notification(info)
            elif info_type == InfoType.QUAKE:
                await self.send_quake_notification(info)
            elif info_type == InfoType.TSUNAMI:
                tsunami_info = self.get_tsunami_info(info)
                if tsunami_info.get('has_tsunami', False):
                    await self.send_tsunami_notification(info, tsunami_info)

            # [追加] 処理成功をカウント
            self.processing_stats[f'{info_type.value}_processed'] += 1

            self.processed_ids[info_type.value].add(info_id)
            self.last_ids[info_type.value] = info_id
            self.manage_processed_ids(info_type.value)

        except Exception as e:
            print(f"❌ {info_type.value}通知送信エラー: {e}")

    @tasks.loop(seconds=10)
    async def check_earthquake_info(self):
        """地震・津波情報の監視（エラーハンドリング強化）"""
        try:
            if not self.session or self.session.closed:
                logger.warning("セッション再作成中...")
                await self.recreate_session()

            await self.check_earthquake_data()
            await self.check_tsunami_data()
            await self.output_stats_if_needed()  # [追加] 定期的に統計情報を出力

        except Exception as e:
            logger.error(f"監視ループエラー: {e}")
            if "session" in str(e).lower():
                try:
                    await self.recreate_session()
                except Exception as session_error:
                    logger.error(f"セッション再作成エラー: {session_error}")

    @check_earthquake_info.before_loop
    async def before_check_earthquake_info(self):
        await self.bot.wait_until_ready()
        logger.info("地震・津波情報監視開始 (P2P地震情報 API v2)")

    # (以下、通知送信処理・スラッシュコマンドは変更なし)
    # --- 通知送信処理 ---

    async def send_eew_notification(self, data):
        """緊急地震速報の送信"""
        await self.send_notification(data, InfoType.EEW.value, "🚨 緊急地震速報")

    async def send_quake_notification(self, data):
        """地震情報の送信"""
        await self.send_notification(data, InfoType.QUAKE.value, "📊 地震情報")

    async def send_notification(self, data, info_type, title_prefix):
        """通知の送信（共通処理・エラーハンドリング強化）"""
        try:
            earthquake = data.get('earthquake', {})
            if not earthquake:
                logger.warning("地震データが空のため通知をスキップ")
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
            if info_type == InfoType.EEW.value:
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
            if points and isinstance(points, list):
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
            if tsunami_info['has_tsunami'] and info_type == InfoType.QUAKE.value:
                tsunami_text = f"🌊 **{tsunami_info.get('warning_level', '津波予報')}** が発表されています"
                embed.add_field(name="🌊 津波情報", value=tsunami_text, inline=False)

            embed.set_footer(text="Powered by P2P地震情報 API v2 | 気象庁データ")
            embed.set_thumbnail(url="https://www.p2pquake.net/images/QuakeLogo_100x100.png")

            # 該当チャンネルに送信
            await self.send_embed_to_channels(embed, info_type)

        except Exception as e:
            logger.error(f"{info_type}通知処理エラー: {e}")
            raise

    async def send_tsunami_notification(self, data, tsunami_info):
        """津波予報の送信（エラーハンドリング強化）"""
        try:
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
            if earthquake and isinstance(earthquake, dict):
                hypocenter = earthquake.get('hypocenter', {})
                embed.add_field(name="🌏 震源地", value=f"```{hypocenter.get('name', '不明')}```", inline=True)
                embed.add_field(name="📊 マグニチュード",
                                value=f"```{self.format_magnitude(earthquake.get('magnitude', -1))}```", inline=True)
                embed.add_field(name="📏 深さ", value=f"```{self.format_depth(hypocenter.get('depth', -1))}```",
                                inline=True)

            # 津波予報区域
            areas = tsunami_info.get('areas', [])
            if areas and isinstance(areas, list):
                area_text = ""
                for area in areas[:5]:  # 最大5件
                    if isinstance(area, dict):
                        area_text += f"🌊 **{area.get('grade', warning_level)}** - {area.get('name', '不明')}\n"
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

            description_text = tsunami_info.get('description', '')
            if description_text and isinstance(description_text, str):
                embed.add_field(name="ℹ️ 詳細情報", value=description_text[:500], inline=False)

            embed.set_footer(text="気象庁 | 津波から身を守るため直ちに避難を")
            embed.set_thumbnail(url="https://www.p2pquake.net/images/QuakeLogo_100x100.png")

            # 津波チャンネルに送信
            await self.send_embed_to_channels(embed, InfoType.TSUNAMI.value)

        except Exception as e:
            logger.error(f"津波通知処理エラー: {e}")
            raise

    async def send_embed_to_channels(self, embed, info_type):
        """指定された情報タイプのチャンネルにEmbedを送信（エラーハンドリング強化）"""
        if not self.config:
            return

        sent_count = 0
        failed_count = 0

        for guild_id, guild_config in self.config.items():
            if not isinstance(guild_config, dict) or info_type not in guild_config:
                continue

            try:
                guild = self.bot.get_guild(int(guild_id))
                if not guild:
                    logger.warning(f"サーバー未発見: {guild_id}")
                    failed_count += 1
                    continue

                channel_id = guild_config[info_type]
                channel = guild.get_channel(channel_id)

                if not channel:
                    logger.warning(f"チャンネル未発見 ({info_type}): {channel_id} (Guild: {guild.name})")
                    failed_count += 1
                    continue

                await channel.send(embed=embed)
                sent_count += 1

            except discord.Forbidden:
                logger.error(f"権限不足 ({info_type}): {guild.name}")
                failed_count += 1
            except discord.HTTPException as e:
                logger.error(f"Discord API エラー ({info_type}): {guild.name} - {e}")
                failed_count += 1
            except Exception as e:
                logger.error(f"送信失敗 ({info_type}): {guild.name} - {e}")
                failed_count += 1

        if sent_count > 0 or failed_count > 0:
            logger.info(f"{info_type}通知送信完了: 成功 {sent_count}件, 失敗 {failed_count}件")

    # --- スラッシュコマンド群 ---

    @app_commands.command(name="earthquake_channel", description="地震・津波情報の通知チャンネルを設定します。")
    @app_commands.describe(
        channel="通知を送信するチャンネル",
        info_type="通知したい情報の種類"
    )
    async def set_channel(self, interaction: discord.Interaction,
                          channel: discord.TextChannel,
                          info_type: Literal["緊急地震速報", "地震情報", "津波予報", "すべて"]):
        try:
            guild_id = str(interaction.guild.id)

            # 設定の初期化
            if guild_id not in self.config:
                self.config[guild_id] = {}

            # 情報タイプに応じて設定
            if info_type == "緊急地震速報":
                self.config[guild_id][InfoType.EEW.value] = channel.id
            elif info_type == "地震情報":
                self.config[guild_id][InfoType.QUAKE.value] = channel.id
            elif info_type == "津波予報":
                self.config[guild_id][InfoType.TSUNAMI.value] = channel.id
            elif info_type == "すべて":
                self.config[guild_id][InfoType.EEW.value] = channel.id
                self.config[guild_id][InfoType.QUAKE.value] = channel.id
                self.config[guild_id][InfoType.TSUNAMI.value] = channel.id

            self.save_config()

            await interaction.response.send_message(
                f"✅ **{info_type}** の通知チャンネルを {channel.mention} に設定しました。",
                ephemeral=False
            )
            logger.info(f"チャンネル設定: {guild_id} - {info_type} -> {channel.id}")

        except Exception as e:
            logger.error(f"チャンネル設定エラー: {e}")
            await interaction.response.send_message(
                f"❌ 設定中にエラーが発生しました: {e}",
                ephemeral=True
            )

    @app_commands.command(name="earthquake_status", description="地震・津波情報システムの状態を確認します。")
    async def status_system(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=False)

            embed = discord.Embed(
                title="🔧 地震・津波情報システム状態",
                color=discord.Color.blue(),
                timestamp=datetime.now(self.jst)
            )

            # 監視状態
            monitor_status = "✅ 動作中" if self.check_earthquake_info.is_running() else "❌ 停止中"
            session_status = "✅ 正常" if self.session and not self.session.closed else "❌ 無効"

            embed.add_field(name="🔄 監視状態", value=monitor_status, inline=True)
            embed.add_field(name="🌐 セッション状態", value=session_status, inline=True)

            # 最後のID状況
            id_status = ""
            type_names = {
                InfoType.EEW.value: 'EEW',
                InfoType.QUAKE.value: 'QUAKE',
                InfoType.TSUNAMI.value: 'TSUNAMI'
            }

            for info_type, last_id in self.last_ids.items():
                processed_count = len(self.processed_ids.get(info_type, set()))
                id_display = last_id[:8] + "..." if last_id else "未取得"
                id_status += f"**{type_names[info_type]}**: `{id_display}` (処理済み: {processed_count}件)\n"

            embed.add_field(name="🆔 最後のID", value=id_status, inline=False)

            # 通知チャンネル状況
            guild_id = str(interaction.guild.id)
            if guild_id in self.config:
                channel_status = ""
                type_names_jp = {
                    InfoType.EEW.value: '緊急地震速報',
                    InfoType.QUAKE.value: '地震情報',
                    InfoType.TSUNAMI.value: '津波予報'
                }

                for info_type in [InfoType.EEW.value, InfoType.QUAKE.value, InfoType.TSUNAMI.value]:
                    if info_type in self.config[guild_id]:
                        channel = interaction.guild.get_channel(self.config[guild_id][info_type])
                        status = f"✅ {channel.mention}" if channel else "❌ 削除済み"
                    else:
                        status = "⚠️ 未設定"
                    channel_status += f"**{type_names_jp[info_type]}**: {status}\n"
            else:
                channel_status = "⚠️ すべて未設定"

            embed.add_field(name="📢 通知チャンネル", value=channel_status, inline=False)

            # 最後のエラー時刻
            if self.error_stats['last_error_time']:
                last_error = self.error_stats['last_error_time'].strftime('%m/%d %H:%M:%S')
                embed.add_field(name="🕐 最後のエラー", value=last_error, inline=True)

            embed.set_footer(text="システム診断完了 | P2P地震情報 API v2")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"ステータス表示エラー: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ ステータス取得エラー: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ ステータス取得エラー: {e}")

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
        try:
            await interaction.response.defer(ephemeral=False)

            guild_id = str(interaction.guild.id)

            # 通知チャンネルの取得
            target_channel = None
            is_configured = False

            if guild_id in self.config:
                channel_mapping = {
                    "緊急地震速報": InfoType.EEW.value,
                    "地震情報": InfoType.QUAKE.value,
                    "津波予報": InfoType.TSUNAMI.value
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
            await target_channel.send(embed=embed)

            if is_configured:
                await interaction.followup.send(
                    f"✅ 設定されたチャンネル {target_channel.mention} に **{info_type}** のテスト通知を送信しました。")
            else:
                await interaction.followup.send(
                    f"✅ このチャンネルに **{info_type}** のテスト通知を送信しました。\n"
                    f"ℹ️ 本番の通知は `/earthquake_channel` コマンドで設定したチャンネルに送信されます。")

        except discord.Forbidden:
            await interaction.followup.send(f"❌ {target_channel.mention} にメッセージを送信する権限がありません。")
        except Exception as e:
            logger.error(f"テスト通知エラー: {e}")
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

    # --- 追加コマンド ---

    @app_commands.command(name="earthquake_help", description="このシステムのヘルプを表示します。")
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
`/earthquake_status` - システム状態を確認

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

        await interaction.response.send_message(embed=embed, ephemeral=False)


async def setup(bot: commands.Bot):
    """Cogのセットアップ"""
    try:
        logger.info("EarthquakeTsunamiCog セットアップ関数開始...")
        cog = EarthquakeTsunamiCog(bot)
        await bot.add_cog(cog)
        logger.info("EarthquakeTsunamiCog セットアップ関数完了")
    except Exception as e:
        logger.error(f"Cogセットアップエラー: {e}")
        raise