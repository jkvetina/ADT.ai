"""The per-command SUMMARY prose the help screen prints.

Split out of `help.py` by ADT #309, which needed room in that file for a
per-command section-override map and found it 26 bytes under the 20 000
guard in `tests/contracts/test_context_file_size.py`. This is the seam the
file already had: everything here is reference TEXT, while `help.py` is the
renderer that groups and formats it.

**A summary answers "what is this module FOR", never "how does it work"**
(Jan, 2026-08-13, ADT #320). Say what the command is used for, when you
reach for it, and what you get out of it, in the words someone who has not
read the source would use. Do NOT name flags, output section headers, config
keys, file formats, storage engines, or exit-code semantics: the option rows
three lines below this block explain the flags, one at a time and correctly,
and `USAGE/<command>.md` explains everything else. The reader of this block
has not yet decided to run the command.

Three contract tests guard it, all in `tests/cli/test_help.py`:
`test_command_help_summary_fits_eight_lines_on_a_default_terminal`,
`test_command_help_summary_names_none_of_its_own_flags`, and
`test_command_help_spells_no_m_dash_as_two_hyphens`. The flag test is the
mechanical half of the rule above; the rest of "how" is not machine-checkable,
so read this docstring before adding a sentence.

Punctuation is contracted too. Two hyphens are not an m-dash and not a
separator: write a comma, a "so", (round brackets), or two sentences.
"""

from __future__ import annotations

COMMAND_SUMMARIES = {
    "flow": (
        "Shows how the pages of an APEX application lead to one another.",
        "Use it to answer which pages a page can be reached from and where it can take "
        "a user next, the questions that otherwise mean clicking through the whole "
        "application and hoping you did not miss a link.",
        "It answers them from a picture of the application held locally, so asking is "
        "cheap, and it can draw that picture as a diagram to read or share.",
    ),
    "calendar": (
        "Shows when you worked, drawn from the history of your repository.",
        "Use it to see the shape of a month at a glance (the busy stretches, the quiet "
        "ones, the day you have forgotten about) and which tickets the work went to.",
        "It reports on work already recorded and changes nothing, so it is safe to run "
        "whenever the question comes up, including for someone else's month.",
    ),
    "connection": (
        "Manages the environments, schemas and passwords ADT.ai connects with.",
        "Use it instead of editing the connection file by hand: it knows the shape that "
        "file has to keep, so a change to one environment cannot quietly break another, "
        "and it shows you what it is about to do before it does it.",
        "Passwords are asked for when they are needed rather than typed on the command "
        "line, so they stay out of your shell history and off your screen.",
    ),
    "dependencies": (
        "Answers what a database object uses, and what would break if you changed it.",
        "Use it before a change, to find the callers nobody remembers, and after one, "
        "to see how far the effect actually reached, including into APEX applications, "
        "which is where the surprising callers usually are.",
        "The answers come from a picture of the database you refresh when it moves on, "
        "so the question is fast to ask and can be asked without a connection.",
    ),
    "discovery": (
        "Answers saved questions about a database and writes down what it found.",
        "Use it to take stock of a schema, or to gather facts while working out what is "
        "wrong, without opening a SQL client and without any risk of changing something: "
        "it only ever reads.",
        "The questions live with the project rather than in someone's scratch file, so "
        "the same one asked next month gives an answer you can compare with this one.",
    ),
    "doctor": (
        "Checks whether this machine is set up to run ADT.ai, and fixes it when not.",
        "Use it first when something does not work: it tells a local setup problem apart "
        "from a database or repository one, which is usually the whole question.",
        "It also installs and updates what ADT.ai depends on, and sets up a new project "
        "so you are not assembling one by hand.",
    ),
    "export_apex": (
        "Brings APEX applications out of the builder and into your repository.",
        "Use it so that what was built by clicking has a history, can be reviewed like "
        "any other change, and can be deployed again somewhere else, with the "
        "application living beside the database code it runs on.",
        "It can show you what is there before you commit to exporting it, and write the "
        "result in whichever form your reviewers and your deployment need.",
    ),
    "export_data": (
        "Brings the contents of tables out of the database and into your repository.",
        "Use it for the rows an application needs in order to work at all (lookup "
        "values, defaults, configuration held as data), which belong under review "
        "beside the code that reads them.",
        "Each table comes back both as something you can read in a diff and as "
        "something that can put the same rows into another environment.",
    ),
    "export_db": (
        "Brings database objects out of the database and into your repository.",
        "Use it to keep the repository the authoritative copy of the schema, so a change "
        "made anywhere shows up in a review and can be deployed again rather than "
        "reconstructed from memory.",
        "You can take a whole schema or only what has moved recently, and what comes out "
        "is written to be read by a person, not just replayed by a tool.",
    ),
    "rebuild": (
        "Refreshes ADT.ai's picture of your repository's history.",
        "Use it after new commits or a change of branch, so that everything built on "
        "that history (releases, activity reports, searches) is working from what is "
        "there now rather than from what was there yesterday.",
        "It is the one step those commands cannot do for you, because only you know when "
        "the history has moved.",
    ),
    "recompile": (
        "Gets a schema back to a working state after something has broken it.",
        "Use it when a deployment or a change has left objects that no longer compile, "
        "which is the normal aftermath of touching anything that others depend on.",
        "Most of them come back on their own once the thing beneath them does. What "
        "cannot be fixed is reported with the reason and, more usefully, with which "
        "failures are the real cause and which are only consequences of it, so you "
        "start at the one object that matters instead of the twenty it took down.",
    ),
    "search_repo": (
        "Finds where something happened in your repository's history.",
        "Use it to track down the change behind a file, a database object or a release "
        "(who made it, when, and what moved along with it) when you know what you "
        "are looking for but not where it is.",
        "It can also bring an older version of a file back so you can look at what it "
        "used to say, without disturbing the one you are working on.",
    ),
    "ut3": (
        "Runs the unit tests installed in a database schema and reports how they went.",
        "Use it to find out whether the code in a schema still does what it is meant "
        "to, before a deployment rather than after one, and on evidence rather than "
        "on the fact that it compiled.",
        "The report says which tests passed, what they cost in time, and how much of the "
        "code they actually exercised, grouped so you can see which part of the "
        "application is thin on tests rather than only which test failed.",
        "It can stand in the way of a deployment: a run that finds problems fails, so it "
        "works as a gate without anyone having to read it.",
    ),
    "validate": (
        "Checks that exported APEX source is still sound before it goes back into APEX.",
        "Use it after editing an exported application outside the builder, to find the "
        "mistakes while they are cheap: an import that fails halfway has already left "
        "the application in a state someone has to undo.",
        "It needs no database, no credentials and no environment, so it can run anywhere: "
        "on your machine, or automatically on every change before anyone sees it.",
    ),
}
