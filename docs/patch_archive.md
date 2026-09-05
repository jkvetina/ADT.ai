# Archiving Delivered Patches (adtai patch -archive)

What `-archive` takes, what it prints, how a pattern selects folders, and where the zips are filed once a patch has shipped. Building and deploying a patch are on [patch.md](patch.md) and [patch_deploy.md](patch_deploy.md).

## What a run takes

`-archive` zips delivered patch folders into `patch_archive/`, removes them from `patch/`, and takes ticket numbers, LIKE patterns, or both.

A bare `-archive` names no patch, so it takes none. It prints the inventory and nothing else, which is how you find the folder to name:

```text
ALL PATCH FOLDERS:
------------------

  FOLDER                    STATUS
  -----------------------   ------
  260822-2-CORE_SPINE
  260821-1-APP_NOTIFY
  260818-1-CORE_LOGS
```

Name a patch and the run takes it, printing a receipt above that same listing:

```text
ARCHIVING PATCHES:
------------------

  FOLDER               STATUS
  ------------------   ------
  260818-1-CORE_LOGS
```

The receipt says which folders this run took, in the same `FOLDER | STATUS` columns as the listing.

`ALL PATCH FOLDERS:` under it is what is LEFT on disk, so the next pattern has something to aim at. It carries no `patch_show_patches` cap and no `-by`/`-my`/`-recent` filter, which is why it is not the narrowed `RECENT PATCH FOLDERS:` a bare run prints.

## Selecting folders

A pattern is compared against three spellings of each folder: its name, its patch code, and its name with the day written as a four-digit year. So `202608%` selects a month even though a folder writes its day as `yymmdd`, and `-archive %` asks for every folder at once.

Refs matching nothing archive nothing, at exit `0`, and the listing then holds the whole inventory.

## Where the zips are filed

Inside the archive the zips are filed by month, `patch_archive/2026-08/260818-1-CORE_LOGS.zip`. The month is the day the PATCH was built, read off its own folder name and never the day you archive it, so a June patch swept up in August still lands under June and archiving one folder twice puts it in the same place both times.

`patch_archive_subfolder` is the level, a `strftime` format: `'%Y'` files by year, `''` gives one flat folder. A name carrying no readable day goes to the archive root rather than a guessed month. Zips written before this key sit flat and are left there.
