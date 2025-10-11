import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
from typing import Optional, Literal
import urllib.parse
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


class R6SiegeTracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.base_url = "https://r6-api.vercel.app/api"

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

    def create_profile_embed(self, account_data: dict, stats_data: dict, username: str, platform: str) -> discord.Embed:
        """Create profile embed"""
        embed = discord.Embed(
            title=f"🎮 Rainbow Six Siege Stats",
            color=discord.Color.blue()
        )

        # Player information
        profile = account_data.get("profiles", [{}])[0]
        player_name = profile.get("nameOnPlatform", username)
        level = account_data.get("level", "N/A")

        # Profile picture
        if "profilePicture" in account_data:
            embed.set_thumbnail(url=account_data["profilePicture"])

        embed.add_field(name="Player", value=player_name, inline=True)
        embed.add_field(name="Platform", value=platform.upper(), inline=True)
        embed.add_field(name="Level", value=str(level), inline=True)

        # Parse statistics
        try:
            stats_found = False

            # Navigate through the new structure
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

                            # Extract statistics
                            kills = season_stats.get("kills", 0)
                            deaths = season_stats.get("deaths", 0)
                            match_outcomes = season_stats.get("match_outcomes", {})

                            wins = match_outcomes.get("wins", 0)
                            losses = match_outcomes.get("losses", 0)
                            abandons = match_outcomes.get("abandons", 0)

                            # Calculate derived stats
                            kd = (kills / deaths) if deaths > 0 else kills
                            total_matches = wins + losses + abandons
                            win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

                            # Get rank information
                            rank = profile_data.get("rank", 0)
                            rank_points = profile_data.get("rank_points", 0)
                            max_rank = profile_data.get("max_rank", 0)
                            season_id = profile_data.get("season_id", "N/A")

                            # Determine section name based on board_id
                            if board_id == "standard":
                                section_name = "🏆 Ranked (Current Season)"
                            elif board_id == "living_game_mode":
                                section_name = "⚔️ Quick Match"
                            elif board_id == "casual":
                                section_name = "🎮 Casual"
                            else:
                                section_name = f"📊 {board_id.title()}"

                            stats_found = True

                            embed.add_field(name=section_name, value=f"Season {season_id}", inline=False)

                            # Basic stats
                            embed.add_field(name="K/D", value=f"{kd:.2f}", inline=True)
                            embed.add_field(name="Win Rate", value=f"{win_rate:.1f}%", inline=True)
                            embed.add_field(name="Matches", value=str(total_matches), inline=True)

                            embed.add_field(name="Kills", value=str(kills), inline=True)
                            embed.add_field(name="Deaths", value=str(deaths), inline=True)
                            embed.add_field(name="Wins", value=str(wins), inline=True)

                            # Rank info for ranked mode
                            if board_id == "standard" and rank > 0:
                                embed.add_field(name="Rank", value=f"{rank}", inline=True)
                                embed.add_field(name="MMR", value=f"{rank_points:,}", inline=True)
                                embed.add_field(name="Max Rank", value=f"{max_rank}", inline=True)

            if not stats_found:
                embed.add_field(
                    name="⚠️ No Statistics Available",
                    value="No statistics data found. The account may be private or have no gameplay data for this season.",
                    inline=False
                )

        except Exception as e:
            embed.add_field(
                name="⚠️ Statistics Parsing Error",
                value=f"Error: {str(e)}\nPlease contact the bot administrator.",
                inline=False
            )
            # Log the error for debugging
            print(f"Stats parsing error: {e}")
            import traceback
            traceback.print_exc()

        embed.set_footer(text="Powered by R6Data API • Current Season Stats")
        return embed

    @app_commands.command(name="r6debug", description="[Debug] Display raw API response for player stats")
    @app_commands.describe(
        username="Player username (Ubisoft Connect name)",
        platform="Platform"
    )
    async def r6debug(
            self,
            interaction: discord.Interaction,
            username: str,
            platform: PLATFORMS = "uplay"
    ):
        """Debug command to see raw API response"""
        await interaction.response.defer(ephemeral=True)

        try:
            # Get account information
            account_data = await self.fetch_account_info(username, platform)

            # Get statistics
            platform_family = self.get_platform_family(platform)
            stats_data = await self.fetch_player_stats(username, platform, platform_family)

            # Create debug message with formatted JSON
            import json

            debug_msg = f"**Account Data:**\n```json\n{json.dumps(account_data, indent=2)[:1000]}\n```\n\n"
            debug_msg += f"**Stats Data:**\n```json\n{json.dumps(stats_data, indent=2)[:1000]}\n```"

            await interaction.followup.send(debug_msg, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    @app_commands.command(name="r6stats", description="Display Rainbow Six Siege player statistics")
    @app_commands.describe(
        username="Player username (Ubisoft Connect name)",
        platform="Platform"
    )
    async def r6stats(
            self,
            interaction: discord.Interaction,
            username: str,
            platform: PLATFORMS = "uplay"
    ):
        """Get R6 Siege player statistics"""
        await interaction.response.defer()

        try:
            # Get account information
            account_data = await self.fetch_account_info(username, platform)

            # Get statistics
            platform_family = self.get_platform_family(platform)
            stats_data = await self.fetch_player_stats(username, platform, platform_family)

            # Create and send embed
            embed = self.create_profile_embed(account_data, stats_data, username, platform)
            await interaction.followup.send(embed=embed)

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
        except DataParseError as e:
            await interaction.followup.send(f"⚠️ {str(e)}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                f"❌ An unexpected error occurred: {str(e)}\n"
                f"Debug info: Please check player name and platform",
                ephemeral=True
            )

    @app_commands.command(name="r6operators", description="Search Rainbow Six Siege operator information")
    @app_commands.describe(
        name="Operator name (e.g., Ash, Thermite)",
        role="Filter by role"
    )
    async def r6operators(
            self,
            interaction: discord.Interaction,
            name: Optional[str] = None,
            role: Optional[Literal["attacker", "defender"]] = None
    ):
        """Get operator information"""
        await interaction.response.defer()

        try:
            # Build query parameters
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

            # Display first operator info (if multiple, show first one)
            operator = data[0] if isinstance(data, list) else data

            embed = discord.Embed(
                title=f"{operator.get('name', 'Unknown')}",
                description=f"**Real Name:** {operator.get('realname', 'N/A')}",
                color=discord.Color.orange()
            )

            embed.add_field(name="Birthplace", value=operator.get('birthplace', 'N/A'), inline=True)
            embed.add_field(name="Age", value=operator.get('age', 'N/A'), inline=True)
            embed.add_field(name="Unit", value=operator.get('unit', 'N/A'), inline=True)

            embed.add_field(name="Health", value=operator.get('health', 'N/A'), inline=True)
            embed.add_field(name="Speed", value=operator.get('speed', 'N/A'), inline=True)
            embed.add_field(name="Role", value=operator.get('roles', 'N/A'), inline=True)

            embed.add_field(
                name="Season Introduced",
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

    @app_commands.command(name="r6status", description="Check Rainbow Six Siege server status")
    async def r6status(self, interaction: discord.Interaction):
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


async def setup(bot: commands.Bot):
    """Cog setup function"""
    await bot.add_cog(R6SiegeTracker(bot))