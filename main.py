import discord
import random
from discord.ext import commands
import asyncio
import asqlite
from collections import defaultdict
import settings
import re
import settings.utils as utils
from keep_alive import keep_alive
keep_alive()

bot = commands.Bot(command_prefix=settings.CMD_PREF, intents=settings.INTENTS)
invites = {}  # To store the invites and their usage count

# Initialize the database connection
async def init_db():
    async with asqlite.connect('cashbot.db') as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                xp INTEGER DEFAULT 0,
                coins INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS giveaway_entries (
                user_id INTEGER PRIMARY KEY
            )
        ''')
        await db.commit()

# Add user to the database if not already present
async def ensure_user_in_db(user_id):
    async with asqlite.connect('cashbot.db') as db:
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            if not await cursor.fetchone():
                await db.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
                await db.commit()

# Store current invites in a global dictionary
async def cache_invites(guild):
    global invites
    invites[guild.id] = await guild.invites()

# Detect which invite was used and reward the inviter
@bot.event
async def on_member_join(member):
    await ensure_user_in_db(member.id)

    # Get the updated list of invites
    new_invites = await member.guild.invites()
    old_invites = invites.get(member.guild.id, [])

    # Find which invite link's usage count has changed
    used_invite = None
    for invite in new_invites:
        for old_invite in old_invites:
            if invite.code == old_invite.code and invite.uses > old_invite.uses:
                used_invite = invite
                break
        if used_invite:
            break

    # Cache the updated invites
    await cache_invites(member.guild)

    if used_invite:
        inviter = used_invite.inviter

        # Award coins to the inviter
        async with asqlite.connect('cashbot.db') as db:
            await ensure_user_in_db(inviter.id)
            await db.execute("UPDATE users SET coins = coins + 50 WHERE user_id = ?", (inviter.id,))
            await db.commit()
        await update_user_data(inviter.id)

        # Send a welcome message
        channel = discord.utils.get(member.guild.text_channels, name="general")
        if channel:
            embed = discord.Embed(
                title="Welcome!",
                description=f"Welcome to the server, {member.mention}! Invited by {inviter.mention}.",
                color=discord.Color.green()
            )
            await channel.send(embed=embed)

        # Notify the inviter
        embed = discord.Embed(
            title="Invite Success!",
            description=f"You invited {member.mention} and earned 50 coins!",
            color=discord.Color.green()
        )
        try:
            await inviter.send(embed=embed)
        except discord.Forbidden:
            pass  # In case the bot can't DM the user

# Function to parse time duration (e.g., 30s, 2m, 4h, 3d)
def parse_time(duration):
    time_units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    match = re.match(r"(\d+)([smhd])", duration)
    if not match:
        return None
    amount, unit = match.groups()
    return int(amount) * time_units[unit]

# Giveaway function to run the giveaway
async def run_giveaway(ctx, duration, prize, num_winners):
    await asyncio.sleep(duration)

    async with asqlite.connect('cashbot.db') as db:
        async with db.execute("SELECT user_id FROM giveaway_entries") as cursor:
            entries = await cursor.fetchall()

            if len(entries) < num_winners:
                embed = discord.Embed(
                    title="Not Enough Participants",
                    description="There are not enough participants for the number of winners.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            winners = random.sample([entry[0] for entry in entries], num_winners)
            winner_mentions = ", ".join([f"<@{winner}>" for winner in winners])
            embed = discord.Embed(
                title="🎉 Giveaway Ended",
                description=f"The giveaway for **{prize}** has ended!\nWinners: {winner_mentions}",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)

            # Clear giveaway entries after picking winners
            await db.execute("DELETE FROM giveaway_entries")
            await db.commit()

# Command to start a giveaway
@bot.command()
async def start_giveaway(ctx, duration: str, num_winners: int, *, prize: str):
    seconds = parse_time(duration)
    if seconds is None:
        embed = discord.Embed(
            title="Invalid Time Format",
            description="Please use the format like `30s`, `2m`, `4h`, `3d`.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    if num_winners < 1:
        embed = discord.Embed(
            title="Invalid Winner Count",
            description="There must be at least 1 winner.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    # Clear previous giveaway entries before starting a new one
    async with asqlite.connect('cashbot.db') as db:
        await db.execute("DELETE FROM giveaway_entries")
        await db.commit()

    embed = discord.Embed(
        title="🎉 Giveaway Started",
        description=f"A giveaway for **{prize}** has started!\n"
                    f"Duration: {duration}\n"
                    f"Winners: {num_winners}\n"
                    f"Type `!enter_giveaway` to join!",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

    await run_giveaway(ctx, seconds, prize, num_winners)

# Command to join the giveaway
@bot.command()
async def enter_giveaway(ctx):
    async with asqlite.connect('cashbot.db') as db:
        await ensure_user_in_db(ctx.author.id)

        # Check if the user has enough coins
        async with db.execute("SELECT coins FROM users WHERE user_id = ?", (ctx.author.id,)) as cursor:
            data = await cursor.fetchone()
            if data and data[0] < 100:
                embed = discord.Embed(
                    title="Not Enough Coins",
                    description=f"{ctx.author.mention}, you need 100 coins to enter the giveaway.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

        # Check if the user is already entered
        async with db.execute("SELECT * FROM giveaway_entries WHERE user_id = ?", (ctx.author.id,)) as cursor:
            if await cursor.fetchone():
                embed = discord.Embed(
                    title="Already Entered",
                    description=f"{ctx.author.mention}, you are already in the giveaway.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

        # Deduct coins and add the user to the giveaway
        await db.execute("UPDATE users SET coins = coins - 100 WHERE user_id = ?", (ctx.author.id,))
        await db.execute("INSERT INTO giveaway_entries (user_id) VALUES (?)", (ctx.author.id,))
        await db.commit()

        embed = discord.Embed(
            title="Giveaway Entry Confirmed",
            description=f"{ctx.author.mention} has entered the giveaway!",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

# Command to check balance (coins and XP)
@bot.command()
async def balance(ctx):
    async with asqlite.connect('cashbot.db') as db:
        async with db.execute("SELECT xp, coins FROM users WHERE user_id = ?", (ctx.author.id,)) as cursor:
            data = await cursor.fetchone()
            if data:
                xp, coins = data
                embed = discord.Embed(
                    title="Balance",
                    description=f"{ctx.author.mention}, you have {coins} coins and {xp} XP.",
                    color=discord.Color.gold()
                )
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(
                    title="Not in System",
                    description=f"{ctx.author.mention}, you are not in the system yet.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)

# Command to display the leaderboard for coins and XP
@bot.command()
async def leaderboard(ctx, category: str = 'coins'):
    if category not in ['coins', 'xp']:
        embed = discord.Embed(
            title="Invalid Category",
            description="Invalid category! Use `coins` or `xp`.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    async with asqlite.connect('cashbot.db') as db:
        query = f"SELECT user_id, {category} FROM users ORDER BY {category} DESC LIMIT 10"
        async with db.execute(query) as cursor:
            top_users = await cursor.fetchall()

        if not top_users:
            embed = discord.Embed(
                title="No Data Found",
                description=f"No data found for {category}.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        leaderboard_message = ""
        for rank, (user_id, value) in enumerate(top_users, start=1):
            user = await bot.fetch_user(user_id)
            leaderboard_message += f"{rank}. {user.name} - {value} {category}\n"

        embed = discord.Embed(
            title=f"Top 10 users by {category}",
            description=leaderboard_message,
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

# Track messages and update user data
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await ensure_user_in_db(message.author.id)
    await update_user_data(message.author.id)
    await bot.process_commands(message)

# Update user XP and coins
async def update_user_data(user_id):
    async with asqlite.connect('cashbot.db') as db:
        await db.execute("UPDATE users SET xp = xp + 10, coins = coins + 5 WHERE user_id = ?", (user_id,))
        await db.commit()

# Initialize the bot and cache invites on ready
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await init_db()
    for guild in bot.guilds:
        await cache_invites(guild)  # Cache invites for all guilds the bot is in

bot.run(settings.TOKEN)
