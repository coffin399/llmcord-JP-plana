import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
from typing import Optional, Literal, List, Dict
import urllib.parse
from datetime import datetime
import io
from PLANA.tracker.error.errors import (
    R6APIError,
    PlayerNotFoundError,
    InvalidPlatformError,
    RateLimitError,
    DataParseError,
    NetworkError,
    TimeoutError,
    OperatorNotFoundError,
    StatsNotAvailableError,
    ServerStatusError
)

# Supported platforms
PLATFORMS = Literal["uplay", "psn", "xbl"]


class R6SiegeTrackerExtended(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.base_url = "https://r6-api.vercel.app/api"

        # Rank names mapping
        self.rank_names = {
            0: "Unranked",
            1: "Copper V", 2: "Copper IV", 3: "Copper III", 4: "Copper II", 5: "Copper I",
            6: "Bronze V", 7: "Bronze IV", 8: "Bronze III", 9: "Bronze II", 10: "Bronze I",
            11: "Silver V", 12: "Silver IV", 13: "Silver III", 14: "Silver II", 15: "Silver I",
            16: "Gold V", 17: "Gold IV", 18: "Gold III", 19: "Gold II", 20: "Gold I",
            21: "Platinum V", 22: "Platinum IV", 23: "Platinum III", 24: "Platinum II", 25: "Platinum I",
            26: "Emerald V", 27: "Emerald IV", 28: "Emerald III", 29: "Emerald II", 30: "Emerald I",
            31: "Diamond V", 32: "Diamond IV", 33: "Diamond III", 34: "Diamond II", 35: "Diamond I",
            36: "Champion"
        }

    async def fetch_account_info(self, username: str, platform: str) -> dict:
        """Fetch player account information"""
        encoded_username = urllib.parse.quote(username)
        url = f"{self.base_url}/stats?type=accountInfo&nameOnPlatform={encoded_username}&platformType={platform}"

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status == 404:
                        raise PlayerNotFoundError(username, platform)
                    elif response.status == 429:
                        raise RateLimitError()
                    elif response.status != 200:
                        response_text = await response.text()
                        raise R6APIError(f"Status {response.status}: {response_text}")

                    data = await response.json()
                    if not data or "profiles" not in data:
                        raise StatsNotAvailableError(username, "No profile data returned from API")

                    return data
            except aiohttp.ClientConnectionError as e:
                raise NetworkError(str(e))
            except aiohttp.ServerTimeoutError:
                raise TimeoutError(15)
            except aiohttp.ClientError as e:
                raise NetworkError(str(e))

    async def fetch_player_stats(self, username: str, platform: str, platform_family: str = "pc") -> dict:
        """Fetch player statistics"""
        encoded_username = urllib.parse.quote(username)
        url = f"{self.base_url}/stats?type=stats&nameOnPlatform={encoded_username}&platformType={platform}&platform_families={platform_family}"

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status == 404:
                        raise StatsNotAvailableError(username, "Statistics not found")
                    elif response.status == 429:
                        raise RateLimitError()
                    elif response.status != 200:
                        response_text = await response.text()
                        raise R6APIError(f"Status {response.status}: {response_text}")

                    data = await response.json()
                    if not data:
                        raise StatsNotAvailableError(username, "No statistics data returned")

                    return data
            except aiohttp.ClientConnectionError as e:
                raise NetworkError(str(e))
            except aiohttp.ServerTimeoutError:
                raise TimeoutError(15)
            except aiohttp.ClientError as e:
                raise NetworkError(str(e))

    def get_platform_family(self, platform: str) -> str:
        """Get platform family from platform"""
        mapping = {
            "uplay": "pc",
            "psn": "console",
            "xbl": "console"
        }
        return mapping.get(platform, "pc")

    def format_number(self, value) -> str:
        """Format numbers to readable format"""
        try:
            num = float(value)
            if num >= 1000000:
                return f"{num / 1000000:.1f}M"
            elif num >= 1000:
                return f"{num / 1000:.1f}K"
            else:
                return f"{int(num):,}"
        except (ValueError, TypeError):
            return str(value)

    def format_playtime(self, seconds) -> str:
        """Format playtime to readable format"""
        try:
            sec = int(seconds)
            hours = sec // 3600
            minutes = (sec % 3600) // 60
            if hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m"
        except (ValueError, TypeError):
            return "N/A"

    def get_rank_name(self, rank_id: int) -> str:
        """Get rank name from rank ID"""
        return self.rank_names.get(rank_id, f"Rank {rank_id}")

    def get_rank_color(self, rank_id: int) -> discord.Color:
        """Get color based on rank"""
        if rank_id >= 36:
            return discord.Color.from_rgb(255, 215, 0)  # Champion - Gold
        elif rank_id >= 31:
            return discord.Color.from_rgb(180, 180, 255)  # Diamond - Light Blue
        elif rank_id >= 26:
            return discord.Color.from_rgb(80, 200, 120)  # Emerald - Green
        elif rank_id >= 21:
            return discord.Color.from_rgb(0, 180, 240)  # Platinum - Cyan
        elif rank_id >= 16:
            return discord.Color.from_rgb(255, 215, 0)  # Gold - Yellow
        elif rank_id >= 11:
            return discord.Color.from_rgb(192, 192, 192)  # Silver - Silver
        elif rank_id >= 6:
            return discord.Color.from_rgb(205, 127, 50)  # Bronze - Bronze
        elif rank_id >= 1:
            return discord.Color.from_rgb(184, 115, 51)  # Copper - Dark Orange
        else:
            return discord.Color.dark_grey()  # Unranked

    def extract_season_history(self, stats_data: dict) -> List[Dict]:
        """Extract seasonal ranking history from stats data"""
        history = []

        try:
            if isinstance(stats_data, dict) and "platform_families_full_profiles" in stats_data:
                platform_families = stats_data.get("platform_families_full_profiles", [])

                for pf in platform_families:
                    if not isinstance(pf, dict):
                        continue

                    board_ids = pf.get("board_ids_full_profiles", [])

                    for board in board_ids:
                        if not isinstance(board, dict):
                            continue

                        board_id = board.get("board_id", "")

                        # Only process ranked stats
                        if board_id != "standard":
                            continue

                        full_profiles = board.get("full_profiles", [])

                        for fp in full_profiles:
                            if not isinstance(fp, dict):
                                continue

                            profile_data = fp.get("profile", {})
                            season_stats = fp.get("season_statistics", {})

                            if not season_stats:
                                continue

                            season_id = profile_data.get("season_id", "Unknown")
                            rank = profile_data.get("rank", 0)
                            rank_points = profile_data.get("rank_points", 0)
                            max_rank = profile_data.get("max_rank", 0)
                            max_rank_points = profile_data.get("max_rank_points", 0)

                            kills = season_stats.get("kills", 0)
                            deaths = season_stats.get("deaths", 0)
                            match_outcomes = season_stats.get("match_outcomes", {})

                            wins = match_outcomes.get("wins", 0)
                            losses = match_outcomes.get("losses", 0)

                            kd = (kills / deaths) if deaths > 0 else kills
                            win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

                            history.append({
                                "season": season_id,
                                "rank": rank,
                                "rank_name": self.get_rank_name(rank),
                                "mmr": rank_points,
                                "max_rank": max_rank,
                                "max_rank_name": self.get_rank_name(max_rank),
                                "max_mmr": max_rank_points,
                                "kills": kills,
                                "deaths": deaths,
                                "kd": kd,
                                "wins": wins,
                                "losses": losses,
                                "win_rate": win_rate,
                                "total_matches": wins + losses
                            })

        except Exception as e:
            print(f"Error extracting season history: {e}")
            import traceback
            traceback.print_exc()

        # Sort by season (most recent first)
        history.sort(key=lambda x: x["season"], reverse=True)
        return history

    def create_mmr_history_embed(self, history: List[Dict], username: str, platform: str) -> discord.Embed:
        """Create MMR history embed with seasonal data"""
        embed = discord.Embed(
            title=f"📊 MMR & Rank History",
            description=f"Player: **{username}** ({platform.upper()})",
            color=discord.Color.blue()
        )

        if not history:
            embed.add_field(
                name="⚠️ No Ranked History",
                value="No ranked match history found for this player.",
                inline=False
            )
            return embed

        # Display up to 5 most recent seasons
        for i, season_data in enumerate(history[:5]):
            rank_emoji = "👑" if season_data["rank"] >= 36 else "🏆"

            season_text = (
                f"{rank_emoji} **{season_data['rank_name']}** ({season_data['mmr']:,} MMR)\n"
                f"🔝 Peak: **{season_data['max_rank_name']}** ({season_data['max_mmr']:,} MMR)\n"
                f"📈 K/D: **{season_data['kd']:.2f}** | Win Rate: **{season_data['win_rate']:.1f}%**\n"
                f"🎮 Matches: **{season_data['total_matches']}** ({season_data['wins']}W / {season_data['losses']}L)"
            )

            embed.add_field(
                name=f"Season {season_data['season']}",
                value=season_text,
                inline=False
            )

        # Summary statistics
        if len(history) > 0:
            avg_kd = sum(s["kd"] for s in history) / len(history)
            avg_wr = sum(s["win_rate"] for s in history) / len(history)
            total_matches = sum(s["total_matches"] for s in history)

            embed.add_field(
                name="📊 Overall Statistics",
                value=(
                    f"Average K/D: **{avg_kd:.2f}**\n"
                    f"Average Win Rate: **{avg_wr:.1f}%**\n"
                    f"Total Ranked Matches: **{total_matches:,}**"
                ),
                inline=False
            )

        embed.set_footer(text=f"Powered by R6Data API • Showing {min(5, len(history))} most recent seasons")
        return embed

    def create_detailed_stats_embed(self, account_data: dict, stats_data: dict, username: str,
                                    platform: str) -> discord.Embed:
        """Create detailed statistics embed with all game modes"""
        # Get the highest rank for color
        highest_rank = 0
        history = self.extract_season_history(stats_data)
        if history:
            highest_rank = max(s["rank"] for s in history)

        embed = discord.Embed(
            title=f"🎮 Rainbow Six Siege - Detailed Stats",
            color=self.get_rank_color(highest_rank)
        )

        # Player information
        profile = account_data.get("profiles", [{}])[0]
        player_name = profile.get("nameOnPlatform", username)
        level = account_data.get("level", "N/A")

        if "profilePicture" in account_data:
            embed.set_thumbnail(url=account_data["profilePicture"])

        embed.add_field(name="👤 Player", value=player_name, inline=True)
        embed.add_field(name="🎯 Platform", value=platform.upper(), inline=True)
        embed.add_field(name="⭐ Level", value=str(level), inline=True)

        # Parse all game mode statistics
        try:
            if isinstance(stats_data, dict) and "platform_families_full_profiles" in stats_data:
                platform_families = stats_data.get("platform_families_full_profiles", [])

                for pf in platform_families:
                    if not isinstance(pf, dict):
                        continue

                    board_ids = pf.get("board_ids_full_profiles", [])

                    for board in board_ids:
                        if not isinstance(board, dict):
                            continue

                        board_id = board.get("board_id", "")
                        full_profiles = board.get("full_profiles", [])

                        if not full_profiles:
                            continue

                        for fp in full_profiles:
                            if not isinstance(fp, dict):
                                continue

                            profile_data = fp.get("profile", {})
                            season_stats = fp.get("season_statistics", {})

                            if not season_stats:
                                continue

                            kills = season_stats.get("kills", 0)
                            deaths = season_stats.get("deaths", 0)
                            assists = season_stats.get("assists", 0)
                            headshots = season_stats.get("headshots", 0)
                            melee_kills = season_stats.get("melee_kills", 0)
                            revives = season_stats.get("revives", 0)

                            match_outcomes = season_stats.get("match_outcomes", {})
                            wins = match_outcomes.get("wins", 0)
                            losses = match_outcomes.get("losses", 0)
                            abandons = match_outcomes.get("abandons", 0)

                            # Calculate stats
                            kd = (kills / deaths) if deaths > 0 else kills
                            headshot_rate = (headshots / kills * 100) if kills > 0 else 0
                            total_matches = wins + losses + abandons
                            win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

                            # Game mode names
                            if board_id == "standard":
                                mode_name = "🏆 Ranked"
                                rank = profile_data.get("rank", 0)
                                mmr = profile_data.get("rank_points", 0)
                                season = profile_data.get("season_id", "N/A")

                                embed.add_field(
                                    name=f"{mode_name} (Season {season})",
                                    value=f"**{self.get_rank_name(rank)}** - {mmr:,} MMR",
                                    inline=False
                                )
                            elif board_id == "living_game_mode":
                                mode_name = "⚔️ Quick Match"
                            elif board_id == "casual":
                                mode_name = "🎮 Casual"
                            else:
                                mode_name = f"📊 {board_id.title()}"

                            # Display stats for this mode
                            stats_text = (
                                f"```\n"
                                f"K/D Ratio:      {kd:.2f}\n"
                                f"Win Rate:       {win_rate:.1f}%\n"
                                f"Headshot %:     {headshot_rate:.1f}%\n"
                                f"───────────────────────\n"
                                f"Kills:          {kills:,}\n"
                                f"Deaths:         {deaths:,}\n"
                                f"Assists:        {assists:,}\n"
                                f"Headshots:      {headshots:,}\n"
                                f"Melee Kills:    {melee_kills:,}\n"
                                f"Revives:        {revives:,}\n"
                                f"───────────────────────\n"
                                f"Matches:        {total_matches:,}\n"
                                f"Wins:           {wins:,}\n"
                                f"Losses:         {losses:,}\n"
                                f"```"
                            )

                            embed.add_field(name=mode_name, value=stats_text, inline=True)

        except Exception as e:
            embed.add_field(
                name="⚠️ Statistics Parsing Error",
                value=f"Error: {str(e)}",
                inline=False
            )
            print(f"Stats parsing error: {e}")
            import traceback
            traceback.print_exc()

        embed.set_footer(text="Powered by R6Data API • Current Season Data")
        return embed

    def create_comprehensive_stats_embed(self, account_data: dict, stats_data: dict, username: str, platform: str) -> \
    List[discord.Embed]:
        """Create comprehensive statistics embeds with all data including MMR history"""
        embeds = []

        # Extract season history first
        history = self.extract_season_history(stats_data)
        highest_rank = max((s["rank"] for s in history), default=0) if history else 0

        # === EMBED 1: Player Overview & Current Season ===
        profile = account_data.get("profiles", [{}])[0]
        player_name = profile.get("nameOnPlatform", username)
        level = account_data.get("level", "N/A")

        embed1 = discord.Embed(
            title=f"🎮 {player_name} - Rainbow Six Siege Stats",
            description=f"**Platform:** {platform.upper()} | **Level:** {level}",
            color=self.get_rank_color(highest_rank)
        )

        if "profilePicture" in account_data:
            embed1.set_thumbnail(url=account_data["profilePicture"])

        # Current Season Ranked Stats
        if history:
            current = history[0]
            rank_emoji = "👑" if current["rank"] >= 36 else "🏆"

            current_stats = (
                f"{rank_emoji} **{current['rank_name']}** - {current['mmr']:,} MMR\n"
                f"🔝 **Peak:** {current['max_rank_name']} ({current['max_mmr']:,} MMR)\n"
                f"📊 **Season:** {current['season']}\n\n"
                f"**Performance:**\n"
                f"├ K/D Ratio: **{current['kd']:.2f}**\n"
                f"├ Win Rate: **{current['win_rate']:.1f}%**\n"
                f"├ Matches: **{current['total_matches']:,}**\n"
                f"├ Wins: **{current['wins']:,}**\n"
                f"└ Losses: **{current['losses']:,}**"
            )
            embed1.add_field(name="🏆 Current Season (Ranked)", value=current_stats, inline=False)

        embed1.set_footer(text="Page 1/3 • Powered by R6Data API")
        embeds.append(embed1)

        # === EMBED 2: MMR History & Season Progress ===
        embed2 = discord.Embed(
            title=f"📊 MMR History & Seasonal Rankings",
            description=f"Player: **{player_name}**",
            color=self.get_rank_color(highest_rank)
        )

        if history:
            # Display last 5 seasons
            for i, season_data in enumerate(history[:5]):
                rank_emoji = "👑" if season_data["rank"] >= 36 else "🏆"

                season_text = (
                    f"{rank_emoji} **{season_data['rank_name']}** ({season_data['mmr']:,} MMR)\n"
                    f"🔝 Peak: {season_data['max_rank_name']} ({season_data['max_mmr']:,})\n"
                    f"📈 K/D: {season_data['kd']:.2f} | WR: {season_data['win_rate']:.1f}%\n"
                    f"🎮 {season_data['total_matches']} matches ({season_data['wins']}W-{season_data['losses']}L)"
                )

                embed2.add_field(
                    name=f"Season {season_data['season']}",
                    value=season_text,
                    inline=False
                )

            # Overall statistics
            avg_kd = sum(s["kd"] for s in history) / len(history)
            avg_wr = sum(s["win_rate"] for s in history) / len(history)
            total_matches = sum(s["total_matches"] for s in history)
            total_wins = sum(s["wins"] for s in history)
            highest_mmr = max(s["max_mmr"] for s in history)
            highest_rank_name = max(history, key=lambda s: s["max_rank"])["max_rank_name"]

            overall = (
                f"**Career Highs:**\n"
                f"├ Highest Rank: **{highest_rank_name}**\n"
                f"├ Highest MMR: **{highest_mmr:,}**\n"
                f"└ Total Wins: **{total_wins:,}**\n\n"
                f"**Averages:**\n"
                f"├ Avg K/D: **{avg_kd:.2f}**\n"
                f"├ Avg Win Rate: **{avg_wr:.1f}%**\n"
                f"└ Total Matches: **{total_matches:,}**"
            )
            embed2.add_field(name="📈 Career Statistics", value=overall, inline=False)
        else:
            embed2.add_field(
                name="⚠️ No Ranked History",
                value="No ranked match history found.",
                inline=False
            )

        embed2.set_footer(text="Page 2/3 • Showing up to 5 most recent seasons")
        embeds.append(embed2)

        # === EMBED 3: Detailed Game Mode Statistics ===
        embed3 = discord.Embed(
            title=f"📊 Detailed Statistics by Game Mode",
            description=f"Player: **{player_name}**",
            color=self.get_rank_color(highest_rank)
        )

        try:
            stats_found = False
            if isinstance(stats_data, dict) and "platform_families_full_profiles" in stats_data:
                platform_families = stats_data.get("platform_families_full_profiles", [])

                for pf in platform_families:
                    if not isinstance(pf, dict):
                        continue

                    board_ids = pf.get("board_ids_full_profiles", [])

                    for board in board_ids:
                        if not isinstance(board, dict):
                            continue

                        board_id = board.get("board_id", "")
                        full_profiles = board.get("full_profiles", [])

                        if not full_profiles:
                            continue

                        for fp in full_profiles:
                            if not isinstance(fp, dict):
                                continue

                            season_stats = fp.get("season_statistics", {})

                            if not season_stats:
                                continue

                            kills = season_stats.get("kills", 0)
                            deaths = season_stats.get("deaths", 0)
                            assists = season_stats.get("assists", 0)
                            headshots = season_stats.get("headshots", 0)
                            melee_kills = season_stats.get("melee_kills", 0)
                            revives = season_stats.get("revives", 0)

                            match_outcomes = season_stats.get("match_outcomes", {})
                            wins = match_outcomes.get("wins", 0)
                            losses = match_outcomes.get("losses", 0)

                            kd = (kills / deaths) if deaths > 0 else kills
                            headshot_rate = (headshots / kills * 100) if kills > 0 else 0
                            total_matches = wins + losses
                            win_rate = (wins / total_matches * 100) if total_matches > 0 else 0

                            # Game mode names
                            if board_id == "standard":
                                mode_name = "🏆 Ranked"
                            elif board_id == "living_game_mode":
                                mode_name = "⚔️ Quick Match"
                            elif board_id == "casual":
                                mode_name = "🎮 Casual"
                            else:
                                mode_name = f"📊 {board_id.title()}"

                            stats_found = True

                            stats_text = (
                                f"**Combat Stats:**\n"
                                f"├ K/D: **{kd:.2f}** ({kills:,} kills / {deaths:,} deaths)\n"
                                f"├ Headshot %: **{headshot_rate:.1f}%** ({headshots:,} headshots)\n"
                                f"├ Assists: **{assists:,}**\n"
                                f"├ Melee Kills: **{melee_kills:,}**\n"
                                f"└ Revives: **{revives:,}**\n\n"
                                f"**Match Record:**\n"
                                f"├ Win Rate: **{win_rate:.1f}%**\n"
                                f"├ Matches: **{total_matches:,}**\n"
                                f"├ Wins: **{wins:,}**\n"
                                f"└ Losses: **{losses:,}**"
                            )

                            embed3.add_field(name=mode_name, value=stats_text, inline=False)

            if not stats_found:
                embed3.add_field(
                    name="⚠️ No Statistics Available",
                    value="No statistics data found for this season.",
                    inline=False
                )

        except Exception as e:
            embed3.add_field(
                name="⚠️ Statistics Parsing Error",
                value=f"Error: {str(e)}",
                inline=False
            )
            print(f"Stats parsing error: {e}")
            import traceback
            traceback.print_exc()

        embed3.set_footer(text="Page 3/3 • Current Season Data")
        embeds.append(embed3)

        return embeds

    @app_commands.command(name="r6s-stats",
                          description="Display comprehensive R6 Siege player statistics (MMR history, game modes, etc)")
    @app_commands.describe(
        username="Player username (Ubisoft Connect name)",
        platform="Platform"
    )
    async def r6s_stats(
            self,
            interaction: discord.Interaction,
            username: str,
            platform: PLATFORMS = "uplay"
    ):
        """Get comprehensive R6 Siege player statistics"""
        await interaction.response.defer()

        try:
            account_data = await self.fetch_account_info(username, platform)
            platform_family = self.get_platform_family(platform)
            stats_data = await self.fetch_player_stats(username, platform, platform_family)

            embeds = self.create_comprehensive_stats_embed(account_data, stats_data, username, platform)
            await interaction.followup.send(embeds=embeds)

        except PlayerNotFoundError as e:
            await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
        except StatsNotAvailableError as e:
            await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
        except RateLimitError as e:
            await interaction.followup.send(f"⏱️ {str(e)}", ephemeral=True)
        except NetworkError as e:
            await interaction.followup.send(f"🌐 {str(e)}", ephemeral=True)
        except TimeoutError as e:
            await interaction.followup.send(f"⏰ {str(e)}", ephemeral=True)
        except R6APIError as e:
            await interaction.followup.send(f"❌ API Error: {str(e)}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                f"❌ An unexpected error occurred: {str(e)}",
                ephemeral=True
            )

    @app_commands.command(name="r6s-compare", description="Compare two players' statistics")
    @app_commands.describe(
        player1="First player username",
        player2="Second player username",
        platform="Platform (both players must be on same platform)"
    )
    async def r6s_compare(
            self,
            interaction: discord.Interaction,
            player1: str,
            player2: str,
            platform: PLATFORMS = "uplay"
    ):
        """Compare two players"""
        await interaction.response.defer()

        try:
            # Fetch both players' data
            platform_family = self.get_platform_family(platform)

            acc1 = await self.fetch_account_info(player1, platform)
            stats1 = await self.fetch_player_stats(player1, platform, platform_family)
            history1 = self.extract_season_history(stats1)

            acc2 = await self.fetch_account_info(player2, platform)
            stats2 = await self.fetch_player_stats(player2, platform, platform_family)
            history2 = self.extract_season_history(stats2)

            # Create comparison embed
            embed = discord.Embed(
                title="⚔️ Player Comparison",
                description=f"**{player1}** vs **{player2}** ({platform.upper()})",
                color=discord.Color.purple()
            )

            # Current season stats comparison
            if history1 and history2:
                s1 = history1[0]
                s2 = history2[0]

                # Rank comparison
                rank_comp = f"**{player1}**: {s1['rank_name']} ({s1['mmr']:,} MMR)\n"
                rank_comp += f"**{player2}**: {s2['rank_name']} ({s2['mmr']:,} MMR)"
                embed.add_field(name="🏆 Current Rank", value=rank_comp, inline=False)

                # K/D comparison
                kd_comp = f"**{player1}**: {s1['kd']:.2f}\n"
                kd_comp += f"**{player2}**: {s2['kd']:.2f}"
                embed.add_field(name="📈 K/D Ratio", value=kd_comp, inline=True)

                # Win rate comparison
                wr_comp = f"**{player1}**: {s1['win_rate']:.1f}%\n"
                wr_comp += f"**{player2}**: {s2['win_rate']:.1f}%"
                embed.add_field(name="🎯 Win Rate", value=wr_comp, inline=True)

                # Matches comparison
                match_comp = f"**{player1}**: {s1['total_matches']:,}\n"
                match_comp += f"**{player2}**: {s2['total_matches']:,}"
                embed.add_field(name="🎮 Matches Played", value=match_comp, inline=True)

            # Level comparison
            level1 = acc1.get("level", 0)
            level2 = acc2.get("level", 0)
            level_comp = f"**{player1}**: Level {level1}\n"
            level_comp += f"**{player2}**: Level {level2}"
            embed.add_field(name="⭐ Player Level", value=level_comp, inline=False)

            embed.set_footer(text="Powered by R6Data API • Current Season Stats")
            await interaction.followup.send(embed=embed)

        except PlayerNotFoundError as e:
            await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
        except StatsNotAvailableError as e:
            await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                f"❌ An unexpected error occurred: {str(e)}",
                ephemeral=True
            )

    @app_commands.command(name="r6s-operator", description="Search Rainbow Six Siege operator information")
    @app_commands.describe(
        name="Operator name (e.g., Ash, Thermite)",
        role="Filter by role"
    )
    async def r6s_operator(
            self,
            interaction: discord.Interaction,
            name: Optional[str] = None,
            role: Optional[Literal["attacker", "defender"]] = None
    ):
        """Get operator information"""
        await interaction.response.defer()

        try:
            params = []
            if name:
                params.append(f"name={urllib.parse.quote(name)}")
            if role:
                params.append(f"roles={role}")

            query_string = "&".join(params) if params else ""
            url = f"{self.base_url}/operators?{query_string}" if query_string else f"{self.base_url}/operators"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 404:
                        raise OperatorNotFoundError(name if name else "Unknown")
                    elif response.status != 200:
                        raise R6APIError(f"Status {response.status}")

                    data = await response.json()

            if not data:
                raise OperatorNotFoundError(name if name else "Unknown")

            operator = data[0] if isinstance(data, list) else data

            embed = discord.Embed(
                title=f"🎭 {operator.get('name', 'Unknown')}",
                description=f"**Real Name:** {operator.get('realname', 'N/A')}",
                color=discord.Color.orange()
            )

            embed.add_field(name="📍 Birthplace", value=operator.get('birthplace', 'N/A'), inline=True)
            embed.add_field(name="🎂 Age", value=operator.get('age', 'N/A'), inline=True)
            embed.add_field(name="🎖️ Unit", value=operator.get('unit', 'N/A'), inline=True)

            embed.add_field(name="❤️ Health", value=operator.get('health', 'N/A'), inline=True)
            embed.add_field(name="⚡ Speed", value=operator.get('speed', 'N/A'), inline=True)
            embed.add_field(name="🎯 Role", value=operator.get('roles', 'N/A'), inline=True)

            embed.add_field(
                name="📅 Season Introduced",
                value=operator.get('season_introduced', 'N/A'),
                inline=False
            )

            if operator.get('icon_url'):
                embed.set_thumbnail(url=operator['icon_url'])

            embed.set_footer(text="Powered by R6Data API")
            await interaction.followup.send(embed=embed)

        except OperatorNotFoundError as e:
            await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
        except R6APIError as e:
            await interaction.followup.send(f"❌ API Error: {str(e)}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                f"❌ An unexpected error occurred: {str(e)}",
                ephemeral=True
            )

    @app_commands.command(name="r6s-server-status", description="Check Rainbow Six Siege server status")
    async def r6s_server_status(self, interaction: discord.Interaction):
        """Get R6 Siege server status"""
        await interaction.response.defer()

        try:
            url = f"{self.base_url}/status"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        raise ServerStatusError(f"API returned status {response.status}")

                    data = await response.json()

            embed = discord.Embed(
                title="🌐 Rainbow Six Siege Server Status",
                color=discord.Color.green()
            )

            for platform in data:
                status = platform.get('status', 'Unknown')
                services = platform.get('services', [])

                status_emoji = "🟢" if status.lower() == "online" else "🔴"
                services_text = "\n".join(services) if services else "No information"

                embed.add_field(
                    name=f"{status_emoji} {platform.get('name', 'Unknown')}",
                    value=services_text,
                    inline=False
                )

            embed.set_footer(text="Powered by R6Data API")
            await interaction.followup.send(embed=embed)

        except ServerStatusError as e:
            await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
        except R6APIError as e:
            await interaction.followup.send(f"❌ API Error: {str(e)}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                f"❌ An unexpected error occurred: {str(e)}",
                ephemeral=True
            )

    @app_commands.command(name="r6s-search", description="Search for operators, weapons, maps, and more")
    @app_commands.describe(
        query="Search query (e.g., 'Ash', 'R4-C', 'Oregon')"
    )
    async def r6s_search(
            self,
            interaction: discord.Interaction,
            query: str
    ):
        """Global search across all R6 data"""
        await interaction.response.defer()

        try:
            encoded_query = urllib.parse.quote(query)
            url = f"{self.base_url}/searchAll?q={encoded_query}"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        raise R6APIError(f"Search failed with status {response.status}")

                    data = await response.json()

            if not data or not data.get('results'):
                await interaction.followup.send(
                    f"❌ No results found for '{query}'",
                    ephemeral=True
                )
                return

            summary = data.get('summary', {})
            results = data.get('results', {})

            embed = discord.Embed(
                title=f"🔍 Search Results: '{query}'",
                description=f"Found {summary.get('total', 0)} total results",
                color=discord.Color.blue()
            )

            # Operators
            operators = results.get('operators', [])
            if operators:
                op_text = "\n".join([f"• {op.get('name', 'Unknown')} ({op.get('roles', 'N/A')})"
                                     for op in operators[:3]])
                if len(operators) > 3:
                    op_text += f"\n*...and {len(operators) - 3} more*"
                embed.add_field(name=f"🎭 Operators ({len(operators)})", value=op_text, inline=False)

            # Weapons
            weapons = results.get('weapons', [])
            if weapons:
                weapon_text = "\n".join([f"• {w.get('name', 'Unknown')} ({w.get('type', 'N/A')})"
                                         for w in weapons[:3]])
                if len(weapons) > 3:
                    weapon_text += f"\n*...and {len(weapons) - 3} more*"
                embed.add_field(name=f"🔫 Weapons ({len(weapons)})", value=weapon_text, inline=False)

            # Maps
            maps = results.get('maps', [])
            if maps:
                map_text = "\n".join([f"• {m.get('name', 'Unknown')} ({m.get('location', 'N/A')})"
                                      for m in maps[:3]])
                if len(maps) > 3:
                    map_text += f"\n*...and {len(maps) - 3} more*"
                embed.add_field(name=f"🗺️ Maps ({len(maps)})", value=map_text, inline=False)

            # Seasons
            seasons = results.get('seasons', [])
            if seasons:
                season_text = "\n".join([f"• {s.get('name', 'Unknown')}" for s in seasons[:3]])
                if len(seasons) > 3:
                    season_text += f"\n*...and {len(seasons) - 3} more*"
                embed.add_field(name=f"📅 Seasons ({len(seasons)})", value=season_text, inline=False)

            embed.set_footer(text="Powered by R6Data API • Use specific commands for detailed info")
            await interaction.followup.send(embed=embed)

        except R6APIError as e:
            await interaction.followup.send(f"❌ API Error: {str(e)}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                f"❌ An unexpected error occurred: {str(e)}",
                ephemeral=True
            )

    @app_commands.command(name="r6s-map", description="Get information about Rainbow Six Siege maps")
    @app_commands.describe(
        name="Map name (e.g., 'Oregon', 'Clubhouse')"
    )
    async def r6s_map(
            self,
            interaction: discord.Interaction,
            name: Optional[str] = None
    ):
        """Get map information"""
        await interaction.response.defer()

        try:
            url = f"{self.base_url}/maps"
            if name:
                url += f"?name={urllib.parse.quote(name)}"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        raise R6APIError(f"Status {response.status}")

                    data = await response.json()

            if not data:
                await interaction.followup.send(
                    f"❌ No maps found" + (f" for '{name}'" if name else ""),
                    ephemeral=True
                )
                return

            # Show first map if multiple results
            map_data = data[0] if isinstance(data, list) else data

            embed = discord.Embed(
                title=f"🗺️ {map_data.get('name', 'Unknown Map')}",
                color=discord.Color.green()
            )

            embed.add_field(
                name="📍 Location",
                value=map_data.get('location', 'N/A'),
                inline=True
            )

            release_date = map_data.get('releaseDate', 'N/A')
            embed.add_field(
                name="📅 Release Date",
                value=release_date,
                inline=True
            )

            playlists = map_data.get('playlists', 'N/A')
            embed.add_field(
                name="🎮 Available In",
                value=playlists,
                inline=False
            )

            rework = map_data.get('mapReworked', None)
            if rework:
                embed.add_field(
                    name="🔧 Reworked",
                    value=rework,
                    inline=True
                )

            embed.set_footer(text="Powered by R6Data API")
            await interaction.followup.send(embed=embed)

        except R6APIError as e:
            await interaction.followup.send(f"❌ API Error: {str(e)}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                f"❌ An unexpected error occurred: {str(e)}",
                ephemeral=True
            )

    @app_commands.command(name="r6s-weapon", description="Get information about weapons")
    @app_commands.describe(
        name="Weapon name (e.g., 'R4-C', 'AK-12')"
    )
    async def r6s_weapon(
            self,
            interaction: discord.Interaction,
            name: str
    ):
        """Get weapon information"""
        await interaction.response.defer()

        try:
            encoded_name = urllib.parse.quote(name)
            url = f"{self.base_url}/weapons?name={encoded_name}"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        raise R6APIError(f"Status {response.status}")

                    data = await response.json()

            if not data:
                await interaction.followup.send(
                    f"❌ Weapon '{name}' not found",
                    ephemeral=True
                )
                return

            weapon = data[0] if isinstance(data, list) else data

            embed = discord.Embed(
                title=f"🔫 {weapon.get('name', 'Unknown Weapon')}",
                color=discord.Color.red()
            )

            embed.add_field(
                name="Type",
                value=weapon.get('type', 'N/A'),
                inline=True
            )

            embed.add_field(
                name="Damage",
                value=str(weapon.get('damage', 'N/A')),
                inline=True
            )

            embed.add_field(
                name="Fire Rate",
                value=f"{weapon.get('fireRate', 'N/A')} RPM",
                inline=True
            )

            embed.add_field(
                name="Magazine Size",
                value=str(weapon.get('magazineSize', 'N/A')),
                inline=True
            )

            operators = weapon.get('operators', [])
            if operators:
                op_list = ", ".join(operators[:5])
                if len(operators) > 5:
                    op_list += f", +{len(operators) - 5} more"
                embed.add_field(
                    name="Used By",
                    value=op_list,
                    inline=False
                )

            embed.set_footer(text="Powered by R6Data API")
            await interaction.followup.send(embed=embed)

        except R6APIError as e:
            await interaction.followup.send(f"❌ API Error: {str(e)}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                f"❌ An unexpected error occurred: {str(e)}",
                ephemeral=True
            )

    @app_commands.command(name="r6s-debug", description="[Debug] Display raw API response for player stats")
    @app_commands.describe(
        username="Player username (Ubisoft Connect name)",
        platform="Platform"
    )
    async def r6s_debug(
            self,
            interaction: discord.Interaction,
            username: str,
            platform: PLATFORMS = "uplay"
    ):
        """Debug command to see raw API response"""
        await interaction.response.defer(ephemeral=True)

        try:
            account_data = await self.fetch_account_info(username, platform)
            platform_family = self.get_platform_family(platform)
            stats_data = await self.fetch_player_stats(username, platform, platform_family)

            import json

            debug_msg = f"**Account Data:**\n```json\n{json.dumps(account_data, indent=2)[:1000]}\n```\n\n"
            debug_msg += f"**Stats Data:**\n```json\n{json.dumps(stats_data, indent=2)[:1000]}\n```"

            await interaction.followup.send(debug_msg, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)


async def setup(bot: commands.Bot):
    """Cog setup function"""
    await bot.add_cog(R6SiegeTrackerExtended(bot))