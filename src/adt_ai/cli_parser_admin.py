from __future__ import annotations


def add_admin_parsers(subparsers) -> None:
    doctor = subparsers.add_parser(
        "doctor",
        description="check local ADT.ai environment setup and run explicit updates",
        help="check local setup and run explicit updates",
    )
    doctor.add_argument(
        "-offline",
        action="store_true",
        help="skip online update checks and show local versions only",
    )
    doctor.add_argument(
        "-update",
        action="store_true",
        help="run full ADT.ai, requirements, and SQLcl upgrade",
    )
    doctor.add_argument(
        "-sqlcl",
        action="store_true",
        help="upgrade SQLcl only; runs immediately without -update",
    )
    doctor.add_argument(
        "-init",
        action="store_true",
        help="scaffold project config, ignore rules, and safe local folders",
    )
    doctor.add_argument("--root", "-root", default=".", help="project root folder for -init")
    doctor.add_argument(
        "--force",
        "-force",
        action="store_true",
        help="overwrite existing generated template files with -init",
    )
