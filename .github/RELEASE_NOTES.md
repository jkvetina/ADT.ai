- **New command: `search` replaces `search_repo` and answers the dependency graph and page links as well as commit history.** The `dependencies` and `flow` commands are gone and `rebuild` owns every refresh they ran. There is no `search_repo` alias.
- **`search TERM` finds where a piece of text lives, offline, across five layers**, and lists its hits in a table per layer. It reads the `DB` layer from the files `export_db` wrote, and refreshes the stores it answers from instead of naming the rebuild.
- **`search -restore` puts each matching version back over its original path**, and `-stage` is gone.
- **New command: `rebuild` owns the commit, dependency and page-link caches.** `rebuild -app` reads an application through any configured schema that reaches it, keeps no copy of a schema's source, and mirrors the source text `search TERM` reads.
- **`rebuild -app` survives an APEX id past 64 bits and a component scan the database refuses.** A refused scan no longer ends the run, and a schema that reached the application is not reported as a warning.
- **`diff -apex` compares the APEX applications and static files two schemas own.** It opens on a summary per application, blanks zero counts and drops the application section, and prints one changed property per line.
- **`diff -apex -verbose` lists one line per changed component, pages first**, with one section per page and one for the application, and names the component on every property line.
- **`diff -apex -page` compares only the pages you name, and `-apex -target-app ID` compares the `-app` application with another id on the target**, such as a working copy.
- **`diff -restore` writes the target's version of everything that differs into your checkout, in every mode**, and `-limit N` works in every mode.
- **Every help screen files its flags by one rule, and modes get their own `MODES:` section, right after `ACTIONS:`.** `recompile`'s six report modes, `export_apex -reveal`, `validate -scan` and `diff`'s `-rest`, `-data` and `-apex` render there.
- **Tuning flags filed as actions move to `MODIFIERS:`**, among them `ut`'s `-refresh`, `doctor -offline`, `export_db -delete` and `export_apex -owners`. `-page` reads beside `-app` on `export_apex` and `validate`.
- **`export_apex` no longer warns when it exported an application through another configured schema.** `export_apex -reveal` still names the unconfigured owner and every schema that reaches the application.
- **`live_upload` is a `patch` verb, `patch -upload`.**
- **A commit's number is its position on the branch's first-parent line**, and a merge counts as one commit.

## Verification

| Suite          | Passed | Failed | Unverified | Coverage | Cores | Time |
| -------------- | -----: | -----: | ---------: | -------: | ----: | ---: |
| Unit tests     |   9108 |        |            |     100% |    14 | 1:05 |
| User stories   |    174 |        |          2 |          |     3 | 0:02 |
| Security audit |     25 |        |            |          |     1 | 0:17 |

The 2 unverified user stories are Windows-only contracts, and every release is built on macOS.

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
