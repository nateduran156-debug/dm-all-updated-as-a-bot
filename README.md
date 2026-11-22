# mass dm bot

`/dm <message>` DMs every human in the server, one plain-text message with a real `<@id>` ping at the front. No embeds.

Runs a token pool. When a token starts returning 401 it's marked dead and the queue keeps draining on the remaining tokens, so one reported token doesn't stop a run.

## setup

1. For each token: create an application at https://discord.com/developers/applications, add a bot, copy the token, enable **Server Members Intent** under Bot, then invite it with the `bot` + `applications.commands` scopes. Every token has to be in the server or it can't DM anyone.
2. Deploy to Railway, set `TOKENS` as a service variable (comma separated). The first token is the one that logs in, so keep a healthy one first.
3. Members who want DMs must have **Direct messages from server members** on for that server. Everyone else fails and gets counted.
4. `DM_GAP` is the delay per token in seconds. Default 6. More tokens means the pool runs in parallel, so N tokens is roughly N times the throughput.

## commands

- `/dm <message>` - run it
- `/w add <token>` - append a token
- `/w remove <index>` - drop a token by position
- `/w list` - show pool and dead tokens

`/w` is admin only. Tokens added with `/w` are written to `tokens.json`, which is wiped on redeploy unless you attach a Railway volume, so for permanent tokens update the env var.

## notes

- Bots and the bot account itself are skipped.
- 50007 (DMs closed / no shared server) counts as a failure, not a token death.
- Global slash sync can take an hour. Set `GUILD_ID` for instant sync.
