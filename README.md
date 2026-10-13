# AnkiDose

An [Anki](https://apps.ankiweb.net/) add-on that spreads your daily reviews into blocks over a few hours, so you don't have to do them all at once.

Tell it *"I want to do 700 reviews over the next 5 hours, one block every hour"* and AnkiDose tells you how many cards each block needs, lets you know when you finish a block, and when the next one opens.

## Features

- **Blocks with a goal:** splits the total into blocks (for example, 5 blocks of ~140 cards, one per hour).
- **Your choice when you fall behind:** cards you didn't get to are either *spread* across the remaining blocks or *carried over* to the next one.
- **Notifications:** when you finish a block, a window offers *Keep reviewing* / *Take a break*; when a new block opens, a system notification tells you how many cards it has and how long it will take.
- **Time estimates:** based on your own review history, including the time spent relearning cards you fail.
- **Panel on the deck list:** a progress ring for the current block, a bar split by blocks and, under *Details*, time left, accuracy, streak and your progress line against the plan.
- **Progress line while reviewing:** a thin line along the top edge of the review screen.
- **Adapts to the deck:** if the deck runs out of reviews before reaching the goal, the plan counts as complete.
- **Works with AnkiDroid / AnkiMobile:** progress comes from the review log, so whatever you review on your phone counts as soon as you sync on desktop.
- **Matches Anki's theme:** uses Anki's own colors in light and dark mode.

## Installation

1. Download the `.ankiaddon` file from [Releases](../../releases) and double-click it with Anki open, **or**
2. Copy the files in this repository into a new folder inside your Anki add-ons folder (*Tools → Add-ons → View Files*).
3. Restart Anki.

## Usage

1. **Tools → AnkiDose → New plan…**
2. Pick the deck. The number of cards is filled in with today's due reviews.
3. Set how many hours you want to finish in, how often a block opens, and what to do if you fall behind.
4. Click **Start** and review as usual.

You can also check the status (**Show status**) or stop the plan (**Stop plan**) from the same menu.

## How cards are counted

- It counts **reviews**: each card from the *Due* column counts once, even if you fail it. Relearning steps don't count (Anki doesn't count them under *Due* either).
- New cards only count if you enable *Also count new cards*.
- Each block's goal is fixed when the block starts.

## Configuration

Under **Tools → Add-ons → AnkiDose → Config**:

| Option | What it does |
|---|---|
| `hours` | Default plan length in hours |
| `block_minutes` | How often a block opens |
| `behind_mode` | `"spread"` or `"carry"` |
| `count_new` | Also count new cards |
| `block_end_notice` | `"dialog"` (window) or `"tooltip"` (small message) |
| `system_notifications` | System notification when blocks open/end |
| `sound` | Beep on notifications |

The plan and its history are stored in `user_files/` inside the add-on folder (not in your collection), so syncing never deletes them.

## Requirements

Built and used with Anki 26.9 on Windows. It should work on other recent Anki versions and on macOS/Linux, but it hasn't been tested there.

## License

[AGPL-3.0](LICENSE), the same license as Anki.
