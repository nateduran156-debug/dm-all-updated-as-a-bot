import asyncio
import json
import logging
import os
import pathlib
import time

import discord
from discord import app_commands
from dotenv import load_dotenv

from sender import NoTokens, Sender

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dm")

STATE_FILE = pathlib.Path(os.getenv("STATE_FILE", "tokens.json"))
DM_GAP = float(os.getenv("DM_GAP", "6"))
GUILD_ID = os.getenv("GUILD_ID", "")


def load_tokens():
    tokens = []
    for chunk in os.getenv("TOKENS", "").replace(",", "\n").splitlines():
        chunk = chunk.strip()
        if chunk:
            tokens.append(chunk)
    if STATE_FILE.exists():
        try:
            for t in json.loads(STATE_FILE.read_text()):
                if t not in tokens:
                    tokens.append(t)
        except Exception:
            log.warning("tokens.json unreadable, ignoring it")
    return tokens


def save_tokens(tokens):
    STATE_FILE.write_text(json.dumps(tokens, indent=2))


tokens = load_tokens()
if not tokens:
    raise SystemExit("no tokens configured, set TOKENS")

sender = Sender(tokens, DM_GAP)

intents = discord.Intents.default()
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

busy = asyncio.Lock()


@bot.event
async def on_ready():
    log.info("connected as %s", bot.user)
    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
        else:
            await tree.sync()
    except discord.HTTPException:
        log.exception("command sync failed")


@tree.command(name="dm", description="DM every member of this server")
@app_commands.describe(message="Plain text, sent as a real ping")
async def dm_cmd(interaction: discord.Interaction, message: str):
    if interaction.guild is None:
        await interaction.response.send_message("Run this inside a server.", ephemeral=True)
        return
    if busy.locked():
        await interaction.response.send_message("A run is already going.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    async with busy:
        targets = []
        try:
            async for member in interaction.guild.fetch_members(limit=None):
                if member.bot or member.id == bot.user.id:
                    continue
                targets.append(member.id)
        except discord.HTTPException:
            await interaction.edit_original_response(
                content="Could not read the member list. Is the Server Members intent enabled?"
            )
            return

        if not targets:
            await interaction.edit_original_response(content="Nobody to DM here.")
            return
        if not sender.live():
            await interaction.edit_original_response(
                content="No working tokens left. Add one with `/w add`."
            )
            return

        last = [0.0]

        async def progress(stats):
            now = time.monotonic()
            if stats["done"] < stats["total"] and now - last[0] < 15:
                return
            last[0] = now
            try:
                await interaction.edit_original_response(
                    content=(
                        f"{stats['done']}/{stats['total']} handled - {stats['sent']} sent, "
                        f"{stats['failed']} failed, {stats['dead']} token(s) dead"
                    )
                )
            except discord.HTTPException:
                pass

        try:
            stats = await sender.send_all(targets, message, progress)
        except NoTokens:
            await interaction.edit_original_response(content="No working tokens left.")
            return

        await interaction.edit_original_response(
            content=(
                f"Done. {stats['sent']} sent, {stats['failed']} failed, "
                f"{stats['dead']} token(s) burned. {len(sender.live())} token(s) left in the pool."
            )
        )


w = app_commands.Group(
    name="w",
    description="Manage the DM token pool",
    default_permissions=discord.Permissions(administrator=True),
)


@w.command(name="add", description="Add a bot token to the rotation")
@app_commands.describe(token="Bot token, needs to be invited to this server too")
async def w_add(interaction: discord.Interaction, token: str):
    try:
        size = sender.add(token)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    save_tokens(sender.tokens)
    await interaction.response.send_message(f"Added. Pool size {size}.", ephemeral=True)


@w.command(name="remove", description="Remove a token by its position")
@app_commands.describe(index="Position from /w list, starting at 1")
async def w_remove(interaction: discord.Interaction, index: int):
    try:
        token = sender.remove(index)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    save_tokens(sender.tokens)
    await interaction.response.send_message(
        f"Removed `{token[:10]}...`. Pool size {len(sender.tokens)}.", ephemeral=True
    )


@w.command(name="list", description="Show the pool and which tokens are burned")
async def w_list(interaction: discord.Interaction):
    if not sender.tokens:
        await interaction.response.send_message("Pool is empty.", ephemeral=True)
        return
    lines = []
    for i, token in enumerate(sender.tokens, 1):
        state = "dead" if token in sender.dead else "ok"
        lines.append(f"{i}. `{token[:10]}...` {state}")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


tree.add_command(w)


async def main():
    try:
        await bot.start(tokens[0])
    finally:
        await sender.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except discord.LoginFailure:
        raise SystemExit("the first token was rejected at login")
