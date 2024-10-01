import discord
from discord.ext import commands
import yt_dlp
import asyncio
import logging
import os
from dotenv import load_dotenv

# =========================
# Load Environment Variables
# =========================

load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
PRIORITY_USER_ID = int(os.getenv('PRIORITY_USER_ID'))
BLOCKED_USER_ID = int(os.getenv('BLOCKED_USER_ID'))
DJ_ROLE_NAME = os.getenv('DJ_ROLE_NAME', 'DJ')  # Defaults to 'DJ' if not set
MP3_FILE_PATH = os.getenv('MP3_FILE_PATH', 'audio/welcome.mp3')  # Default path

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
    If the priority user joins a voice channel, the bot joins, plays the MP3,
    and then disconnects after the audio finishes.
    """
    # Check if the member is the priority user and has just joined a voice channel
    if member.id == PRIORITY_USER_ID:
        if before.channel is None and after.channel is not None:
            # Priority user has joined a voice channel
            voice_channel = after.channel
            # Check if the bot is already connected to a voice channel in this guild
            voice_client = discord.utils.get(bot.voice_clients, guild=member.guild)
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
            if os.path.isfile(MP3_FILE_PATH):
                try:
                    source = discord.FFmpegPCMAudio(MP3_FILE_PATH)
                    if not voice_client.is_playing():
                        # Define a callback to disconnect after the audio finishes
                        def after_playing(error):
                            coro = voice_client.disconnect()
                            fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
                            try:
                                fut.result()
                            except:
                                pass

                        voice_client.play(source, after=after_playing)
                        # Send a message to the system channel if available
                        if member.guild.system_channel:
                            await member.guild.system_channel.send(f"Playing welcome sound for {member.mention}!")
                except Exception as e:
                    logging.error(f"Error playing MP3: {e}")
            else:
                logging.error(f"MP3 file not found at path: {MP3_FILE_PATH}")

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
    """Skips the currently playing song. Only the priority user can use this."""
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice_client and voice_client.is_playing():
        # Only allow skip if the user is the priority user
        if ctx.author.id == PRIORITY_USER_ID:
            voice_client.stop()
            await ctx.send("Skipped the current song.")
        else:
            await ctx.send("You don't have permission to skip songs.")
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
    """Clears the music queue. Only the priority user can use this."""
    # Only allow clear if the user is the priority user
    if ctx.author.id == PRIORITY_USER_ID:
        if ctx.guild.id in music_queues:
            music_queues[ctx.guild.id].clear()
            await ctx.send("Cleared the music queue.")
        else:
            await ctx.send("The music queue is already empty.")
    else:
        await ctx.send("You don't have permission to clear the queue.")

@bot.command()
async def stop(ctx):
    """Stops playing music and disconnects the bot from the voice channel. Only the priority user can use this."""
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice_client:
        # Only allow stop if the user is the priority user
        if ctx.author.id == PRIORITY_USER_ID:
            music_queues[ctx.guild.id].clear()
            await voice_client.disconnect()
            await ctx.send("Stopped playing and disconnected from the voice channel.")
        else:
            await ctx.send("You don't have permission to stop the music.")
    else:
        await ctx.send("I'm not connected to any voice channel.")

# =========================
# Running the Bot
# =========================

if __name__ == "__main__":
    if not TOKEN:
        logging.error("Bot token not found. Please set DISCORD_BOT_TOKEN in the .env file.")
    else:
        bot.run(TOKEN)
