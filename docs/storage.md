# Local Stores (adtai)

ADT.ai keeps what it learns about a project between runs in SQLite files under `config/`, one file per subject, with three YAML files beside them. This page is the map: what each file holds, who writes and reads it, when deleting it is safe, and the conventions every store is measured against. Each store has its own page.

## The stores

Every file is generated, gitignored, and rebuilt from git or from the database. None of them holds anything a human edits.

| Store                                                                              | File                              | Written by                      | Read by                                                                       | Rebuild                                                                              |
| ---------------------------------------------------------------------------------- | --------------------------------- | ------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| [commits](storage_commits.md)                                                      | `config/commits/<branch>.db`      | [rebuild](rebuild.md)           | [search_repo](search_repo.md), [calendar](calendar.md)                        | Delete the file and run `rebuild`; everything in it comes from git.                  |
| [apex](storage_apex.md)                                                            | `config/internal/apex.db`         | [export_apex](export_apex.md)   | [export_apex](export_apex.md), [validate](validate.md)                        | Delete the file; the next export refills it, and the timers start from nothing.      |
| [dependencies](storage_dependencies.md), [APEX half](storage_dependencies_apex.md) | `config/internal/dependencies.db` | [dependencies](dependencies.md) | [dependencies](dependencies.md), [recompile](recompile.md)                    | Any refresh; a file older than the opener can lift is wiped by a refresh.            |
| [flow](storage_flow.md)                                                            | `config/internal/flow.db`         | [flow](flow.md)                 | [flow](flow.md)                                                               | `flow -refresh` per application; the store is a cache of the live dictionary.        |
| [ut](storage_ut.md)                                                                | `config/internal/ut.db`           | [ut](ut.md)                     | [ut](ut.md)                                                                   | Delete the file; the history restarts, and the last twenty runs per schema are kept. |

## The YAML siblings

Three facts are small enough to stay in YAML, a handful of lines a human can read at a glance. They live beside the stores and are gitignored with them.

| File                                  | Holds                                                                                       | Written by                |
| ------------------------------------- | ------------------------------------------------------------------------------------------- | ------------------------- |
| `config/internal/recent.yaml`         | The per-scope watermark that bare `-recent` measures from.                                  | [export_db](export_db.md) |
| `config/internal/ut_timers.yaml`      | How long the last run of each schema and `-name` selection took, for the countdown.         | [ut](ut.md)               |
| `config/internal/job_signatures.yaml` | The hash of every scheduler job the last windowed export wrote, per environment and schema. | [export_db](export_db.md) |

The generated folders keep their documented places beside these files: `config/commits/` holds the commit stores, and `config/discovery/`, `config/flow/` and `config/temp/` hold command output. None of them moves under `config/internal/`.

## How to read a store page

Each store page opens on a Mermaid diagram of its tables, then one column table per SQLite table, then its indexes. A relationship line in the diagram means rows refer to each other, whether or not the store declares the foreign key; the Key cell says which it is.

| Cell     | Reads as                                                                                |
| -------- | --------------------------------------------------------------------------------------- |
| Type     | The declared SQLite type, with its default where one is declared.                       |
| Nullable | No where the column is declared NOT NULL or belongs to the primary key.                 |
| Key      | PK for a primary key column; FK and the column it points at for a declared foreign key. |
| Unique   | Whether the index refuses a second row with the same values.                            |

A contract test builds every store in memory from the shipped DDL and compares it with these pages, so a schema change that skips its page fails the suite.

## Conventions

The stores were written at different times, and until version bumps across all five it showed. This is the standard each one is measured against, and a contract test holds every store to it.

| Rule    | Standard                                                                                                                                                                                                                                                                                           |
| ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Names   | ADT.ai's own tables and columns are `snake_case`. A dictionary mirror keeps the Oracle name verbatim, upper case, so a query reads the same on both sides.                                                                                                                                         |
| Version | Every file carries a `_meta(key, value)` table with a `schema_version` row. The opener lifts an older file in place through the store's own migrations, and refuses one it cannot lift rather than guessing.                                                                                       |
| Stamps  | A timestamp column ends in `_at` and holds `YYYY-MM-DD HH:MM:SS`; its page names the clock. A stamp read off a clock ADT does not own keeps that clock's own form: git's author date carries its offset, APEX's last update keeps the minute precision the export writes into every metadata file. |
| Ids     | An APEX application id is `app_id INTEGER` in every table that carries one.                                                                                                                                                                                                                        |
| Indexes | Named `ix_<table>_<purpose>`, or `ux_<table>_<purpose>` when unique.                                                                                                                                                                                                                               |
| Links   | A child table declares its foreign key with `ON DELETE CASCADE`, and the opener turns foreign keys on.                                                                                                                                                                                             |
| Opener  | One shared opener creates the folder, sets the row factory, enables foreign keys, lifts the version and closes on failure. No store carries a private copy or calls SQLite itself.                                                                                                                 |

## What an older file goes through

Every store lifts what it can in place. The commit store, the APEX cache and the run history keep every row; the dictionary mirror keeps every row of a version 3 file and wipes anything older on a refresh; the navigation store is a cache and drops its pre-version tables for the next refresh to refill. Each page says which.

## Standing differences

| Store        | Difference                                                                                         | Why it stands                                                                                                                       |
| ------------ | -------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| dependencies | APEX component and property ids are INTEGER, while the flow store keeps its component ids as TEXT. | The mirror's queries sort and join on them numerically, and none measured has exceeded 64 bits; the flow store met one that did.    |
| apex         | `updated_at` is minute precision where every other stamp carries seconds.                          | It is APEX's own stamp, and the value the export writes into every application's metadata file; a finer one would rewrite them all. |
| commits      | `authored_at` carries a `+HH:MM` offset no other stamp has.                                        | The clock is the author's, not this machine's, and dropping the offset would move commits across calendar days.                     |
