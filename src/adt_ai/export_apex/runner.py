from __future__ import annotations

import datetime
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from multiprocessing.pool import ThreadPool
from pathlib import Path
from typing import Any, Protocol

import yaml

from adt_ai.db import QueryGateway
from adt_ai.export_apex import queries
from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.progress import DottedProgressBar
from adt_ai.row_values import row_value

GatewayFactory = Callable[[str], QueryGateway]


class ApexProgressReporter(Protocol):
    def run(
        self,
        header: str,
        target_seconds: float,
        operation: Callable[[], None],
    ) -> float:
        ...


ACTION_HEADERS = {
    "full": "  FULL APP EXPORT",
    "split": "  SPLIT COMPONENTS",
    "readable": "  READABLE COMPONENTS",
    "embedded": "  EMBEDDED CODE REPORT",
    "rest": "  REST SERVICES",
    "files": "  APPLICATION FILES",
    "files_ws": "  WORKSPACE FILES",
}


@dataclass(frozen=True)
class ApexExportRequest:
    root        : Path
    schemas     : list[str]
    applications: dict[str, list[ApexApplication]]
    actions     : Mapping[str, bool]
    config      : Mapping[str, object]
    release     : str | None = None
    recent_days : int | None = None
    changed_by  : str | None = None
    reporter    : ApexProgressReporter | None = None
    timers_file : Path | None = None


class ApexExportRunner:
    EXPORT_START_QUERY    = queries.EXPORT_START_QUERY
    EXPORT_FULL_QUERY     = queries.EXPORT_FULL_QUERY
    EXPORT_SPLIT_QUERY    = queries.EXPORT_SPLIT_QUERY
    EXPORT_READABLE_QUERY = queries.EXPORT_READABLE_QUERY
    EXPORT_EMBEDDED_QUERY = queries.EXPORT_EMBEDDED_QUERY
    FETCH_FILES_QUERY     = queries.FETCH_FILES_QUERY
    RECENT_COMPONENTS_QUERY = queries.RECENT_COMPONENTS_QUERY
    APEX_FILES_QUERY      = queries.APEX_FILES_QUERY
    APEX_ID_NAMES_QUERY   = queries.APEX_ID_NAMES_QUERY
    WORKSPACE_DEVELOPERS_QUERY = queries.WORKSPACE_DEVELOPERS_QUERY
    PAGE_COMMENTS_QUERY = queries.PAGE_COMMENTS_QUERY
    PAGE_REGION_COMMENTS_QUERY = queries.PAGE_REGION_COMMENTS_QUERY

    def __init__(self, gateway_factory: GatewayFactory) -> None:
        self.gateway_factory = gateway_factory

    def run(self, request: ApexExportRequest) -> None:
        base_resolver = ApexFileResolver.from_config(request.root, dict(request.config))
        reporter = request.reporter or ConsoleApexProgressReporter()
        timers_file = request.timers_file or request.root / "config" / "apex_timers.yaml"
        timers = _load_timers(timers_file)
        _store_application_metadata(
            request.root / "config" / "apex_apps.yaml",
            [
                application
                for schema in request.schemas
                for application in request.applications.get(schema, [])
            ],
        )
        for schema in request.schemas:
            resolver = base_resolver.for_schema(schema)
            gateway = self.gateway_factory(schema)
            developer_rows = gateway.fetch_all(self.WORKSPACE_DEVELOPERS_QUERY)
            developers = _workspace_developers_from_rows(developer_rows)
            _store_workspace_developers(
                request.root / "config" / "apex_developers.yaml",
                developer_rows,
            )
            for application in request.applications.get(schema, []):
                gateway.execute(self.EXPORT_START_QUERY, {"app_id": application.app_id})
                enrichments = _enrichments(gateway, application)
                (resolver.app_root(application) / "comments").mkdir(parents=True, exist_ok=True)
                self._write_page_comments(gateway, resolver, application)
                if request.recent_days and request.recent_days > 0:
                    self._print_recent_changes(
                        gateway,
                        application,
                        developers,
                        request.recent_days,
                        request.changed_by,
                    )
                if any(request.actions.values()):
                    _print_application_export_header(application)
                self._run_text_actions(
                    gateway,
                    resolver,
                    application,
                    request,
                    enrichments,
                    developers,
                    reporter,
                    timers,
                    timers_file,
                )
                if request.actions.get("rest"):
                    self._run_action(
                        reporter,
                        timers,
                        timers_file,
                        application,
                        "rest",
                        lambda gateway=gateway, resolver=resolver: self._write_rest_export(
                            gateway, resolver, request.config
                        ),
                    )
                if request.actions.get("files"):
                    self._run_action(
                        reporter,
                        timers,
                        timers_file,
                        application,
                        "files",
                        lambda gateway=gateway, application=application, resolver=resolver: (
                            self._write_static_files(
                                gateway,
                                resolver,
                                application,
                                application.app_id,
                            )
                        ),
                    )
                if request.actions.get("files_ws"):
                    self._run_action(
                        reporter,
                        timers,
                        timers_file,
                        application,
                        "files_ws",
                        lambda gateway=gateway, application=application, resolver=resolver: (
                            self._write_static_files(gateway, resolver, application, 0)
                        ),
                    )

    def _run_text_actions(
        self,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        application: ApexApplication,
        request: ApexExportRequest,
        enrichments: Mapping[int, str],
        developers: Mapping[str, Mapping[str, str]],
        reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        timers_file: Path,
    ) -> None:
        for action, sql in (
            ("full", self.EXPORT_FULL_QUERY),
            ("split", self.EXPORT_SPLIT_QUERY),
            ("readable", self.EXPORT_READABLE_QUERY),
            ("embedded", self.EXPORT_EMBEDDED_QUERY),
        ):
            if not request.actions.get(action):
                continue

            def operation(sql: str = sql, action: str = action) -> None:
                gateway.execute(
                    sql,
                    _bind_params(
                        sql, {"app_id": application.app_id, **_export_options(request.config)}
                    ),
                )
                self._write_collection_files(
                    gateway,
                    resolver,
                    application,
                    action,
                    enrichments,
                    request.config,
                    developers,
                    request.release,
                )

            self._run_action(
                reporter,
                timers,
                timers_file,
                application,
                action,
                operation,
            )

    def _run_action(
        self,
        reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        timers_file: Path,
        application: ApexApplication,
        action: str,
        operation: Callable[[], None],
    ) -> None:
        elapsed = reporter.run(
            ACTION_HEADERS[action],
            _timer_value(timers, application.app_id, action) or 999.0,
            operation,
        )
        _update_timer(timers, application.app_id, action, elapsed)
        _store_timers(timers_file, timers)

    def _write_collection_files(
        self,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        application: ApexApplication,
        action: str,
        enrichments: Mapping[int, str],
        config: Mapping[str, object],
        developers: Mapping[str, Mapping[str, str]],
        release: str | None,
    ) -> None:
        for row in gateway.fetch_all(self.FETCH_FILES_QUERY):
            file_name = str(row_value(row, "FILE_NAME") or "")
            payload = str(row_value(row, "CLOB_CONTENT") or "")
            relative = _strip_app_prefix(file_name, application)
            if _skip_collection_file(action, relative):
                continue
            target = _target_path(resolver, application, action, file_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            content = _payload_for(
                action,
                payload,
                relative,
                application,
                enrichments,
                config,
                developers,
                release,
            )
            if action == "readable" and target == resolver.workspace_root() / "app_groups.yaml":
                content = _merge_app_groups(target, content)
            target.write_text(content, encoding="utf-8")

    def _write_static_files(
        self,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        application: ApexApplication,
        app_id: int,
    ) -> None:
        for row in gateway.fetch_all(self.APEX_FILES_QUERY, {"app_id": app_id}):
            file_name = str(row_value(row, "FILENAME") or "")
            payload = _blob_bytes(row_value(row, "BLOB_CONTENT"))
            target = (
                resolver.workspace_file(file_name)
                if app_id == 0
                else resolver.application_file(application, file_name)
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)

    def _write_page_comments(
        self,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        application: ApexApplication,
    ) -> None:
        comments: dict[int, dict[str, Any]] = {}
        for row in gateway.fetch_all(self.PAGE_COMMENTS_QUERY, {"app_id": application.app_id}):
            page_id = int(row_value(row, "PAGE_ID") or 0)
            comments[page_id] = {
                "page": {
                    "page_name": row_value(row, "PAGE_NAME"),
                    "page_comment": row_value(row, "PAGE_COMMENT"),
                    "updated_by": row_value(row, "LAST_UPDATED_BY"),
                    "updated_at": row_value(row, "LAST_UPDATED_ON"),
                },
                "regions": {},
            }
        for row in gateway.fetch_all(
            self.PAGE_REGION_COMMENTS_QUERY, {"app_id": application.app_id}
        ):
            page_id = int(row_value(row, "PAGE_ID") or 0)
            region_id = int(row_value(row, "REGION_ID") or 0)
            if page_id not in comments:
                comments[page_id] = {
                    "page": {
                        "page_name": row_value(row, "PAGE_NAME"),
                    },
                    "regions": {},
                }
            comments[page_id]["regions"][region_id] = {
                "region_name": row_value(row, "REGION_NAME"),
                "region_comment": row_value(row, "COMPONENT_COMMENT"),
                "updated_by": row_value(row, "LAST_UPDATED_BY"),
                "updated_at": row_value(row, "LAST_UPDATED_ON"),
            }
        comments_root = resolver.app_root(application) / "comments"
        comments_root.mkdir(parents=True, exist_ok=True)
        for page_id, payload in comments.items():
            _store_yaml_mapping(comments_root / f"p{page_id:05d}.yaml", payload)

    def _print_recent_changes(
        self,
        gateway: QueryGateway,
        application: ApexApplication,
        developers: Mapping[str, Mapping[str, str]],
        recent_days: int,
        changed_by: str | None,
    ) -> None:
        author_label = changed_by if changed_by in developers.get(application.workspace, {}) else ""
        _print_recent_changes_header(application, _recent_since(recent_days), author_label)
        rows = gateway.fetch_all(
            self.RECENT_COMPONENTS_QUERY,
            {
                "app_id": application.app_id,
                "recent": recent_days,
                "author": changed_by,
            },
        )
        _print_recent_components(rows)

    def _write_rest_export(
        self,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        config: Mapping[str, object],
    ) -> None:
        root = resolver.apex_root()
        root.mkdir(parents=True, exist_ok=True)
        resolver.rest_export("__enable_schema").parent.mkdir(parents=True, exist_ok=True)
        lines = _cleanup_sqlcl(gateway.sqlcl_request("SET LINESIZE 200;\nrest export;", root))
        first, modules = _split_rest_modules(lines)
        prefixes = _rest_prefixes(config)
        for module in modules:
            name = _rest_module_name(module)
            if not _matches_prefix(name, prefixes):
                continue
            target = resolver.rest_export(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_plsql_block(module), encoding="utf-8")
        if modules:
            target = resolver.rest_export("__enable_schema")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_plsql_block(_schema_definition(first)), encoding="utf-8")


class ConsoleApexProgressReporter:
    line_width = 78

    def __init__(self, interval: float = 1.0) -> None:
        self.interval = interval
        self._progress = DottedProgressBar(line_width=self.line_width)

    def run(
        self,
        header: str,
        target_seconds: float,
        operation: Callable[[], None],
    ) -> float:
        started_at = time.monotonic()
        progress = 0.0
        with ThreadPool(processes=1) as pool:
            result = pool.apply_async(operation)
            while not result.ready():
                progress = self._print_progress(header, progress, target_seconds, started_at)
            result.get()
        elapsed = time.monotonic() - started_at
        self._print_done(header, elapsed)
        return elapsed

    def _print_progress(
        self,
        header: str,
        progress: float,
        target_seconds: float,
        started_at: float,
    ) -> float:
        target = target_seconds if target_seconds > 0 else 999.0
        elapsed = time.monotonic() - started_at
        visible_progress = min(max(progress, elapsed), target)
        percent = min(int((visible_progress / target * 100) + 0.5), 99)
        remaining = max(0, int((target - visible_progress) + 0.5))
        self._print_line(header, percent, remaining)
        time.sleep(self.interval)
        return min(max(progress + self.interval, elapsed), target)

    def _print_done(self, header: str, elapsed: float) -> None:
        self._print_line(header, 100, int(elapsed), close=True)

    def _print_line(
        self,
        header: str,
        percent: int,
        seconds: int,
        close: bool = False,
    ) -> None:
        self._progress.print_line(header, percent, seconds, close=close)

    def _line_text(self, header: str, percent: int, seconds: int) -> str:
        return self._progress.line_text(header, percent, seconds)


def _load_timers(path: Path) -> dict[Any, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _store_timers(path: Path, timers: Mapping[Any, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            dict(timers),
            default_flow_style=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _timer_value(timers: Mapping[Any, Any], app_id: int, action: str) -> float:
    app_timers = timers.get(app_id) or timers.get(str(app_id)) or {}
    if not isinstance(app_timers, Mapping):
        return 0.0
    value = app_timers.get(action) or 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _update_timer(timers: dict[Any, Any], app_id: int, action: str, elapsed: float) -> None:
    key: Any = app_id if app_id in timers or str(app_id) not in timers else str(app_id)
    app_timers = timers.get(key)
    if not isinstance(app_timers, dict):
        app_timers = {}
        timers[key] = app_timers
    previous = _timer_value(timers, app_id, action)
    timer = (elapsed + previous) / 2 if previous > 0 else elapsed
    app_timers[action] = round(timer, 2)


def _print_application_export_header(application: ApexApplication) -> None:
    message = f"APP {application.app_id}/{application.app_alias}, EXPORTING:"
    print()
    print(message)
    print("-" * len(message))


def _print_recent_changes_header(
    application: ApexApplication,
    changed_since: str,
    author: str,
) -> None:
    suffix = f" BY {author}" if author else ""
    message = (
        f"APP {application.app_id}/{application.app_alias}, "
        f"CHANGES SINCE {changed_since}{suffix}:"
    )
    print()
    print(message)
    print("-" * len(message))


def _recent_since(recent_days: int) -> str:
    return str(datetime.date.today() - datetime.timedelta(days=recent_days - 1))


def _print_recent_components(rows: list[dict[str, Any]]) -> None:
    grouped: dict[str, dict[object, dict[str, object]]] = {}
    for row in rows:
        group = str(row_value(row, "TYPE_NAME") or "")
        component_id = row_value(row, "ID")
        grouped.setdefault(group, {})[component_id] = {
            "name": row_value(row, "NAME"),
            "pages": _used_on_pages(row_value(row, "USED_ON_PAGES")),
        }
    for group in sorted(grouped):
        print(f"  {group}:")
        page_width = 0
        if group == "PAGE":
            page_width = max((len(str(component_id)) for component_id in grouped[group]), default=0)
        for component_id, info in grouped[group].items():
            name = str(info["name"] or "")
            pages = info["pages"]
            if group == "PAGE":
                page_id, _, page_name = name.partition(".")
                page_id = page_id or str(component_id)
                name = f"{page_id:>{page_width}}) {page_name.strip()}"
            elif pages:
                name += f" {pages}"
            print(f"    {'- ' if group != 'PAGE' else ''}{name}")
        print()


def _used_on_pages(value: object) -> list[object]:
    if value is None:
        return []
    if hasattr(value, "aslist"):
        return list(value.aslist())
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _store_application_metadata(path: Path, applications: list[ApexApplication]) -> None:
    if not applications:
        return
    payload = _load_yaml_mapping(path)
    for application in applications:
        payload[application.app_id] = {
            "owner": application.owner,
            "workspace": application.workspace,
            "workspace_id": application.workspace_id,
            "app_group": application.app_group,
            "app_id": application.app_id,
            "app_alias": application.app_alias,
            "app_name": application.app_name,
            "pages": application.pages,
            "updated_at": application.updated_at,
        }
    _store_yaml_mapping(path, payload)


def _store_workspace_developers(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    payload = _load_yaml_mapping(path)
    for row in rows:
        workspace = str(row_value(row, "WORKSPACE") or "")
        user_name = str(row_value(row, "USER_NAME") or "")
        user_mail = str(row_value(row, "USER_MAIL") or "")
        if not workspace or not user_name:
            continue
        workspace_developers = payload.get(workspace)
        if not isinstance(workspace_developers, dict):
            workspace_developers = {}
            payload[workspace] = workspace_developers
        workspace_developers[user_name] = user_mail
    _store_yaml_mapping(path, payload)


def _workspace_developers_from_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    developers: dict[str, dict[str, str]] = {}
    for row in rows:
        workspace = str(row_value(row, "WORKSPACE") or "")
        user_name = str(row_value(row, "USER_NAME") or "")
        user_mail = str(row_value(row, "USER_MAIL") or "")
        if not workspace or not user_name:
            continue
        developers.setdefault(workspace, {})[user_name] = user_mail
    return developers


def _load_yaml_mapping(path: Path) -> dict[Any, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _store_yaml_mapping(path: Path, payload: Mapping[Any, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            dict(payload),
            default_flow_style=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _merge_app_groups(path: Path, new_text: str) -> str:
    """Merge the workspace-shared ``app_groups.yaml`` instead of overwriting it.

    APEX's readable export emits only the application group(s) the exported app
    belongs to, so exporting a second app in the same workspace would otherwise
    clobber the first app's groups. Merge the freshly exported blocks with the
    existing file, keyed (deduplicated) by the group ``id``: existing entries
    keep their position, the latest export wins on conflicts, new groups are
    appended.
    """
    existing_text = path.read_text(encoding="utf-8") if path.is_file() else ""
    merged: dict[str, str] = {}
    for key, block in _parse_app_group_blocks(existing_text):
        merged[key] = block
    for key, block in _parse_app_group_blocks(new_text):
        merged[key] = block
    if not merged:
        return new_text
    return _render_app_group_blocks(merged.values())


def _parse_app_group_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == "---":
            continue
        if line.startswith("- "):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)
    parsed: list[tuple[str, str]] = []
    for block_lines in blocks:
        while block_lines and not block_lines[-1].strip():
            block_lines.pop()
        if not block_lines:
            continue
        block_id = None
        for line in block_lines:
            match = re.match(r"\s*id:\s*(\d+)", line)
            if match:
                block_id = match.group(1)
                break
        block_text = "\n".join(block_lines)
        parsed.append((block_id if block_id is not None else block_text, block_text))
    return parsed


def _render_app_group_blocks(blocks: Any) -> str:
    lines = ["---"]
    for block_text in blocks:
        lines.append(block_text)
        lines.append("")
    return "\n".join(lines) + "\n"


def _export_options(config: Mapping[str, object]) -> dict[str, object]:
    return {
        "originals"               : _flag(config.get("apex_keep_original_id")),
        "with_comments"           : _flag(config.get("apex_comments")),
        "with_date"               : _flag(config.get("apex_with_date")),
        "with_ir_public_reports"  : _flag(config.get("apex_with_ir_public_reports")),
        "with_ir_private_reports" : _flag(config.get("apex_with_ir_private_reports")),
        "with_ir_notifications"   : _flag(config.get("apex_with_ir_notifications")),
        "with_translations"       : _flag(config.get("apex_with_translations")),
        "with_no_subscriptions"   : _flag(config.get("apex_with_no_subscriptions")),
        "with_acl_assignments"    : _flag(config.get("apex_with_acl_assignments")),
        "with_audit_info"         : str(config.get("apex_with_audit_info") or ""),
    }


def _bind_params(sql: str, params: Mapping[str, Any]) -> dict[str, Any]:
    bind_names = set(re.findall(r":([A-Za-z][A-Za-z0-9_]*)", sql))
    return {
        key: value
        for key, value in params.items()
        if key in bind_names
    }


def _target_path(
    resolver: ApexFileResolver,
    application: ApexApplication,
    action: str,
    file_name: str,
) -> Path:
    relative = _strip_app_prefix(file_name, application)
    if action == "full":
        return resolver.full_export(application)
    if relative.startswith("workspace/"):
        return resolver.workspace_root() / Path(relative.removeprefix("workspace/"))
    if action == "split":
        return resolver.split_export(application, relative)
    if action == "readable":
        return resolver.readable_export(application, relative)
    if action == "embedded":
        return resolver.embedded_export(application, _embedded_relative(relative))
    raise ValueError(f"Unsupported APEX export action: {action}")


def _strip_app_prefix(file_name: str, application: ApexApplication) -> str:
    prefix = f"f{application.app_id}/"
    return file_name[len(prefix):] if file_name.startswith(prefix) else file_name


def _embedded_relative(relative: str) -> str:
    prefix = "embedded_code/"
    if relative.startswith(prefix):
        relative = relative[len(prefix):]
    if relative.startswith("pages/p"):
        relative = relative.replace("pages/p", "pages/page_", 1)
    return relative


def _payload_for(
    action: str,
    payload: str,
    relative: str,
    application: ApexApplication,
    enrichments: Mapping[int, str],
    config: Mapping[str, object],
    developers: Mapping[str, Mapping[str, str]],
    release: str | None = None,
) -> str:
    if action == "embedded":
        lines = payload.splitlines(keepends=True)
        embedded_payload = "".join(lines[10:]) if len(lines) > 10 else payload
        output = _normalize_text_line_endings(embedded_payload)
    elif action == "full" and relative.endswith(".sql"):
        output = _enrich_sql(payload, enrichments)
    elif action == "split" and relative.endswith(".sql"):
        output = _clean_split_sql(payload, relative, application, enrichments, config, developers)
    else:
        output = payload
    return _override_apex_release(output, release) if relative.endswith(".sql") else output


def _override_apex_release(payload: str, release: str | None) -> str:
    if not release:
        return payload
    return re.sub(r"p_release=>'[^']+'", f"p_release=>'{release}'", payload)


def _normalize_text_line_endings(payload: str) -> str:
    return payload.replace("\r\n", "\n").replace("\r", "\n")


def _skip_collection_file(action: str, relative: str) -> bool:
    if action != "split":
        return False
    return (
        relative == "install.sql"
        or relative == "application/create_application.sql"
        or re.fullmatch(r"f\d+\.sql", relative) is not None
    )


def _clean_split_sql(
    payload: str,
    relative: str,
    application: ApexApplication,
    enrichments: Mapping[int, str],
    config: Mapping[str, object],
    developers: Mapping[str, Mapping[str, str]],
) -> str:
    offset = _default_id_offset(payload)
    payload = re.sub(r",p_default_id_offset=>(\d+)", ",p_default_id_offset=>0", payload)
    payload = _clean_page_author(payload, relative, application, config, developers)
    return _enrich_sql(payload, enrichments, offset=offset)


def _clean_page_author(
    payload: str,
    relative: str,
    application: ApexApplication,
    config: Mapping[str, object],
    developers: Mapping[str, Mapping[str, str]],
) -> str:
    if not relative.startswith("application/pages/page_"):
        return payload
    author = str(config.get("apex_authors") or "")
    if not author or not bool(config.get("apex_keep_developers")):
        return payload
    developer = _extract_first(r",p_last_updated_by=>'([^']+)'", payload)
    if developer in developers.get(application.workspace, {}):
        return payload
    payload = re.sub(r",p_last_updated_by=>'([^']+)'", f",p_last_updated_by=>'{author}'", payload)
    timestamp = str(config.get("apex_timestamps") or "")
    if timestamp:
        payload = re.sub(
            r",p_last_upd_yyyymmddhh24miss=>'(\d+)'",
            f",p_last_upd_yyyymmddhh24miss=>'{timestamp}'",
            payload,
        )
    return payload


def _enrich_sql(payload: str, enrichments: Mapping[int, str], offset: int = 0) -> str:
    for component_id, component_name in enrichments.items():
        payload = payload.replace(
            f".id({component_id})\n",
            f".id({component_id})  -- {component_name}\n",
        )
        if offset:
            shifted_id = component_id - offset
            payload = payload.replace(
                f".id({shifted_id})\n",
                f".id({shifted_id})  -- {component_name}\n",
            )
    return payload


def _default_id_offset(payload: str) -> int:
    match = re.search(r",p_default_id_offset=>(\d+)", payload)
    return int(match.group(1)) if match else 0


def _extract_first(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def _enrichments(gateway: QueryGateway, application: ApexApplication) -> dict[int, str]:
    rows = gateway.fetch_all(queries.APEX_ID_NAMES_QUERY, {"app_id": application.app_id})
    return {
        int(row_value(row, "COMPONENT_ID")): (
            f"{row_value(row, 'COMPONENT_TYPE')}: {row_value(row, 'COMPONENT_NAME')}"
        )
        for row in rows
        if row_value(row, "COMPONENT_ID") is not None
    }


def _flag(value: object) -> str:
    return "Y" if bool(value) else "N"


def _blob_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if hasattr(value, "read"):
        return value.read()
    return bytes(value)


def _cleanup_sqlcl(output: str) -> list[str]:
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("Connected."):
            lines = lines[index + 1:]
            break
    if len(lines) >= 2 and lines[-2].startswith("Disconnected") and lines[-1].startswith("Version"):
        lines = lines[:-2]
    return lines


def _split_rest_modules(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    first: list[str] = []
    modules: list[list[str]] = []
    current: list[str] = []
    append = False
    for index, line in enumerate(lines):
        module_started = "ORDS.DEFINE_MODULE" in line
        if not append and not module_started:
            first.append(line)
        if module_started:
            if current:
                modules.append(current)
            current = []
            append = True
        next_line_is_end = index + 1 < len(lines) and lines[index + 1].startswith("END;")
        if line.strip().startswith("COMMIT;") and next_line_is_end:
            append = False
        if append:
            current.append(line.rstrip())
    if current:
        modules.append(current)
    return first, modules


def _rest_module_name(module: list[str]) -> str:
    text = "\n".join(module)
    match = re.search(r"p_module_name\s*=>\s*'([^']+)'", text)
    if not match:
        raise ValueError("Could not find ORDS module name in SQLcl REST export")
    return match.group(1)


def _schema_definition(lines: list[str]) -> list[str]:
    content: list[str] = []
    for line in lines:
        if line.startswith("BEGIN"):
            continue
        if line.startswith("-- Schema:"):
            line = line.split(" Date:")[0]
        if line.startswith("END;"):
            break
        content.append(line.rstrip())
    return list(filter(None, content))


def _plsql_block(lines: list[str]) -> str:
    body = "\n".join(list(filter(None, lines)))
    return f"BEGIN\n{body}\nEND;\n/\n"


def _rest_prefixes(config: Mapping[str, object]) -> list[str]:
    value = config.get("apex_rest_prefixes")
    if value is None:
        return [""]
    values = value if isinstance(value, list | tuple) else [value]
    return [str(item) for item in values]


def _matches_prefix(name: str, prefixes: list[str]) -> bool:
    return not prefixes or any(name.startswith(prefix) for prefix in prefixes)
