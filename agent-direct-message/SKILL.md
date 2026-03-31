---
name: agent-direct-message
description: >-
  Cross-device real-time collaboration tool for strict turn-based 1:1 messaging
  with another AI agent via a relay server. Use when the user asks you to
  collaborate with an agent on another device, send a direct message to a remote
  agent, or coordinate work across machines using a shared token.
---

# Agent Direct Message

A cross-device collaboration tool for turn-based 1:1 messaging with another AI agent through a shared relay server.

## How It Works

Two agents on different devices share a **token** to establish a private channel. Messages are exchanged in strict alternating turns.

## Two-Step Workflow

Always start by **checking** the token, then decide whether to **send**.

### Step 1: Check the channel

```bash
python3 SCRIPTS_DIR/client.py --token <TOKEN> --check
```

Output (JSON to stdout):

```json
{"role": "A", "message": null}
```

- `message` is `null` → no one has sent anything yet. You are the **initiator**.
- `message` is a string → the other agent already sent something. You are the **responder**.

### Step 2: Send a message (and wait for reply)

```bash
python3 SCRIPTS_DIR/client.py --token <TOKEN> --message "<YOUR_MESSAGE>"
```

This deposits your message, then **blocks** until the other agent replies. The reply is printed as JSON to stdout:

```json
{"message": "their reply", "from": "A"}
```

### Close the session

```bash
python3 SCRIPTS_DIR/client.py --token <TOKEN> --exit
```

## Environment

- **`AGENT_DM_GATEWAY`**: Base URL of the relay server. Default: `http://localhost:8000`
- Replace `SCRIPTS_DIR` with the absolute path to the `scripts/` directory in this project.

## Typical Flows

### You are the initiator (check returns null)

1. `--check` → message is null
2. `--message "your request"` → blocks → returns other agent's reply
3. Process reply. If more rounds needed, `--message "follow-up"` again.
4. `--exit` when done.

### You are the responder (check returns a message)

1. `--check` → message is "their request"
2. Process their request.
3. `--message "your reply"` → blocks → returns their next message
4. Process and repeat. `--exit` when done.

## Guidelines

- **Always check first.** Never send without checking — you might miss a message already waiting for you.
- **One message at a time.** After sending, wait for the reply before sending again. The script enforces this by blocking.
- **Parse the JSON output.** Read `message` from both check and send results.
- **End cleanly.** When the task is complete, send a final message, then `--exit`.
- **Expect latency.** The other agent may take time to respond. Blocking waits of minutes are normal.
- **Don't repeat yourself.** Each message should advance the collaboration.
