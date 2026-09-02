# What we are going to build

This is the simple version. No tools, no jargon. Just what contextd is for, what it can already do, and what we still need to make.

---

## The problem

You use more than one AI helper while you work. Cursor, Claude, a terminal agent, maybe something in the browser.

Each one is blind to the others. None of them automatically know:

- which file you just saved
- what you just committed
- that a build just failed
- what you were trying to do ten minutes ago

So **you** become the messenger. You copy errors. You re-explain the same thing. You paste terminal output into chat. That is tiring, and it is slow.

We are building a quiet helper that watches your work **on your own computer** and keeps one shared picture of “what I am doing right now.” Every AI tool should be able to read that picture instead of asking you.

Nothing leaves your laptop. No accounts. No cloud.

---

## What the finished thing should feel like

You install it once. You forget it is there.

While you code, it notices the useful stuff:

- you opened or saved a file
- you ran a command
- you started a build or a dev server
- you made a git commit
- you changed project dependencies

It ignores the boring stuff (listing files, hopping folders) and keeps the important stuff.

When you open Cursor or Claude and ask for help, the tool can already see that picture. You should not have to paste the error or explain which file you are in.

If you tell it your intent — “I am fixing the login bug” — that should sit next to what it observed, so the help stays on track.

---

## What we already have

We already have the **watching** half.

A small program can run in the background and notice:

- files changing
- developer tools starting and stopping (builds, node, python, docker)
- git commits
- important project files changing (like the file that lists your dependencies)

It gives each moment a simple importance score. A commit matters more than “list files.” A dependency change matters more than a random save.

It writes those moments into a local notebook on your machine. Once a day-ish it throws away old, unimportant notes so the notebook does not grow forever.

**What we do not have yet:** a way for Cursor or Claude to **ask** “what is going on?” The notebook is private. The watchers fill it. Nothing reads it back out to an AI yet.

That is the gap. Watching without answering is not the product.

---

## What we are going to build

In order. Each step should be something you can feel.

### 1. Remember meaning, not just a log

Today we store “this happened.” Next we store “this is similar to that.”

So later, when you are stuck on login again, the helper can pull up the last time you were stuck on login — not just the last ten random saves.

If the local AI on your machine is off, the rest should still work. Watching must never depend on a model being online.

### 2. Answer “what am I doing right now?”

This is the heart of the product.

The helper should be able to hand back a short briefing:

- what you just did
- anything from the past that is related

Until this exists, no AI tool can use contextd. This is the first thing that looks like the real product.

### 3. Let every AI tool read that briefing

Cursor, Claude, Continue, terminal agents — they should all get the **same** picture, without you copy-pasting.

You should not need a special setup for each tool. One shared answer, many tools.

### 4. See the editor and the terminal the way a human does

We already see files, processes, and commits.

We still need:

- what you are editing in VS Code / Cursor (which file, which error in the panel)
- a one-line install for your shell so typed commands are noticed automatically

Then the picture matches real work, not just side effects.

### 5. Get smarter about what to keep

Right now importance is a few simple rules. Good enough to start.

Next, the helper should:

- tell coding apart from research or general computer use
- shrink noisy notes into a short summary
- keep a living “working memory” of the current session (the last few minutes)
- keep a longer memory of how you solve things, not only what you typed
- archive old but important notes instead of only deleting junk

You should not notice this. The briefings should just get better and shorter.

### 6. Make it boring to install

One command. It starts when you log in. You can change a simple settings file if you want.

If this step is painful, people will not use the rest.

---

## How we will know we are done with the first real version

A normal afternoon:

1. You start the helper.
2. You edit a file, run a build, commit.
3. You open an AI chat and it already knows what you were doing.
4. You did not paste anything.

If that loop works, we have a product. Everything after that is “make the picture better.”

---

## What we are not building

- Another coding assistant. We sit **under** the assistants you already use.
- Anything that sends your work to someone else’s servers.
- A dashboard you have to babysit. If you have to manage it, we failed.

---

## One sentence

**Watch your work locally, keep one honest picture of it, and let every AI tool read that picture so you can stop being the copy-paste middleman.**
