import json
import os
from pathlib import Path
from typing import Callable, Dict

from nicegui import app, ui
from pydantic import TypeAdapter

from tg_signer.webui.data import (
    CONFIG_META,
    DEFAULT_LOG_FILE,
    DEFAULT_WORKDIR,
    LOG_DIR,
    ConfigKind,
    delete_config,
    delete_plan,
    export_plans_json,
    get_workdir,
    import_plans_json,
    list_accounts,
    list_job_runs,
    list_log_files,
    list_plans,
    list_task_names,
    load_config,
    load_logs,
    load_sign_records,
    load_user_infos,
    run_plan_now,
    runtime_stats,
    save_account,
    save_config,
    save_plan,
    set_plan_enabled,
)
from tg_signer.webui.interactive import InteractiveSignerConfig
from tg_signer.webui.schema_utils import clean_schema

# DESIGN.md tokens (Apple control console)
PRIMARY = "#0066cc"
INK = "#1d1d1f"
CANVAS = "#ffffff"
PARCHMENT = "#f5f5f7"
SURFACE_TILE = "#272729"
SUCCESS = "#34c759"
FAIL = "#ff3b30"

SIGNER_TEMPLATE: Dict[str, object] = {
    "chats": [
        {
            "chat_id": "@channel_or_user",
            "message_thread_id": None,
            "name": "示例任务",
            "delete_after": None,
            "actions": [{"action": 1, "text": "签到"}],
            "action_interval": 1,
        }
    ],
    "sign_at": "0 6 * * *",
    "random_seconds": 0,
    "sign_interval": 1,
}

MONITOR_TEMPLATE: Dict[str, object] = {
    "match_cfgs": [
        {
            "chat_id": "@channel_or_user",
            "rule": "contains",
            "rule_value": "关键词",
            "from_user_ids": None,
            "always_ignore_me": False,
            "default_send_text": "自动回复",
            "ai_reply": False,
            "ai_prompt": None,
            "send_text_search_regex": None,
            "send_text_template": None,
            "delete_after": None,
            "ignore_case": True,
            "forward_to_chat_id": None,
            "external_forwards": None,
            "push_via_server_chan": False,
            "server_chan_send_key": None,
        }
    ]
}


AUTH_CODE_ENV = "TG_SIGNER_GUI_AUTHCODE"
AUTH_STORAGE_KEY = "tg_signer_gui_auth_code"


class UIState:
    def __init__(self) -> None:
        self.workdir: Path = get_workdir(DEFAULT_WORKDIR)
        self.session_dir: Path = Path(".")
        self.log_path: Path = DEFAULT_LOG_FILE
        self.log_limit: int = 200
        self.record_filter: str = ""

    def set_workdir(self, path_str: str) -> None:
        self.workdir = get_workdir(Path(path_str).expanduser())

    def set_session_dir(self, path_str: str) -> None:
        self.session_dir = Path(path_str).expanduser()

    def set_log_path(self, path_str: str) -> None:
        self.log_path = Path(path_str).expanduser()


state = UIState()


def pretty_json(data: Dict[str, object]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def notify_error(exc: Exception) -> None:
    ui.notify(f"{exc}", type="negative")


class BaseConfigBlock:
    def __init__(
        self,
        kind: ConfigKind,
        template: Dict[str, object],
    ):
        self.kind = kind
        self.template = template
        self.title = "签到配置 (signer)" if kind == "signer" else "监控配置 (monitor)"
        self.root_dir, self.cfg_cls = CONFIG_META[kind]
        with ui.card().classes("w-full shadow-md"):
            ui.label(self.title).classes("text-lg font-semibold")
            ui.label(f"目录: {self.root_dir}/<name>/config.json").classes(
                "text-sm text-gray-500"
            )
            with ui.row().classes("items-end w-full gap-3"):
                self.select = ui.select(
                    label="选择配置",
                    options=[],
                    with_input=True,
                    on_change=self.load_current,
                ).classes("min-w-[240px]")
                ui.button("重置", on_click=self.clear_selection).props("outline")
                self.name_input = ui.input(
                    label="保存为/新建名称",
                    placeholder="my_task",
                ).classes("min-w-[200px]")
                ui.button("使用示例", on_click=self.fill_template)
                self.setup_toolbar()

            # MonitorConfig schema causes json_editor to fail rendering due to "format": "uri" etc.
            # We need to clean the schema before passing it to the editor.
            schema = TypeAdapter(self.cfg_cls | None).json_schema()
            if self.kind == "monitor":
                schema = clean_schema(schema)

            def on_change(e):
                self.editor.properties["content"] = e.content

            self.editor = ui.json_editor(
                {"content": {"json": None}},
                schema=schema,
                on_change=on_change,
            )
            self.selected_name: dict[str, str] = {"value": ""}

            with ui.row().classes("gap-2 items-center"):
                ui.button("刷新列表", on_click=self.refresh_options)
                ui.button("加载", on_click=self.load_current)
                ui.button("保存", color="primary", on_click=self.save_current)
                ui.button("删除", color="negative", on_click=self.delete_current)
            self.setup_footer()

    def clear_selection(self) -> None:
        self.select.value = None
        self.name_input.value = ""
        self.fill_template()
        self.selected_name["value"] = ""

    def setup_toolbar(self):
        """Override to add more buttons to the top toolbar"""
        pass

    def setup_footer(self):
        """Override to add more buttons to the bottom footer"""
        pass

    def __call__(self, *args, **kwargs):
        self.refresh_options()

    def refresh_options(self) -> None:
        options = list_task_names(self.kind, state.workdir)
        self.select.options = options
        self.select.update()

    def load_current(self) -> None:
        target = self.select.value
        if not target:
            return
        try:
            entry = load_config(self.kind, target, workdir=state.workdir)
            self.editor.properties["content"]["json"] = entry.payload
            self.name_input.value = entry.name
            self.editor.update()
            self.name_input.update()
            self.editor.run_editor_method(":expand", "[]", "path => true")
            self.selected_name["value"] = target
            self.on_loaded(target)
        except Exception as exc:  # noqa: BLE001
            notify_error(exc)

    def on_loaded(self, target: str):
        """Hook called after config is loaded"""
        pass

    def save_current(self) -> None:
        target = (self.name_input.value or self.select.value or "").strip()
        if not target:
            ui.notify("请先填写配置名称", type="warning")
            return
        try:
            save_config(
                self.kind,
                target,
                self.editor.properties["content"]["json"] or "{}",
                workdir=state.workdir,
            )
            self.refresh_options()
            self.select.value = target
            self.select.update()
            ui.notify("保存成功", type="positive")
        except Exception as exc:  # noqa: BLE001
            notify_error(exc)

    def fill_template(self) -> None:
        self.editor.properties["content"]["json"] = self.template
        self.editor.update()

    def delete_current(self) -> None:
        target = (self.select.value or "").strip() or (
            self.name_input.value or ""
        ).strip()
        if not target:
            ui.notify("请选择要删除的配置", type="warning")
            return
        try:
            delete_config(self.kind, target, workdir=state.workdir)
            self.refresh_options()
            if self.select.value == target:
                self.select.value = None
                self.select.update()
            ui.notify("已删除配置", type="positive")
        except Exception as exc:  # noqa: BLE001
            notify_error(exc)


class SignerBlock(BaseConfigBlock):
    def __init__(
        self,
        template: Dict[str, object],
        *,
        goto_records: Callable[[str], None] = lambda _task: None,
    ):
        self.record_btn = None
        self.record_hint = None
        self._goto_records = goto_records
        super().__init__("signer", template)

    def setup_toolbar(self):
        ui.button("交互式配置", on_click=self.open_interactive).props("outline")

    def setup_footer(self):
        self.record_hint = ui.label("").classes("text-sm text-primary")
        self.record_btn = ui.button(
            "查看签到记录",
            color="primary",
            on_click=self.goto_records,
        ).classes("min-w-[120px]")
        self.record_btn.disable()

    def on_loaded(self, target: str):
        records = load_sign_records(state.workdir)
        has_record = any(r.task == target for r in records)
        if has_record:
            self.record_btn.enable()
            self.record_hint.text = f"发现签到记录: {target}"
        else:
            self.record_btn.disable()
            self.record_hint.text = "无签到记录"
        self.record_hint.update()
        self.record_btn.update()

    def goto_records(self):
        self._goto_records(self.selected_name["value"])

    def open_interactive(self):
        def on_complete():
            self.refresh_options()
            # If the user saved a config with the same name as currently selected, reload it
            if self.select.value:
                self.load_current()

        initial_config = self.editor.properties["content"].get("json")
        initial_name = self.name_input.value or self.select.value or ""

        wizard = InteractiveSignerConfig(
            state.workdir,
            on_complete=on_complete,
            initial_config=initial_config,
            initial_name=initial_name,
        )
        wizard.open()


class MonitorBlock(BaseConfigBlock):
    def __init__(self, template: Dict[str, object]):
        super().__init__("monitor", template)


def user_info_block() -> Callable[[], None]:
    container = ui.column().classes("w-full gap-2")

    def refresh() -> None:
        container.clear()
        entries = load_user_infos(state.workdir)
        with container:
            if not entries:
                ui.label("未找到用户信息").classes("text-gray-500")
                return
            for entry in entries:
                name = entry.data.get("first_name") or ""
                header = f"{entry.user_id} {name}".strip()
                with ui.expansion(header, icon="person"):
                    ui.label(f"文件: {entry.path}")
                    ui.code(pretty_json(entry.data), language="json").classes("w-full")

                    if entry.latest_chats:
                        ui.separator().classes("my-2")
                        ui.label(f"最近聊天 ({len(entry.latest_chats)})").classes(
                            "font-semibold"
                        )

                        chat_rows = []
                        for chat in entry.latest_chats:
                            chat_rows.append(
                                {
                                    "id": chat.get("id"),
                                    "title": chat.get("title")
                                    or chat.get("first_name")
                                    or "N/A",
                                    "type": chat.get("type"),
                                    "username": chat.get("username") or "",
                                }
                            )

                        ui.table(
                            columns=[
                                {
                                    "name": "id",
                                    "label": "ID",
                                    "field": "id",
                                    "align": "left",
                                },
                                {
                                    "name": "title",
                                    "label": "名称",
                                    "field": "title",
                                    "align": "left",
                                },
                                {
                                    "name": "type",
                                    "label": "类型",
                                    "field": "type",
                                    "align": "left",
                                },
                                {
                                    "name": "username",
                                    "label": "用户名",
                                    "field": "username",
                                    "align": "left",
                                },
                            ],
                            rows=chat_rows,
                            pagination=10,
                        ).classes("w-full").props("flat dense")
                    else:
                        ui.label("未找到最近聊天记录").classes(
                            "text-gray-500 text-sm mt-2"
                        )

    return refresh


class SignRecordBlock:
    def __init__(self):
        self.container = ui.column().classes("w-full gap-3")
        with ui.row().classes("items-end gap-3"):
            self.filter_input = ui.input(
                label="筛选任务/用户",
                placeholder="输入任务名或用户ID过滤",
                value=state.record_filter,
                on_change=lambda e: self._update_filter(e.value),
            ).classes("w-full")
            ui.button("清除筛选", on_click=lambda: self._update_filter("")).props(
                "outline"
            )
        self.status = ui.label("").classes("text-sm text-gray-500")

    def _update_filter(self, value: str) -> None:
        state.record_filter = value or ""
        self.refresh()

    def refresh(
        self,
    ) -> None:
        self.container.clear()
        records = load_sign_records(state.workdir)
        keyword = (state.record_filter or "").lower().strip()
        if keyword:
            records = [
                r
                for r in records
                if keyword in r.task.lower()
                or (r.user_id and keyword in str(r.user_id).lower())
            ]
        with self.container:
            if not records:
                self.status.text = "未找到匹配的签到记录" if keyword else "尚无签到记录"
                self.status.update()
                return
            self.status.text = f"共 {len(records)} 组记录"
            self.status.update()
            for record in records:
                user_text = record.user_id or "默认"
                header = f"{record.task} / {user_text}（{len(record.records)}条）"
                with ui.expansion(header, icon="event").classes("shadow-sm"):
                    ui.label(f"来源: {record.path}").classes("text-gray-500")
                    if not record.records:
                        ui.label("暂无记录").classes("text-gray-500")
                        continue
                    rows = [{"日期": k, "时间": v} for k, v in record.records]
                    ui.table(
                        columns=[
                            {"name": "日期", "label": "日期", "field": "日期"},
                            {"name": "时间", "label": "时间", "field": "时间"},
                        ],
                        rows=rows,
                    ).classes("w-full").props("flat dense")

    def __call__(self, *args, **kwargs):
        return self.refresh()


def log_block() -> Callable[[], None]:
    with ui.card().classes("w-full shadow-sm"):
        ui.label("日志查看").classes("text-md font-semibold")
        ui.label("查看最新日志行，可自定义文件路径和行数。").classes(
            "text-sm text-gray-500 mb-1"
        )

        with ui.row().classes("items-end w-full gap-3 flex-wrap"):
            limit_input = ui.number(
                label="日志行数",
                value=state.log_limit,
                min=10,
                max=2000,
                format="%d",
            ).classes("w-32")
            log_select = ui.select(
                label=f"选择日志文件（{LOG_DIR}/）",
                options=[],
                on_change=lambda e: select_log_file(e.value),
            ).classes("min-w-[220px]")
            log_path_input = ui.input(
                label="日志路径（可自定义）", value=str(state.log_path)
            ).classes("w-full")
        log_area = ui.scroll_area().classes(
            "w-full bg-gray-50 rounded-lg border border-gray-200"
        )
        log_area.style("max-height: 420px")
        with log_area:
            log_list = (
                ui.column()
                .classes("w-full gap-0 p-3 font-mono text-sm")
                .style("white-space: pre;")
            )

        def classify_line(line: str) -> str:
            upper = line.upper()
            if "ERROR" in upper:
                return "text-red-700"
            if "WARN" in upper:
                return "text-amber-700"
            if "INFO" in upper:
                return "text-blue-700"
            return "text-gray-800"

        def refresh_log_options() -> None:
            options = [str(p) for p in list_log_files(LOG_DIR)]
            current_path = str(log_path_input.value or state.log_path)
            if current_path and current_path not in options:
                options.insert(0, current_path)
            log_select.options = options
            log_select.value = current_path
            log_select.update()

        def select_log_file(path_value: str | None) -> None:
            if not path_value:
                return
            log_path_input.value = path_value
            log_path_input.update()
            refresh()

        def refresh() -> None:
            refresh_log_options()
            try:
                state.log_limit = int(limit_input.value or state.log_limit)
            except ValueError:
                state.log_limit = 200
            state.set_log_path(log_path_input.value or str(DEFAULT_LOG_FILE))
            path, lines = load_logs(state.log_limit, log_path_input.value)
            log_list.clear()
            if not lines:
                with log_list:
                    ui.label(f"未找到日志文件: {path}").classes("text-gray-500 text-sm")
                log_list.update()
                refresh_status(f"未找到日志文件: {path}")
                return

            with log_list:
                for line in lines:
                    color = classify_line(line)
                    ui.label(line).classes(f"w-full {color}").style("white-space: pre;")
            log_list.update()
            refresh_status(f"文件: {path} | 显示最新 {len(lines)} 行")

        with ui.row().classes("gap-2 mt-2 items-center justify-between"):
            ui.button("刷新日志", on_click=refresh)
            log_status = ui.label("").classes("text-xs text-gray-500")

        def refresh_status(text: str) -> None:
            log_status.text = text
            log_status.update()

        refresh_log_options()

    return refresh


def top_controls(on_refresh: Callable[[], None]) -> None:
    with ui.card().classes("w-full").style(
        f"background:{PARCHMENT};border:1px solid #e0e0e0;box-shadow:none;"
    ):
        ui.label("基础设置").classes("text-lg font-semibold").style(f"color:{INK};")
        with ui.row().classes("items-end w-full gap-3 flex-wrap"):
            workdir_input = ui.input(
                label="工作目录",
                value=str(state.workdir),
                placeholder=".signer",
            ).classes("min-w-[220px]")
            session_input = ui.input(
                label="Session 目录",
                value=str(state.session_dir),
                placeholder=".",
            ).classes("min-w-[220px]")
            ui.button(
                "应用并刷新",
                color="primary",
                on_click=lambda: _apply_paths(workdir_input, session_input, on_refresh),
            ).props("rounded").style(f"background:{PRIMARY} !important;")


def _apply_paths(workdir_input, session_input, on_refresh: Callable[[], None]) -> None:
    try:
        state.set_workdir(workdir_input.value or str(DEFAULT_WORKDIR))
        state.set_session_dir(session_input.value or ".")
        ui.notify(
            f"已切换: workdir={state.workdir} session_dir={state.session_dir}",
            type="positive",
        )
    except Exception as exc:  # noqa: BLE001
        notify_error(exc)
        return
    on_refresh()


def _hero_stats_block() -> Callable[[], None]:
    container = ui.element("div").classes("w-full rounded-xl p-6 mb-3").style(
        f"background:{SURFACE_TILE};color:#fff;"
    )

    def refresh() -> None:
        container.clear()
        stats = runtime_stats(state.workdir)
        with container:
            with ui.row().classes("w-full justify-between items-end flex-wrap gap-4"):
                for label, value in (
                    ("SCHEDULED", stats.scheduled),
                    ("RUNNING", stats.running),
                    ("FAILED", stats.failed),
                    ("TOTAL", stats.total_plans),
                ):
                    with ui.column().classes("items-start gap-1"):
                        ui.label(str(value)).style(
                            "font-size:40px;font-weight:600;letter-spacing:-0.28px;line-height:1.1;"
                        )
                        ui.label(label).style(
                            "font-size:12px;letter-spacing:-0.12px;opacity:0.8;"
                        )
                status = "运行中" if stats.scheduler_running else "未启动"
                tick = stats.last_tick_at or "—"
                with ui.column().classes("items-end gap-1"):
                    ui.label(f"调度器 · {status}").style("font-size:14px;")
                    ui.label(f"上次 tick: {tick}").style(
                        "font-size:12px;opacity:0.7;"
                    )

    return refresh


def _plans_block() -> Callable[[], None]:
    root = ui.column().classes("w-full gap-3")

    def open_editor(plan_id: int | None = None) -> None:
        existing = None
        if plan_id is not None:
            for p in list_plans(state.workdir):
                if p.id == plan_id:
                    existing = p
                    break
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl"):
            ui.label("编辑计划" if existing else "新建计划").classes(
                "text-lg font-semibold"
            )
            account_in = ui.input(
                "账号", value=(existing.account if existing else "")
            ).classes("w-full")
            task_type_in = ui.select(
                options=["sign", "automation", "monitor"],
                value=(existing.task_type if existing else "sign"),
                label="任务类型",
            ).classes("w-full")
            task_ref_in = ui.input(
                "任务名", value=(existing.task_ref if existing else "")
            ).classes("w-full")
            schedule_in = ui.input(
                "时刻/cron",
                value=(existing.schedule_expr if existing else "06:00:00"),
                placeholder="06:00:00 或 0 6 * * *",
            ).classes("w-full")
            random_in = ui.number(
                "随机秒数",
                value=(existing.random_seconds if existing else 0),
                min=0,
            ).classes("w-full")
            retries_in = ui.number(
                "最大重试",
                value=(existing.max_retries if existing else 1),
                min=0,
            ).classes("w-full")
            enabled_in = ui.switch(
                "启用", value=(existing.enabled if existing else True)
            )

            def save() -> None:
                try:
                    payload = {
                        "id": existing.id if existing else None,
                        "account": (account_in.value or "").strip(),
                        "task_type": task_type_in.value or "sign",
                        "task_ref": (task_ref_in.value or "").strip(),
                        "schedule_expr": (schedule_in.value or "").strip(),
                        "random_seconds": int(random_in.value or 0),
                        "max_retries": int(retries_in.value or 0),
                        "enabled": bool(enabled_in.value),
                        # Keep last_run; next_run is recomputed by save_plan when
                        # schedule/random/enable changes.
                        "next_run_at": existing.next_run_at if existing else None,
                        "last_run_at": existing.last_run_at if existing else None,
                    }
                    if not payload["account"] or not payload["task_ref"]:
                        ui.notify("账号和任务名必填", type="warning")
                        return
                    save_plan(payload, workdir=state.workdir)
                    ui.notify("计划已保存", type="positive")
                    dialog.close()
                    refresh()
                except Exception as exc:  # noqa: BLE001
                    notify_error(exc)

            with ui.row().classes("gap-2"):
                ui.button("保存", color="primary", on_click=save).props(
                    f"background:{PRIMARY} !important;"
                )
                ui.button("取消", on_click=dialog.close).props("outline")
        dialog.open()

    def refresh() -> None:
        root.clear()
        with root:
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                ui.label("计划表").classes("text-lg font-semibold").style(
                    f"color:{INK};"
                )
                with ui.row().classes("gap-2"):
                    ui.button("新建计划", color="primary", on_click=lambda: open_editor()).style(
                        f"background:{PRIMARY} !important;"
                    ).props("rounded")
                    ui.button(
                        "导出 JSON",
                        on_click=lambda: ui.download(
                            export_plans_json(state.workdir).encode("utf-8"),
                            "schedule_plans.json",
                        ),
                    ).props("outline rounded")
                    import_area = ui.textarea(
                        label="导入 JSON",
                        placeholder='粘贴 {"plans":[...]} 后点导入',
                    ).classes("w-80")
                    ui.button(
                        "导入",
                        on_click=lambda: _do_import(import_area),
                    ).props("outline rounded")

            plans = list_plans(state.workdir)
            if not plans:
                with ui.card().classes("w-full").style(
                    f"background:{PARCHMENT};box-shadow:none;"
                ):
                    ui.label("暂无计划").style(
                        "font-size:28px;font-weight:400;color:#1d1d1f;"
                    )
                    ui.label("创建账号绑定任务的每日计划，替代外部脚本串跑。").classes(
                        "text-gray-500"
                    )
                    ui.button(
                        "新建计划", color="primary", on_click=lambda: open_editor()
                    ).style(f"background:{PRIMARY} !important;").props(
                        "rounded"
                    )
                return

            for idx, plan in enumerate(plans):
                bg = CANVAS if idx % 2 == 0 else PARCHMENT
                with ui.card().classes("w-full").style(
                    f"background:{bg};box-shadow:none;border:1px solid #e0e0e0;"
                ):
                    with ui.row().classes(
                        "w-full items-center justify-between flex-wrap gap-2"
                    ):
                        title = f"#{plan.id} {plan.account} · {plan.task_type}/{plan.task_ref}"
                        ui.label(title).style(
                            "font-size:17px;font-weight:600;color:#1d1d1f;"
                        )
                        badge = "启用" if plan.enabled else "暂停"
                        color = SUCCESS if plan.enabled else "#7a7a7a"
                        ui.badge(badge).style(
                            f"background:{color};color:#fff;border-radius:9999px;"
                        )
                    ui.label(
                        f"调度 {plan.schedule_expr} · 下次 {plan.next_run_at or '—'} · 上次 {plan.last_run_at or '—'}"
                    ).classes("text-sm text-gray-600")
                    with ui.row().classes("gap-2 flex-wrap mt-1"):
                        ui.button(
                            "立即执行",
                            on_click=lambda p=plan: _run_now(p.id),
                        ).props("rounded dense").style(
                            f"background:{PRIMARY} !important;color:#fff;"
                        )
                        ui.button(
                            "编辑",
                            on_click=lambda p=plan: open_editor(p.id),
                        ).props("outline rounded dense")
                        ui.button(
                            "暂停" if plan.enabled else "启用",
                            on_click=lambda p=plan: _toggle(p.id, not p.enabled),
                        ).props("outline rounded dense")
                        ui.button(
                            "删除",
                            color="negative",
                            on_click=lambda p=plan: _delete(p.id),
                        ).props("outline rounded dense")

    def _do_import(area) -> None:
        try:
            n = import_plans_json(area.value or "", workdir=state.workdir)
            ui.notify(f"已导入 {n} 条计划", type="positive")
            refresh()
        except Exception as exc:  # noqa: BLE001
            notify_error(exc)

    def _toggle(plan_id: int, enabled: bool) -> None:
        try:
            set_plan_enabled(plan_id, enabled, workdir=state.workdir)
            refresh()
        except Exception as exc:  # noqa: BLE001
            notify_error(exc)

    def _delete(plan_id: int) -> None:
        try:
            delete_plan(plan_id, workdir=state.workdir)
            ui.notify("已删除计划", type="positive")
            refresh()
        except Exception as exc:  # noqa: BLE001
            notify_error(exc)

    def _run_now(plan_id: int) -> None:
        async def _go():
            try:
                await run_plan_now(plan_id)
                ui.notify("已触发执行", type="positive")
                refresh()
            except Exception as exc:  # noqa: BLE001
                notify_error(exc)

        # NiceGUI will schedule the coroutine when button is async-compatible;
        # use create_task via asyncio for safety.
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            loop.create_task(_go())
        except RuntimeError:
            asyncio.ensure_future(_go())

    return refresh


def _accounts_block() -> Callable[[], None]:
    root = ui.column().classes("w-full gap-3")

    def refresh() -> None:
        root.clear()
        with root:
            ui.label("账号").classes("text-lg font-semibold")
            ui.label("根据 session 目录发现账号，可设置代理。").classes(
                "text-sm text-gray-500"
            )
            accounts = list_accounts(state.workdir, state.session_dir)
            if not accounts:
                ui.label("未发现账号 session。请先 CLI login。").classes("text-gray-500")
                return
            for acc in accounts:
                with ui.card().classes("w-full").style(
                    f"background:{PARCHMENT};box-shadow:none;"
                ):
                    with ui.row().classes(
                        "w-full items-end gap-3 flex-wrap justify-between"
                    ):
                        with ui.column().classes("gap-1"):
                            ui.label(acc.name).style("font-size:21px;font-weight:600;")
                            sess = "有 session" if acc.session_present else "无 session"
                            ui.label(sess).classes("text-sm text-gray-600")
                        proxy_in = ui.input(
                            "代理",
                            value=acc.proxy or "",
                            placeholder="socks5://host:port",
                        ).classes("min-w-[280px]")
                        enabled_in = ui.switch("启用", value=acc.enabled)

                        def save(
                            name=acc.name, proxy_in=proxy_in, enabled_in=enabled_in
                        ):
                            try:
                                save_account(
                                    {
                                        "name": name,
                                        "proxy": (proxy_in.value or "").strip() or None,
                                        "enabled": bool(enabled_in.value),
                                        "labels": "",
                                    },
                                    workdir=state.workdir,
                                    session_dir=state.session_dir,
                                )
                                ui.notify(f"已保存 {name}", type="positive")
                            except Exception as exc:  # noqa: BLE001
                                notify_error(exc)

                        ui.button("保存", color="primary", on_click=save).style(
                            f"background:{PRIMARY} !important;"
                        ).props("rounded")

    return refresh


def _jobs_block() -> Callable[[], None]:
    root = ui.column().classes("w-full gap-2")

    def refresh() -> None:
        root.clear()
        with root:
            ui.label("执行历史").classes("text-lg font-semibold")
            jobs = list_job_runs(state.workdir, limit=80)
            if not jobs:
                ui.label("尚无执行记录").classes("text-gray-500")
                return
            rows = [
                {
                    "id": j.id,
                    "account": j.account,
                    "task": f"{j.task_type}/{j.task_ref}",
                    "status": j.status.value
                    if hasattr(j.status, "value")
                    else j.status,
                    "attempt": j.attempt,
                    "started": j.started_at or "",
                    "finished": j.finished_at or "",
                    "error": (j.error or "")[:120],
                    "source": j.source,
                }
                for j in jobs
            ]
            ui.table(
                columns=[
                    {"name": "id", "label": "ID", "field": "id"},
                    {"name": "account", "label": "账号", "field": "account"},
                    {"name": "task", "label": "任务", "field": "task"},
                    {"name": "status", "label": "状态", "field": "status"},
                    {"name": "attempt", "label": "次数", "field": "attempt"},
                    {"name": "started", "label": "开始", "field": "started"},
                    {"name": "finished", "label": "结束", "field": "finished"},
                    {"name": "error", "label": "错误", "field": "error"},
                    {"name": "source", "label": "来源", "field": "source"},
                ],
                rows=rows,
                pagination=20,
            ).classes("w-full").props("flat dense")

    return refresh


def _build_dashboard(container) -> None:
    with container:
        ui.label("TG Signer 运维台").classes(
            "text-2xl font-semibold tracking-wide mb-2"
        ).style(f"color:{INK};")
        refreshers: list[Callable[[], None]] = []
        refresh_records: "SignRecordBlock"

        def refresh_all() -> None:
            for refresh in refreshers:
                refresh()

        top_controls(refresh_all)
        refreshers.append(_hero_stats_block())

        with ui.tabs().classes("w-full") as tabs:
            tab_plans = ui.tab("计划表")
            tab_accounts = ui.tab("账号")
            tab_configs = ui.tab("配置管理")
            tab_jobs = ui.tab("执行历史")
            tab_users = ui.tab("用户信息")
            tab_records = ui.tab("签到记录")
            tab_logs = ui.tab("日志")

        def goto_records(task_name: str) -> None:
            tabs.value = tab_records
            tabs.update()
            refresh_records.filter_input.set_value(task_name)

        with ui.tab_panels(tabs, value=tab_plans).classes("w-full"):
            with ui.tab_panel(tab_plans):
                refreshers.append(_plans_block())
            with ui.tab_panel(tab_accounts):
                refreshers.append(_accounts_block())
            with ui.tab_panel(tab_configs):
                ui.label(
                    "管理 signer 和 monitor 的配置文件，支持查看、编辑和删除。"
                ).classes("text-gray-600")
                with ui.tabs().classes("mt-2") as sub_tabs:
                    tab_signer = ui.tab("Signer")
                    tab_monitor = ui.tab("Monitor")
                with ui.tab_panels(sub_tabs, value=tab_signer).classes("w-full"):
                    with ui.tab_panel(tab_signer):
                        refreshers.append(
                            SignerBlock(SIGNER_TEMPLATE, goto_records=goto_records)
                        )
                    with ui.tab_panel(tab_monitor):
                        refreshers.append(MonitorBlock(MONITOR_TEMPLATE))
            with ui.tab_panel(tab_jobs):
                refreshers.append(_jobs_block())
            with ui.tab_panel(tab_users):
                ui.label("查看当前已登录账户信息 (users/*/me.json)。").classes(
                    "text-gray-600"
                )
                refreshers.append(user_info_block())
            with ui.tab_panel(tab_records):
                ui.label(
                    "签到记录（优先读取 SQLite，兼容旧 sign_record.json）"
                ).classes("text-gray-600")
                refresh_records = SignRecordBlock()
                refreshers.append(refresh_records)
            with ui.tab_panel(tab_logs):
                ui.label("查看日志文件的最新行。").classes("text-gray-600")
                refreshers.append(log_block())

        refresh_all()
        ui.timer(5.0, refresh_all)


def _auth_gate(container, auth_code: str, on_success: Callable[[], None]) -> None:
    with container:
        ui.label("TG Signer Web 控制台").classes(
            "text-2xl font-semibold tracking-wide mb-2"
        )
        ui.label("已启用访问控制，请输入 Auth Code 继续使用 Web 控制台。").classes(
            "text-gray-600"
        )
        with ui.column().classes("w-full items-center"):
            with ui.card().classes("w-full max-w-xl shadow-md"):
                ui.label("Auth Code 验证").classes("text-lg font-semibold")
                ui.label("检测到auth_code环境变量已配置，首次访问需验证。").classes(
                    "text-sm text-gray-500"
                )
                code_input = ui.input(
                    label="Auth Code",
                    placeholder="请输入授权码",
                    password=True,
                    password_toggle_button=True,
                ).classes("w-full")
                status = ui.label("").classes("text-sm text-negative")

                def verify() -> None:
                    # TODO: Security improvements needed
                    # 1. Add rate limiting (e.g. max 5 attempts per minute) to prevent brute-force attacks.
                    # 2. Use secrets.compare_digest(code, auth_code) to prevent timing attacks.
                    code = (code_input.value or "").strip()
                    if not code:
                        ui.notify("请输入授权码", type="warning")
                        return
                    if code != auth_code:
                        status.text = "授权码错误，请重试"
                        status.update()
                        code_input.set_value("")
                        ui.notify("认证失败", type="negative")
                        return
                    app.storage.user[AUTH_STORAGE_KEY] = auth_code
                    ui.notify("认证成功", type="positive")
                    container.clear()
                    on_success()

                ui.button("验证并进入", color="primary", on_click=verify).classes(
                    "w-full mt-2"
                )


def build_ui(auth_code: str = None) -> None:
    ui.page_title("TG Signer Web 控制台")
    root = ui.column().classes("w-full gap-3")

    def render_dashboard() -> None:
        root.clear()
        _build_dashboard(root)

    auth_code = auth_code or (os.environ.get(AUTH_CODE_ENV) or "").strip()
    if not auth_code:
        render_dashboard()
        return

    if app.storage.user.get(AUTH_STORAGE_KEY) == auth_code:
        render_dashboard()
        return

    root.clear()
    _auth_gate(root, auth_code, render_dashboard)


def main(
    host: str = None,
    port: int = None,
    storage_secret: str = None,
    *,
    enable_scheduler: bool = False,
    workdir: str = None,
    session_dir: str = ".",
    proxy: str = None,
    num_of_dialogs: int = 50,
) -> None:
    if workdir:
        state.set_workdir(workdir)
    if session_dir:
        state.set_session_dir(session_dir)

    if enable_scheduler:

        from tg_signer.runtime.service import RuntimeService, set_runtime

        async def _boot_runtime():
            rt = RuntimeService(
                workdir=str(state.workdir),
                session_dir=str(state.session_dir),
                default_proxy=proxy,
                num_of_dialogs=num_of_dialogs,
            )
            await rt.start()
            set_runtime(rt)

        # NiceGUI shares the process loop; schedule runtime start at app startup.
        app.on_startup(_boot_runtime)

        async def _shutdown_runtime():
            from tg_signer.runtime.service import get_runtime

            rt = get_runtime()
            if rt is not None:
                await rt.stop()

        app.on_shutdown(_shutdown_runtime)

    ui.run(
        build_ui,
        title="TG Signer WebUI",
        favicon="⚙️",
        reload=False,
        host=host,
        port=port,
        show=False,
        storage_secret=storage_secret or os.urandom(10).hex(),
    )
