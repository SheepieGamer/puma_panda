from dotenv import load_dotenv
import os
import discord
load_dotenv()


TOKEN = os.getenv('TOKEN')
CMD_PREF = "$"
INTENTS = discord.Intents.all()
