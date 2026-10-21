# FKG Reader

[English](README.md) | [简体中文](README.zh-CN.md)

An unofficial local reader for compatible FLOWER KNIGHT GIRL story data.

**This repository contains the program only. It does not include game scripts, translations, images, music, voices, videos, or a game data directory. The program does not download this content.** Before use, make sure you have the necessary rights to obtain and use your local data. This project is not affiliated with, endorsed by, or authorized by the game's rights holders, developers, or operators.

## Features

- Browse stories by category, chapter, or character, with search and favorites.
- Read Japanese, Chinese, or both side by side, and swap the bilingual columns. Available languages depend on your local data.
- Review dialogue history, replay available voice lines, mark stories as read manually, and resume from automatically saved reading positions.
- Display backgrounds, character portraits, icons, card images, and local videos, and play BGM, voices, and sound effects when the corresponding resources are available.
- Adjust font size, dialogue box height and opacity, individual audio volumes, and portrait alignment. Temporarily hide the dialogue box to view the scene.
- Customize terminology and edit translations locally. Personal edits are saved separately from the default content.
- Switch the interface between English and Simplified Chinese without replacing the original Japanese titles.

The reader uses simplified scene presentation and does not fully reproduce the original game. Interactive minigames cannot run here; video subtitles depend on the video file itself. Text embedded in images requires compatible transcribed text to display a translation. The program does not perform OCR or generate translations.

## Getting started

### 1. Get the source

Download the repository source using **Code → Download ZIP**, or clone the repository. If using a ZIP, extract it completely into a folder you can write to. Do not run the program from inside the archive.

On **Windows 10/11**, `Start Reader.cmd` looks for a usable Python 3.10+ installation or a previously prepared local runtime. If none is found, it asks before downloading an official Python runtime from `www.python.org`, verifies its pinned SHA-256 checksum, and prepares it under `runtime/`. This requires an internet connection for the first setup only. It does not need administrator rights, change PATH, or install Python system-wide. Later starts reuse the available environment without downloading it again.

You may decline the download and install [Python 3.10 or later](https://www.python.org/downloads/) yourself. On other systems, install Python manually and make sure `python` or `python3` works in your terminal. This project is distributed as source code without a bundled runtime. It uses only the Python standard library; no additional Python packages are required. The reading interface opens in your browser.

### 2. Add compatible local data

Place compatible data that you are entitled to use in the `content` folder at the repository root. The layout should be:

```text
FKGReader/
├─ Start Reader.cmd
├─ Stop Reader.cmd
├─ Check Files.cmd
├─ app/
├─ runtime/                 # Created only if local Python setup is needed
├─ content/
│  ├─ manifest.json
│  ├─ catalog/
│  ├─ stories/
│  ├─ dates/
│  ├─ translations/
│  ├─ glossary/
│  └─ assets/
└─ userdata/                # Personal files created during use
```

Make sure `content/manifest.json` exists, not `content/content/manifest.json`. A single `.bin` file, an arbitrary folder, or a raw game directory is not a substitute for this compatible structure. The source download contains no readable stories. Without local data, the program will report that content is missing.

### 3. Start and stop the reader

On Windows, double-click `Start Reader.cmd`. Once the reader is ready, it opens your browser at:

```text
http://127.0.0.1:8765
```

The page connects to a local service only. No game account or connection to the game servers is required for reading. Closing the browser tab does not stop the service; double-click `Stop Reader.cmd` when finished.

`Stop Reader.cmd` and `Check Files.cmd` reuse an available Python environment and never initiate a download. If necessary, run `Start Reader.cmd` to prepare one first. Python setup downloads no game data or translations.

With a manually installed Python, you can also start and stop the reader from a terminal in the repository root. Replace `python` with `python3` or `py -3` if that is how Python is installed on your system:

```sh
python -B app/launcher.py
python -B app/launcher.py --stop
```

If another copy is using the port, stop that copy first. Do not run multiple copies at once or casually change the address or port to continue reading.

## Reading

Open **Library**, choose a category, chapter, or character, and then select a story. Search supports hiragana, katakana, and half-width kana.

Click the background, a character, or the dialogue box to advance. You can also press Space or the Right Arrow key. **History** shows dialogue you have viewed, with a replay button when a voice line is available. Reading positions are saved automatically; read marks are set manually.

Use **Reading settings** to adjust text language, font size, the dialogue box, audio volumes, and portrait alignment. **Hide text** temporarily leaves only the background and characters visible. The first click to restore the text does not advance the dialogue.

**Edit translation** is hidden in Japanese-only mode. In Chinese or bilingual mode, it saves your changes as separate personal edits without overwriting the default text. **Glossary** customizes translations of linked terms; it does not replace arbitrary similar text or translate content that has no translation.

If audio does not play, check the master sound toggle and the relevant volume. Your browser may require a click on the page before allowing playback. Missing local audio files cannot be played.

## Saves and backups

There are two separate storage locations:

- **Personal files in the program folder:** translation edits are stored in `userdata/personal-translations.json`, and runtime logs are also under `userdata`. Use the interface's import and export functions to back up personal translations.
- **Browser local storage:** reading positions, history, read marks, favorites, personal terminology, the player name, and display and volume settings. These are not stored in `content`.

Use the same browser, browser profile, and `http://127.0.0.1:8765` address. `localhost`, different ports, and private windows use separate storage. Clearing site data can erase browser records; copying the program folder does not copy them. There is currently no one-click migration for all browser records.

Resetting all reading progress clears saved positions and their history, but preserves manual read marks, favorites, and personal settings. Before replacing files, stop the reader, back up `userdata`, and preserve your browser data.

## Replacing program files or data

Program files and `content` are independent. Keep your own `content` and `userdata` when replacing program files, and do not overwrite `userdata` when organizing or replacing local data. Do not delete the entire `content` folder and replace it with only a small set of new files.

The reader has no online updater, automatic game content downloader, or general ZIP import feature. Default translations must match the source identity and content checksums. If they do not match, the reader falls back to Japanese; do not alter validation fields to force an old translation onto different source text.

On Windows, `Check Files.cmd` checks program files only by default. To include local data, run `& ".\Check Files.cmd" --include-content` in PowerShell. If using a manually installed Python, you can instead run this command in the repository root:

```sh
python -B app/verify-release.py --include-content
```

Replace `python` with `python3` or `py -3` if needed. Large data sets may take time to verify. Checksums confirm agreement with a manifest, not the data's origin, authenticity, completeness, or permission to use it.

## Troubleshooting

**Local content is missing:** check `content/manifest.json` and `content/catalog`, and make sure the folder is not nested one level too deep. Downloading the program alone does not provide stories.

**Chinese or bilingual text is unavailable:** the story needs a matching local translation. No translations are bundled with the program or generated automatically.

**Images or audio are missing:** resources may be absent, incompatible, or referenced incorrectly. Check the **Assets** panel for missing items. The reader does not fetch replacements online.

**Previous records are missing:** first check that the browser, profile, address, and port are unchanged. Do not immediately clear site data.

**Python setup fails or is cancelled:** no unverified download is executed. Check access to `https://www.python.org/` and that the folder is writable, then retry. Alternatively, install Python 3.10+ manually. Do not disable certificate checks, antivirus, or system security policies. A prepared private runtime can be used offline and is not included in the source download.

**The reader will not start:** confirm that the source is fully extracted, the folder is writable, a compatible Python environment is available, and another reader is not using the port. Additional errors may be recorded under `userdata/logs`. When reporting a problem, include steps and error messages, not complete story data, account information, or personal records.

## Copyright and permissions

This is an unofficial tool. It does not claim ownership of the original work or permission to redistribute it. The game name is used only to identify compatibility. Rights in game scripts, images, characters, artwork, music, voices, videos, logos, and related materials belong to their respective rights holders. Translations and other derivative content may involve both original-work rights and separate rights.

This repository contains none of that data, provides no download links for it, and includes no account login or features to bypass payment or access restrictions. Keeping data outside the repository does not automatically make its acquisition, translation, copying, or distribution lawful. Follow applicable laws, terms of service, and permissions. Being free, noncommercial, or credited does not by itself establish permission.

**No open-source license has been assigned to the program source; the relevant rights are reserved. Public visibility does not grant additional permission to modify, redistribute, or use the code commercially.** Third-party components remain subject to their own licenses, which are not replaced by this project's rights statement.

See [Rights and usage boundaries](RIGHTS.md) and [Third-party notices](THIRD_PARTY_NOTICES.md) for the full notices, currently in Chinese. Raise rights-related questions through the repository's available contact channels, without publicly re-uploading protected text, assets, or personal information. Whether a particular use or distribution is lawful depends on the facts and applicable law. These notices are not a guarantee of authorization or legal advice.
