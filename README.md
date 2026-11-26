# Forge

A personal workout tracker that runs on your Mac Mini and lives on your iPhone home screen.

- **Log workouts fast.** Start from a routine or an empty workout. Your last numbers are already filled in, and you tick off each set. A rest timer starts on its own and beeps when time's up.
- **Routines and folders**, with supersets, warm-up, drop and failure sets, RPE, notes, and a rep range for each exercise.
- **Records and charts.** PRs are flagged when you finish a workout: heaviest weight, best estimated 1-rep max, and best set volume. Each exercise has its own chart and history.
- **Apple Watch heart rate.** Shows average, max, a heart-rate graph and time in each zone for every workout, plus your resting heart rate trend.
- **Weekly overload check.** Every Sunday it looks at each lift and suggests adding weight, adding reps, holding, or backing off. It only suggests; it never changes your routines. It can also post the report to Discord.
- **Bring your Hevy history** from a CSV export.

Everything stays on the Mac Mini in one SQLite file (`data/forge.db`), backed up every night. There's nothing to install from pip: it only uses the Python that ships with macOS.

---

## Setup (about 15 minutes)

### 1. Get the code onto the Mac Mini

```bash
cd ~
git clone https://github.com/jespinozaorders-bot/forge.git
cd forge
```

Keep it in your home folder (`~/forge`). macOS blocks background jobs from reading Documents, Desktop or Downloads.

### 2. Install

```bash
bash install.sh
```

This creates the database and downloads exercise pictures. It also sets up three background jobs: the app itself, the Sunday report and the nightly backup. At the end it prints three things: the address to open on your iPhone, your **access key**, and the heart-rate URL. Run `./forgectl info` any time to see them again.

### 3. Reach it from the gym: Tailscale

At home, your iPhone can reach the Mac Mini over Wi-Fi. At the gym, your phone is on cellular and can't see your home network, so you need Tailscale (free for personal use).

1. Install Tailscale on the Mac Mini and on your iPhone, and sign in to both with the same account.
2. In the Tailscale admin page, turn on **MagicDNS**. The Mac Mini then gets a name like `mac-mini`.
3. On the iPhone, use `http://mac-mini:8790` (or the Mac's `100.x.x.x` Tailscale address) instead of the Wi-Fi address. That address works at home and at the gym.

### 4. Put it on your home screen

1. On your iPhone, open the address in **Safari**. Add `?key=YOUR_ACCESS_KEY` to the end the first time, or type the key in when it asks.
2. Tap Share, then **Add to Home Screen**.

It opens full-screen like an app. Your workout in progress is saved to the Mac Mini as you go, so you can minimize it, close the app or switch phones and pick up where you left off.

### 5. Bring your Hevy history (optional)

1. In Hevy, go to Profile, then Settings, then **Export & Import Data**, then **Export Workouts**. Save the CSV to Files.
2. In Forge, go to **Settings**, then **Choose Hevy CSV**.

You can also do it on the Mac: `./forgectl import-hevy ~/Downloads/workouts.csv`. Importing the same file twice won't make duplicates. Exercise names that Forge doesn't know become custom exercises, with all their history kept.

### 6. Weekly report on Discord (optional)

Open `config.json` and paste a Discord webhook URL into `"discord_webhook"`. You make one in Discord under Edit Channel, then Integrations, then Webhooks. The report goes out Sundays at 6:37 pm. To see it any time, run `./forgectl suggest`.

---

## Apple Watch heart rate

Your Apple Watch records heart rate into Apple Health during every workout. An iPhone Shortcut sends those readings to Forge, and Forge lines them up with whatever you logged during the same time window. Start an Apple Watch workout (Traditional Strength Training) when you start your Forge workout. It records heart rate more often during a workout, so the graph is more detailed.

### Shortcut 1: send heart rate after each Apple Watch workout

In the **Shortcuts** app, create a new shortcut named *Send heart rate to Forge* with these actions:

1. **Find Health Samples** where *Type* is *Heart Rate* and *Start Date* is in the last *4 hours*. Sort by *Start Date*, oldest first, with no limit.
2. **Repeat with Each** item in *Health Samples*:
   - **Format Date**: *Start Date* of *Repeat Item*, with Date Format set to *ISO 8601* and *Include ISO 8601 Time* turned on.
   - **Text**: `Formatted Date,Value`. Insert the *Formatted Date* variable, type a comma, then insert *Repeat Item* and choose its *Value*.
3. After *End Repeat*: **Combine Text** with *Repeat Results*, separated by *New Lines*.
4. **Get Contents of URL**:
   - URL: your heart-rate URL from `./forgectl info`, using the Tailscale name, for example `http://mac-mini:8790/api/health/heart-rate`
   - Method: *POST*
   - Headers: add `X-Forge-Key`, with your access key as the value
   - Request Body: *File*, with *Combined Text* as the file

Then make it run on its own. Go to **Automation**, then **New Automation**, then **Apple Watch Workout**. Choose *Ends*, pick your strength workout types, set it to **Run Immediately**, and choose this shortcut. Your heart-rate graph shows up on the finished workout a few seconds after you end the workout on your watch.

Sending the same readings twice is harmless. To fill in older workouts, run the shortcut by hand once with a longer window, like the last 30 days.

### Shortcut 2: resting heart rate (once a day)

Same idea, run daily from a **Time of Day** automation, for example 9:13 am:

1. **Find Health Samples** where *Type* is *Resting Heart Rate* and *Start Date* is in the last *2 days*.
2. Repeat with each: **Text** `resting Formatted Date,Value`. Use the same Format Date step as above, with the word `resting` and a space before the date.
3. **Combine Text** with new lines, then **Get Contents of URL** with the same settings as Shortcut 1.

### Prefer an app instead of a Shortcut?

The *Health Auto Export* app can post Apple Health data to a URL on a schedule. Point its REST API automation at the same heart-rate URL, add the `X-Forge-Key` header, and choose the Heart Rate and Resting Heart Rate metrics. Forge reads its JSON format directly.

Heart-rate zones use 220 minus your age. Set your birth year, or your real max heart rate if you know it, under **Settings** in the app.

---

## How the overload check decides

For each lift you did in the last 7 days, it looks at your heaviest working sets from the most recent session. Warm-ups are ignored.

| What happened | Suggestion |
|---|---|
| Every set reached the top of your rep range, and nothing was RPE 10 | Add weight: +10 lb for barbell leg and hip lifts, +5 lb for everything else (kg: +5 / +2.5 / +2). Start back at the bottom of the range. |
| Inside the range | Same weight, one more rep |
| Below the range once | Hold the weight and aim for the bottom of the range |
| Below the range two sessions in a row | Drop about 10% and build back up |

The rep range comes from the routine (set it on each exercise in the routine editor). If the routine doesn't have one, it uses your default under Settings, which starts at 8–12.

---

## Everyday commands

Run these from the `~/forge` folder.

| Command | What it does |
|---|---|
| `./forgectl info` | Show the iPhone address, access key and heart-rate URL |
| `./forgectl suggest` | Print this week's overload suggestions |
| `./forgectl import-hevy file.csv` | Import a Hevy export |
| `./forgectl library` | Re-download exercise pictures |
| `./forgectl backup` | Back up now (it also runs nightly and keeps 30 copies in `data/backups`) |
| `./update.sh` | Get the latest version from GitHub and restart |
| `bash install.sh --uninstall` | Stop the background jobs. Your data is kept. |

Logs are in `data/logs/`. The app runs on port 8790 (change `port` in `config.json` and run `bash install.sh --refresh`).

## Exercise pictures

Pictures and how-to steps come from [free-exercise-db](https://github.com/yuhonas/free-exercise-db), an open, public-domain exercise library. It adds about 800 more exercises to the roughly 130 built in. Built-in exercises use names like *Bench Press (Barbell)*, so a Hevy import lines up with them.

## Coming next

- **Nutrition (phase 2):** food logging from USDA FoodData Central and Open Food Facts (with barcode scanning), macro targets, a weight trend, and a calorie target that adjusts each week based on your real intake and weight change.
- Body measurements and progress photos
- A native iPhone app for a live heart rate and a lock-screen rest timer

## Development

```bash
python3 -m unittest discover tests       # tests
FORGE_DEBUG=1 python3 -m forge serve     # run in the foreground with request logs
```

The code is Python standard library only: `forge/server.py` (API), `forge/stats.py` (PRs and overload logic), `forge/health.py` (heart rate) and `static/index.html` (the whole app).
