import discord
from discord.ext import commands
import yt_dlp
import asyncio
import logging
import os
import json
from dotenv import load_dotenv

# =========================
# Load Environment Variables
# =========================

load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
PRIORITY_USER_ID = int(os.getenv('PRIORITY_USER_ID'))
BLOCKED_USER_ID = int(os.getenv('BLOCKED_USER_ID'))
DJ_ROLE_NAME = os.getenv('DJ_ROLE_NAME', 'DJ')  # Defaults to 'DJ' if not set
DEFAULT_MP3_FILE_PATH = os.getenv('DEFAULT_MP3_FILE_PATH', 'audio/welcome.mp3')  # Default welcome sound
WELCOME_SOUNDS_FILE = 'welcome_sounds.json'  # JSON file to store user-specific welcome sounds

# =========================
# Configure Logging
# =========================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s:%(levelname)s:%(name)s: %(message)s'
)

# =========================
# Bot Initialization
# =========================

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True
intents.members = True  # Needed to access member roles and member events

bot = commands.Bot(command_prefix='!', intents=intents)

# Global dictionary to store queues for each guild
music_queues = {}

# =========================
# Load Welcome Sounds
# =========================

def load_welcome_sounds():
    """Loads the welcome sounds from the JSON file."""
    if not os.path.isfile(WELCOME_SOUNDS_FILE):
        with open(WELCOME_SOUNDS_FILE, 'w') as f:
            json.dump({}, f)
        return {}
    with open(WELCOME_SOUNDS_FILE, 'r') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            logging.error("welcome_sounds.json is not a valid JSON file.")
            return {}

def save_welcome_sounds(welcome_sounds):
    """Saves the welcome sounds to the JSON file."""
    with open(WELCOME_SOUNDS_FILE, 'w') as f:
        json.dump(welcome_sounds, f, indent=4)

welcome_sounds = load_welcome_sounds()

# =========================
# Helper Functions
# =========================

def has_dj_role(member: discord.Member) -> bool:
    """Checks if the member has the DJ role."""
    return any(role.name == DJ_ROLE_NAME for role in member.roles)

async def play_next(ctx, voice_client):
    """Plays the next song in the queue."""
    if music_queues.get(ctx.guild.id):
        if music_queues[ctx.guild.id]:
            source = music_queues[ctx.guild.id].pop(0)
            voice_client.play(
                source['audio'],
                after=lambda e: asyncio.run_coroutine_threadsafe(
                    play_next(ctx, voice_client), bot.loop
                )
            )
            await ctx.send(f"Now playing: **{source['title']}**")
        else:
            await voice_client.disconnect()
            await ctx.send("Music queue is empty. Disconnected from the voice channel.")
    else:
        await voice_client.disconnect()
        await ctx.send("Music queue is empty. Disconnected from the voice channel.")

# =========================
# Global Command Check
# =========================

@bot.check
def global_command_check(ctx):
    """
    Global check that:
    1. Blocks the specific user.
    2. Ensures only users with the DJ role can use commands.
    """
    # Blocked User Check
    if ctx.author.id == BLOCKED_USER_ID:
        return False  # Block the user

    # DJ Role Check
    if has_dj_role(ctx.author):
        return True  # User has DJ role

    return False  # User does not have DJ role

# =========================
# Event Handlers
# =========================

@bot.event
async def on_ready():
    logging.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    logging.info('------')

@bot.event
async def on_command_error(ctx, error):
    """Handles errors for blocked users and permission issues."""
    if isinstance(error, commands.CheckFailure):
        if ctx.author.id == BLOCKED_USER_ID:
            await ctx.send("nu uh. Not you")
        else:
            await ctx.send("You don't have permission to use this command.")
    else:
        # Log unexpected errors
        logging.error(f"Unhandled exception: {error}")
        await ctx.send("An unexpected error occurred. Please try again later.")

@bot.event
async def on_voice_state_update(member, before, after):
    """
    Event listener that triggers when a user's voice state changes.
    If any user joins a voice channel, the bot joins, plays the corresponding MP3,
    and then disconnects after the audio finishes.
    """
    # Avoid bot's own voice state changes
    if member.bot:
        return

    # Check if the member has just joined a voice channel
    if before.channel is None and after.channel is not None:
        guild = member.guild
        voice_channel = after.channel
        user_id = str(member.id)

        # Determine which MP3 to play
        mp3_file = welcome_sounds.get(user_id, DEFAULT_MP3_FILE_PATH)

        # Check if the MP3 file exists
        if not os.path.isfile(mp3_file):
            logging.error(f"MP3 file not found for user {member.name} ({user_id}): {mp3_file}")
            return

        # Check if the bot is already connected to a voice channel in this guild
        voice_client = discord.utils.get(bot.voice_clients, guild=guild)

        if voice_client and voice_client.is_connected():
            if voice_client.channel != voice_channel:
                try:
                    await voice_client.move_to(voice_channel)
                except Exception as e:
                    logging.error(f"Error moving to voice channel: {e}")
                    return
        else:
            try:
                voice_client = await voice_channel.connect()
            except Exception as e:
                logging.error(f"Error connecting to voice channel: {e}")
                return

        # Play the MP3 file
        try:
            source = discord.FFmpegPCMAudio(mp3_file)
            if not voice_client.is_playing():
                # Define a callback to disconnect after the audio finishes
                def after_playing(error):
                    coro = voice_client.disconnect()
                    fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
                    try:
                        fut.result()
                    except Exception as e:
                        logging.error(f"Error disconnecting after playing: {e}")

                voice_client.play(source, after=after_playing)
                # Send a message to the system channel if available
                if guild.system_channel:
                    await guild.system_channel.send(f"Playing welcome sound for {member.mention}!")
        except Exception as e:
            logging.error(f"Error playing MP3: {e}")

# =========================
# Bot Commands
# =========================

@bot.command()
async def play(ctx, *, search: str):
    """Plays a song from YouTube based on the search query."""
    # Check if the user is in a voice channel
    if not ctx.author.voice:
        await ctx.send("You need to be in a voice channel to play music.")
        return

    voice_channel = ctx.author.voice.channel

    # Connect to the voice channel if not already connected
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if not voice_client or not voice_client.is_connected():
        try:
            voice_client = await voice_channel.connect()
        except Exception as e:
            logging.error(f"Error connecting to voice channel: {e}")
            await ctx.send("Failed to connect to the voice channel.")
            return
    else:
        if voice_client.channel != voice_channel:
            try:
                await voice_client.move_to(voice_channel)
            except Exception as e:
                logging.error(f"Error moving to voice channel: {e}")
                await ctx.send("Failed to move to the voice channel.")
                return

    # Initialize the queue for the guild if not present
    if ctx.guild.id not in music_queues:
        music_queues[ctx.guild.id] = []

    # Define yt_dlp options
    ytdl_format_options = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'default_search': 'auto',
        'quiet': True,
    }

    ffmpeg_options = {
        'options': '-vn',
    }

    ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

    # Search and extract video info
    try:
        info = ytdl.extract_info(f"ytsearch:{search}", download=False)['entries'][0]
    except Exception as e:
        logging.error(f"Error fetching song: {e}")
        await ctx.send("Sorry, I couldn't find the song.")
        return

    # Prepare the audio source
    url = info['url']
    title = info.get('title', 'Unknown Title')
    try:
        source = discord.FFmpegPCMAudio(url, **ffmpeg_options)
    except Exception as e:
        logging.error(f"Error preparing audio source: {e}")
        await ctx.send("Failed to prepare the audio source.")
        return

    # Create a dict to store title and audio source
    song = {'title': title, 'audio': source}

    # Add the song to the queue with priority if applicable
    if ctx.author.id == PRIORITY_USER_ID:
        music_queues[ctx.guild.id].insert(0, song)
        await ctx.send(f"Priority song added to the front of the queue: **{title}**")
    else:
        music_queues[ctx.guild.id].append(song)
        await ctx.send(f"Added to queue: **{title}**")

    # If not playing, start playing
    if not voice_client.is_playing() and not voice_client.is_paused():
        await play_next(ctx, voice_client)

@bot.command()
async def pause(ctx):
    """Pauses the currently playing song."""
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await ctx.send("Paused the current song.")
    else:
        await ctx.send("No song is currently playing.")

@bot.command()
async def resume(ctx):
    """Resumes a paused song."""
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await ctx.send("Resumed the song.")
    else:
        await ctx.send("The song is not paused.")

@bot.command()
async def skip(ctx):
    """Skips the currently playing song. Any user with the DJ role can use this."""
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await ctx.send("Skipped the current song.")
    else:
        await ctx.send("No song is currently playing.")

@bot.command()
async def queue(ctx):
    """Displays the current music queue."""
    if ctx.guild.id in music_queues and music_queues[ctx.guild.id]:
        queue_list = '\n'.join([f"{idx+1}. {song['title']}" for idx, song in enumerate(music_queues[ctx.guild.id])])
        await ctx.send(f"Current Music Queue:\n{queue_list}")
    else:
        await ctx.send("The music queue is empty.")

@bot.command()
async def clear(ctx):
    """Clears the music queue. Any user with the DJ role can use this."""
    if ctx.guild.id in music_queues:
        music_queues[ctx.guild.id].clear()
        await ctx.send("Cleared the music queue.")
    else:
        await ctx.send("The music queue is already empty.")

@bot.command()
async def stop(ctx):
    """Stops playing music and disconnects the bot from the voice channel. Any user with the DJ role can use this."""
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice_client:
        music_queues[ctx.guild.id].clear()
        await voice_client.disconnect()
        await ctx.send("Stopped playing and disconnected from the voice channel.")
    else:
        await ctx.send("I'm not connected to any voice channel.")

# =========================
# Welcome Sound Management Commands
# =========================

@bot.command()
@commands.has_permissions(administrator=True)
async def setwelcome(ctx, member: discord.Member, *, mp3_file: str):
    """
    Sets a specific welcome sound for a user.
    Usage: !setwelcome @User path/to/file.mp3
    """
    # Check if the MP3 file exists
    if not os.path.isfile(mp3_file):
        await ctx.send(f"MP3 file not found at path: {mp3_file}")
        return

    # Update the welcome_sounds dictionary
    welcome_sounds[str(member.id)] = mp3_file
    save_welcome_sounds(welcome_sounds)
    await ctx.send(f"Set welcome sound for {member.mention} to `{mp3_file}`.")

@bot.command()
@commands.has_permissions(administrator=True)
async def removewelcome(ctx, member: discord.Member):
    """
    Removes a user's specific welcome sound.
    Usage: !removewelcome @User
    """
    user_id = str(member.id)
    if user_id in welcome_sounds:
        del welcome_sounds[user_id]
        save_welcome_sounds(welcome_sounds)
        await ctx.send(f"Removed welcome sound for {member.mention}.")
    else:
        await ctx.send(f"No specific welcome sound set for {member.mention}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def listwelcomes(ctx):
    """
    Lists all users with specific welcome sounds.
    Usage: !listwelcomes
    """
    if not welcome_sounds:
        await ctx.send("No specific welcome sounds have been set.")
        return

    embed = discord.Embed(title="Welcome Sounds", color=discord.Color.blue())
    for user_id, mp3 in welcome_sounds.items():
        member = ctx.guild.get_member(int(user_id))
        if member:
            embed.add_field(name=member.display_name, value=mp3, inline=False)
        else:
            embed.add_field(name=f"User ID: {user_id}", value=mp3, inline=False)

    await ctx.send(embed=embed)

# =========================
# Running the Bot
# =========================

if __name__ == "__main__":
    if not TOKEN:
        logging.error("Bot token not found. Please set DISCORD_BOT_TOKEN in the .env file.")
    else:
        bot.run(TOKEN)
